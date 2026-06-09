import subprocess


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
