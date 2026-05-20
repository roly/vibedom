"""Global vibedom configuration — stored in ~/.vibedom/config.json."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_CONFIG_FILE = 'config.json'
_CLOUDFLARE_GATEWAY_BASE = 'https://gateway.ai.cloudflare.com/v1'


@dataclass
class GlobalConfig:
    """Global vibedom settings shared across all workspaces.

    Stored at ~/.vibedom/config.json.

    Example usage::

        cfg = GlobalConfig.load(Path.home() / '.vibedom')
        if cfg.is_cloudflare_configured():
            print(cfg.anthropic_base_url())
    """

    cloudflare_account_id: Optional[str] = None
    cloudflare_gateway_id: Optional[str] = None
    cloudflare_gateway_token: Optional[str] = None
    vibedom_username: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, config_dir: Path) -> 'GlobalConfig':
        """Load config from config_dir/config.json, returning defaults if absent."""
        path = config_dir / _CONFIG_FILE
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        return cls(
            cloudflare_account_id=data.get('cloudflare_account_id'),
            cloudflare_gateway_id=data.get('cloudflare_gateway_id'),
            cloudflare_gateway_token=data.get('cloudflare_gateway_token'),
            vibedom_username=data.get('vibedom_username'),
        )

    def save(self, config_dir: Path) -> None:
        """Persist config to config_dir/config.json, creating the directory if needed."""
        config_dir.mkdir(parents=True, exist_ok=True)
        data = {
            'cloudflare_account_id': self.cloudflare_account_id,
            'cloudflare_gateway_id': self.cloudflare_gateway_id,
            'cloudflare_gateway_token': self.cloudflare_gateway_token,
            'vibedom_username': self.vibedom_username,
        }
        (config_dir / _CONFIG_FILE).write_text(json.dumps(data, indent=2) + '\n')

    # ------------------------------------------------------------------ #
    # Cloudflare AI Gateway helpers
    # ------------------------------------------------------------------ #

    def is_cloudflare_configured(self) -> bool:
        """Return True when both Cloudflare account ID and gateway ID are set."""
        return bool(self.cloudflare_account_id and self.cloudflare_gateway_id)

    def anthropic_base_url(self) -> Optional[str]:
        """Return the Cloudflare AI Gateway URL for Anthropic, or None if not configured."""
        if not self.is_cloudflare_configured():
            return None
        return f'{_CLOUDFLARE_GATEWAY_BASE}/{self.cloudflare_account_id}/{self.cloudflare_gateway_id}/anthropic'

    def extra_env(self) -> dict:
        """Return environment variables to inject into containers.

        Injects ANTHROPIC_BASE_URL when Cloudflare AI Gateway is configured.
        """
        env: dict = {}
        url = self.anthropic_base_url()
        if url:
            env['ANTHROPIC_BASE_URL'] = url
        return env

    def proxy_env(self) -> dict:
        """Return environment variables to inject into the mitmproxy host process.

        The addon reads these to inject Cloudflare gateway auth and user-id headers
        on requests destined for gateway.ai.cloudflare.com.
        """
        env: dict = {}
        if self.cloudflare_gateway_token:
            env['VIBEDOM_CF_AIG_TOKEN'] = self.cloudflare_gateway_token
        if self.vibedom_username:
            env['VIBEDOM_USER'] = self.vibedom_username
        return env
