import json
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from vibedom.cli import main
from helpers import _make_complete_state, _make_running_state


def test_merge_command_squash(tmp_path):
    """merge command should squash by default."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-130000-000000'
    session_dir.mkdir(parents=True)
    bundle_path = session_dir / 'repo.bundle'
    bundle_path.write_text('fake bundle')
    (session_dir / 'state.json').write_text(
        _make_complete_state(workspace, bundle_path=str(bundle_path))
    )

    runner = CliRunner()

    with patch('vibedom.cli.Path.home') as mock_home:
        mock_home.return_value = tmp_path

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git rev-parse --git-dir (is git repo)
                MagicMock(returncode=0, stdout=''),  # git status --porcelain (clean)
                MagicMock(returncode=0, stdout='main\n'),  # git rev-parse --abbrev-ref HEAD
                MagicMock(returncode=1),  # git remote get-url (doesn't exist)
                MagicMock(returncode=0),  # git remote add
                MagicMock(returncode=0),  # git fetch
                MagicMock(returncode=0),  # git merge --squash
                MagicMock(returncode=0),  # git commit
                MagicMock(returncode=0),  # git remote remove
            ]

            result = runner.invoke(main, ['merge', 'myapp-happy-turing'])

            assert result.exit_code == 0
            merge_calls = [call for call in mock_run.call_args_list
                          if 'merge' in ' '.join(call[0][0])]
            assert any('--squash' in ' '.join(call[0][0]) for call in merge_calls)


def test_merge_command_keep_history(tmp_path):
    """merge command with --merge flag should keep full history."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-130000-000000'
    session_dir.mkdir(parents=True)
    bundle_path = session_dir / 'repo.bundle'
    bundle_path.write_text('fake bundle')
    (session_dir / 'state.json').write_text(
        _make_complete_state(workspace, bundle_path=str(bundle_path))
    )

    runner = CliRunner()

    with patch('vibedom.cli.Path.home') as mock_home:
        mock_home.return_value = tmp_path

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git rev-parse --git-dir (is git repo)
                MagicMock(returncode=0, stdout=''),  # git status --porcelain (clean)
                MagicMock(returncode=0, stdout='main\n'),  # git rev-parse --abbrev-ref HEAD
                MagicMock(returncode=1),  # git remote get-url (doesn't exist)
                MagicMock(returncode=0),  # git remote add
                MagicMock(returncode=0),  # git fetch
                MagicMock(returncode=0),  # git merge (no squash)
                MagicMock(returncode=0),  # git remote remove
            ]

            result = runner.invoke(main, ['merge', 'myapp-happy-turing', '--merge'])

            assert result.exit_code == 0
            merge_calls = [call for call in mock_run.call_args_list
                          if 'merge' in ' '.join(call[0][0])]
            assert not any('--squash' in ' '.join(call[0][0]) for call in merge_calls)


def test_merge_proceeds_with_uncommitted_changes(tmp_path):
    """merge should proceed even when workspace has uncommitted changes."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-130000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(_make_complete_state(workspace))
    (session_dir / 'repo.bundle').write_bytes(b'bundle')

    runner = CliRunner()

    with patch('vibedom.cli.Path.home') as mock_home:
        mock_home.return_value = tmp_path

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git rev-parse --git-dir (is git repo)
                MagicMock(returncode=0, stdout='main\n'),  # git rev-parse --abbrev-ref HEAD
                MagicMock(returncode=1),  # git remote get-url (not found, will add)
                MagicMock(returncode=0),  # git remote add
                MagicMock(returncode=0),  # git fetch
                MagicMock(returncode=0),  # git merge --squash
                MagicMock(returncode=0),  # git commit
                MagicMock(returncode=0),  # git remote remove (cleanup)
            ]

            result = runner.invoke(main, ['merge', 'myapp-happy-turing'])

            assert result.exit_code == 0


def test_merge_fails_if_session_running(tmp_path):
    """merge should fail if the session container is still running."""
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
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),        # git rev-parse --git-dir
                MagicMock(returncode=0, stdout=''),  # git status --porcelain
            ]
            with patch('vibedom.session.Session.is_container_running', return_value=True):
                result = runner.invoke(main, ['merge', 'myapp-happy-turing'])

    assert result.exit_code == 1
    assert 'running' in result.output.lower()
