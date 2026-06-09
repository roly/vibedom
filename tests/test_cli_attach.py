import json
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from vibedom.cli import main


def _running_state(workspace, runtime='docker'):
    return json.dumps({
        'session_id': 'myapp-happy-turing',
        'workspace': str(workspace),
        'runtime': runtime,
        'container_name': 'vibedom-myapp',
        'status': 'running',
        'started_at': '2026-02-19T10:00:00',
        'ended_at': None,
        'bundle_path': None,
    })


def test_attach_execs_into_running_session(tmp_path):
    """attach should exec into the running session's container."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260219-100000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(_running_state(workspace))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(main, ['attach', 'myapp-happy-turing'])

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert 'exec' in cmd
    assert '-it' in cmd
    assert '/work/repo' in cmd
    assert 'vibedom-myapp' in cmd
    assert 'bash' in cmd


def test_attach_uses_container_cmd_for_apple(tmp_path):
    """attach should use 'container' command for apple runtime."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260219-100000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(_running_state(workspace, runtime='apple'))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            runner.invoke(main, ['attach', 'myapp-happy-turing'])

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == 'container'


def test_attach_rejects_non_running_session(tmp_path):
    """attach should reject sessions that are not running."""
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
        'status': 'complete',
        'started_at': '2026-02-19T10:00:00',
        'ended_at': '2026-02-19T11:00:00',
        'bundle_path': None,
    }))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        result = runner.invoke(main, ['attach', 'myapp-happy-turing'])

    assert result.exit_code != 0
    assert 'not running' in result.output
