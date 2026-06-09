import tempfile
import subprocess
from pathlib import Path
import pytest
import shutil
from unittest.mock import patch, MagicMock
from vibedom.vm import VMManager
from vibedom.session import Session

@pytest.fixture
def test_workspace():
    """Create a temporary workspace for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / 'workspace'
        workspace.mkdir()
        (workspace / 'test.txt').write_text('hello')
        yield workspace

@pytest.fixture
def test_config():
    """Create a temporary config directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        yield config_dir


@pytest.fixture(autouse=True)
def _available_runtimes():
    """Make both docker and container appear installed so VMManager() doesn't raise."""
    with patch('shutil.which', side_effect=lambda cmd: f'/usr/local/bin/{cmd}' if cmd in ('docker', 'container') else None):
        yield

@pytest.mark.integration
def test_vm_start_stop(test_workspace, test_config):
    """Should start and stop VM successfully."""
    vm = VMManager(test_workspace, test_config)

    vm.start()

    # Check container is running
    result = vm.exec(['echo', 'test'])
    assert result.returncode == 0
    assert 'test' in result.stdout

    vm.stop()

@pytest.mark.integration
def test_vm_git_repo_initialized(test_workspace, test_config):
    """VM should initialize git repo from workspace."""
    import subprocess
    from vibedom.session import Session

    # Create test git workspace
    (test_workspace / 'test.txt').write_text('test content')
    subprocess.run(['git', 'init'], cwd=test_workspace, check=True)
    subprocess.run(['git', 'add', '.'], cwd=test_workspace, check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial'], cwd=test_workspace, check=True)

    session = Session(test_workspace, Path('/tmp/vibedom-test-logs'))
    vm = VMManager(test_workspace, test_config, session_dir=session.session_dir)

    try:
        vm.start()

        # Verify git repo initialized in container
        result = vm.exec(['sh', '-c', 'cd /work/repo && git log --oneline'])
        assert 'Initial' in result.stdout

    finally:
        vm.stop()
        shutil.rmtree(session.session_dir, ignore_errors=True)

@pytest.mark.integration
def test_vm_mounts_session_repo(test_workspace, test_config):
    """VM should mount session repo directory."""
    session = Session(test_workspace, Path('/tmp/vibedom-test-logs'))

    vm = VMManager(test_workspace, test_config, session_dir=session.session_dir)

    try:
        vm.start()

        # Verify repo directory exists in session
        repo_dir = session.session_dir / 'repo'
        assert repo_dir.exists(), "Repo directory should exist in session dir"

        # Verify .git exists in mounted repo
        git_dir = repo_dir / '.git'
        assert git_dir.exists(), "Git directory should exist in mounted repo"

    finally:
        vm.stop()
        shutil.rmtree(session.session_dir, ignore_errors=True)


def test_detect_runtime_prefers_apple(test_workspace, test_config):
    """Should prefer apple/container over Docker when both are available."""
    with patch('shutil.which') as mock_which:
        mock_which.side_effect = lambda cmd: '/usr/local/bin/' + cmd if cmd in ('docker', 'container') else None
        vm = VMManager(test_workspace, test_config)
        assert vm.runtime == 'apple'
        assert vm.runtime_cmd == 'container'


def test_detect_runtime_falls_back_to_apple(test_workspace, test_config):
    """Should fall back to apple/container when Docker is not available."""
    with patch('shutil.which') as mock_which:
        mock_which.side_effect = lambda cmd: '/usr/local/bin/container' if cmd == 'container' else None
        vm = VMManager(test_workspace, test_config)
        assert vm.runtime == 'apple'
        assert vm.runtime_cmd == 'container'


def test_detect_runtime_uses_docker_when_only_docker(test_workspace, test_config):
    """Should use Docker when only Docker is available."""
    with patch('shutil.which') as mock_which:
        mock_which.side_effect = lambda cmd: '/usr/local/bin/docker' if cmd == 'docker' else None
        vm = VMManager(test_workspace, test_config)
        assert vm.runtime == 'docker'
        assert vm.runtime_cmd == 'docker'


def test_detect_runtime_raises_when_neither(test_workspace, test_config):
    """Should raise RuntimeError when no runtime found."""
    with patch('shutil.which', return_value=None):
        with pytest.raises(RuntimeError, match="No container runtime found"):
            VMManager(test_workspace, test_config)


def test_explicit_runtime_docker(test_workspace, test_config):
    """Should use Docker when explicitly specified."""
    with patch('shutil.which') as mock_which:
        mock_which.return_value = '/usr/local/bin/docker'
        vm = VMManager(test_workspace, test_config, runtime='docker')
        assert vm.runtime == 'docker'
        assert vm.runtime_cmd == 'docker'


def test_explicit_runtime_apple(test_workspace, test_config):
    """Should use apple/container when explicitly specified."""
    with patch('shutil.which') as mock_which:
        mock_which.return_value = '/usr/local/bin/container'
        vm = VMManager(test_workspace, test_config, runtime='apple')
        assert vm.runtime == 'apple'
        assert vm.runtime_cmd == 'container'


def test_explicit_runtime_raises_if_not_available(test_workspace, test_config):
    """Should raise RuntimeError when explicit runtime not found."""
    with patch('shutil.which', return_value=None):
        with pytest.raises(RuntimeError, match="Docker runtime requested but not found"):
            VMManager(test_workspace, test_config, runtime='docker')


def test_detect_runtime_falls_back_to_podman(test_workspace, test_config):
    """Should fall back to podman when docker and apple/container are not available."""
    with patch('shutil.which') as mock_which:
        mock_which.side_effect = lambda cmd: '/usr/local/bin/podman' if cmd == 'podman' else None
        vm = VMManager(test_workspace, test_config)
        assert vm.runtime == 'podman'
        assert vm.runtime_cmd == 'podman'


def test_explicit_runtime_podman(test_workspace, test_config):
    """Should use podman when explicitly specified."""
    with patch('shutil.which') as mock_which:
        mock_which.return_value = '/usr/local/bin/podman'
        vm = VMManager(test_workspace, test_config, runtime='podman')
        assert vm.runtime == 'podman'
        assert vm.runtime_cmd == 'podman'


def test_explicit_runtime_podman_raises_if_not_available(test_workspace, test_config):
    """Should raise RuntimeError when podman explicitly requested but not found."""
    with patch('shutil.which', return_value=None):
        with pytest.raises(RuntimeError, match="Podman runtime requested but not found"):
            VMManager(test_workspace, test_config, runtime='podman')


def test_start_uses_apple_runtime(test_workspace, test_config, tmp_path):
    """start() should use 'container' command when runtime is apple."""
    with patch('shutil.which') as mock_which:
        mock_which.side_effect = lambda cmd: '/usr/local/bin/container' if cmd == 'container' else None
        vm = VMManager(test_workspace, test_config, session_dir=tmp_path / 'session')

    with patch('vibedom.vm.VMManager._apple_host_ip', return_value='192.168.64.1'):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            with patch('vibedom.vm.ProxyManager') as mock_proxy_cls:
                mock_proxy = MagicMock()
                mock_proxy.start.return_value = 54321
                mock_proxy.ca_cert_path = None
                mock_proxy_cls.return_value = mock_proxy
                with patch('shutil.copy'):
                    try:
                        vm.start()
                    except RuntimeError:
                        pass

            calls = mock_run.call_args_list
            run_call = next(c for c in calls if 'run' in c[0][0])
            assert run_call[0][0][0] == 'container'
            assert '--detach' in run_call[0][0]
            assert '--privileged' not in run_call[0][0]


def test_start_uses_docker_runtime(test_workspace, test_config, tmp_path):
    """start() should use 'docker' command when runtime is docker."""
    with patch('shutil.which') as mock_which:
        mock_which.side_effect = lambda cmd: '/usr/local/bin/docker' if cmd == 'docker' else None
        vm = VMManager(test_workspace, test_config, session_dir=tmp_path / 'session')

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        with patch('vibedom.vm.ProxyManager') as mock_proxy_cls:
            mock_proxy = MagicMock()
            mock_proxy.start.return_value = 54321
            mock_proxy.ca_cert_path = None
            mock_proxy_cls.return_value = mock_proxy
            with patch('shutil.copy'):
                try:
                    vm.start()
                except RuntimeError:
                    pass

        calls = mock_run.call_args_list
        run_call = next(c for c in calls if 'run' in c[0][0])
        assert run_call[0][0][0] == 'docker'
        assert '-d' in run_call[0][0]
        assert '--privileged' not in run_call[0][0]


def test_stop_uses_apple_commands(test_workspace, test_config):
    """stop() should use 'container stop' + 'container delete' for apple runtime."""
    with patch('shutil.which') as mock_which:
        mock_which.side_effect = lambda cmd: '/usr/local/bin/container' if cmd == 'container' else None
        vm = VMManager(test_workspace, test_config)

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        vm.stop()

        calls = [c[0][0] for c in mock_run.call_args_list]
        assert calls[0][:2] == ['container', 'stop']
        assert calls[1][:2] == ['container', 'delete']


def test_stop_uses_docker_command(test_workspace, test_config):
    """stop() should use 'docker rm -f' for docker runtime."""
    with patch('shutil.which') as mock_which:
        mock_which.side_effect = lambda cmd: '/usr/local/bin/docker' if cmd == 'docker' else None
        vm = VMManager(test_workspace, test_config)

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        vm.stop()

        calls = [c[0][0] for c in mock_run.call_args_list]
        assert calls[0] == ['docker', 'rm', '-f', vm.container_name]


def test_exec_uses_detected_runtime(test_workspace, test_config):
    """exec() should use detected runtime command."""
    with patch('shutil.which') as mock_which:
        mock_which.side_effect = lambda cmd: '/usr/local/bin/container' if cmd == 'container' else None
        vm = VMManager(test_workspace, test_config)

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='hello', stderr=''
        )
        vm.exec(['echo', 'hello'])

        call_args = mock_run.call_args[0][0]
        assert call_args[:2] == ['container', 'exec']


def test_start_mounts_claude_volume(test_workspace, test_config, tmp_path):
    """start() should mount vibedom-claude-config volume for Claude persistence."""
    session_dir = tmp_path / 'session'
    session_dir.mkdir()

    vm = VMManager(test_workspace, test_config, session_dir, runtime='docker')

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        with patch('shutil.copy'):
            with patch('vibedom.vm.ProxyManager') as mock_proxy_cls:
                mock_proxy = MagicMock()
                mock_proxy.start.return_value = 54321
                mock_proxy.ca_cert_path = None
                mock_proxy_cls.return_value = mock_proxy
                try:
                    vm.start()
                except RuntimeError:
                    pass  # May fail on readiness check, that's ok

        # Find the 'run' call
        calls = mock_run.call_args_list
        run_call = next(c for c in calls if 'run' in c[0][0])
        cmd = run_call[0][0]

        # Check that Claude config volume is mounted
        assert '-v' in cmd
        volume_mount = 'vibedom-claude-config:/root/.claude'
        assert volume_mount in cmd, f"Claude config volume mount '{volume_mount}' not found in command"


def test_start_skips_claude_mounts_if_not_exists(test_workspace, test_config, tmp_path):
    """start() should not fail if ~/.claude doesn't exist."""
    session_dir = tmp_path / 'session'
    session_dir.mkdir()

    vm = VMManager(test_workspace, test_config, session_dir, runtime='docker')

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        with patch('shutil.copy'):
            with patch('vibedom.vm.ProxyManager') as mock_proxy_cls:
                mock_proxy = MagicMock()
                mock_proxy.start.return_value = 54321
                mock_proxy.ca_cert_path = None
                mock_proxy_cls.return_value = mock_proxy
                try:
                    vm.start()
                except RuntimeError:
                    pass

        # Should still succeed, just without Claude mounts
        assert mock_run.called


def test_vm_start_uses_host_proxy(tmp_path):
    """VMManager.start() should start ProxyManager and pass port to container."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    config_dir = tmp_path / 'config'
    config_dir.mkdir()
    session_dir = tmp_path / 'session'
    session_dir.mkdir()

    vm = VMManager(workspace, config_dir, session_dir, runtime='docker')

    with patch('vibedom.vm.ProxyManager') as mock_proxy_cls:
        mock_proxy = MagicMock()
        mock_proxy.start.return_value = 54321
        mock_proxy.ca_cert_path = tmp_path / 'cert.pem'
        mock_proxy_cls.return_value = mock_proxy

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch('shutil.copy'):
                try:
                    vm.start()
                except RuntimeError:
                    pass

        assert mock_proxy.start.called
        run_calls = [c for c in mock_run.call_args_list if 'run' in c[0][0]]
        assert run_calls, "Expected a 'docker run' call"
        cmd = ' '.join(run_calls[0][0][0])
        assert 'host.docker.internal' in cmd
        assert '54321' in cmd


def test_vm_start_with_project_network(tmp_path):
    """VMManager.start() should add --network flag when network specified."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    vm = VMManager(workspace, tmp_path / 'config', tmp_path / 'session',
                   runtime='docker', network='wapi_shared')

    with patch('vibedom.vm.ProxyManager') as mock_proxy_cls:
        mock_proxy = MagicMock()
        mock_proxy.start.return_value = 54321
        mock_proxy.ca_cert_path = None
        mock_proxy_cls.return_value = mock_proxy

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch('shutil.copy'):
                try:
                    vm.start()
                except RuntimeError:
                    pass

        run_calls = [c for c in mock_run.call_args_list if 'run' in c[0][0]]
        assert run_calls, "Expected a 'docker run' call"
        cmd = ' '.join(run_calls[0][0][0])
        assert '--network' in cmd
        assert 'wapi_shared' in cmd


def test_vm_start_network_ignored_with_apple_runtime(tmp_path):
    """VMManager.start() should warn and skip --network when runtime is apple."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    vm = VMManager(workspace, tmp_path / 'config', tmp_path / 'session',
                   runtime='apple', network='myproject_default')

    with patch('vibedom.vm.VMManager._apple_host_ip', return_value='192.168.64.1'):
        with patch('vibedom.vm.ProxyManager') as mock_proxy_cls:
            mock_proxy = MagicMock()
            mock_proxy.start.return_value = 54321
            mock_proxy.ca_cert_path = None
            mock_proxy_cls.return_value = mock_proxy

            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                with patch('shutil.copy'):
                    import io
                    with patch('vibedom.vm.sys.stderr', new_callable=io.StringIO) as mock_stderr:
                        try:
                            vm.start()
                        except RuntimeError:
                            pass
                        warning = mock_stderr.getvalue()

    assert 'network' in warning.lower()
    assert 'apple' in warning.lower() or 'not supported' in warning.lower()
    run_calls = [c for c in mock_run.call_args_list if 'run' in c[0][0]]
    assert run_calls
    cmd = ' '.join(run_calls[0][0][0])
    assert '--network' not in cmd


def test_vm_stop_stops_proxy(tmp_path):
    """VMManager.stop() should stop the ProxyManager."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    vm = VMManager(workspace, tmp_path / 'config', tmp_path / 'session',
                   runtime='docker')

    mock_proxy = MagicMock()
    vm._proxy = mock_proxy

    with patch('subprocess.run', return_value=MagicMock(returncode=0)):
        vm.stop()

    assert mock_proxy.stop.called


def test_vm_start_adds_host_aliases_for_docker(tmp_path):
    """start() should add --add-host flags for each host_alias when runtime is docker."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    vm = VMManager(workspace, tmp_path / 'config', tmp_path / 'session',
                   runtime='docker',
                   host_aliases={'wapi-redis': 'host', 'custom-svc': '10.0.0.5'})

    with patch('vibedom.vm.ProxyManager') as mock_proxy_cls:
        mock_proxy = MagicMock()
        mock_proxy.start.return_value = 54321
        mock_proxy.ca_cert_path = None
        mock_proxy_cls.return_value = mock_proxy

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch('shutil.copy'):
                try:
                    vm.start()
                except RuntimeError:
                    pass

    run_calls = [c for c in mock_run.call_args_list if 'run' in c[0][0]]
    cmd = run_calls[0][0][0]
    cmd_str = ' '.join(cmd)
    assert '--add-host' in cmd_str
    assert 'wapi-redis:host.docker.internal' in cmd_str
    assert 'custom-svc:10.0.0.5' in cmd_str


def test_vm_start_adds_host_aliases_env_for_apple(tmp_path):
    """start() should set VIBEDOM_HOST_ALIASES env var for apple/container runtime."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    vm = VMManager(workspace, tmp_path / 'config', tmp_path / 'session',
                   runtime='apple',
                   host_aliases={'wapi-redis': 'host', 'wapi-mysql': 'host'})

    with patch('vibedom.vm.VMManager._apple_host_ip', return_value='192.168.64.1'):
        with patch('vibedom.vm.ProxyManager') as mock_proxy_cls:
            mock_proxy = MagicMock()
            mock_proxy.start.return_value = 54321
            mock_proxy.ca_cert_path = None
            mock_proxy_cls.return_value = mock_proxy

            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                with patch('shutil.copy'):
                    try:
                        vm.start()
                    except RuntimeError:
                        pass

    run_calls = [c for c in mock_run.call_args_list if 'run' in c[0][0]]
    cmd = run_calls[0][0][0]
    # Find the VIBEDOM_HOST_ALIASES value
    aliases_value = None
    for i, arg in enumerate(cmd):
        if arg == '-e' and i + 1 < len(cmd) and cmd[i + 1].startswith('VIBEDOM_HOST_ALIASES='):
            aliases_value = cmd[i + 1][len('VIBEDOM_HOST_ALIASES='):]
            break
    assert aliases_value is not None, "VIBEDOM_HOST_ALIASES env var not found in command"
    aliases = dict(pair.split('=', 1) for pair in aliases_value.split(','))
    assert aliases.get('wapi-redis') == '192.168.64.1'
    assert aliases.get('wapi-mysql') == '192.168.64.1'


def test_vm_start_no_host_aliases_adds_no_add_host(tmp_path):
    """start() should not add --add-host flags when host_aliases is empty."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    vm = VMManager(workspace, tmp_path / 'config', tmp_path / 'session', runtime='docker')

    with patch('vibedom.vm.ProxyManager') as mock_proxy_cls:
        mock_proxy = MagicMock()
        mock_proxy.start.return_value = 54321
        mock_proxy.ca_cert_path = None
        mock_proxy_cls.return_value = mock_proxy

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch('shutil.copy'):
                try:
                    vm.start()
                except RuntimeError:
                    pass

    run_calls = [c for c in mock_run.call_args_list if 'run' in c[0][0]]
    cmd = run_calls[0][0][0]
    # host.docker.internal is added for Docker by default, but no extra --add-host entries
    add_host_entries = [cmd[i + 1] for i, a in enumerate(cmd) if a == '--add-host']
    non_default = [e for e in add_host_entries if not e.startswith('host.docker.internal')]
    assert non_default == []


# --- Persistent container lifecycle tests ---

def test_vm_exists_returns_true_when_container_present(tmp_path):
    """exists() should return True when docker inspect succeeds."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    vm = VMManager(workspace, tmp_path / 'config', runtime='docker')

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert vm.exists() is True

    call_args = mock_run.call_args[0][0]
    assert 'inspect' in call_args


def test_vm_exists_returns_false_when_container_absent(tmp_path):
    """exists() should return False when docker inspect fails."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    vm = VMManager(workspace, tmp_path / 'config', runtime='docker')

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert vm.exists() is False


def test_vm_is_running_returns_true_when_running(tmp_path):
    """is_running() should return True when container is in running state."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    vm = VMManager(workspace, tmp_path / 'config', runtime='docker')

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout='running\n')
        assert vm.is_running() is True


def test_vm_is_running_returns_false_when_stopped(tmp_path):
    """is_running() should return False when container is stopped or absent."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    vm = VMManager(workspace, tmp_path / 'config', runtime='docker')

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout='exited\n')
        assert vm.is_running() is False


def test_vm_is_running_returns_false_when_inspect_fails(tmp_path):
    """is_running() should return False when inspect fails (container doesn't exist)."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    vm = VMManager(workspace, tmp_path / 'config', runtime='docker')

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout='')
        assert vm.is_running() is False


def test_vm_pause_stops_without_removing_docker(tmp_path):
    """pause() should call 'docker stop' but NOT 'docker rm'."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    vm = VMManager(workspace, tmp_path / 'config', runtime='docker')

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        vm.pause()

    calls = [c[0][0] for c in mock_run.call_args_list]
    assert any(c[:2] == ['docker', 'stop'] for c in calls)
    assert not any('rm' in c for c in calls)


def test_vm_pause_stops_without_removing_apple(tmp_path):
    """pause() should call 'container stop' but NOT 'container delete' for apple runtime."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    vm = VMManager(workspace, tmp_path / 'config', runtime='apple')

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        vm.pause()

    calls = [c[0][0] for c in mock_run.call_args_list]
    assert any(c[:2] == ['container', 'stop'] for c in calls)
    assert not any('delete' in c for c in calls)


def test_vm_restart_starts_stopped_container(tmp_path):
    """restart() should call 'docker start' and wait for readiness."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    vm = VMManager(workspace, tmp_path / 'config', runtime='docker')

    # First call (docker start) succeeds, second call (docker exec test) succeeds
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        vm.restart()

    calls = [c[0][0] for c in mock_run.call_args_list]
    assert any(c[:2] == ['docker', 'start'] for c in calls)


def test_vm_start_mounts_repo_from_container_dir(tmp_path):
    """start() should mount repo from container_dir when provided."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    container_dir = tmp_path / 'containers' / 'myapp'
    container_dir.mkdir(parents=True)

    vm = VMManager(workspace, tmp_path / 'config', container_dir=container_dir, runtime='docker')

    with patch('vibedom.vm.ProxyManager') as mock_proxy_cls:
        mock_proxy = MagicMock()
        mock_proxy.start.return_value = 54321
        mock_proxy.ca_cert_path = None
        mock_proxy_cls.return_value = mock_proxy

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch('shutil.copy'):
                try:
                    vm.start()
                except RuntimeError:
                    pass

    run_calls = [c for c in mock_run.call_args_list if 'run' in c[0][0]]
    assert run_calls
    cmd = ' '.join(run_calls[0][0][0])
    assert str(container_dir / 'repo') in cmd
    assert ':/work/repo' in cmd
