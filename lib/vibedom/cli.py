#!/usr/bin/env python3
"""vibedom CLI - Secure AI agent sandbox."""

import os
import shutil
import signal as signal_module
import sys
import subprocess
import click
from pathlib import Path
from typing import Optional
from vibedom.ssh_keys import generate_deploy_key, get_public_key
from vibedom.gitleaks import scan_workspace
from vibedom.review_ui import review_findings
from vibedom.whitelist import create_default_whitelist
from vibedom.vm import VMManager
from vibedom.session import Session, SessionCleanup, SessionRegistry
from vibedom.project_config import ProjectConfig
from vibedom.proxy import ProxyManager
from vibedom.container_state import ContainerState, ContainerRegistry


def _execute_deletions(to_delete: list, skipped: int, force: bool, dry_run: bool) -> None:
    """Execute or preview deletions for prune/housekeeping commands.

    Args:
        to_delete: List of Session objects to delete
        skipped: Count of sessions skipped (still running)
        force: Delete without prompting if True
        dry_run: Preview without deleting if True
    """
    deleted = 0
    for session in to_delete:
        name = session.display_name
        if dry_run:
            click.echo(f"Would delete: {name}")
            deleted += 1
        elif force or click.confirm(f"Delete {name}?", default=True):
            SessionCleanup._delete_session(session.session_dir)
            click.echo(f"Deleted {name}")
            deleted += 1

    if dry_run:
        click.echo(f"\nWould delete {deleted} session(s), skip {skipped} (still running)")
    else:
        click.echo(f"\nDeleted {deleted} session(s), skipped {skipped} (still running)")

@click.group()
@click.version_option()
def main():
    """Secure AI agent sandbox for running Claude Code and OpenCode."""
    pass

@main.command()
@click.option('--runtime', '-r', type=click.Choice(['auto', 'docker', 'apple'],
              case_sensitive=False), default='auto',
              help='Container runtime to use for building the image (default: auto-detect)')
def init(runtime: str):
    """Initialize vibedom (first-time setup)."""
    click.echo("🔧 Initializing vibedom...")

    # Create config directory
    config_dir = Path.home() / '.vibedom'
    keys_dir = config_dir / 'keys'
    keys_dir.mkdir(parents=True, exist_ok=True)

    # Generate deploy key
    key_path = keys_dir / 'id_ed25519_vibedom'
    if key_path.exists():
        click.echo(f"✓ Deploy key already exists at {key_path}")
    else:
        click.echo("Generating SSH deploy key...")
        generate_deploy_key(key_path)
        click.echo(f"✓ Deploy key created at {key_path}")

    # Show public key
    pubkey = get_public_key(key_path)
    click.echo("\n" + "="*60)
    click.echo("📋 Add this public key to your GitLab account:")
    click.echo("   Settings → SSH Keys")
    click.echo("="*60)
    click.echo(pubkey)
    click.echo("="*60 + "\n")

    # Create whitelist
    click.echo("Creating network whitelist...")
    whitelist_path = create_default_whitelist(config_dir)
    click.echo(f"✓ Whitelist created at {whitelist_path}")
    click.echo("  Edit this file to add your internal domains")

    # Build VM image
    click.echo("\nBuilding VM image (this may take a few minutes on first run)...")
    try:
        rt = None if runtime == 'auto' else runtime
        _, runtime_cmd = VMManager._detect_runtime(rt)
        if VMManager.image_exists(runtime_cmd):
            click.echo("✓ VM image already up to date")
        else:
            VMManager.build_image(rt)
            click.echo("✓ VM image built successfully")
    except RuntimeError as e:
        click.secho(f"⚠️  Could not build VM image: {e}", fg='yellow')
        click.echo("  Run 'vibedom build' manually once a container runtime is installed")

    click.echo("\n✅ Initialization complete!")

@main.command()
@click.argument('workspace', type=click.Path(exists=True))
@click.option('--runtime', '-r', type=click.Choice(['auto', 'docker', 'apple'],
              case_sensitive=False), default='auto',
              help='Container runtime (auto-detect, docker, or apple)')
def run(workspace, runtime):
    """Run AI agent in sandboxed environment."""
    workspace_path = Path(workspace).resolve()
    if not workspace_path.is_dir():
        click.secho(f"❌ Error: {workspace_path} is not a directory", fg='red')
        sys.exit(1)

    logs_dir = Path.home() / '.vibedom' / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Resolve runtime before creating session so state.json has correct value
    try:
        resolved_runtime, _ = VMManager._detect_runtime(
            runtime if runtime != 'auto' else None
        )
    except RuntimeError as e:
        click.secho(f"❌ {e}", fg='red')
        sys.exit(1)

    session = Session.start(workspace_path, resolved_runtime, logs_dir)
    session.log_event('Starting sandbox...')

    try:
        click.echo("🔍 Scanning for secrets...")
        findings = scan_workspace(workspace_path)

        if not review_findings(findings):
            session.log_event('Cancelled by user', level='WARN')
            session.state.mark_abandoned(session.session_dir)
            click.secho("❌ Cancelled", fg='yellow')
            sys.exit(1)

        click.echo("🚀 Starting sandbox...")
        config_dir = Path.home() / '.vibedom'
        project_config = ProjectConfig.load(workspace_path)
        vm = VMManager(workspace_path, config_dir,
                       session_dir=session.session_dir,
                       runtime=resolved_runtime,
                       network=project_config.network if project_config else None,
                       base_image=project_config.base_image if project_config else None,
                       host_aliases=project_config.host_aliases if project_config else None)
        vm.start()

        # Store proxy info so reload-whitelist can send SIGHUP to the host process
        if vm._proxy:
            session.state.proxy_port = vm._proxy.port
            session.state.proxy_pid = vm._proxy.pid
            session.state.save(session.session_dir)

        session.log_event('VM started successfully')

        click.echo("\n✅ Sandbox running!")
        click.echo(f"📋 Session ID: {session.state.session_id}")
        click.echo(f"📁 Session: {session.session_dir}")
        click.echo(f"📦 Live repo: {session.session_dir / 'repo'}")
        click.echo("\n💡 To test changes mid-session:")
        click.echo(f"  git remote add vibedom-live {session.session_dir / 'repo'}")
        click.echo("  git fetch vibedom-live")
        click.echo("\n🛑 To stop:")
        click.echo(f"  vibedom stop {session.state.session_id}")

    except Exception as e:
        session.log_event(f'Error: {e}', level='ERROR')
        session.state.mark_abandoned(session.session_dir)
        click.secho(f"❌ Error: {e}", fg='red')
        sys.exit(1)

@main.command()
@click.argument('session_id', required=False)
def stop(session_id):
    """Stop a sandbox session and create git bundle.

    SESSION_ID is a session ID (e.g. myapp-happy-turing) or workspace name.
    If omitted, auto-selects the only running session or prompts.
    """
    logs_dir = Path.home() / '.vibedom' / 'logs'
    registry = SessionRegistry(logs_dir)

    if session_id and ContainerRegistry().find(session_id):
        click.secho(
            f"'{session_id}' is a persistent container — use 'vibedom down {session_id}' to stop it.",
            fg='yellow'
        )
        sys.exit(1)

    session = registry.resolve(session_id, running_only=(session_id is None))

    if session.state.status != 'running':
        click.secho(
            f"Session '{session.state.session_id}' is not running "
            f"(status: {session.state.status})",
            fg='yellow'
        )
        sys.exit(1)

    # Create bundle + finalize (updates state.json)
    click.echo("Creating git bundle...")
    session.finalize()

    try:
        config_dir = Path.home() / '.vibedom'
        vm = VMManager(Path(session.state.workspace), config_dir,
                       session_dir=session.session_dir,
                       runtime=session.state.runtime)
        vm.stop()
    except Exception as e:
        click.secho(f"❌ Error stopping container: {e}", fg='red')
        sys.exit(1)

    # Stop the host proxy process (not tracked by the fresh VMManager above)
    if session.state.proxy_pid:
        try:
            os.kill(session.state.proxy_pid, signal_module.SIGTERM)
        except ProcessLookupError:
            pass  # Already gone

    if session.state.status == 'complete' and session.state.bundle_path:
        bundle_path = Path(session.state.bundle_path)
        click.echo("\n✅ Session complete!")
        click.echo(f"📋 Session ID: {session.state.session_id}")
        click.echo(f"📦 Bundle: {bundle_path}")
        click.echo(f"\n📋 To review: vibedom review {session.state.session_id}")
        click.echo(f"🔀 To merge:  vibedom merge {session.state.session_id}")
    else:
        click.secho("⚠️  Bundle creation failed", fg='yellow')
        click.echo(f"📁 Live repo available: {session.session_dir / 'repo'}")


@main.command('list')
def list_sessions():
    """List all sessions and persistent containers with their status."""
    containers = ContainerRegistry().all()
    logs_dir = Path.home() / '.vibedom' / 'logs'
    sessions = SessionRegistry(logs_dir).all() if logs_dir.exists() else []

    if not containers and not sessions:
        click.echo("No sessions found")
        return

    if containers:
        click.echo(f"{'WORKSPACE':<25} {'CONTAINER':<35} {'STATUS':<12} {'PROXY'}")
        click.echo('-' * 85)
        for c in containers:
            workspace_name = Path(c.workspace).name
            proxy_info = f"port {c.proxy_port} (PID {c.proxy_pid})" if c.proxy_port else "none"
            live_status = _live_container_status(c)
            click.echo(
                f"{workspace_name:<25} "
                f"{c.container_name:<35} "
                f"{live_status:<12} "
                f"{proxy_info}"
            )

    if sessions:
        if containers:
            click.echo()
        click.echo(f"{'ID':<40} {'WORKSPACE':<20} {'STATUS':<12} {'STARTED'}")
        click.echo('-' * 85)
        for session in sessions:
            workspace_name = Path(session.state.workspace).name
            click.echo(
                f"{session.state.session_id:<40} "
                f"{workspace_name:<20} "
                f"{session.state.status:<12} "
                f"{session.age_str}"
            )


@main.command('attach')
@click.argument('session_id', required=False)
def attach(session_id):
    """Open a shell in a running session's workspace (/work/repo).

    SESSION_ID is a session ID or workspace name.
    If omitted, auto-selects the only running session or prompts.
    """
    logs_dir = Path.home() / '.vibedom' / 'logs'
    registry = SessionRegistry(logs_dir)
    running = registry.running()

    session = registry.resolve(session_id, running_only=True, sessions=running)

    if session.state.status != 'running':
        click.secho(
            f"Session '{session.state.session_id}' is not running "
            f"(status: {session.state.status})",
            fg='red'
        )
        sys.exit(1)

    runtime_cmd = 'container' if session.state.runtime == 'apple' else 'docker'
    cmd = [runtime_cmd, 'exec', '-it', '-w', '/work/repo',
           session.state.container_name, 'bash']
    try:
        subprocess.run(cmd)
    except FileNotFoundError:
        click.secho(f"❌ Error: {runtime_cmd} command not found", fg='red')
        sys.exit(1)


@main.command('review')
@click.argument('session_id')
@click.option('--branch', help='Branch to review from bundle (default: current branch)')
def review(session_id: str, branch: Optional[str]) -> None:
    """Review changes from a session bundle.

    SESSION_ID is a session ID (e.g. myapp-happy-turing) or workspace name.
    """
    logs_dir = Path.home() / '.vibedom' / 'logs'
    registry = SessionRegistry(logs_dir)
    session_obj = registry.find(session_id)

    if not session_obj:
        click.secho(f"❌ No session found for '{session_id}'", fg='red')
        sys.exit(1)

    workspace_path = Path(session_obj.state.workspace)
    session_dir = session_obj.session_dir

    # Check if workspace is a git repository
    try:
        subprocess.run(
            ['git', '-C', str(workspace_path), 'rev-parse', '--git-dir'],
            capture_output=True, check=True
        )
    except subprocess.CalledProcessError:
        click.secho(f"❌ Error: {workspace_path} is not a git repository", fg='red')
        sys.exit(1)

    # Check if session is still running
    if session_obj.is_container_running():
        click.secho("❌ Session is still running. Stop it first:", fg='red')
        click.echo(f"  vibedom stop {session_obj.state.session_id}")
        sys.exit(1)

    # Check if bundle exists
    bundle_path = session_dir / 'repo.bundle'
    if not bundle_path.exists():
        click.secho(f"❌ Bundle not found at {bundle_path}", fg='red')
        click.echo("Session may have failed or been deleted.")
        sys.exit(1)

    # Get current branch or use --branch argument
    if not branch:
        try:
            result = subprocess.run(
                ['git', '-C', str(workspace_path), 'rev-parse', '--abbrev-ref', 'HEAD'],
                capture_output=True, text=True, check=True
            )
            branch = result.stdout.strip()
        except subprocess.CalledProcessError:
            click.secho("❌ Error: Could not determine current branch", fg='red')
            sys.exit(1)

    # Generate remote name from session ID
    session_id = session_obj.state.session_id
    remote_name = f'vibedom-{session_id}'

    # Check if remote already exists
    result = subprocess.run(
        ['git', '-C', str(workspace_path), 'remote', 'get-url', remote_name],
        capture_output=True
    )

    if result.returncode != 0:
        # Add remote
        click.echo(f"Adding remote: {remote_name}")
        try:
            subprocess.run(
                ['git', '-C', str(workspace_path), 'remote', 'add', remote_name, str(bundle_path)],
                check=True
            )
        except subprocess.CalledProcessError:
            click.secho("❌ Error: Failed to add git remote", fg='red')
            sys.exit(1)
    else:
        click.echo(f"Using existing remote: {remote_name}")

    # Fetch bundle
    click.echo("Fetching bundle...")
    try:
        subprocess.run(
            ['git', '-C', str(workspace_path), 'fetch', remote_name],
            check=True
        )
    except subprocess.CalledProcessError:
        click.secho("❌ Error: Failed to fetch bundle", fg='red')
        sys.exit(1)

    # Show session info
    click.echo(f"\n✅ Session: {session_dir.name}")
    click.echo(f"📦 Bundle: {bundle_path}")
    click.echo(f"🌿 Branch: {branch}\n")

    # Show commit log
    click.echo("📝 Commits:")
    result = subprocess.run(
        ['git', '-C', str(workspace_path), 'log', '--oneline',
         f'{branch}..{remote_name}/{branch}'],
        capture_output=True, text=True, check=True
    )
    if result.stdout:
        click.echo(result.stdout)
    else:
        click.echo("  (no new commits)")

    # Show diff
    click.echo("\n📊 Changes:")
    result = subprocess.run(
        ['git', '-C', str(workspace_path), 'diff',
         f'{branch}..{remote_name}/{branch}'],
        capture_output=True, text=True, check=True
    )
    if result.stdout:
        click.echo(result.stdout)
    else:
        click.echo("  (no changes)")

    # Show merge hint
    click.echo(f"\n💡 To merge: vibedom merge {session_obj.state.session_id}")


@main.command('merge')
@click.argument('session_id')
@click.option('--branch', help='Branch to merge from bundle (default: current branch)')
@click.option('--merge', 'keep_history', is_flag=True,
              help='Keep full commit history (default: squash)')
def merge(session_id: str, branch: Optional[str], keep_history: bool) -> None:
    """Merge changes from a session bundle (squash by default).

    SESSION_ID is a session ID (e.g. myapp-happy-turing) or workspace name.
    """
    logs_dir = Path.home() / '.vibedom' / 'logs'
    registry = SessionRegistry(logs_dir)
    session_obj = registry.find(session_id)

    if not session_obj:
        click.secho(f"❌ No session found for '{session_id}'", fg='red')
        sys.exit(1)

    workspace_path = Path(session_obj.state.workspace)

    # Check if workspace is a git repository
    try:
        subprocess.run(
            ['git', '-C', str(workspace_path), 'rev-parse', '--git-dir'],
            capture_output=True, check=True
        )
    except subprocess.CalledProcessError:
        click.secho(f"❌ Error: {workspace_path} is not a git repository", fg='red')
        sys.exit(1)

    if session_obj.is_container_running():
        click.secho("❌ Session is still running. Stop it first:", fg='red')
        click.echo(f"  vibedom stop {session_obj.state.session_id}")
        sys.exit(1)

    session_dir = session_obj.session_dir

    # Check if bundle exists
    bundle_path = session_dir / 'repo.bundle'
    if not bundle_path.exists():
        click.secho(f"❌ Bundle not found at {bundle_path}", fg='red')
        click.echo("Session may have failed or been deleted.")
        sys.exit(1)

    # Get current branch or use --branch argument
    if not branch:
        try:
            result = subprocess.run(
                ['git', '-C', str(workspace_path), 'rev-parse', '--abbrev-ref', 'HEAD'],
                capture_output=True, text=True, check=True
            )
            branch = result.stdout.strip()
        except subprocess.CalledProcessError:
            click.secho("❌ Error: Could not determine current branch", fg='red')
            sys.exit(1)

    # Generate remote name from session ID
    session_id = session_obj.state.session_id
    remote_name = f'vibedom-{session_id}'

    # Check if remote exists (might have been added by review)
    result = subprocess.run(
        ['git', '-C', str(workspace_path), 'remote', 'get-url', remote_name],
        capture_output=True
    )

    if result.returncode != 0:
        # Add remote
        click.echo(f"Adding remote: {remote_name}")
        try:
            subprocess.run(
                ['git', '-C', str(workspace_path), 'remote', 'add', remote_name, str(bundle_path)],
                check=True
            )
        except subprocess.CalledProcessError:
            click.secho("❌ Error: Failed to add git remote", fg='red')
            sys.exit(1)

        # Fetch bundle
        click.echo("Fetching bundle...")
        try:
            subprocess.run(
                ['git', '-C', str(workspace_path), 'fetch', remote_name],
                check=True
            )
        except subprocess.CalledProcessError:
            click.secho("❌ Error: Failed to fetch bundle", fg='red')
            sys.exit(1)
    else:
        click.echo(f"Using existing remote: {remote_name}")

    # Perform merge
    remote_branch = f'{remote_name}/{branch}'

    try:
        if keep_history:
            # Regular merge (keep commits)
            click.echo(f"Merging {remote_branch} (keeping commit history)...")
            subprocess.run(
                ['git', '-C', str(workspace_path), 'merge', remote_branch],
                check=True
            )
        else:
            # Squash merge (default)
            click.echo(f"Merging {remote_branch} (squash)...")
            subprocess.run(
                ['git', '-C', str(workspace_path), 'merge', '--squash', remote_branch],
                check=True
            )

            # Create commit with summary message
            commit_msg = f"""Apply changes from vibedom session

Session: {session_id}
Bundle: {bundle_path}
Branch: {branch}

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"""

            subprocess.run(
                ['git', '-C', str(workspace_path), 'commit', '-m', commit_msg],
                check=True
            )
    except subprocess.CalledProcessError:
        click.secho("❌ Merge failed", fg='red')
        click.echo("Resolve conflicts manually and commit.")
        # Don't remove remote - user might need it
        sys.exit(1)

    # Clean up remote
    click.echo(f"Cleaning up remote: {remote_name}")
    subprocess.run(
        ['git', '-C', str(workspace_path), 'remote', 'remove', remote_name],
        check=True
    )

    click.echo("\n✅ Merge complete!")


@main.command('reload-whitelist')
def reload_whitelist() -> None:
    """Reload domain whitelist for all running sessions and containers.

    After editing ~/.vibedom/config/trusted_domains.txt, use this command
    to apply the changes without restarting containers.
    """
    logs_dir = Path.home() / '.vibedom' / 'logs'
    running_sessions = SessionRegistry(logs_dir).running() if logs_dir.exists() else []
    running_containers = [c for c in ContainerRegistry().all() if c.status == 'running']

    if not running_sessions and not running_containers:
        click.echo("No running sessions found")
        return

    failed = 0

    for session in running_sessions:
        if not session.state.proxy_pid:
            click.secho(
                f"⚠️  No proxy PID for {session.display_name} "
                f"(started with older vibedom?)",
                fg='yellow'
            )
            failed += 1
            continue
        try:
            os.kill(session.state.proxy_pid, signal_module.SIGHUP)
            click.echo(f"✅ Reloaded whitelist for {session.display_name}")
        except ProcessLookupError:
            click.secho(
                f"❌ Proxy process not found for {session.display_name}",
                fg='red'
            )
            failed += 1

    for container in running_containers:
        name = Path(container.workspace).name
        if not container.proxy_pid:
            click.secho(
                f"⚠️  No proxy PID for {name} (started with older vibedom?)",
                fg='yellow'
            )
            failed += 1
            continue
        try:
            os.kill(container.proxy_pid, signal_module.SIGHUP)
            click.echo(f"✅ Reloaded whitelist for {name}")
        except ProcessLookupError:
            click.secho(f"❌ Proxy process not found for {name}", fg='red')
            failed += 1

    if failed:
        sys.exit(1)


@main.command('proxy-restart')
@click.argument('session_id', required=False)
def proxy_restart(session_id: Optional[str]) -> None:
    """Restart the host proxy for a running session.

    SESSION_ID is a session ID or workspace name.
    If omitted, auto-selects the only running session.

    Stops the proxy if it is running, then starts a fresh one on the same
    port so the container's HTTP_PROXY setting remains valid.
    """
    logs_dir = Path.home() / '.vibedom' / 'logs'
    registry = SessionRegistry(logs_dir)
    session = registry.resolve(session_id, running_only=True)

    if not session.state.proxy_port:
        click.secho(
            "❌ No proxy port recorded for this session "
            "(started with older vibedom?)",
            fg='red'
        )
        sys.exit(1)

    # Stop existing proxy if still running
    if session.state.proxy_pid:
        try:
            os.kill(session.state.proxy_pid, signal_module.SIGTERM)
            click.echo(f"Stopped proxy (PID {session.state.proxy_pid})")
        except ProcessLookupError:
            click.echo(f"Proxy (PID {session.state.proxy_pid}) was already stopped")

    # Start fresh proxy on the same port
    config_dir = Path.home() / '.vibedom'
    proxy = ProxyManager(session_dir=session.session_dir, config_dir=config_dir)
    try:
        proxy.start(port=session.state.proxy_port)
    except RuntimeError as e:
        click.secho(f"❌ Failed to start proxy: {e}", fg='red')
        sys.exit(1)

    # Persist new PID
    session.state.proxy_pid = proxy.pid
    session.state.save(session.session_dir)

    click.echo(
        f"✅ Proxy restarted on port {proxy.port} (PID {proxy.pid})"
    )


@main.command()
@click.argument('session_id')
@click.option('--force', '-f', is_flag=True, help='Delete without prompting')
def rm(session_id: str, force: bool) -> None:
    """Delete a specific session directory.

    SESSION_ID is a session ID (e.g. myapp-happy-turing) or workspace name.
    Running sessions are refused unless --force is used.
    """
    logs_dir = Path.home() / '.vibedom' / 'logs'
    registry = SessionRegistry(logs_dir)
    session_obj = registry.find(session_id)

    if not session_obj:
        click.secho(f"❌ No session found for '{session_id}'", fg='red')
        sys.exit(1)

    if session_obj.is_container_running():
        click.secho("❌ Session is still running. Stop it first:", fg='red')
        click.echo(f"  vibedom stop {session_obj.state.session_id}")
        sys.exit(1)

    name = session_obj.display_name
    if force or click.confirm(f"Delete session '{name}'?", default=False):
        SessionCleanup._delete_session(session_obj.session_dir)
        click.echo(f"Deleted {name}")
    else:
        click.echo("Aborted")


@main.command()
@click.option('--force', '-f', is_flag=True, help='Delete without prompting')
@click.option('--dry-run', is_flag=True, help='Preview without deleting')
def prune(force: bool, dry_run: bool) -> None:
    """Remove all session directories without running containers."""
    logs_dir = Path.home() / '.vibedom' / 'logs'
    if not logs_dir.exists():
        click.echo("No sessions to delete")
        return
    registry = SessionRegistry(logs_dir)
    sessions = registry.all()
    to_delete = SessionCleanup._filter_not_running(sessions)
    skipped = len(sessions) - len(to_delete)

    if not to_delete:
        click.echo("No sessions to delete")
        return

    click.echo(f"Found {len(to_delete)} session(s) to delete")
    _execute_deletions(to_delete, skipped, force, dry_run)


@main.command()
@click.option('--days', '-d', default=7, help='Delete sessions older than N days')
@click.option('--force', '-f', is_flag=True, help='Delete without prompting')
@click.option('--dry-run', is_flag=True, help='Preview without deleting')
def housekeeping(days: int, force: bool, dry_run: bool) -> None:
    """Remove sessions older than N days without running containers."""
    logs_dir = Path.home() / '.vibedom' / 'logs'
    if not logs_dir.exists():
        click.echo(f"No sessions older than {days} days")
        return
    registry = SessionRegistry(logs_dir)
    sessions = registry.all()
    old_sessions = SessionCleanup._filter_by_age(sessions, days)
    to_delete = SessionCleanup._filter_not_running(old_sessions)
    skipped = len(old_sessions) - len(to_delete)

    if not to_delete:
        click.echo(f"No sessions older than {days} days")
        return

    click.echo(f"Found {len(to_delete)} session(s) older than {days} days")
    _execute_deletions(to_delete, skipped, force, dry_run)


def _live_container_status(c: ContainerState) -> str:
    """Query the container runtime for the actual current status."""
    if c.runtime == 'apple':
        result = subprocess.run(
            ['container', 'inspect', c.container_name],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return 'gone'
        try:
            import json as _json
            data = _json.loads(result.stdout)
            if isinstance(data, list):
                return data[0].get('status', 'unknown') if data else 'gone'
            return data.get('status', 'unknown')
        except (ValueError, KeyError, IndexError):
            return 'unknown'
    else:
        result = subprocess.run(
            ['docker', 'inspect', '--format', '{{.State.Status}}', c.container_name],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return 'gone'
        return result.stdout.strip() or 'unknown'


def _proxy_is_alive(pid: Optional[int]) -> bool:
    """Check whether a proxy process is still running.

    os.kill(pid, 0) raises ProcessLookupError when the process does not exist
    and PermissionError (EPERM) when it exists but is owned by another user.
    Both mean the proxy we started is not reachable; any other OSError is re-raised.
    """
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _ensure_proxy_running(
    container_state: ContainerState,
    container_dir: Path,
    config_dir: Path,
) -> Optional[ProxyManager]:
    """Ensure the host proxy is running. Restarts it if dead. Returns the proxy manager or None."""
    if _proxy_is_alive(container_state.proxy_pid):
        return None  # Already running

    proxy = ProxyManager(session_dir=container_dir, config_dir=config_dir)
    try:
        proxy.start(port=container_state.proxy_port)
    except RuntimeError as e:
        click.secho(f"⚠️  Could not start proxy: {e}", fg='yellow')
        return None
    container_state.proxy_pid = proxy.pid
    container_state.proxy_port = proxy.port
    container_state.save(container_dir)
    click.echo(f"Proxy started on port {proxy.port} (PID {proxy.pid})")
    return proxy


@main.command()
@click.argument('workspace', type=click.Path(exists=True))
@click.option('--runtime', '-r', type=click.Choice(['auto', 'docker', 'apple'],
              case_sensitive=False), default='auto',
              help='Container runtime (auto-detect, docker, or apple)')
def up(workspace, runtime):
    """Start a persistent project container.

    Creates the container on first use; restarts it if stopped; does nothing if already running.
    """
    workspace_path = Path(workspace).resolve()
    if not workspace_path.is_dir():
        click.secho(f"Error: {workspace_path} is not a directory", fg='red')
        sys.exit(1)

    config_dir = Path.home() / '.vibedom'
    containers_dir = config_dir / 'containers'
    container_dir = containers_dir / workspace_path.name
    container_dir.mkdir(parents=True, exist_ok=True)

    try:
        resolved_runtime, _ = VMManager._detect_runtime(
            runtime if runtime != 'auto' else None
        )
    except RuntimeError as e:
        click.secho(f"Error: {e}", fg='red')
        sys.exit(1)

    project_config = ProjectConfig.load(workspace_path)
    registry = ContainerRegistry(containers_dir)
    container_state = registry.find(workspace_path.name)

    vm = VMManager(
        workspace_path, config_dir,
        container_dir=container_dir,
        runtime=resolved_runtime,
        network=project_config.network if project_config else None,
        base_image=project_config.base_image if project_config else None,
        host_aliases=project_config.host_aliases if project_config else None,
        memory=project_config.memory if project_config else None,
    )

    if vm.is_running():
        click.echo(f"Container '{vm.container_name}' is already running.")
        if container_state:
            _ensure_proxy_running(container_state, container_dir, config_dir)
        click.echo(f"Repo: {container_dir / 'repo'}")
        return

    if vm.exists():
        # Container stopped — restart proxy then container
        click.echo(f"Restarting container '{vm.container_name}'...")
        if container_state is None:
            container_state = ContainerState.create(workspace_path, resolved_runtime)
        proxy = ProxyManager(session_dir=container_dir, config_dir=config_dir)
        try:
            proxy.start(port=container_state.proxy_port)
        except RuntimeError as e:
            click.secho(f"Error starting proxy: {e}", fg='red')
            sys.exit(1)
        try:
            vm.restart()
        except RuntimeError as e:
            proxy.stop()
            click.secho(f"Error: {e}", fg='red')
            sys.exit(1)
        container_state.mark_running(proxy.port, proxy.pid, container_dir)
    elif container_state is not None:
        # Container no longer exists in the runtime but the state file and repo are intact.
        # Repo data is safe in the bind-mount directory — just recreate the container.
        click.echo(f"Container '{vm.container_name}' not found. Recreating with existing repo...")
        try:
            vm.start()
        except RuntimeError as e:
            click.secho(f"Error: {e}", fg='red')
            sys.exit(1)
        container_state.mark_running(vm._proxy.port, vm._proxy.pid, container_dir)

    else:
        # First-time creation
        click.echo("Scanning for secrets...")
        findings = scan_workspace(workspace_path)
        if not review_findings(findings):
            click.secho("Cancelled", fg='yellow')
            sys.exit(1)

        click.echo(f"Starting container '{vm.container_name}'...")
        try:
            vm.start()
        except RuntimeError as e:
            click.secho(f"Error: {e}", fg='red')
            sys.exit(1)

        container_state = ContainerState.create(workspace_path, resolved_runtime)
        if vm._proxy:
            container_state.mark_running(vm._proxy.port, vm._proxy.pid, container_dir)
        else:
            container_state.save(container_dir)

        # Run one-time setup commands if specified
        if project_config and project_config.setup:
            click.echo("Running setup commands...")
            for setup_cmd in project_config.setup:
                click.echo(f"  $ {setup_cmd}")
                result = vm.exec(['sh', '-c', setup_cmd])
                if result.returncode != 0:
                    click.secho(f"  Warning: setup command failed: {result.stderr}", fg='yellow')

    click.echo(f"\nContainer running!")
    click.echo(f"Workspace: {workspace_path}")
    click.echo(f"Repo: {container_dir / 'repo'}")
    click.echo(f"\nTo sync code:")
    click.echo(f"  vibedom pull {workspace_path.name}   # container -> host")
    click.echo(f"  vibedom push {workspace_path.name}   # host -> container")
    click.echo(f"\nTo open a shell:")
    click.echo(f"  vibedom shell {workspace_path.name}")
    click.echo(f"\nTo stop:")
    click.echo(f"  vibedom down {workspace_path.name}")


@main.command()
@click.argument('workspace', required=False)
def down(workspace):
    """Stop a persistent container (preserves filesystem).

    WORKSPACE is the workspace directory name or path.
    If omitted, uses the only running container or prompts.
    """
    config_dir = Path.home() / '.vibedom'
    containers_dir = config_dir / 'containers'
    registry = ContainerRegistry(containers_dir)

    identifier = workspace or ''
    container_state = registry.find(identifier) if identifier else None

    if container_state is None and not identifier:
        all_containers = [c for c in registry.all() if c.status == 'running']
        if len(all_containers) == 1:
            container_state = all_containers[0]
        elif len(all_containers) > 1:
            click.secho("Multiple running containers. Specify a workspace name.", fg='red')
            for c in all_containers:
                click.echo(f"  {Path(c.workspace).name}")
            sys.exit(1)
        else:
            click.secho("No running containers found.", fg='yellow')
            return

    if container_state is None:
        click.secho(f"No container found for '{workspace}'.", fg='red')
        sys.exit(1)

    container_dir = containers_dir / Path(container_state.workspace).name
    vm = VMManager(
        Path(container_state.workspace), config_dir,
        container_dir=container_dir,
        runtime=container_state.runtime,
    )

    click.echo(f"Stopping container '{container_state.container_name}'...")
    vm.pause()

    if container_state.proxy_pid:
        try:
            os.kill(container_state.proxy_pid, signal_module.SIGTERM)
        except ProcessLookupError:
            pass

    container_state.mark_stopped(container_dir)
    click.echo("Container stopped (filesystem preserved). Run 'vibedom up' to restart.")


@main.command()
@click.argument('workspace', required=False)
@click.option('--force', '-f', is_flag=True, help='Skip confirmation prompt')
def destroy(workspace, force):
    """Remove a persistent container and its state.

    WORKSPACE is the workspace directory name or path.
    This removes the container and its repo — use 'vibedom down' to just stop it.
    """
    config_dir = Path.home() / '.vibedom'
    containers_dir = config_dir / 'containers'
    registry = ContainerRegistry(containers_dir)

    container_state = registry.find(workspace) if workspace else None

    if container_state is None and not workspace:
        click.secho("Specify a workspace name.", fg='red')
        sys.exit(1)

    if container_state is None:
        click.secho(f"No container found for '{workspace}'.", fg='red')
        sys.exit(1)

    name = Path(container_state.workspace).name
    if not force and not click.confirm(
        f"Destroy container '{container_state.container_name}' and delete repo data for '{name}'?",
        default=False,
    ):
        click.echo("Aborted")
        return

    container_dir = containers_dir / name
    vm = VMManager(
        Path(container_state.workspace), config_dir,
        container_dir=container_dir,
        runtime=container_state.runtime,
    )

    click.echo(f"Destroying container '{container_state.container_name}'...")
    vm.stop()

    if container_state.proxy_pid:
        try:
            os.kill(container_state.proxy_pid, signal_module.SIGTERM)
        except ProcessLookupError:
            pass

    shutil.rmtree(container_dir, ignore_errors=True)
    click.echo(f"Container '{container_state.container_name}' destroyed.")


@main.command()
@click.argument('workspace', required=False)
def status(workspace):
    """Show status of persistent containers."""
    config_dir = Path.home() / '.vibedom'
    containers_dir = config_dir / 'containers'
    registry = ContainerRegistry(containers_dir)

    if workspace:
        container_state = registry.find(workspace)
        if not container_state:
            click.secho(f"No container found for '{workspace}'.", fg='red')
            sys.exit(1)
        containers = [container_state]
    else:
        containers = registry.all()

    if not containers:
        click.echo("No persistent containers found. Run 'vibedom up <workspace>' to create one.")
        return

    click.echo(f"{'WORKSPACE':<25} {'CONTAINER':<35} {'STATUS':<10} {'PROXY'}")
    click.echo('-' * 85)
    for c in containers:
        workspace_name = Path(c.workspace).name
        proxy_info = f"port {c.proxy_port}" if c.proxy_port else "none"
        if c.proxy_pid and _proxy_is_alive(c.proxy_pid):
            proxy_info += f" (PID {c.proxy_pid})"
        else:
            proxy_info += " (dead)" if c.proxy_pid else ""
        live_status = _live_container_status(c)
        click.echo(
            f"{workspace_name:<25} "
            f"{c.container_name:<35} "
            f"{live_status:<10} "
            f"{proxy_info}"
        )


@main.command('shell')
@click.argument('workspace', required=False)
def shell_cmd(workspace):
    """Open a shell in a running container's workspace (/work/repo).

    WORKSPACE is the workspace directory name or path.
    If omitted, uses the only running container or prompts.
    """
    config_dir = Path.home() / '.vibedom'
    containers_dir = config_dir / 'containers'
    registry = ContainerRegistry(containers_dir)

    container_state = registry.find(workspace) if workspace else None

    if container_state is None and not workspace:
        running = [c for c in registry.all() if c.status == 'running']
        if len(running) == 1:
            container_state = running[0]
        elif len(running) > 1:
            click.secho("Multiple running containers. Specify a workspace name.", fg='red')
            sys.exit(1)
        else:
            click.secho("No running containers found.", fg='red')
            sys.exit(1)

    if container_state is None:
        click.secho(f"No container found for '{workspace}'.", fg='red')
        sys.exit(1)

    # Ensure proxy is alive before entering
    container_dir = containers_dir / Path(container_state.workspace).name
    _ensure_proxy_running(container_state, container_dir, config_dir)

    runtime_cmd = 'container' if container_state.runtime == 'apple' else 'docker'
    cmd = [runtime_cmd, 'exec', '-it', '-w', '/work/repo',
           container_state.container_name, 'bash', '--login']
    try:
        subprocess.run(cmd)
    except FileNotFoundError:
        click.secho(f"Error: {runtime_cmd} command not found", fg='red')
        sys.exit(1)


def _validate_sync_paths(paths: tuple, src: Path) -> list[Path]:
    """Validate and resolve path arguments for sync commands.

    Each path must be relative (no leading '/') and must resolve to a location
    inside src after resolving any '..' components.  Absolute paths and path
    traversals that escape src are rejected to prevent accidental writes
    outside the workspace or container repo.

    Args:
        paths: Raw path strings provided by the user.
        src: The source root directory that all paths must stay within.

    Returns:
        List of resolved absolute Path objects, each guaranteed to be inside src.

    Raises:
        click.ClickException: If any path is invalid or escapes src.
    """
    validated = []
    src_resolved = src.resolve()
    for raw in paths:
        if Path(raw).is_absolute():
            raise click.ClickException(
                f"Path argument must be relative, not absolute: '{raw}'"
            )
        resolved = (src_resolved / raw).resolve()
        try:
            resolved.relative_to(src_resolved)
        except ValueError:
            raise click.ClickException(
                f"Path '{raw}' escapes the source directory — path traversal not allowed"
            )
        validated.append(resolved)
    return validated


def _make_workspace_relative(raw: str, workspace_root: Path, cwd: Path | None = None) -> str:
    """Resolve a path argument relative to CWD if CWD is inside workspace_root.

    Returns a workspace-root-relative path string. Falls back to raw unchanged
    if CWD is outside workspace_root or if the resolved path escapes the root.
    """
    if cwd is None:
        cwd = Path.cwd()
    cwd = cwd.resolve()
    workspace_resolved = workspace_root.resolve()
    try:
        cwd.relative_to(workspace_resolved)
    except ValueError:
        return raw
    try:
        return str((cwd / raw).resolve().relative_to(workspace_resolved))
    except ValueError:
        return raw


def _build_rsync_cmd(
    src: Path,
    dst: Path,
    paths: tuple,
    delete: bool,
    dry_run: bool,
    extra_excludes: list,
) -> list:
    """Build an rsync command for syncing src to dst.

    Args:
        src: Source directory (trailing slash makes rsync sync its contents)
        dst: Destination directory
        paths: Specific sub-paths to sync (relative to src). If empty, sync all.
            All paths must have already been validated via _validate_sync_paths.
        delete: Include --delete flag (destructive)
        dry_run: Include --dry-run flag
        extra_excludes: Additional patterns to exclude beyond .gitignore
    """
    cmd = ['rsync', '-av']

    if dry_run:
        cmd.append('--dry-run')

    # Exclude .git always
    cmd += ['--exclude=.git/']

    # Use .gitignore rules from the workspace (source side)
    cmd += ['--filter=:- .gitignore']

    for pattern in extra_excludes:
        cmd.append(f'--exclude={pattern}')

    if delete:
        cmd.append('--delete')

    if paths:
        # --relative with /./  preserves the path structure in the destination.
        # e.g. /src_root/./sub/dir/file.py lands at /dst/sub/dir/file.py
        cmd.append('--relative')
        src_resolved = src.resolve()
        dst_resolved = dst.resolve()
        for raw in paths:
            cmd.append(f'{src_resolved}/./{raw}')
        cmd.append(str(dst_resolved))
    else:
        cmd.append(f'{src}/')
        cmd.append(str(dst))

    return cmd


def _find_deletions(cmd: list[str]) -> list[str]:
    """Run a silent rsync dry-run and return paths that would be deleted.

    Parses lines beginning with 'deleting ' from rsync's stdout.
    """
    dry_cmd = list(cmd)
    if '--dry-run' not in dry_cmd:
        dry_cmd.append('--dry-run')
    result = subprocess.run(dry_cmd, capture_output=True, text=True)
    return [
        line[len('deleting '):]
        for line in result.stdout.splitlines()
        if line.startswith('deleting ')
    ]


@main.command()
@click.argument('workspace')
@click.argument('paths', nargs=-1)
@click.option('--delete', is_flag=True, help='Also remove files in host that are absent in container')
@click.option('--dry-run', '-n', is_flag=True, help='Show what would be synced without doing it')
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation for full-tree sync')
@click.option('--force', '-f', is_flag=True, help='Skip all confirmations')
def pull(workspace, paths, delete, dry_run, yes, force):
    """Sync code from container to host workspace.

    WORKSPACE is the workspace directory name or path.
    PATHS are optional relative paths to sync (e.g. src/ app/).
    If no paths given, syncs everything (respecting .gitignore) after confirmation.
    """
    config_dir = Path.home() / '.vibedom'
    containers_dir = config_dir / 'containers'
    registry = ContainerRegistry(containers_dir)

    container_state = registry.find(workspace)
    if container_state is None:
        click.secho(f"No container found for '{workspace}'.", fg='red')
        sys.exit(1)

    workspace_path = Path(container_state.workspace)
    container_dir = containers_dir / workspace_path.name
    repo_dir = container_dir / 'repo'

    if paths:
        paths = tuple(_make_workspace_relative(p, workspace_path) for p in paths)
        click.echo("Resolved: " + ", ".join(paths))
        try:
            _validate_sync_paths(paths, repo_dir)
        except click.ClickException as e:
            click.secho(f"Error: {e.format_message()}", fg='red')
            sys.exit(1)

    # Full-tree sync without --dry-run requires confirmation
    if not paths and not dry_run and not yes and not force:
        if not click.confirm(
            f"Sync all files from container repo to {workspace_path.name}?",
            default=False,
        ):
            click.echo("Aborted")
            return

    project_config = ProjectConfig.load(workspace_path)
    extra_excludes = (project_config.sync_exclude or []) if project_config else []

    cmd = _build_rsync_cmd(
        src=repo_dir,
        dst=workspace_path,
        paths=paths,
        delete=delete,
        dry_run=dry_run,
        extra_excludes=extra_excludes,
    )

    if delete and not force and not dry_run:
        deletions = _find_deletions(cmd)
        if deletions:
            click.echo("These files will be deleted from the host:")
            for f in deletions:
                click.echo(f"  {f}")
            if not click.confirm("\nProceed?", default=False):
                click.echo("Aborted")
                return

    if dry_run:
        click.echo("Dry run — showing what would be synced:")
    else:
        click.echo(f"Pulling from container to {workspace_path.name}...")

    result = subprocess.run(cmd, capture_output=False, text=True)
    if result.returncode != 0:
        click.secho("rsync failed", fg='red')
        sys.exit(result.returncode)

    if not dry_run:
        click.echo("Done.")


@main.command()
@click.argument('workspace')
@click.argument('paths', nargs=-1)
@click.option('--delete', is_flag=True, help='Also remove files in container that are absent on host')
@click.option('--dry-run', '-n', is_flag=True, help='Show what would be synced without doing it')
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation for full-tree sync')
@click.option('--force', '-f', is_flag=True, help='Skip all confirmations')
def push(workspace, paths, delete, dry_run, yes, force):
    """Sync code from host workspace to container.

    WORKSPACE is the workspace directory name or path.
    PATHS are optional relative paths to sync (e.g. src/ app/).
    If no paths given, syncs everything (respecting .gitignore) after confirmation.
    """
    config_dir = Path.home() / '.vibedom'
    containers_dir = config_dir / 'containers'
    registry = ContainerRegistry(containers_dir)

    container_state = registry.find(workspace)
    if container_state is None:
        click.secho(f"No container found for '{workspace}'.", fg='red')
        sys.exit(1)

    workspace_path = Path(container_state.workspace)
    container_dir = containers_dir / workspace_path.name
    repo_dir = container_dir / 'repo'

    if paths:
        paths = tuple(_make_workspace_relative(p, workspace_path) for p in paths)
        click.echo("Resolved: " + ", ".join(paths))
        try:
            _validate_sync_paths(paths, workspace_path)
        except click.ClickException as e:
            click.secho(f"Error: {e.format_message()}", fg='red')
            sys.exit(1)

    # Full-tree sync without --dry-run requires confirmation
    if not paths and not dry_run and not yes and not force:
        if not click.confirm(
            f"Sync all files from {workspace_path.name} to container repo?",
            default=False,
        ):
            click.echo("Aborted")
            return

    project_config = ProjectConfig.load(workspace_path)
    extra_excludes = (project_config.sync_exclude or []) if project_config else []

    cmd = _build_rsync_cmd(
        src=workspace_path,
        dst=repo_dir,
        paths=paths,
        delete=delete,
        dry_run=dry_run,
        extra_excludes=extra_excludes,
    )

    if delete and not force and not dry_run:
        deletions = _find_deletions(cmd)
        if deletions:
            click.echo("These files will be deleted from the container:")
            for f in deletions:
                click.echo(f"  {f}")
            if not click.confirm("\nProceed?", default=False):
                click.echo("Aborted")
                return

    if dry_run:
        click.echo("Dry run — showing what would be synced:")
    else:
        click.echo(f"Pushing from {workspace_path.name} to container...")

    result = subprocess.run(cmd, capture_output=False, text=True)
    if result.returncode != 0:
        click.secho("rsync failed", fg='red')
        sys.exit(result.returncode)

    if not dry_run:
        click.echo("Done.")


if __name__ == '__main__':
    main()
