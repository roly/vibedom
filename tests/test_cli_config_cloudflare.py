import io
import json
import sys
import types
from unittest.mock import patch
from click.testing import CliRunner
from vibedom.cli import main


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

    assert whitelist.read_text().count('gateway.ai.cloudflare.com') == 1


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
# config cloudflare — interactive token prompt behaviour
# ------------------------------------------------------------------ #

def test_config_cloudflare_prompts_for_token_visibly(tmp_path):
    """config cloudflare should prompt for auth token with hide_input=False."""
    import click

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
        result = runner.invoke(
            main,
            ['config', 'cloudflare', '--account-id', 'acc', '--gateway-id', 'gw'],
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
    fake_stdin = io.StringIO('\n')
    with patch.object(sys, 'stdin', fake_stdin):
        result = _prompt_prefilled('Token', prefill='existing-tok')
    assert result == 'existing-tok'


def test_prompt_prefilled_non_tty_clears_value():
    """Non-TTY stdin: entering a space clears the value to empty string."""
    from vibedom.cli import _prompt_prefilled
    fake_stdin = io.StringIO(' \n')
    with patch.object(sys, 'stdin', fake_stdin):
        result = _prompt_prefilled('Token', prefill='existing-tok')
    assert result == ''


def test_prompt_prefilled_non_tty_new_value():
    """Non-TTY stdin: entering a new value replaces the prefill."""
    from vibedom.cli import _prompt_prefilled
    fake_stdin = io.StringIO('new-tok\n')
    with patch.object(sys, 'stdin', fake_stdin):
        result = _prompt_prefilled('Token', prefill='old-tok')
    assert result == 'new-tok'


def test_prompt_prefilled_tty_uses_readline_prefill():
    """TTY stdin: readline pre_input_hook is registered with the prefill text."""
    from vibedom.cli import _prompt_prefilled

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
    assert len(hooks_registered) == 2
    assert hooks_registered[1] is None
