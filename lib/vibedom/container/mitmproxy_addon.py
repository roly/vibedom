"""Mitmproxy addon for enforcing whitelist and DLP scrubbing."""

import datetime
import json
import os
import signal
import sys
from pathlib import Path
from typing import Optional

try:
    from mitmproxy import http
except ImportError:
    from unittest.mock import MagicMock

    class MockResponse:
        """Mock Response for testing when mitmproxy not installed."""
        @staticmethod
        def make(status_code, body, headers):
            return MagicMock(status_code=status_code, content=body, headers=headers)

    http = MagicMock()
    http.Response = MockResponse

# Import DLP scrubber (copied alongside this file to /mnt/config/)
sys.path.insert(0, str(Path(__file__).parent))
from dlp_scrubber import DLPScrubber

_CLOUDFLARE_GATEWAY_HOST = 'gateway.ai.cloudflare.com'

# Content types safe to scrub (text-based)
SCRUBBABLE_CONTENT_TYPES = (
    'text/',
    'application/json',
    'application/x-www-form-urlencoded',
    'application/xml',
    'application/javascript',
)


class VibedomProxy:
    """Mitmproxy addon for vibedom sandbox."""

    def __init__(self):
        self.whitelist = self.load_whitelist()
        # Write to session directory instead of container-local /var/log
        network_log = os.environ.get(
            'VIBEDOM_NETWORK_LOG_PATH', '/mnt/session/network.jsonl'
        )
        self.network_log_path = Path(network_log)
        self.network_log_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize DLP scrubber
        gitleaks_config = os.environ.get(
            'VIBEDOM_GITLEAKS_CONFIG',
            str(Path(__file__).parent / 'gitleaks.toml')
        )
        config_path = gitleaks_config if Path(gitleaks_config).exists() else None
        self.scrubber = DLPScrubber(gitleaks_config=config_path)

        # Cloudflare AI Gateway auth — injected as headers on requests to the gateway
        self.cf_aig_token: Optional[str] = os.environ.get('VIBEDOM_CF_AIG_TOKEN')
        self.vibedom_user: Optional[str] = os.environ.get('VIBEDOM_USER')

        # Register SIGHUP handler for whitelist reload
        signal.signal(signal.SIGHUP, self._reload_whitelist)

    def load_whitelist(self) -> set:
        """Load whitelist from mounted config."""
        whitelist_path = Path(
            os.environ.get('VIBEDOM_WHITELIST_PATH', '/mnt/config/trusted_domains.txt')
        )
        if not whitelist_path.exists():
            print(
                f"WARNING: Whitelist file not found at {whitelist_path}, blocking all traffic",
                file=sys.stderr
            )
            return set()

        domains = set()
        with open(whitelist_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    domains.add(line.lower())
        return domains

    def _reload_whitelist(self, signum, frame):
        """Reload whitelist when SIGHUP received."""
        self.whitelist = self.load_whitelist()
        print(f"Reloaded whitelist: {len(self.whitelist)} domains", file=sys.stderr)

    def is_allowed(self, domain: str) -> bool:
        """Check if domain is whitelisted."""
        domain = domain.lower()

        if domain in self.whitelist:
            return True

        parts = domain.split('.')
        for i in range(len(parts)):
            parent = '.'.join(parts[i:])
            if parent in self.whitelist:
                return True

        return False

    def _is_scrubbable(self, content_type: str | None) -> bool:
        """Check if content type is text-based and safe to scrub."""
        if not content_type:
            return False
        return any(content_type.startswith(ct) for ct in SCRUBBABLE_CONTENT_TYPES)

    def _scrub_body(self, content: bytes, content_type: str | None) -> tuple[bytes, list]:
        """Scrub request/response body if text-based.

        Returns:
            Tuple of (possibly-scrubbed content, list of findings)
        """
        if not content or not self._is_scrubbable(content_type):
            return content, []

        try:
            text = content.decode('utf-8')
        except UnicodeDecodeError:
            return content, []

        result = self.scrubber.scrub(text)
        if result.was_scrubbed:
            return result.text.encode('utf-8'), result.findings
        return content, []

    def _scrub_url(self, url: str) -> tuple[str, list]:
        """Scrub secrets from URL query parameters.

        Returns:
            Tuple of (possibly-scrubbed URL, list of findings)
        """
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

        parsed = urlparse(url)
        if not parsed.query:
            return url, []

        query_params = parse_qs(parsed.query)
        findings = []

        for key, values in query_params.items():
            for i, value in enumerate(values):
                result = self.scrubber.scrub(value)
                if result.was_scrubbed:
                    query_params[key][i] = result.text
                    findings.extend(result.findings)

        if not findings:
            return url, []

        scrubbed_query = urlencode(query_params, doseq=True)
        scrubbed_url = urlunparse(parsed._replace(query=scrubbed_query))
        return scrubbed_url, findings

    def _format_findings(self, findings: list) -> list[dict]:
        """Format findings for audit logging with truncated secrets."""
        return [
            {
                'pattern': f.pattern_id,
                'category': f.category,
                'original_prefix': f.matched_text[:4] + '***',
                'original_length': len(f.matched_text),
                'replaced_with': f.placeholder,
            }
            for f in findings
        ]

    def _inject_cloudflare_headers(self, flow: http.HTTPFlow) -> None:
        """Inject Cloudflare AI Gateway auth and user-id headers when configured."""
        host = (flow.request.host_header or flow.request.host or '').lower()
        if _CLOUDFLARE_GATEWAY_HOST not in host:
            return
        if self.cf_aig_token:
            flow.request.headers['cf-aig-authorization'] = f'Bearer {self.cf_aig_token}'
        if self.vibedom_user:
            flow.request.headers['cf-aig-metadata'] = json.dumps({'user': self.vibedom_user})

    def request(self, flow: http.HTTPFlow) -> None:
        """Intercept, scrub, and filter requests."""
        domain = flow.request.host_header or flow.request.host

        # Inject Cloudflare gateway auth headers before the request leaves the proxy
        self._inject_cloudflare_headers(flow)

        scrubbed_findings = []

        # Scrub URL query parameters
        url_scrubbed, url_findings = self._scrub_url(flow.request.pretty_url)
        if url_scrubbed != flow.request.pretty_url:
            flow.request.url = url_scrubbed
            scrubbed_findings.extend(url_findings)

        # Scrub request body before forwarding
        if flow.request.content:
            content_type = flow.request.headers.get('Content-Type', '')
            scrubbed_content, findings = self._scrub_body(
                flow.request.content, content_type
            )
            if findings:
                flow.request.content = scrubbed_content
                scrubbed_findings.extend(findings)

        # Log request (with scrubbing info)
        self.log_request(flow, allowed=self.is_allowed(domain),
                         scrubbed=scrubbed_findings)

        # Block if not whitelisted
        if not self.is_allowed(domain):
            flow.response = http.Response.make(
                403,
                b"Domain not whitelisted by vibedom",
                {"Content-Type": "text/plain"}
            )

    def log_request(self, flow: http.HTTPFlow, allowed: bool,
                    scrubbed: list | None = None) -> None:
        """Log network request with optional scrubbing details."""
        entry = {
            'timestamp': datetime.datetime.now(datetime.UTC).isoformat(),
            'method': flow.request.method,
            'url': flow.request.pretty_url,
            'host': flow.request.host_header or flow.request.host,
            'allowed': allowed,
        }

        if scrubbed:
            entry['scrubbed'] = self._format_findings(scrubbed)

        try:
            with open(self.network_log_path, 'a') as f:
                f.write(json.dumps(entry) + '\n')
        except OSError as e:
            print(f"Warning: Failed to log request: {e}", file=sys.stderr)


addons = [VibedomProxy()]
