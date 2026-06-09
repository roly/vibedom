import subprocess
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from vibedom.cli import main
from helpers import _make_complete_state, _make_running_state


def test_review_command_success(tmp_path):
    """review command should add remote, fetch, show commits and diff."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-120000-000000'
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
                MagicMock(returncode=0, stdout='main\n'),  # git rev-parse --abbrev-ref HEAD
                MagicMock(returncode=1),  # git remote get-url (doesn't exist)
                MagicMock(returncode=0),  # git remote add
                MagicMock(returncode=0),  # git fetch
                MagicMock(returncode=0, stdout='abc123 commit message\n'),  # git log
                MagicMock(returncode=0, stdout='diff content\n'),  # git diff
            ]

            result = runner.invoke(main, ['review', 'myapp-happy-turing'])

            assert result.exit_code == 0
            assert 'myapp-happy-turing' in result.output

            calls = [' '.join(call[0][0]) for call in mock_run.call_args_list]
            assert any('remote add' in call for call in calls)
            assert any('fetch' in call for call in calls)
            assert any('log' in call for call in calls)
            assert any('diff' in call for call in calls)


def test_review_no_session_found(tmp_path):
    """review should error if no session found."""
    runner = CliRunner()

    with patch('vibedom.cli.Path.home') as mock_home:
        mock_home.return_value = tmp_path

        result = runner.invoke(main, ['review', 'nonexistent-session'])

        assert result.exit_code == 1
        assert 'No session found' in result.output


def test_review_fails_if_session_running(tmp_path):
    """review should error if container is still running."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-120000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'repo.bundle').write_text('fake bundle')
    (session_dir / 'state.json').write_text(_make_running_state(workspace))

    runner = CliRunner()

    with patch('vibedom.cli.Path.home') as mock_home:
        mock_home.return_value = tmp_path

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git rev-parse (is git repo)
                MagicMock(returncode=0, stdout='vibedom-myapp\n'),  # docker ps (running)
            ]

            result = runner.invoke(main, ['review', 'myapp-happy-turing'])

            assert result.exit_code == 1
            assert 'still running' in result.output


def test_review_fails_if_bundle_missing(tmp_path):
    """review should error if bundle file is missing."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-120000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(_make_complete_state(workspace))

    runner = CliRunner()

    with patch('vibedom.cli.Path.home') as mock_home:
        mock_home.return_value = tmp_path

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git rev-parse (is git repo)
            ]

            result = runner.invoke(main, ['review', 'myapp-happy-turing'])

            assert result.exit_code == 1
            assert 'Bundle not found' in result.output


def test_review_fails_if_not_git_repo(tmp_path):
    """review should error if workspace is not a git repository."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-120000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(_make_complete_state(workspace))

    runner = CliRunner()

    with patch('vibedom.cli.Path.home') as mock_home:
        mock_home.return_value = tmp_path

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(128, 'git rev-parse')

            result = runner.invoke(main, ['review', 'myapp-happy-turing'])

            assert result.exit_code == 1
            assert 'not a git repository' in result.output


def test_review_fails_on_git_remote_add_error(tmp_path):
    """review should error gracefully if git remote add fails."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-120000-000000'
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
                MagicMock(returncode=0, stdout='main\n'),  # git rev-parse --abbrev-ref HEAD
                MagicMock(returncode=1),  # git remote get-url (doesn't exist)
                subprocess.CalledProcessError(128, 'git remote add'),  # git remote add fails
            ]

            result = runner.invoke(main, ['review', 'myapp-happy-turing'])

            assert result.exit_code == 1
            assert 'Failed to add git remote' in result.output
