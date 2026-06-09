import json
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from vibedom.cli import main
from helpers import _make_complete_state, _make_running_state


def test_run_writes_state_json(tmp_path):
    """vibedom run should write state.json to the session directory."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('vibedom.cli.scan_workspace', return_value=[]):
            with patch('vibedom.cli.review_findings', return_value=True):
                with patch('vibedom.cli.VMManager') as mock_vm_cls:
                    mock_vm_cls._detect_runtime.return_value = ('docker', 'docker')
                    mock_vm = MagicMock()
                    mock_vm._proxy = None
                    mock_vm_cls.return_value = mock_vm

                    runner.invoke(main, ['run', str(workspace)])

    session_dirs = list((tmp_path / '.vibedom' / 'logs').glob('session-*'))
    assert len(session_dirs) == 1, f"Expected 1 session dir, got: {session_dirs}"
    state_file = session_dirs[0] / 'state.json'
    assert state_file.exists(), "state.json not written"
    state = json.loads(state_file.read_text())
    assert state['status'] == 'running'
    assert state['workspace'] == str(workspace)
    assert state['runtime'] == 'docker'


def test_run_shows_session_id(tmp_path):
    """vibedom run should display the session ID in output."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('vibedom.cli.scan_workspace', return_value=[]):
            with patch('vibedom.cli.review_findings', return_value=True):
                with patch('vibedom.cli.VMManager') as mock_vm_cls:
                    mock_vm_cls._detect_runtime.return_value = ('docker', 'docker')
                    mock_vm = MagicMock()
                    mock_vm._proxy = None
                    mock_vm_cls.return_value = mock_vm

                    result = runner.invoke(main, ['run', str(workspace)])

    assert 'Session ID:' in result.output


def test_run_reads_vibedom_yml(tmp_path):
    """vibedom run should pass base_image and network from vibedom.yml to VMManager."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    (workspace / 'vibedom.yml').write_text(
        'base_image: myapp-php:latest\nnetwork: myapp_net\n'
    )

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('vibedom.cli.scan_workspace', return_value=[]):
            with patch('vibedom.cli.review_findings', return_value=True):
                with patch('vibedom.cli.VMManager') as mock_vm_cls:
                    mock_vm_cls._detect_runtime.return_value = ('docker', 'docker')
                    mock_vm = MagicMock()
                    mock_vm._proxy = None
                    mock_vm_cls.return_value = mock_vm

                    runner.invoke(main, ['run', str(workspace)])

    call_kwargs = mock_vm_cls.call_args[1]
    assert call_kwargs.get('base_image') == 'myapp-php:latest'
    assert call_kwargs.get('network') == 'myapp_net'


def test_run_passes_host_aliases_from_vibedom_yml(tmp_path):
    """vibedom run should pass host_aliases from vibedom.yml to VMManager."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    (workspace / 'vibedom.yml').write_text(
        'host_aliases:\n  wapi-redis: host\n  wapi-mysql: host\n'
    )

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('vibedom.cli.scan_workspace', return_value=[]):
            with patch('vibedom.cli.review_findings', return_value=True):
                with patch('vibedom.cli.VMManager') as mock_vm_cls:
                    mock_vm_cls._detect_runtime.return_value = ('docker', 'docker')
                    mock_vm = MagicMock()
                    mock_vm._proxy = None
                    mock_vm_cls.return_value = mock_vm

                    runner.invoke(main, ['run', str(workspace)])

    call_kwargs = mock_vm_cls.call_args[1]
    assert call_kwargs.get('host_aliases') == {'wapi-redis': 'host', 'wapi-mysql': 'host'}


def test_run_stores_proxy_info_in_state(tmp_path):
    """vibedom run should save proxy_port and proxy_pid to state.json."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('vibedom.cli.scan_workspace', return_value=[]):
            with patch('vibedom.cli.review_findings', return_value=True):
                with patch('vibedom.cli.VMManager') as mock_vm_cls:
                    mock_vm_cls._detect_runtime.return_value = ('docker', 'docker')
                    mock_proxy = MagicMock()
                    mock_proxy.port = 54321
                    mock_proxy.pid = 99999
                    mock_vm = MagicMock()
                    mock_vm._proxy = mock_proxy
                    mock_vm_cls.return_value = mock_vm

                    runner.invoke(main, ['run', str(workspace)])

    session_dirs = list((tmp_path / '.vibedom' / 'logs').glob('session-*'))
    assert session_dirs
    state = json.loads((session_dirs[0] / 'state.json').read_text())
    assert state['proxy_port'] == 54321
    assert state['proxy_pid'] == 99999


def test_stop_uses_session_registry(tmp_path):
    """stop should find session via SessionRegistry, not log parsing."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260219-100000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(json.dumps({
        'session_id': 'myapp-happy-turing',
        'workspace': str(workspace),
        'runtime': 'docker',
        'container_name': 'vibedom-myapp',
        'status': 'running',
        'started_at': '2026-02-19T10:00:00',
        'ended_at': None,
        'bundle_path': None,
    }))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('vibedom.cli.VMManager') as mock_vm_cls:
            mock_vm = MagicMock()
            mock_vm_cls.return_value = mock_vm
            with patch('vibedom.session.Session.create_bundle', return_value=None):
                result = runner.invoke(main, ['stop', 'myapp-happy-turing'])

    assert result.exit_code == 0


def test_rm_deletes_complete_session(tmp_path):
    """rm should delete a complete session directory."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-120000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(_make_complete_state(workspace))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        result = runner.invoke(main, ['rm', 'myapp-happy-turing', '--force'])

    assert result.exit_code == 0
    assert 'Deleted' in result.output
    assert not session_dir.exists()


def test_rm_no_session_found(tmp_path):
    """rm should error if session not found."""
    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        result = runner.invoke(main, ['rm', 'nonexistent-session', '--force'])

    assert result.exit_code == 1
    assert 'No session found' in result.output


def test_rm_refuses_running_session(tmp_path):
    """rm should refuse to delete a running session."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-120000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(_make_running_state(workspace))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('vibedom.session.Session.is_container_running', return_value=True):
            result = runner.invoke(main, ['rm', 'myapp-happy-turing', '--force'])

    assert result.exit_code == 1
    assert 'still running' in result.output


def test_rm_prompts_for_confirmation(tmp_path):
    """rm without --force should prompt before deleting."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-120000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(_make_complete_state(workspace))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        result = runner.invoke(main, ['rm', 'myapp-happy-turing'], input='n\n')

    assert result.exit_code == 0
    assert 'Aborted' in result.output
    assert session_dir.exists()
