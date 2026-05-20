import json
import os
import signal
import subprocess
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from vibedom.cli import main

def test_cli_shows_help():
    """CLI should show help message when invoked with --help"""
    result = subprocess.run(
        ['vibedom', '--help'],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0
    assert 'vibedom' in result.stdout.lower()
    assert 'init' in result.stdout
    assert 'run' in result.stdout


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


def _make_complete_state(workspace, session_id='myapp-happy-turing', bundle_path=None):
    """Helper to create a complete (non-running) session state dict."""
    import json
    return json.dumps({
        'session_id': session_id,
        'workspace': str(workspace),
        'runtime': 'docker',
        'container_name': 'vibedom-myapp',
        'status': 'complete',
        'started_at': '2026-02-19T10:00:00',
        'ended_at': '2026-02-19T11:00:00',
        'bundle_path': bundle_path,
    })


def _make_running_state(workspace, session_id='myapp-happy-turing',
                        proxy_pid=99999, proxy_port=54321, runtime='docker'):
    """Helper to create a running session state dict."""
    return json.dumps({
        'session_id': session_id,
        'workspace': str(workspace),
        'runtime': runtime,
        'container_name': 'vibedom-myapp',
        'status': 'running',
        'started_at': '2026-02-19T10:00:00',
        'ended_at': None,
        'bundle_path': None,
        'proxy_port': proxy_port,
        'proxy_pid': proxy_pid,
    })


def test_review_command_success(tmp_path):
    """review command should add remote, fetch, show commits and diff."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    # Create fake session with state.json and bundle
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
            # Mock git commands; no container-check subprocess needed because
            # is_container_running() short-circuits on status='complete'
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

            # Verify git commands were called
            calls = [' '.join(call[0][0]) for call in mock_run.call_args_list]
            assert any('remote add' in call for call in calls)
            assert any('fetch' in call for call in calls)
            assert any('log' in call for call in calls)
            assert any('diff' in call for call in calls)


def test_review_no_session_found(tmp_path):
    """review should error if no session found."""
    # No session dirs created - registry will find nothing
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

    # Create session with 'running' status and a bundle
    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-120000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'repo.bundle').write_text('fake bundle')
    (session_dir / 'state.json').write_text(_make_running_state(workspace))

    runner = CliRunner()

    with patch('vibedom.cli.Path.home') as mock_home:
        mock_home.return_value = tmp_path

        with patch('subprocess.run') as mock_run:
            # git rev-parse check, then docker ps showing container running
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

    # Create session without bundle file
    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260218-120000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(_make_complete_state(workspace))
    # No bundle created

    runner = CliRunner()

    with patch('vibedom.cli.Path.home') as mock_home:
        mock_home.return_value = tmp_path

        with patch('subprocess.run') as mock_run:
            # Only git repo check needed; is_container_running() short-circuits on 'complete'
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
            # Mock git repo check to fail
            mock_run.side_effect = subprocess.CalledProcessError(128, 'git rev-parse')

            result = runner.invoke(main, ['review', 'myapp-happy-turing'])

            assert result.exit_code == 1
            assert 'not a git repository' in result.output


def test_review_fails_on_git_remote_add_error(tmp_path):
    """review should error gracefully if git remote add fails."""
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    # Create fake session
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
            # Mock git commands; status='complete' so no docker ps call
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git rev-parse --git-dir (is git repo)
                MagicMock(returncode=0, stdout='main\n'),  # git rev-parse --abbrev-ref HEAD
                MagicMock(returncode=1),  # git remote get-url (doesn't exist)
                subprocess.CalledProcessError(128, 'git remote add'),  # git remote add fails
            ]

            result = runner.invoke(main, ['review', 'myapp-happy-turing'])

            assert result.exit_code == 1
            assert 'Failed to add git remote' in result.output


def test_merge_command_squash(tmp_path):
    """merge command should squash by default."""
    from vibedom.cli import main

    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    # Create fake session with state.json
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
                MagicMock(returncode=0, stdout='main\n'),  # git rev-parse --abbrev-ref HEAD (branch)
                MagicMock(returncode=1),  # git remote get-url (doesn't exist)
                MagicMock(returncode=0),  # git remote add
                MagicMock(returncode=0),  # git fetch
                MagicMock(returncode=0),  # git merge --squash
                MagicMock(returncode=0),  # git commit
                MagicMock(returncode=0),  # git remote remove
            ]

            result = runner.invoke(main, ['merge', 'myapp-happy-turing'])

            assert result.exit_code == 0
            # Verify squash merge was called
            merge_calls = [call for call in mock_run.call_args_list
                          if 'merge' in ' '.join(call[0][0])]
            assert any('--squash' in ' '.join(call[0][0]) for call in merge_calls)


def test_merge_command_keep_history(tmp_path):
    """merge command with --merge flag should keep full history."""
    from vibedom.cli import main

    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    # Create fake session with state.json
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
                MagicMock(returncode=0, stdout='main\n'),  # git rev-parse --abbrev-ref HEAD (branch)
                MagicMock(returncode=1),  # git remote get-url (doesn't exist)
                MagicMock(returncode=0),  # git remote add
                MagicMock(returncode=0),  # git fetch
                MagicMock(returncode=0),  # git merge (no squash)
                MagicMock(returncode=0),  # git remote remove
            ]

            result = runner.invoke(main, ['merge', 'myapp-happy-turing', '--merge'])

            assert result.exit_code == 0
            # Verify regular merge (no --squash)
            merge_calls = [call for call in mock_run.call_args_list
                          if 'merge' in ' '.join(call[0][0])]
            assert not any('--squash' in ' '.join(call[0][0]) for call in merge_calls)


def test_merge_proceeds_with_uncommitted_changes(tmp_path):
    """merge should proceed even when workspace has uncommitted changes (git handles conflicts)."""
    from vibedom.cli import main

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
    import json
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
                MagicMock(returncode=0),        # git rev-parse --git-dir (is git repo)
                MagicMock(returncode=0, stdout=''),  # git status --porcelain (clean)
            ]
            with patch('vibedom.session.Session.is_container_running', return_value=True):
                result = runner.invoke(main, ['merge', 'myapp-happy-turing'])

    assert result.exit_code == 1
    assert 'running' in result.output.lower()


def test_attach_execs_into_running_session(tmp_path):
    """attach should exec into the running session's container."""
    import json
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
    import json
    workspace = tmp_path / 'myapp'
    workspace.mkdir()
    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260219-100000-000000'
    session_dir.mkdir(parents=True)
    (session_dir / 'state.json').write_text(json.dumps({
        'session_id': 'myapp-happy-turing',
        'workspace': str(workspace),
        'runtime': 'apple',
        'container_name': 'vibedom-myapp',
        'status': 'running',
        'started_at': '2026-02-19T10:00:00',
        'ended_at': None,
        'bundle_path': None,
    }))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            runner.invoke(main, ['attach', 'myapp-happy-turing'])

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == 'container'


def test_attach_rejects_non_running_session(tmp_path):
    """attach should reject sessions that are not running."""
    import json
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
        'status': 'complete',  # not running
        'started_at': '2026-02-19T10:00:00',
        'ended_at': '2026-02-19T11:00:00',
        'bundle_path': None,
    }))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        result = runner.invoke(main, ['attach', 'myapp-happy-turing'])

    assert result.exit_code != 0
    assert 'not running' in result.output


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

    # Find the session directory
    session_dirs = list((tmp_path / '.vibedom' / 'logs').glob('session-*'))
    assert len(session_dirs) == 1, f"Expected 1 session dir, got: {session_dirs}"
    state_file = session_dirs[0] / 'state.json'
    assert state_file.exists(), "state.json not written"
    import json
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


def test_stop_uses_session_registry(tmp_path):
    """stop should find session via SessionRegistry, not log parsing."""
    import json
    workspace = tmp_path / 'myapp'
    workspace.mkdir()

    logs_dir = tmp_path / '.vibedom' / 'logs'
    session_dir = logs_dir / 'session-20260219-100000-000000'
    session_dir.mkdir(parents=True)
    state = {
        'session_id': 'myapp-happy-turing',
        'workspace': str(workspace),
        'runtime': 'docker',
        'container_name': 'vibedom-myapp',
        'status': 'running',
        'started_at': '2026-02-19T10:00:00',
        'ended_at': None,
        'bundle_path': None,
    }
    (session_dir / 'state.json').write_text(json.dumps(state))

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
        # Answer 'n' to the confirmation prompt
        result = runner.invoke(main, ['rm', 'myapp-happy-turing'], input='n\n')

    assert result.exit_code == 0
    assert 'Aborted' in result.output
    assert session_dir.exists()  # Not deleted


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

                    result = runner.invoke(main, ['run', str(workspace)])

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

                    result = runner.invoke(main, ['run', str(workspace)])

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

    # PID should be updated in state.json
    import json as json_mod
    state = json_mod.loads((session_dir / 'state.json').read_text())
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


# ------------------------------------------------------------------ #
# config cloudflare — saves correct config
# ------------------------------------------------------------------ #

def test_config_cloudflare_saves_all_fields(tmp_path):
    """config cloudflare saves account_id, gateway_id, token, and username."""
    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        result = runner.invoke(
            main,
            ['config', 'cloudflare', '--account-id', 'acc123',
             '--gateway-id', 'my-gw', '--auth-token', 'tok',
             '--username', 'alice'],
        )

    assert result.exit_code == 0, result.output
    from vibedom.global_config import GlobalConfig
    saved = GlobalConfig.load(tmp_path / '.vibedom')
    assert saved.cloudflare_account_id == 'acc123'
    assert saved.cloudflare_gateway_id == 'my-gw'
    assert saved.cloudflare_gateway_token == 'tok'
    assert saved.vibedom_username == 'alice'


def test_config_cloudflare_output_includes_anthropic_url(tmp_path):
    """config cloudflare prints the resolved Anthropic URL."""
    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        result = runner.invoke(
            main,
            ['config', 'cloudflare', '--account-id', 'acc',
             '--gateway-id', 'gw', '--auth-token', 'tok'],
        )

    assert 'gateway.ai.cloudflare.com/v1/acc/gw/anthropic' in result.output


def test_config_cloudflare_public_gateway_no_token(tmp_path):
    """config cloudflare with blank token leaves cloudflare_gateway_token as None."""
    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        # Enter blank for token prompt
        result = runner.invoke(
            main,
            ['config', 'cloudflare', '--account-id', 'acc', '--gateway-id', 'gw'],
            input='\n\n',
        )

    assert result.exit_code == 0, result.output
    from vibedom.global_config import GlobalConfig
    saved = GlobalConfig.load(tmp_path / '.vibedom')
    assert saved.cloudflare_gateway_token is None


def test_config_cloudflare_clear_removes_config(tmp_path):
    """config cloudflare --clear wipes all cloudflare fields from config."""
    import json
    config_dir = tmp_path / '.vibedom'
    config_dir.mkdir(parents=True)
    (config_dir / 'config.json').write_text(json.dumps({
        'cloudflare_account_id': 'acc',
        'cloudflare_gateway_id': 'gw',
        'cloudflare_gateway_token': 'tok',
        'vibedom_username': 'alice',
    }))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        result = runner.invoke(main, ['config', 'cloudflare', '--clear'])

    assert result.exit_code == 0, result.output
    assert 'removed' in result.output.lower()
    from vibedom.global_config import GlobalConfig
    saved = GlobalConfig.load(config_dir)
    assert saved.cloudflare_account_id is None
    assert saved.cloudflare_gateway_id is None
    assert saved.cloudflare_gateway_token is None


def test_config_cloudflare_adds_domain_to_whitelist(tmp_path):
    """config cloudflare appends gateway.ai.cloudflare.com to an existing whitelist."""
    config_dir = tmp_path / '.vibedom'
    config_dir.mkdir(parents=True)
    whitelist = config_dir / 'trusted_domains.txt'
    whitelist.write_text('api.anthropic.com\n')

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        result = runner.invoke(
            main,
            ['config', 'cloudflare', '--account-id', 'acc',
             '--gateway-id', 'gw', '--auth-token', 'tok'],
        )

    assert result.exit_code == 0, result.output
    assert 'gateway.ai.cloudflare.com' in whitelist.read_text()


def test_config_cloudflare_idempotent_whitelist(tmp_path):
    """config cloudflare does not add duplicate entry if domain already in whitelist."""
    config_dir = tmp_path / '.vibedom'
    config_dir.mkdir(parents=True)
    whitelist = config_dir / 'trusted_domains.txt'
    whitelist.write_text('api.anthropic.com\ngateway.ai.cloudflare.com\n')

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        runner.invoke(
            main,
            ['config', 'cloudflare', '--account-id', 'acc',
             '--gateway-id', 'gw', '--auth-token', 'tok'],
        )

    content = whitelist.read_text()
    assert content.count('gateway.ai.cloudflare.com') == 1


def test_config_cloudflare_no_whitelist_shows_note(tmp_path):
    """config cloudflare prints a note instead of crashing when whitelist is absent."""
    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        result = runner.invoke(
            main,
            ['config', 'cloudflare', '--account-id', 'acc',
             '--gateway-id', 'gw', '--auth-token', 'tok'],
        )

    assert result.exit_code == 0, result.output
    assert 'vibedom init' in result.output


def test_config_cloudflare_preserves_username_from_existing_config(tmp_path):
    """config cloudflare keeps existing username when --username flag not passed."""
    import json
    config_dir = tmp_path / '.vibedom'
    config_dir.mkdir(parents=True)
    (config_dir / 'config.json').write_text(json.dumps({
        'cloudflare_account_id': 'old-acc',
        'cloudflare_gateway_id': 'old-gw',
        'cloudflare_gateway_token': None,
        'vibedom_username': 'bob',
    }))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        runner.invoke(
            main,
            ['config', 'cloudflare', '--account-id', 'acc', '--gateway-id', 'gw',
             '--auth-token', 'tok'],
        )

    from vibedom.global_config import GlobalConfig
    saved = GlobalConfig.load(config_dir)
    assert saved.vibedom_username == 'bob'


# ------------------------------------------------------------------ #
# vibedom init — Cloudflare section skip / configure paths
# ------------------------------------------------------------------ #

def _init_patches(tmp_path):
    """Context manager stack that stubs out the heavy init side-effects."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch('vibedom.cli.Path.home', return_value=tmp_path))
    stack.enter_context(patch('vibedom.cli.generate_deploy_key'))
    stack.enter_context(patch('vibedom.cli.get_public_key', return_value='ssh-ed25519 AAAA'))
    stack.enter_context(patch('vibedom.cli.VMManager._detect_runtime',
                               return_value=('docker', 'docker')))
    stack.enter_context(patch('vibedom.cli.VMManager.image_exists', return_value=True))
    return stack


def test_init_cloudflare_skipped_when_account_id_blank(tmp_path):
    """vibedom init saves no cloudflare config when account ID is left blank."""
    config_dir = tmp_path / '.vibedom'
    config_dir.mkdir(parents=True)
    whitelist = config_dir / 'trusted_domains.txt'
    whitelist.write_text('api.anthropic.com\n')

    runner = CliRunner()
    with _init_patches(tmp_path):
        # Blank account_id → skip
        result = runner.invoke(main, ['init'], input='\n')

    assert result.exit_code == 0, result.output
    assert 'Skipped' in result.output
    from vibedom.global_config import GlobalConfig
    saved = GlobalConfig.load(config_dir)
    assert not saved.is_cloudflare_configured()


def test_init_cloudflare_skipped_when_gateway_id_blank(tmp_path):
    """vibedom init saves no cloudflare config when gateway ID is left blank."""
    config_dir = tmp_path / '.vibedom'
    config_dir.mkdir(parents=True)
    whitelist = config_dir / 'trusted_domains.txt'
    whitelist.write_text('api.anthropic.com\n')

    runner = CliRunner()
    with _init_patches(tmp_path):
        # account_id provided, gateway_id blank → skip
        result = runner.invoke(main, ['init'], input='acc123\n\n')

    assert result.exit_code == 0, result.output
    assert 'Skipped' in result.output
    from vibedom.global_config import GlobalConfig
    saved = GlobalConfig.load(config_dir)
    assert not saved.is_cloudflare_configured()


def test_init_cloudflare_saves_config_and_updates_whitelist(tmp_path):
    """vibedom init saves full cloudflare config and adds domain to whitelist."""
    config_dir = tmp_path / '.vibedom'
    config_dir.mkdir(parents=True)
    whitelist = config_dir / 'trusted_domains.txt'
    whitelist.write_text('api.anthropic.com\n')

    runner = CliRunner()
    with _init_patches(tmp_path):
        # account_id, gateway_id, token, username
        result = runner.invoke(
            main, ['init'],
            input='acc123\nmy-gw\nsecret-tok\nalice\n',
        )

    assert result.exit_code == 0, result.output
    from vibedom.global_config import GlobalConfig
    saved = GlobalConfig.load(config_dir)
    assert saved.cloudflare_account_id == 'acc123'
    assert saved.cloudflare_gateway_id == 'my-gw'
    assert saved.cloudflare_gateway_token == 'secret-tok'
    assert saved.vibedom_username == 'alice'
    assert 'gateway.ai.cloudflare.com' in whitelist.read_text()


def test_init_cloudflare_configured_without_token(tmp_path):
    """vibedom init saves config with no token when token left blank (public gateway)."""
    config_dir = tmp_path / '.vibedom'
    config_dir.mkdir(parents=True)
    whitelist = config_dir / 'trusted_domains.txt'
    whitelist.write_text('api.anthropic.com\n')

    runner = CliRunner()
    with _init_patches(tmp_path):
        # account_id, gateway_id, blank token, username
        result = runner.invoke(
            main, ['init'],
            input='acc123\nmy-gw\n\nalice\n',
        )

    assert result.exit_code == 0, result.output
    from vibedom.global_config import GlobalConfig
    saved = GlobalConfig.load(config_dir)
    assert saved.cloudflare_account_id == 'acc123'
    assert saved.cloudflare_gateway_id == 'my-gw'
    assert saved.cloudflare_gateway_token is None


# ------------------------------------------------------------------ #
# config cloudflare — interactive token prompt
# ------------------------------------------------------------------ #

def test_config_cloudflare_prompts_for_token_visibly(tmp_path):
    """config cloudflare should prompt for auth token with hide_input=False."""
    import click
    from vibedom.cli import config_cloudflare

    captured_kwargs = {}
    original_prompt = click.prompt

    def capturing_prompt(text, **kwargs):
        if 'auth token' in text.lower():
            captured_kwargs.update(kwargs)
        return original_prompt(text, **kwargs)

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('vibedom.cli.click.prompt', side_effect=capturing_prompt):
            runner.invoke(
                main,
                ['config', 'cloudflare', '--account-id', 'acc', '--gateway-id', 'gw'],
                input='mytoken\n',
            )

    assert captured_kwargs.get('hide_input', False) is False


def test_config_cloudflare_token_prompt_shows_existing_value(tmp_path):
    """config cloudflare token prompt uses existing token as default when re-running."""
    import json
    config_dir = tmp_path / '.vibedom'
    config_dir.mkdir(parents=True)
    (config_dir / 'config.json').write_text(json.dumps({
        'cloudflare_account_id': 'acc',
        'cloudflare_gateway_id': 'gw',
        'cloudflare_gateway_token': 'existing-tok',
        'vibedom_username': 'alice',
    }))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        # Press Enter to accept all defaults
        result = runner.invoke(
            main,
            ['config', 'cloudflare', '--account-id', 'acc', '--gateway-id', 'gw'],
            input='\n\n',
        )

    assert result.exit_code == 0
    from vibedom.global_config import GlobalConfig
    saved = GlobalConfig.load(config_dir)
    assert saved.cloudflare_gateway_token == 'existing-tok'


def test_config_cloudflare_token_prompt_can_be_cleared(tmp_path):
    """Entering blank at the token prompt clears the stored token."""
    import json
    config_dir = tmp_path / '.vibedom'
    config_dir.mkdir(parents=True)
    (config_dir / 'config.json').write_text(json.dumps({
        'cloudflare_account_id': 'acc',
        'cloudflare_gateway_id': 'gw',
        'cloudflare_gateway_token': 'old-tok',
        'vibedom_username': 'alice',
    }))

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        # Supply a space then newline to clear — but click strips, so empty string clears
        result = runner.invoke(
            main,
            ['config', 'cloudflare', '--account-id', 'acc', '--gateway-id', 'gw'],
            # First \n = accept empty string for token (overrides default), second = username
            input=' \n\n',
        )

    assert result.exit_code == 0
    from vibedom.global_config import GlobalConfig
    saved = GlobalConfig.load(config_dir)
    assert saved.cloudflare_gateway_token is None


def test_config_cloudflare_flag_skips_token_prompt(tmp_path):
    """Passing --auth-token flag skips the interactive token prompt."""
    import click

    prompted_for_token = []
    original_prompt = click.prompt

    def detecting_prompt(text, **kwargs):
        if 'auth token' in text.lower():
            prompted_for_token.append(text)
        return original_prompt(text, **kwargs)

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('vibedom.cli.click.prompt', side_effect=detecting_prompt):
            result = runner.invoke(
                main,
                ['config', 'cloudflare', '--account-id', 'acc',
                 '--gateway-id', 'gw', '--auth-token', 'flagtoken'],
            )

    assert result.exit_code == 0
    assert prompted_for_token == [], "Should not prompt for token when --auth-token flag is given"
    from vibedom.global_config import GlobalConfig
    saved = GlobalConfig.load(tmp_path / '.vibedom')
    assert saved.cloudflare_gateway_token == 'flagtoken'


# ------------------------------------------------------------------ #
# _prompt_prefilled — unit tests
# ------------------------------------------------------------------ #

def test_prompt_prefilled_non_tty_enter_keeps_existing():
    """Non-TTY stdin: pressing Enter (empty input) keeps the existing prefill value."""
    from vibedom.cli import _prompt_prefilled
    import io, sys
    fake_stdin = io.StringIO('\n')
    with patch.object(sys, 'stdin', fake_stdin):
        # isatty() on StringIO returns False → falls back to click.prompt
        result = _prompt_prefilled('Token', prefill='existing-tok')
    assert result == 'existing-tok'


def test_prompt_prefilled_non_tty_clears_value():
    """Non-TTY stdin: entering a space clears the value to empty string."""
    from vibedom.cli import _prompt_prefilled
    import io, sys
    fake_stdin = io.StringIO(' \n')
    with patch.object(sys, 'stdin', fake_stdin):
        result = _prompt_prefilled('Token', prefill='existing-tok')
    assert result == ''


def test_prompt_prefilled_non_tty_new_value():
    """Non-TTY stdin: entering a new value replaces the prefill."""
    from vibedom.cli import _prompt_prefilled
    import io, sys
    fake_stdin = io.StringIO('new-tok\n')
    with patch.object(sys, 'stdin', fake_stdin):
        result = _prompt_prefilled('Token', prefill='old-tok')
    assert result == 'new-tok'


def test_prompt_prefilled_tty_uses_readline_prefill():
    """TTY stdin: readline pre_input_hook is registered with the prefill text."""
    from vibedom.cli import _prompt_prefilled
    import sys, types

    hooks_registered = []

    fake_readline = types.SimpleNamespace(
        insert_text=lambda t: None,
        redisplay=lambda: None,
        set_pre_input_hook=lambda fn: hooks_registered.append(fn),
    )

    fake_tty = type('FakeTTY', (), {'isatty': lambda self: True, 'read': lambda self, n: ''})()

    with patch.object(sys, 'stdin', fake_tty):
        with patch.dict('sys.modules', {'readline': fake_readline}):
            with patch('builtins.input', return_value='kept'):
                result = _prompt_prefilled('Token', prefill='my-tok')

    assert result == 'kept'
    # hook was registered (and cleared) — two calls: set + clear
    assert len(hooks_registered) == 2
    assert hooks_registered[1] is None


# ------------------------------------------------------------------ #
# vibedom init — Cloudflare token prompt is not hidden
# ------------------------------------------------------------------ #

def test_init_cloudflare_token_prompt_is_not_hidden(tmp_path):
    """vibedom init token prompt must not use hide_input=True."""
    import click
    from vibedom.cli import main as cli_main

    captured_kwargs = {}
    original_prompt = click.prompt

    def capturing_prompt(text, **kwargs):
        if 'auth token' in text.lower():
            captured_kwargs.update(kwargs)
        return original_prompt(text, **kwargs)

    runner = CliRunner()
    with patch('vibedom.cli.Path.home', return_value=tmp_path):
        with patch('vibedom.cli.generate_deploy_key'):
            with patch('vibedom.cli.get_public_key', return_value='ssh-ed25519 AAAA'):
                with patch('vibedom.cli.create_default_whitelist', return_value=tmp_path / 'w.txt'):
                    with patch('vibedom.cli.click.prompt', side_effect=capturing_prompt):
                        # account_id, gateway_id, token, username
                        result = runner.invoke(main, ['init'], input='acc123\ngw1\nmytoken\nalice\n')

    assert captured_kwargs.get('hide_input', False) is False
