import json
import signal
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from vibedom.cli import main
from helpers import _make_running_state


def test_proxy_restart_stops_and_restarts(tmp_path):
    """proxy-restart should SIGTERM existing proxy then start a new one on same port."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260221-100000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(
        _make_running_state(workspace, proxy_pid=99999, proxy_port=54321)
    )

    runner = CliRunner()
    mock_proxy = MagicMock()
    mock_proxy.pid = 88888
    mock_proxy.port = 54321

    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('os.kill') as mock_kill:
            with patch('vibedom.cli.ProxyManager', return_value=mock_proxy):
                result = runner.invoke(main, ['proxy-restart'])

    assert result.exit_code == 0, result.output
    mock_kill.assert_called_once_with(99999, signal.SIGTERM)
    mock_proxy.start.assert_called_once_with(port=54321)
    assert '88888' in result.output
    assert '54321' in result.output

    state = json.loads((session_dir / 'state.json').read_text())
    assert state['proxy_pid'] == 88888


def test_proxy_restart_when_proxy_already_dead(tmp_path):
    """proxy-restart should proceed cleanly if proxy process is already gone."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260221-100000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(
        _make_running_state(workspace, proxy_pid=99999, proxy_port=54321)
    )

    runner = CliRunner()
    mock_proxy = MagicMock()
    mock_proxy.pid = 88888
    mock_proxy.port = 54321

    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('os.kill', side_effect=ProcessLookupError):
            with patch('vibedom.cli.ProxyManager', return_value=mock_proxy):
                result = runner.invoke(main, ['proxy-restart'])

    assert result.exit_code == 0, result.output
    assert 'already stopped' in result.output
    mock_proxy.start.assert_called_once_with(port=54321)


def test_proxy_restart_fails_if_no_port_recorded(tmp_path):
    """proxy-restart should error if session has no proxy_port (old session)."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260221-100000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(
        _make_running_state(workspace, proxy_pid=None, proxy_port=None)
    )

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        result = runner.invoke(main, ['proxy-restart'])

    assert result.exit_code == 1
    assert 'No proxy port' in result.output
