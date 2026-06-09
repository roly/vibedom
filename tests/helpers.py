import json
from contextlib import ExitStack
from unittest.mock import patch


def _make_complete_state(workspace, session_id='myapp-happy-turing', bundle_path=None):
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


def _init_patches(tmp_path):
    """Context manager stack that stubs out the heavy init side-effects."""
    stack = ExitStack()
    stack.enter_context(patch('vibedom.cli.Path.home', return_value=tmp_path))
    stack.enter_context(patch('vibedom.cli.generate_deploy_key'))
    stack.enter_context(patch('vibedom.cli.get_public_key', return_value='ssh-ed25519 AAAA'))
    stack.enter_context(patch('vibedom.cli.VMManager._detect_runtime',
                               return_value=('docker', 'docker')))
    stack.enter_context(patch('vibedom.cli.VMManager.image_exists', return_value=True))
    return stack
