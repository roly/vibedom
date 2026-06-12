"""VM lifecycle management."""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from vibedom.proxy import ProxyManager


class VMManager:
    """Manages VM instances for sandbox sessions."""

    def __init__(self, workspace: Path, config_dir: Path, session_dir: Optional[Path] = None,
                 runtime: Optional[str] = None, network: Optional[str] = None,
                 base_image: Optional[str] = None,
                 host_aliases: Optional[dict] = None,
                 container_dir: Optional[Path] = None,
                 memory: Optional[str] = None,
                 extra_env: Optional[dict] = None,
                 proxy_extra_env: Optional[dict] = None):
        """Initialize VM manager.

        Args:
            workspace: Path to workspace directory
            config_dir: Path to config directory
            session_dir: Path to session directory (for session logs mount and legacy repo mount)
            runtime: Container runtime ('auto', 'docker', or 'apple'). If None, auto-detects.
            network: Docker network name to join (for project DB/service access).
            base_image: Project base image to layer vibedom on top of. If None, uses vibedom-alpine.
            host_aliases: Dict of hostname -> IP mappings injected into container /etc/hosts.
                Use 'host' as value to resolve to the host machine IP.
            container_dir: Path for persistent container state (repo bind mount comes from here).
                Takes precedence over session_dir for the repo mount.
            memory: Memory limit for the container (e.g. '4g', '2048m'). Defaults to 4g on
                apple/container (whose default 1GB is insufficient for Claude Code).
            extra_env: Additional environment variables to inject into the container.
                Used for e.g. ANTHROPIC_BASE_URL when Cloudflare AI Gateway is configured.
            proxy_extra_env: Additional environment variables to inject into the mitmproxy host
                process. Used for e.g. CF gateway auth token and user ID headers.
        """
        self.workspace = workspace.resolve()
        self.config_dir = config_dir.resolve()
        self.session_dir = session_dir.resolve() if session_dir else None
        self.container_dir = container_dir.resolve() if container_dir else None
        self.container_name = f'vibedom-{workspace.name}'
        self.runtime, self.runtime_cmd = self._detect_runtime(runtime)
        self.memory = memory
        self.extra_env = extra_env or {}
        self.proxy_extra_env = proxy_extra_env or {}
        self.network = network
        self.base_image = base_image
        self.host_aliases = host_aliases or {}
        self._proxy: Optional[ProxyManager] = None

    @staticmethod
    def _detect_runtime(runtime: Optional[str] = None) -> tuple[str, str]:
        """Detect available container runtime or use specified one.

        Args:
            runtime: Explicit runtime ('docker', 'podman', or 'apple'), or None for auto-detect

        Returns:
            Tuple of (runtime_name, command) — e.g. ('apple', 'container'), ('podman', 'podman')
        """
        if runtime == 'docker':
            if not shutil.which('docker'):
                raise RuntimeError("Docker runtime requested but not found on system.")
            return 'docker', 'docker'
        if runtime == 'podman':
            if not shutil.which('podman'):
                raise RuntimeError("Podman runtime requested but not found on system.")
            return 'podman', 'podman'
        if runtime == 'apple':
            if not shutil.which('container'):
                raise RuntimeError("apple/container runtime requested but not found on system.")
            return 'apple', 'container'

        # Auto-detect: apple/container preferred (hardware VM isolation); podman and docker are fallbacks
        if shutil.which('container'):
            return 'apple', 'container'
        if shutil.which('docker'):
            return 'docker', 'docker'
        if shutil.which('podman'):
            return 'podman', 'podman'
        raise RuntimeError(
            "No container runtime found. Install Docker, Podman, or apple/container (experimental, macOS 26+)."
        )

    @staticmethod
    def image_exists(runtime_cmd: str) -> bool:
        """Check whether the vibedom-alpine image has been built."""
        result = subprocess.run(
            [runtime_cmd, 'image', 'inspect', 'vibedom-alpine:latest'],
            capture_output=True
        )
        return result.returncode == 0

    @staticmethod
    def build_image(runtime: Optional[str] = None) -> None:
        """Build the vibedom-alpine container image.

        Args:
            runtime: Container runtime ('docker' or 'apple'), or None for auto-detect
        """
        _, runtime_cmd = VMManager._detect_runtime(runtime)
        container_dir = Path(__file__).parent / 'container'
        dockerfile = container_dir / 'Dockerfile.alpine'

        if not dockerfile.exists():
            raise RuntimeError(f"Dockerfile not found at {dockerfile}")

        try:
            subprocess.run(
                [runtime_cmd, 'build', '-t', 'vibedom-alpine:latest',
                 '-f', str(dockerfile), str(container_dir)],
                check=True
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"`{runtime_cmd} build` failed (exit {exc.returncode})"
            ) from exc

    def _image_name(self) -> str:
        """Return the image to run. Builds project layer if base_image set."""
        if not self.base_image:
            return 'vibedom-alpine:latest'
        tag = f'vibedom-project-{self.container_name}:latest'
        self.build_project_image(self.base_image, tag)
        return tag

    def build_project_image(self, base_image: str, tag: str) -> None:
        """Build vibedom layer on top of a project base image."""
        container_dir = Path(__file__).parent / 'container'
        dockerfile = container_dir / 'Dockerfile.layer'
        subprocess.run(
            [
                self.runtime_cmd, 'build',
                '--build-arg', f'BASE_IMAGE={base_image}',
                '-t', tag,
                '-f', str(dockerfile),
                str(container_dir),
            ],
            check=True
        )

    def _apple_host_ip(self) -> str:
        """Return the host IP as seen from inside an apple/container container.

        apple/container doesn't support --add-host and doesn't inject host.docker.internal,
        so we resolve the host via the default network gateway.
        """
        result = subprocess.run(
            ['container', 'network', 'inspect', 'default'],
            capture_output=True, text=True, check=True
        )
        networks = json.loads(result.stdout)
        return networks[0]['status']['ipv4Gateway']

    def exists(self) -> bool:
        """Check whether the container exists (running or stopped)."""
        if self.runtime == 'apple':
            # apple/container inspect returns an empty array [] with exit code 0 for unknown
            # containers, so we must check the array is non-empty, not just the returncode.
            result = subprocess.run(
                [self.runtime_cmd, 'inspect', self.container_name],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                return False
            try:
                data = json.loads(result.stdout)
                return bool(data)
            except json.JSONDecodeError:
                return False
        else:
            result = subprocess.run(
                [self.runtime_cmd, 'inspect', '--format', '{{.State.Status}}', self.container_name],
                capture_output=True, text=True,
            )
            return result.returncode == 0

    def is_running(self) -> bool:
        """Check whether the container is currently running."""
        if self.runtime == 'apple':
            # apple/container returns JSON array; status field is top-level on each element
            result = subprocess.run(
                [self.runtime_cmd, 'inspect', self.container_name],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                return False
            try:
                data = json.loads(result.stdout)
                if isinstance(data, list):
                    return bool(data) and data[0].get('status') == 'running'
                return data.get('status') == 'running'
            except (json.JSONDecodeError, KeyError, IndexError):
                return False
        else:
            result = subprocess.run(
                [self.runtime_cmd, 'inspect', '--format', '{{.State.Status}}', self.container_name],
                capture_output=True, text=True,
            )
            return result.returncode == 0 and result.stdout.strip() == 'running'

    def pause(self) -> None:
        """Stop the container without removing it (preserves filesystem)."""
        try:
            subprocess.run(
                [self.runtime_cmd, 'stop', self.container_name],
                capture_output=True,
            )
        except FileNotFoundError:
            pass

    def restart(self) -> None:
        """Start a stopped container and wait for readiness."""
        try:
            subprocess.run(
                [self.runtime_cmd, 'start', self.container_name],
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise RuntimeError(
                f"Failed to restart container '{self.container_name}': {e}"
            ) from e

        # Wait for VM to be ready
        for _ in range(60):
            result = subprocess.run(
                [self.runtime_cmd, 'exec', self.container_name,
                 'test', '-f', '/tmp/.vm-ready'],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                return
            time.sleep(1)
        raise RuntimeError(
            f"VM '{self.container_name}' failed to become ready within 60 seconds"
        )

    def start(self) -> None:
        """Start the VM with workspace mounted."""
        # Stop existing container if any
        self.stop()

        # Copy gitleaks config for runtime DLP patterns
        gitleaks_src = Path(__file__).parent / 'config' / 'gitleaks.toml'
        gitleaks_dst = self.config_dir / 'gitleaks.toml'
        shutil.copy(gitleaks_src, gitleaks_dst)

        # Build image before starting proxy — mitmproxy interferes with apple/container
        # build-time network, so we build first while the network is clean.
        image = self._image_name()

        # Start host proxy — logs go to container_dir or session_dir
        proxy_log_dir = self.container_dir or self.session_dir
        if proxy_log_dir is None:
            raise RuntimeError("Either container_dir or session_dir must be set to start the VM")
        self._proxy = ProxyManager(
            session_dir=proxy_log_dir,
            config_dir=self.config_dir,
            extra_env=self.proxy_extra_env,
        )
        proxy_port = self._proxy.start()

        # Ensure mitmproxy conf dir exists so the CA cert will be readable via /mnt/config mount
        conf_dir = self.config_dir / 'mitmproxy'
        conf_dir.mkdir(parents=True, exist_ok=True)

        detach_flag = '--detach' if self.runtime == 'apple' else '-d'
        # apple/container doesn't support --add-host and doesn't inject host.docker.internal,
        # so we use the default network gateway IP to reach the host proxy.
        if self.runtime == 'apple':
            proxy_host = self._apple_host_ip()
        elif self.runtime == 'podman':
            proxy_host = 'host.containers.internal'
        else:
            proxy_host = 'host.docker.internal'
        proxy_url = f'http://{proxy_host}:{proxy_port}'
        # CA bundle path inside the container (set by update-ca-certificates)
        ca_bundle = '/etc/ssl/certs/ca-certificates.crt'

        # apple/container defaults to 1GB which OOM-kills Claude Code; use 4g unless overridden
        memory_limit = self.memory or ('4g' if self.runtime == 'apple' else None)

        cmd = [
            self.runtime_cmd, 'run',
            detach_flag,
            '--name', self.container_name,
        ]
        if memory_limit:
            cmd += ['--memory', memory_limit]
        # Docker/podman on Linux need --add-host to resolve the host gateway hostname;
        # apple/container doesn't support the flag (we use the gateway IP directly instead).
        if self.runtime == 'docker':
            cmd += ['--add-host', 'host.docker.internal:host-gateway']
        elif self.runtime == 'podman':
            cmd += ['--add-host', 'host.containers.internal:host-gateway']
        cmd += [
            # Host IP — lets apps inside the container reach host-bound services
            # (Docker Compose ports, etc.) without hardcoding an address.
            '-e', f'HOST_IP={proxy_host}',
            # Proxy environment variables
            '-e', f'HTTP_PROXY={proxy_url}',
            '-e', f'HTTPS_PROXY={proxy_url}',
            '-e', 'NO_PROXY=localhost,127.0.0.1,::1',
            '-e', f'http_proxy={proxy_url}',
            '-e', f'https_proxy={proxy_url}',
            '-e', 'no_proxy=localhost,127.0.0.1,::1',
            # CA bundle env vars — set here so docker exec sessions inherit them
            '-e', f'REQUESTS_CA_BUNDLE={ca_bundle}',
            '-e', f'SSL_CERT_FILE={ca_bundle}',
            '-e', f'CURL_CA_BUNDLE={ca_bundle}',
            '-e', f'NODE_EXTRA_CA_CERTS={ca_bundle}',
        ]
        # Extra env vars (e.g. ANTHROPIC_BASE_URL for Cloudflare AI Gateway)
        for key, value in self.extra_env.items():
            cmd += ['-e', f'{key}={value}']
        cmd += [
            # Mounts
            '-v', f'{self.workspace}:/mnt/workspace:ro',
            '-v', f'{self.config_dir}:/mnt/config:ro',
        ]

        # Repo mount: prefer container_dir (persistent), fall back to session_dir (legacy)
        if self.container_dir:
            repo_dir = self.container_dir / 'repo'
            repo_dir.mkdir(parents=True, exist_ok=True)
            cmd += ['-v', f'{repo_dir}:/work/repo']
        elif self.session_dir:
            repo_dir = self.session_dir / 'repo'
            repo_dir.mkdir(parents=True, exist_ok=True)
            cmd += ['-v', f'{repo_dir}:/work/repo']

        if self.session_dir:
            cmd += ['-v', f'{self.session_dir}:/mnt/session']

        if self.network:
            if self.runtime == 'apple':
                print(
                    "Warning: vibedom.yml 'network:' is not supported with apple/container — "
                    "ignored. Connect to host services via host.docker.internal instead.",
                    file=sys.stderr,
                )
            else:
                cmd += ['--network', self.network]

        if self.host_aliases:
            if self.runtime == 'apple':
                # apple/container doesn't support --add-host; inject via env var handled in startup.sh
                aliases_str = ','.join(
                    f'{h}={proxy_host if ip == "host" else ip}'
                    for h, ip in self.host_aliases.items()
                )
                cmd += ['-e', f'VIBEDOM_HOST_ALIASES={aliases_str}']
            else:
                for hostname, ip in self.host_aliases.items():
                    resolved = proxy_host if ip == 'host' else ip
                    cmd += ['--add-host', f'{hostname}:{resolved}']

        # Claude/OpenCode config - shared persistent volume across all workspaces.
        # apple/container doesn't support named volumes, so use a bind mount instead.
        if self.runtime == 'apple':
            claude_config_dir = Path.home() / '.vibedom' / 'claude-config'
            claude_config_dir.mkdir(parents=True, exist_ok=True)
            cmd += ['-v', f'{claude_config_dir}:/root/.claude']
        else:
            cmd += ['-v', 'vibedom-claude-config:/root/.claude']

        cmd.append(image)

        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            self._proxy.stop()
            raise RuntimeError(
                f"Failed to start VM container '{self.container_name}': {e}"
            ) from e
        except FileNotFoundError:
            self._proxy.stop()
            raise RuntimeError(
                f"Container command '{self.runtime_cmd}' not found."
            ) from None

        # Wait for VM to be ready (increased timeout for git cloning)
        for _ in range(60):
            result = subprocess.run(
                [self.runtime_cmd, 'exec', self.container_name,
                 'test', '-f', '/tmp/.vm-ready'],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                return
            time.sleep(1)
        raise RuntimeError(
            f"VM '{self.container_name}' failed to become ready within 60 seconds"
        )

    def stop(self) -> None:
        """Stop and remove the VM."""
        try:
            if self.runtime == 'apple':
                subprocess.run(
                    [self.runtime_cmd, 'stop', self.container_name],
                    capture_output=True,
                )
                subprocess.run(
                    [self.runtime_cmd, 'delete', '--force', self.container_name],
                    capture_output=True,
                )
            else:
                subprocess.run(
                    [self.runtime_cmd, 'rm', '-f', self.container_name],
                    capture_output=True,
                )
        except FileNotFoundError:
            pass  # Runtime not installed

        if self._proxy:
            self._proxy.stop()
            self._proxy = None

    def exec(self, command: list[str]) -> subprocess.CompletedProcess:
        """Execute a command inside the VM.

        Args:
            command: Command and arguments to execute

        Returns:
            CompletedProcess with stdout/stderr
        """
        return subprocess.run(
            [self.runtime_cmd, 'exec', self.container_name] + command,
            capture_output=True,
            text=True,
        )
