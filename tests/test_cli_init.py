import click
from unittest.mock import patch
from click.testing import CliRunner
from vibedom.cli import main
from helpers import _init_patches


def test_init_cloudflare_skipped_when_account_id_blank(tmp_path):
    """vibedom init saves no cloudflare config when account ID is left blank."""
    config_dir = tmp_path / '.vibedom'
    config_dir.mkdir(parents=True)
    (config_dir / 'trusted_domains.txt').write_text('api.anthropic.com\n')

    runner = CliRunner()
    with _init_patches(tmp_path):
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
    (config_dir / 'trusted_domains.txt').write_text('api.anthropic.com\n')

    runner = CliRunner()
    with _init_patches(tmp_path):
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
    (config_dir / 'trusted_domains.txt').write_text('api.anthropic.com\n')

    runner = CliRunner()
    with _init_patches(tmp_path):
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


def test_init_cloudflare_token_prompt_is_not_hidden(tmp_path):
    """vibedom init token prompt must not use hide_input=True."""
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
                        runner.invoke(main, ['init'], input='acc123\ngw1\nmytoken\nalice\n')

    assert captured_kwargs.get('hide_input', False) is False
