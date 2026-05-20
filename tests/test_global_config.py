"""Tests for GlobalConfig — Cloudflare AI Gateway and other global settings."""

import json
import pytest
from pathlib import Path
from vibedom.global_config import GlobalConfig


@pytest.fixture
def config_dir(tmp_path):
    return tmp_path / '.vibedom'


def test_load_returns_empty_config_when_no_file(config_dir):
    """Loading from a directory with no config.json returns defaults."""
    cfg = GlobalConfig.load(config_dir)
    assert cfg.cloudflare_account_id is None
    assert cfg.cloudflare_gateway_id is None


def test_save_and_load_roundtrip(config_dir):
    """Saved config can be loaded back."""
    config_dir.mkdir(parents=True)
    cfg = GlobalConfig(
        cloudflare_account_id='abc123',
        cloudflare_gateway_id='my-gateway',
    )
    cfg.save(config_dir)

    loaded = GlobalConfig.load(config_dir)
    assert loaded.cloudflare_account_id == 'abc123'
    assert loaded.cloudflare_gateway_id == 'my-gateway'


def test_save_creates_config_dir(config_dir):
    """save() creates the directory if it doesn't exist."""
    assert not config_dir.exists()
    GlobalConfig(cloudflare_account_id='x', cloudflare_gateway_id='y').save(config_dir)
    assert (config_dir / 'config.json').exists()


def test_anthropic_base_url_when_configured():
    """anthropic_base_url returns the correct gateway URL when both IDs are set."""
    cfg = GlobalConfig(cloudflare_account_id='acc123', cloudflare_gateway_id='gw-prod')
    url = cfg.anthropic_base_url()
    assert url == 'https://gateway.ai.cloudflare.com/v1/acc123/gw-prod/anthropic'


def test_anthropic_base_url_none_when_not_configured():
    """anthropic_base_url returns None when gateway is not configured."""
    assert GlobalConfig().anthropic_base_url() is None
    assert GlobalConfig(cloudflare_account_id='x').anthropic_base_url() is None
    assert GlobalConfig(cloudflare_gateway_id='y').anthropic_base_url() is None


def test_is_cloudflare_configured():
    """is_cloudflare_configured returns True only when both IDs are present."""
    assert not GlobalConfig().is_cloudflare_configured()
    assert not GlobalConfig(cloudflare_account_id='x').is_cloudflare_configured()
    assert not GlobalConfig(cloudflare_gateway_id='y').is_cloudflare_configured()
    assert GlobalConfig(cloudflare_account_id='x', cloudflare_gateway_id='y').is_cloudflare_configured()


def test_load_ignores_unknown_keys(config_dir):
    """Extra keys in config.json are silently ignored (forward-compat)."""
    config_dir.mkdir(parents=True)
    (config_dir / 'config.json').write_text(json.dumps({
        'cloudflare_account_id': 'acc',
        'cloudflare_gateway_id': 'gw',
        'future_unknown_key': 'value',
    }))
    cfg = GlobalConfig.load(config_dir)
    assert cfg.cloudflare_account_id == 'acc'
    assert cfg.cloudflare_gateway_id == 'gw'


def test_save_persists_only_known_fields(config_dir):
    """save() writes only the known fields to disk."""
    cfg = GlobalConfig(cloudflare_account_id='acc', cloudflare_gateway_id='gw')
    cfg.save(config_dir)
    data = json.loads((config_dir / 'config.json').read_text())
    assert set(data.keys()) == {
        'cloudflare_account_id', 'cloudflare_gateway_id',
        'cloudflare_gateway_token', 'vibedom_username',
    }


def test_extra_env_includes_anthropic_url_when_configured():
    """extra_env returns ANTHROPIC_BASE_URL when gateway is configured."""
    cfg = GlobalConfig(cloudflare_account_id='acc', cloudflare_gateway_id='gw')
    env = cfg.extra_env()
    assert env['ANTHROPIC_BASE_URL'] == 'https://gateway.ai.cloudflare.com/v1/acc/gw/anthropic'


def test_extra_env_empty_when_not_configured():
    """extra_env returns empty dict when no gateway is configured."""
    assert GlobalConfig().extra_env() == {}


# ------------------------------------------------------------------ #
# Gateway auth token
# ------------------------------------------------------------------ #

def test_save_and_load_roundtrip_with_token(config_dir):
    """Token and username survive a save/load round-trip."""
    cfg = GlobalConfig(
        cloudflare_account_id='acc',
        cloudflare_gateway_id='gw',
        cloudflare_gateway_token='tok-secret',
        vibedom_username='alice',
    )
    cfg.save(config_dir)
    loaded = GlobalConfig.load(config_dir)
    assert loaded.cloudflare_gateway_token == 'tok-secret'
    assert loaded.vibedom_username == 'alice'


def test_load_token_none_when_absent(config_dir):
    """Token and username default to None when not in config file."""
    config_dir.mkdir(parents=True)
    (config_dir / 'config.json').write_text(json.dumps({
        'cloudflare_account_id': 'acc',
        'cloudflare_gateway_id': 'gw',
    }))
    cfg = GlobalConfig.load(config_dir)
    assert cfg.cloudflare_gateway_token is None
    assert cfg.vibedom_username is None


def test_proxy_env_with_token_and_user():
    """proxy_env returns VIBEDOM_CF_AIG_TOKEN and VIBEDOM_USER when set."""
    cfg = GlobalConfig(
        cloudflare_account_id='acc',
        cloudflare_gateway_id='gw',
        cloudflare_gateway_token='tok-secret',
        vibedom_username='alice',
    )
    env = cfg.proxy_env()
    assert env['VIBEDOM_CF_AIG_TOKEN'] == 'tok-secret'
    assert env['VIBEDOM_USER'] == 'alice'


def test_proxy_env_empty_when_not_set():
    """proxy_env returns empty dict when token and username are absent."""
    assert GlobalConfig().proxy_env() == {}
    assert GlobalConfig(cloudflare_account_id='acc', cloudflare_gateway_id='gw').proxy_env() == {}


def test_proxy_env_partial():
    """proxy_env only includes keys that are actually set."""
    env_token_only = GlobalConfig(cloudflare_gateway_token='t').proxy_env()
    assert 'VIBEDOM_CF_AIG_TOKEN' in env_token_only
    assert 'VIBEDOM_USER' not in env_token_only

    env_user_only = GlobalConfig(vibedom_username='bob').proxy_env()
    assert 'VIBEDOM_USER' in env_user_only
    assert 'VIBEDOM_CF_AIG_TOKEN' not in env_user_only
