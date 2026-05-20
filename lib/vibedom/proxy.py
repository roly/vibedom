"""Host-based mitmproxy process management."""

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


def _find_free_port() -> int:
    """Ask the OS for an available port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def _wait_for_proxy(port: int, timeout: int = 10) -> bool:
    """Poll until mitmproxy is accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


class ProxyManager:
    """Manages a host-side mitmproxy process for one session."""

    def __init__(self, session_dir: Path, config_dir: Path,
                 extra_env: Optional[dict] = None):
        self.session_dir = session_dir
        self.config_dir = config_dir
        self.extra_env = extra_env or {}
        self._process: Optional[subprocess.Popen] = None
        self._log_file = None
        self.port: Optional[int] = None

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process else None

    def start(self, port: Optional[int] = None) -> int:
        """Start mitmdump on the host. Returns the port it's listening on.

        Args:
            port: Port to listen on. If None, an OS-assigned free port is used.
                  Pass a specific port when restarting so the container's
                  HTTP_PROXY setting remains valid.
        """
        mitmdump = shutil.which('mitmdump')
        if not mitmdump:
            # Also check alongside the current Python interpreter (e.g. inside a venv)
            candidate = Path(sys.executable).parent / 'mitmdump'
            if candidate.exists():
                mitmdump = str(candidate)
        if not mitmdump:
            raise RuntimeError(
                "mitmdump not found. Reinstall vibedom: "
                "uv tool install git+https://github.com/timsweb/vibedom.git  "
                "(or: pipx install --force git+https://github.com/timsweb/vibedom.git)"
            )

        self.port = port if port is not None else _find_free_port()

        addon_path = Path(__file__).parent / 'container' / 'mitmproxy_addon.py'
        conf_dir = self.config_dir / 'mitmproxy'
        conf_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update({
            'VIBEDOM_WHITELIST_PATH': str(self.config_dir / 'trusted_domains.txt'),
            'VIBEDOM_NETWORK_LOG_PATH': str(self.session_dir / 'network.jsonl'),
            'VIBEDOM_GITLEAKS_CONFIG': str(self.config_dir / 'gitleaks.toml'),
        })
        env.update(self.extra_env)

        log_path = self.session_dir / 'mitmproxy.log'
        self._log_file = open(log_path, 'w')
        self._process = subprocess.Popen(
            [
                mitmdump,
                '--listen-port', str(self.port),
                '--set', f'confdir={conf_dir}',
                '--mode', 'regular',
                '-q',
                '-s', str(addon_path),
            ],
            env=env,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )

        if not _wait_for_proxy(self.port):
            self._process.terminate()
            raise RuntimeError(
                f"mitmproxy failed to start on port {self.port}. "
                f"Check {log_path}"
            )

        return self.port

    def stop(self) -> None:
        """Terminate the mitmdump process."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
            if self._log_file:
                self._log_file.close()
                self._log_file = None
            self.port = None

    def reload(self) -> None:
        """Send SIGHUP to reload the whitelist."""
        if self._process:
            self._process.send_signal(signal.SIGHUP)

    @property
    def ca_cert_path(self) -> Optional[Path]:
        """Path to the mitmproxy CA cert (exists after start()).

        Example:
            manager = ProxyManager(session_dir=Path('/tmp/session'), config_dir=Path('/tmp/config'))
            manager.start()
            cert = manager.ca_cert_path  # Path to PEM cert, or None if not yet generated
        """
        conf_dir = self.config_dir / 'mitmproxy'
        cert = conf_dir / 'mitmproxy-ca-cert.pem'
        return cert if cert.exists() else None
