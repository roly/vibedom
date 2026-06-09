"""Session management and logging."""

import json
import subprocess
from dataclasses import dataclass, asdict
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


@dataclass
class SessionState:
    """Represents the persisted state of a session (state.json).

    Example:
        state = SessionState.create(workspace, 'docker')
        state.save(session_dir)
        # later:
        state = SessionState.load(session_dir)
    """

    session_id: str
    workspace: str
    runtime: str
    container_name: str
    status: str          # 'running' | 'complete' | 'abandoned'
    started_at: str      # ISO 8601 string
    ended_at: Optional[str] = None
    bundle_path: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_pid: Optional[int] = None

    @classmethod
    def create(
        cls,
        workspace: Path,
        runtime: str,
        session_id: Optional[str] = None,
        container_name: Optional[str] = None,
    ) -> 'SessionState':
        """Create a new SessionState for a fresh session."""
        from vibedom.words import generate_session_id
        if session_id is None:
            session_id = generate_session_id(workspace.name)
        if container_name is None:
            container_name = f'vibedom-{workspace.name}'
        return cls(
            session_id=session_id,
            workspace=str(workspace),
            runtime=runtime,
            container_name=container_name,
            status='running',
            started_at=datetime.now().isoformat(timespec='seconds'),
        )

    @classmethod
    def load(cls, session_dir: Path) -> 'SessionState':
        """Load state from session directory."""
        state_file = session_dir / 'state.json'
        try:
            data = json.loads(state_file.read_text())
            return cls(**data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Malformed state.json in {session_dir}: {e}") from e
        except TypeError as e:
            raise ValueError(f"Invalid state.json schema in {session_dir}: {e}") from e

    def save(self, session_dir: Path) -> None:
        """Persist state to session directory."""
        state_file = session_dir / 'state.json'
        state_file.write_text(json.dumps(asdict(self), indent=2))

    def mark_complete(self, bundle_path: Path, session_dir: Path) -> None:
        """Transition to complete status and persist."""
        self.status = 'complete'
        self.ended_at = datetime.now().isoformat(timespec='seconds')
        self.bundle_path = str(bundle_path)
        self.save(session_dir)

    def mark_abandoned(self, session_dir: Path) -> None:
        """Transition to abandoned status and persist."""
        self.status = 'abandoned'
        self.ended_at = datetime.now().isoformat(timespec='seconds')
        self.save(session_dir)

    @property
    def started_at_dt(self) -> datetime:
        """started_at as a datetime object."""
        return datetime.fromisoformat(self.started_at)


class Session:
    """Manages a sandbox session: lifecycle, state, and logging."""

    def __init__(self, state: 'SessionState', session_dir: Path):
        self.state = state
        self.session_dir = session_dir
        self.network_log = session_dir / 'network.jsonl'
        self.session_log = session_dir / 'session.log'

    @classmethod
    def start(cls, workspace: Path, runtime: str, logs_dir: Path) -> 'Session':
        """Create and initialise a new session.

        Args:
            workspace: Resolved path to workspace directory
            runtime: Container runtime ('docker' or 'apple')
            logs_dir: Base directory for all session logs

        Returns:
            New Session with state.json written and initial log entry

        Example:
            >>> session = Session.start(Path('/projects/myapp'), 'docker', logs_dir)
        """
        state = SessionState.create(workspace, runtime)
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        session_dir = logs_dir / f'session-{timestamp}'
        session_dir.mkdir(parents=True, exist_ok=True)
        state.save(session_dir)
        session = cls(state, session_dir)
        session.log_event(f'Session started for workspace: {workspace}')
        return session

    @classmethod
    def load(cls, session_dir: Path) -> 'Session':
        """Load an existing session from its directory.

        Args:
            session_dir: Path to session directory containing state.json

        Returns:
            Session restored from state.json
        """
        state = SessionState.load(session_dir)
        return cls(state, session_dir)

    def is_container_running(self) -> bool:
        """Check if this session's container is currently running.

        Returns False immediately (without subprocess call) if status is not 'running'.
        """
        if self.state.status != 'running':
            return False
        runtime_cmd = 'container' if self.state.runtime == 'apple' else self.state.runtime
        try:
            result = subprocess.run(
                [runtime_cmd, 'ps', '--filter', f'name={self.state.container_name}',
                 '--format', '{{.Names}}'],
                capture_output=True, text=True, check=False
            )
            return any(
                line.strip() == self.state.container_name
                for line in result.stdout.splitlines()
            )
        except Exception:
            return False

    @property
    def age_str(self) -> str:
        """Human-readable age of this session (e.g. '2h ago', '3d ago')."""
        age = datetime.now() - self.state.started_at_dt
        if age.days > 0:
            return f"{age.days}d ago"
        hours = age.seconds // 3600
        if hours > 0:
            return f"{hours}h ago"
        minutes = age.seconds // 60
        if minutes > 0:
            return f"{minutes}m ago"
        return f"{age.seconds}s ago"

    @property
    def display_name(self) -> str:
        """One-line display string for list/prompt output."""
        workspace_name = Path(self.state.workspace).name
        return f"{self.state.session_id} ({workspace_name}, {self.state.status}, {self.age_str})"

    def log_network_request(
        self,
        method: str,
        url: str,
        allowed: bool,
        reason: Optional[str] = None
    ) -> None:
        """Log a network request to network.jsonl."""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'method': method,
            'url': url,
            'allowed': allowed,
            'reason': reason,
        }
        try:
            with open(self.network_log, 'a') as f:
                f.write(json.dumps(entry) + '\n')
        except (IOError, OSError) as e:
            import sys
            print(f"Warning: Failed to log network request: {e}", file=sys.stderr)

    def log_event(self, message: str, level: str = 'INFO') -> None:
        """Log an event to session.log."""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        entry = f'[{timestamp}] {level}: {message}\n'
        try:
            with open(self.session_log, 'a') as f:
                f.write(entry)
        except (IOError, OSError) as e:
            import sys
            print(f"Warning: Failed to log event: {e}", file=sys.stderr)

    def create_bundle(self) -> Optional[Path]:
        """Create git bundle from session repository.

        Returns:
            Path to bundle file if successful, None if creation failed

        Example:
            >>> bundle_path = session.create_bundle()
            >>> if bundle_path:
            ...     # git remote add vibedom bundle_path
        """
        bundle_path = self.session_dir / 'repo.bundle'
        repo_dir = self.session_dir / 'repo'
        try:
            self.log_event('Creating git bundle...')
            if not repo_dir.exists():
                self.log_event('Repository directory not found', level='ERROR')
                return None
            subprocess.run(
                ['git', '-C', str(repo_dir), 'bundle', 'create', str(bundle_path), '--all'],
                capture_output=True, check=True, text=True
            )
            verify = subprocess.run(
                ['git', 'bundle', 'verify', str(bundle_path)],
                capture_output=True, check=False, text=True
            )
            if verify.returncode == 0:
                self.log_event(f'Bundle created: {bundle_path}')
                return bundle_path
            else:
                self.log_event(f'Bundle verification failed: {verify.stderr}', level='ERROR')
                return None
        except subprocess.CalledProcessError as e:
            self.log_event(f'Bundle creation failed: {e.stderr}', level='ERROR')
            return None
        except Exception as e:
            self.log_event(f'Unexpected error creating bundle: {e}', level='ERROR')
            return None

    def finalize(self) -> None:
        """Finalize session: create bundle and update state.json.

        Sets status to 'complete' if bundle created, 'abandoned' otherwise.
        """
        self.log_event('Finalizing session...')
        bundle_path = self.create_bundle()
        if bundle_path:
            self.state.mark_complete(bundle_path, self.session_dir)
            self.log_event('Session complete')
        else:
            self.state.mark_abandoned(self.session_dir)
            self.log_event('Session abandoned (bundle creation failed)', level='WARN')


class SessionRegistry:
    """Discovers and resolves sessions from the logs directory."""

    def __init__(self, logs_dir: Path):
        self.logs_dir = logs_dir

    def all(self) -> list['Session']:
        """All sessions sorted newest first, skipping invalid directories."""
        sessions = []
        for session_dir in sorted(self.logs_dir.glob('session-*'), reverse=True):
            if not session_dir.is_dir():
                continue
            try:
                sessions.append(Session.load(session_dir))
            except (FileNotFoundError, ValueError, KeyError):
                continue
        return sessions

    def running(self) -> list['Session']:
        """Sessions with status 'running'."""
        return [s for s in self.all() if s.state.status == 'running']

    def find(self, id_or_name: str) -> Optional['Session']:
        """Find session by session ID or workspace name (most recent match)."""
        for session in self.all():
            if session.state.session_id == id_or_name:
                return session
            if Path(session.state.workspace).name == id_or_name:
                return session
        return None

    def resolve(
        self,
        id_or_name: Optional[str],
        running_only: bool = False,
        sessions: Optional[list['Session']] = None,
    ) -> 'Session':
        """Resolve to a single session, auto-selecting or prompting as needed.

        Args:
            id_or_name: Session ID or workspace name; None means auto-select
            running_only: If True, only consider running sessions
            sessions: Pre-filtered list (avoids double-loading when provided)

        Raises:
            SystemExit (via click.ClickException) if no match found
        """
        import click
        if id_or_name:
            session = self.find(id_or_name)
            if not session:
                raise click.ClickException(f"No session found for '{id_or_name}'")
            return session

        candidates = sessions if sessions is not None else (
            self.running() if running_only else self.all()
        )
        noun = "running sessions" if running_only else "sessions"

        if not candidates:
            raise click.ClickException(f"No {noun} found")
        if len(candidates) == 1:
            return candidates[0]

        click.echo(f"Multiple {noun} found:")
        for i, s in enumerate(candidates, 1):
            click.echo(f"  {i}. {s.display_name}")
        choice = click.prompt("Select session", type=click.IntRange(1, len(candidates)))
        return candidates[choice - 1]


class SessionCleanup:
    """Filter and delete helpers for prune/housekeeping commands."""

    @staticmethod
    def _filter_by_age(sessions: list['Session'], days: int) -> list['Session']:
        """Return sessions older than N days."""
        cutoff = datetime.now() - timedelta(days=days)
        return [s for s in sessions if s.state.started_at_dt < cutoff]

    @staticmethod
    def _filter_not_running(sessions: list['Session']) -> list['Session']:
        """Return sessions whose status is not 'running'."""
        return [s for s in sessions if s.state.status != 'running']

    @staticmethod
    def _delete_session(session_dir: Path) -> None:
        """Delete a session directory."""
        try:
            shutil.rmtree(session_dir, ignore_errors=True)
        except Exception:
            pass
