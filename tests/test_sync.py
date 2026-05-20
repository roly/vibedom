"""Tests for vibedom pull/push sync commands."""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from click.testing import CliRunner
from vibedom.cli import main, _make_workspace_relative, _find_deletions
from vibedom.container_state import ContainerState


@pytest.fixture
def sync_env(tmp_path):
    """Set up a minimal workspace and container environment for sync tests."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    (workspace / 'src').mkdir()
    (workspace / 'src' / 'app.php').write_text('<?php echo "hello";')
    (workspace / '.gitignore').write_text("vendor/\n.env\n")

    containers_dir = tmp_path / '.vibedom' / 'containers'
    container_dir = containers_dir / 'myapp'
    repo_dir = container_dir / 'repo'
    repo_dir.mkdir(parents=True)
    (repo_dir / 'src').mkdir()
    (repo_dir / 'src' / 'app.php').write_text('<?php echo "modified";')

    state = ContainerState.create(workspace, 'docker')
    state.status = 'running'
    state.proxy_pid = 99999
    state.proxy_port = 54321
    state.save(container_dir)

    return {
        'workspace': workspace,
        'containers_dir': containers_dir,
        'container_dir': container_dir,
        'repo_dir': repo_dir,
        'state': state,
    }


def test_pull_copies_files_from_container_to_host(sync_env, tmp_path):
    """pull should rsync from container repo to workspace."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            # --yes skips the full-tree confirmation prompt
            result = runner.invoke(main, ['pull', 'myapp', '--yes'], catch_exceptions=False)

    assert result.exit_code == 0
    # rsync should have been called
    assert mock_run.called
    rsync_calls = [c for c in mock_run.call_args_list if 'rsync' in str(c)]
    assert rsync_calls, "rsync should have been called for pull"


def test_push_copies_files_from_host_to_container(sync_env, tmp_path):
    """push should rsync from workspace to container repo."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            # --yes skips the full-tree confirmation prompt
            result = runner.invoke(main, ['push', 'myapp', '--yes'], catch_exceptions=False)

    assert result.exit_code == 0
    rsync_calls = [c for c in mock_run.call_args_list if 'rsync' in str(c)]
    assert rsync_calls, "rsync should have been called for push"


def test_pull_with_path_syncs_specific_directory(sync_env):
    """pull with a path argument should sync only that path."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(main, ['pull', 'myapp', 'src/'], catch_exceptions=False)

    assert result.exit_code == 0
    rsync_calls = [c for c in mock_run.call_args_list if 'rsync' in str(c)]
    assert rsync_calls
    cmd_str = str(rsync_calls[0])
    # Path 'src/' should appear as a sub-path in the rsync arguments
    assert '/src' in cmd_str


def test_pull_uses_gitignore_excludes(sync_env):
    """pull should pass --filter=':- .gitignore' to rsync."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(main, ['pull', 'myapp', '--yes'], catch_exceptions=False)

    rsync_calls = [c for c in mock_run.call_args_list if 'rsync' in str(c)]
    cmd = rsync_calls[0][0][0]
    cmd_str = ' '.join(cmd)
    assert '.gitignore' in cmd_str


def test_pull_no_delete_by_default(sync_env):
    """pull should NOT pass --delete by default (additive only)."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(main, ['pull', 'myapp', '--yes'], catch_exceptions=False)

    rsync_calls = [c for c in mock_run.call_args_list if 'rsync' in str(c)]
    cmd = rsync_calls[0][0][0]
    assert '--delete' not in cmd


def test_pull_with_delete_flag(sync_env):
    """pull --delete should pass --delete to rsync."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(main, ['pull', 'myapp', '--delete', '--yes'], catch_exceptions=False)

    rsync_calls = [c for c in mock_run.call_args_list if 'rsync' in str(c)]
    cmd = rsync_calls[0][0][0]
    assert '--delete' in cmd


def test_pull_fails_gracefully_when_container_not_found():
    """pull should exit with error when container not found."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = None
        mock_registry_cls.return_value = mock_registry

        result = runner.invoke(main, ['pull', 'nonexistent'])

    assert result.exit_code != 0


def test_pull_dry_run_shows_output_without_syncing(sync_env):
    """pull --dry-run should pass --dry-run to rsync."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout='', stderr='')
            result = runner.invoke(main, ['pull', 'myapp', '--dry-run'], catch_exceptions=False)

    rsync_calls = [c for c in mock_run.call_args_list if 'rsync' in str(c)]
    cmd = rsync_calls[0][0][0]
    assert '--dry-run' in cmd or '-n' in cmd


def test_pull_full_tree_prompts_for_confirmation(sync_env):
    """pull without paths should ask for confirmation before syncing."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            # Decline the prompt — rsync must NOT be called
            result = runner.invoke(main, ['pull', 'myapp'], input='n\n', catch_exceptions=False)

    assert result.exit_code == 0
    rsync_calls = [c for c in mock_run.call_args_list if 'rsync' in str(c)]
    assert not rsync_calls, "rsync should NOT run when user declines confirmation"


def test_push_full_tree_prompts_for_confirmation(sync_env):
    """push without paths should ask for confirmation before syncing."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(main, ['push', 'myapp'], input='n\n', catch_exceptions=False)

    assert result.exit_code == 0
    rsync_calls = [c for c in mock_run.call_args_list if 'rsync' in str(c)]
    assert not rsync_calls, "rsync should NOT run when user declines confirmation"


def test_pull_rejects_absolute_path_argument(sync_env):
    """pull should reject absolute path arguments."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        result = runner.invoke(main, ['pull', 'myapp', '/etc/passwd'])

    assert result.exit_code != 0
    assert 'absolute' in result.output.lower() or 'error' in result.output.lower()


def test_push_rejects_absolute_path_argument(sync_env):
    """push should reject absolute path arguments."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        result = runner.invoke(main, ['push', 'myapp', '/etc/passwd'])

    assert result.exit_code != 0
    assert 'absolute' in result.output.lower() or 'error' in result.output.lower()


def test_pull_multi_path_rsync_has_correct_argument_order(sync_env):
    """pull with multiple paths should produce rsync src1 src2 dst (not interleaved pairs)."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(
                main, ['pull', 'myapp', 'src/'], catch_exceptions=False
            )

    assert result.exit_code == 0
    rsync_calls = [c for c in mock_run.call_args_list if 'rsync' in str(c)]
    assert rsync_calls
    cmd = rsync_calls[0][0][0]
    # Last argument must be the destination (workspace path)
    workspace_path = Path(sync_env['state'].workspace)
    assert cmd[-1] == str(workspace_path.resolve()), (
        f"Last rsync argument should be destination '{workspace_path}', got '{cmd[-1]}'"
    )


def test_make_workspace_relative_from_cwd_inside_workspace(tmp_path):
    workspace = tmp_path / 'myapp'
    (workspace / 'src' / 'app').mkdir(parents=True)
    cwd = workspace / 'src'
    result = _make_workspace_relative('app', workspace, cwd=cwd)
    assert result == 'src/app'


def test_make_workspace_relative_from_cwd_at_workspace_root(tmp_path):
    workspace = tmp_path / 'myapp'
    (workspace / 'src').mkdir(parents=True)
    result = _make_workspace_relative('src', workspace, cwd=workspace)
    assert result == 'src'


def test_make_workspace_relative_from_cwd_outside_workspace(tmp_path):
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    cwd = tmp_path  # outside workspace
    result = _make_workspace_relative('src/app', workspace, cwd=cwd)
    assert result == 'src/app'  # unchanged


def test_make_workspace_relative_from_cwd_deeply_nested(tmp_path):
    workspace = tmp_path / 'myapp'
    (workspace / 'src' / 'app' / 'Controllers').mkdir(parents=True)
    cwd = workspace / 'src' / 'app'
    result = _make_workspace_relative('Controllers', workspace, cwd=cwd)
    assert result == 'src/app/Controllers'


def test_make_workspace_relative_escape_protection(tmp_path):
    """Test that escape attempts with ../ are returned unchanged."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    cwd = workspace
    result = _make_workspace_relative('../../etc/passwd', workspace, cwd=cwd)
    assert result == '../../etc/passwd'


def test_pull_resolves_paths_via_make_workspace_relative(sync_env):
    """pull should call _make_workspace_relative for each path argument."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch('vibedom.cli._make_workspace_relative', return_value='src/app.php') as mock_resolve:
                result = runner.invoke(
                    main, ['pull', 'myapp', 'app.php'], catch_exceptions=False
                )

    assert result.exit_code == 0
    mock_resolve.assert_called_once()
    assert mock_resolve.call_args[0][0] == 'app.php'


def test_push_resolves_paths_via_make_workspace_relative(sync_env):
    """push should call _make_workspace_relative for each path argument."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch('vibedom.cli._make_workspace_relative', return_value='src/app.php') as mock_resolve:
                result = runner.invoke(
                    main, ['push', 'myapp', 'app.php'], catch_exceptions=False
                )

    assert result.exit_code == 0
    mock_resolve.assert_called_once()
    assert mock_resolve.call_args[0][0] == 'app.php'


def test_pull_prints_resolved_paths(sync_env):
    """pull should print the resolved workspace-relative path before syncing."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch('vibedom.cli._make_workspace_relative', return_value='src/app/Controllers'):
                result = runner.invoke(
                    main, ['pull', 'myapp', 'app/Controllers'], catch_exceptions=False
                )

    assert 'src/app/Controllers' in result.output


def test_push_prints_resolved_paths(sync_env):
    """push should print the resolved workspace-relative path before syncing."""
    runner = CliRunner()

    with patch('vibedom.cli.ContainerRegistry') as mock_registry_cls:
        mock_registry = MagicMock()
        mock_registry.find.return_value = sync_env['state']
        mock_registry_cls.return_value = mock_registry

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch('vibedom.cli._make_workspace_relative', return_value='src/app/Controllers'):
                result = runner.invoke(
                    main, ['push', 'myapp', 'app/Controllers'], catch_exceptions=False
                )

    assert 'src/app/Controllers' in result.output


def test_find_deletions_returns_deleted_paths(tmp_path):
    cmd = ['rsync', '-av', '--delete', f'{tmp_path}/', str(tmp_path / 'dst')]
    mock_stdout = 'sending incremental file list\ndeleting .env.secrets\ndeleting tmp/scratch.txt\nsrc/app.php\n'
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_stdout)
        result = _find_deletions(cmd)
    assert result == ['.env.secrets', 'tmp/scratch.txt']


def test_find_deletions_adds_dry_run_flag(tmp_path):
    cmd = ['rsync', '-av', '--delete', f'{tmp_path}/', str(tmp_path / 'dst')]
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout='')
        _find_deletions(cmd)
    called_cmd = mock_run.call_args[0][0]
    assert '--dry-run' in called_cmd


def test_find_deletions_does_not_duplicate_dry_run(tmp_path):
    cmd = ['rsync', '-av', '--delete', '--dry-run', f'{tmp_path}/', str(tmp_path / 'dst')]
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout='')
        _find_deletions(cmd)
    called_cmd = mock_run.call_args[0][0]
    assert called_cmd.count('--dry-run') == 1


def test_find_deletions_returns_empty_when_nothing_deleted(tmp_path):
    cmd = ['rsync', '-av', '--delete', f'{tmp_path}/', str(tmp_path / 'dst')]
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout='src/app.php\nsrc/other.py\n')
        result = _find_deletions(cmd)
    assert result == []
