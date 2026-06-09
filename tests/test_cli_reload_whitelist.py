import signal
from unittest.mock import patch
from click.testing import CliRunner
from vibedom.cli import main
from helpers import _make_running_state


def test_reload_whitelist_sends_sighup_to_all_running(tmp_path):
    """reload-whitelist should send SIGHUP to host proxy PID for all running sessions."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-120000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(_make_running_state(workspace, proxy_pid=99999))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('os.kill') as mock_kill:
            result = runner.invoke(main, ['reload-whitelist'])

            assert result.exit_code == 0
            mock_kill.assert_called_once_with(99999, signal.SIGHUP)


def test_reload_whitelist_no_running_sessions(tmp_path):
    """reload-whitelist should report nothing to do if no sessions are running."""
    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        result = runner.invoke(main, ['reload-whitelist'])

    assert result.exit_code == 0
    assert 'No running sessions' in result.output


def test_reload_whitelist_fails_gracefully(tmp_path):
    """reload-whitelist should exit 1 if process not found for any session."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-120000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(_make_running_state(workspace, proxy_pid=99999))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('os.kill', side_effect=ProcessLookupError):
            result = runner.invoke(main, ['reload-whitelist'])

            assert result.exit_code == 1
            assert 'not found' in result.output


def test_reload_whitelist_warns_if_no_proxy_pid(tmp_path):
    """reload-whitelist should warn when session has no proxy PID (older vibedom session)."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-120000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(_make_running_state(workspace, proxy_pid=None))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        result = runner.invoke(main, ['reload-whitelist'])

        assert result.exit_code == 1
        assert 'No proxy PID' in result.output


def test_reload_whitelist_sends_sighup_via_pid(tmp_path):
    """reload-whitelist should send SIGHUP to the host proxy PID from session state."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260220-120000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(
        _make_running_state(workspace, proxy_pid=99999, proxy_port=54321)
    )

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('os.kill') as mock_kill:
            result = runner.invoke(main, ['reload-whitelist'])

    assert result.exit_code == 0
    mock_kill.assert_called_once_with(99999, signal.SIGHUP)
