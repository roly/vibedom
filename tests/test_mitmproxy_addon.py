import sys
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / 'vm'))

try:
    from mitmproxy import http
except ImportError:
    from unittest.mock import MagicMock

    class MockResponse:
        """Mock Response for testing when mitmproxy not installed."""
        @staticmethod
        def make(status_code, body, headers):
            return Mock(status_code=status_code, content=body, headers=headers)

    class MockHTTPFlow:
        """Mock HTTPFlow for testing when mitmproxy not installed."""
        request = None
        response = None

    http = MagicMock()
    http.Response = MockResponse
    http.HTTPFlow = MockHTTPFlow


@patch('pathlib.Path.mkdir')
def test_request_headers_pass_through(mock_mkdir):
    """Should allow Authorization header through (for API calls)."""
    from mitmproxy_addon import VibedomProxy

    proxy = VibedomProxy()

    flow = MagicMock()
    flow.request.host = "api.anthropic.com"
    flow.request.host_header = "api.anthropic.com"
    flow.request.content = None
    flow.request.pretty_url = "https://api.anthropic.com/v1/messages"
    flow.request.url = "https://api.anthropic.com/v1/messages"
    flow.request.headers = {
        "Authorization": "Bearer sk-ant-api123",
        "Content-Type": "application/json"
    }

    proxy.request(flow)

    # Authorization header should still be present
    assert flow.request.headers.get("Authorization") == "Bearer sk-ant-api123"


@patch('pathlib.Path.mkdir')
def test_response_body_not_scrubbed(mock_mkdir):
    """Should not scrub response bodies (not needed for our threat model)."""
    from mitmproxy_addon import VibedomProxy

    proxy = VibedomProxy()

    flow = Mock(spec=http.HTTPFlow)
    flow.response = Mock()
    flow.response.content = b'{"api_key": "AKIAIOSFODNN7EXAMPLE"}'
    flow.response.headers = {"Content-Type": "application/json"}

    # response() method should not exist - responses pass through unmodified
    assert not hasattr(proxy, 'response')

    # Response content should remain unchanged (no scrubbing)
    assert flow.response.content == b'{"api_key": "AKIAIOSFODNN7EXAMPLE"}'


def test_addon_reads_whitelist_from_env(tmp_path, monkeypatch):
    """VibedomProxy should read whitelist path from VIBEDOM_WHITELIST_PATH env var."""
    whitelist = tmp_path / 'domains.txt'
    whitelist.write_text('example.com\n')
    monkeypatch.setenv('VIBEDOM_WHITELIST_PATH', str(whitelist))
    monkeypatch.setenv('VIBEDOM_NETWORK_LOG_PATH', str(tmp_path / 'network.jsonl'))
    monkeypatch.setenv('VIBEDOM_GITLEAKS_CONFIG', str(tmp_path / 'gitleaks.toml'))

    from mitmproxy_addon import VibedomProxy
    proxy = VibedomProxy()
    assert 'example.com' in proxy.whitelist


def test_addon_reads_network_log_from_env(tmp_path, monkeypatch):
    """VibedomProxy should write network log to VIBEDOM_NETWORK_LOG_PATH."""
    log_path = tmp_path / 'network.jsonl'
    monkeypatch.setenv('VIBEDOM_NETWORK_LOG_PATH', str(log_path))
    monkeypatch.setenv('VIBEDOM_WHITELIST_PATH', str(tmp_path / 'domains.txt'))
    monkeypatch.setenv('VIBEDOM_GITLEAKS_CONFIG', str(tmp_path / 'gitleaks.toml'))

    from mitmproxy_addon import VibedomProxy
    proxy = VibedomProxy()
    assert proxy.network_log_path == log_path


def test_addon_reads_gitleaks_config_from_env(tmp_path, monkeypatch):
    """VibedomProxy should read gitleaks config from VIBEDOM_GITLEAKS_CONFIG env var."""
    monkeypatch.setenv('VIBEDOM_WHITELIST_PATH', str(tmp_path / 'domains.txt'))
    monkeypatch.setenv('VIBEDOM_NETWORK_LOG_PATH', str(tmp_path / 'network.jsonl'))
    gitleaks_config = tmp_path / 'gitleaks.toml'
    gitleaks_config.write_text('')  # empty but exists
    monkeypatch.setenv('VIBEDOM_GITLEAKS_CONFIG', str(gitleaks_config))

    from mitmproxy_addon import VibedomProxy
    proxy = VibedomProxy()
    # Just verify it instantiates without error when all env vars are set
    assert proxy.network_log_path == tmp_path / 'network.jsonl'


@patch('pathlib.Path.mkdir')
def test_log_request_includes_timestamp(mock_mkdir, tmp_path, monkeypatch):
    """log_request should include an ISO timestamp in each network log entry."""
    log_path = tmp_path / 'network.jsonl'
    monkeypatch.setenv('VIBEDOM_NETWORK_LOG_PATH', str(log_path))
    monkeypatch.setenv('VIBEDOM_WHITELIST_PATH', str(tmp_path / 'domains.txt'))
    monkeypatch.setenv('VIBEDOM_GITLEAKS_CONFIG', str(tmp_path / 'gitleaks.toml'))

    from mitmproxy_addon import VibedomProxy
    import json as json_mod

    proxy = VibedomProxy()

    flow = MagicMock()
    flow.request.method = 'GET'
    flow.request.pretty_url = 'https://api.anthropic.com/v1/messages'
    flow.request.host_header = 'api.anthropic.com'
    flow.request.host = 'api.anthropic.com'

    proxy.log_request(flow, allowed=True)

    entry = json_mod.loads(log_path.read_text().strip())
    assert 'timestamp' in entry
    assert 'T' in entry['timestamp']  # ISO format contains 'T' separator


@patch('pathlib.Path.mkdir')
def test_missing_whitelist_prints_warning(mock_mkdir, tmp_path, monkeypatch, capsys):
    """load_whitelist should warn to stderr when whitelist file is missing."""
    monkeypatch.setenv('VIBEDOM_WHITELIST_PATH', str(tmp_path / 'nonexistent.txt'))
    monkeypatch.setenv('VIBEDOM_NETWORK_LOG_PATH', str(tmp_path / 'network.jsonl'))
    monkeypatch.setenv('VIBEDOM_GITLEAKS_CONFIG', str(tmp_path / 'gitleaks.toml'))

    from mitmproxy_addon import VibedomProxy
    proxy = VibedomProxy()

    assert proxy.whitelist == set()
    captured = capsys.readouterr()
    assert 'WARNING' in captured.err
    assert 'blocking all traffic' in captured.err


# ------------------------------------------------------------------ #
# Cloudflare AI Gateway header injection
# ------------------------------------------------------------------ #

def _make_proxy(tmp_path, monkeypatch, cf_token=None, cf_user=None):
    """Instantiate VibedomProxy with Cloudflare env vars optionally set."""
    monkeypatch.setenv('VIBEDOM_WHITELIST_PATH', str(tmp_path / 'domains.txt'))
    monkeypatch.setenv('VIBEDOM_NETWORK_LOG_PATH', str(tmp_path / 'network.jsonl'))
    monkeypatch.setenv('VIBEDOM_GITLEAKS_CONFIG', str(tmp_path / 'gitleaks.toml'))
    if cf_token:
        monkeypatch.setenv('VIBEDOM_CF_AIG_TOKEN', cf_token)
    if cf_user:
        monkeypatch.setenv('VIBEDOM_USER', cf_user)
    from mitmproxy_addon import VibedomProxy
    return VibedomProxy()


def _make_flow(host):
    flow = MagicMock()
    flow.request.host = host
    flow.request.host_header = host
    flow.request.headers = {}
    return flow


@patch('pathlib.Path.mkdir')
def test_cf_headers_injected_for_gateway_host(mock_mkdir, tmp_path, monkeypatch):
    """cf-aig-authorization header injected when request goes to gateway.ai.cloudflare.com."""
    proxy = _make_proxy(tmp_path, monkeypatch, cf_token='my-token')
    flow = _make_flow('gateway.ai.cloudflare.com')

    proxy._inject_cloudflare_headers(flow)

    assert flow.request.headers.get('cf-aig-authorization') == 'Bearer my-token'


@patch('pathlib.Path.mkdir')
def test_cf_user_header_injected_for_gateway_host(mock_mkdir, tmp_path, monkeypatch):
    """cf-aig-metadata header injected with user JSON when VIBEDOM_USER is set."""
    import json as json_mod
    proxy = _make_proxy(tmp_path, monkeypatch, cf_token='tok', cf_user='alice')
    flow = _make_flow('gateway.ai.cloudflare.com')

    proxy._inject_cloudflare_headers(flow)

    metadata = json_mod.loads(flow.request.headers['cf-aig-metadata'])
    assert metadata['user'] == 'alice'


@patch('pathlib.Path.mkdir')
def test_cf_both_headers_injected_together(mock_mkdir, tmp_path, monkeypatch):
    """Both cf-aig-authorization and cf-aig-metadata are injected when both configured."""
    proxy = _make_proxy(tmp_path, monkeypatch, cf_token='tok', cf_user='bob')
    flow = _make_flow('gateway.ai.cloudflare.com')

    proxy._inject_cloudflare_headers(flow)

    assert 'cf-aig-authorization' in flow.request.headers
    assert 'cf-aig-metadata' in flow.request.headers


@patch('pathlib.Path.mkdir')
def test_cf_headers_not_injected_for_other_hosts(mock_mkdir, tmp_path, monkeypatch):
    """No Cloudflare headers injected for requests to non-gateway hosts."""
    proxy = _make_proxy(tmp_path, monkeypatch, cf_token='tok', cf_user='alice')
    flow = _make_flow('api.anthropic.com')

    proxy._inject_cloudflare_headers(flow)

    assert 'cf-aig-authorization' not in flow.request.headers
    assert 'cf-aig-metadata' not in flow.request.headers


@patch('pathlib.Path.mkdir')
def test_cf_headers_not_injected_when_unconfigured(mock_mkdir, tmp_path, monkeypatch):
    """No Cloudflare headers injected when neither token nor user is configured."""
    proxy = _make_proxy(tmp_path, monkeypatch)  # no token, no user
    flow = _make_flow('gateway.ai.cloudflare.com')

    proxy._inject_cloudflare_headers(flow)

    assert 'cf-aig-authorization' not in flow.request.headers
    assert 'cf-aig-metadata' not in flow.request.headers


@patch('pathlib.Path.mkdir')
def test_cf_headers_injected_via_request_pipeline(mock_mkdir, tmp_path, monkeypatch):
    """Headers are injected when request() is called (integration through the pipeline)."""
    whitelist = tmp_path / 'domains.txt'
    whitelist.write_text('gateway.ai.cloudflare.com\n')
    monkeypatch.setenv('VIBEDOM_WHITELIST_PATH', str(whitelist))
    proxy = _make_proxy(tmp_path, monkeypatch, cf_token='pipeline-tok', cf_user='carol')

    flow = _make_flow('gateway.ai.cloudflare.com')
    flow.request.content = None
    flow.request.pretty_url = 'https://gateway.ai.cloudflare.com/v1/abc/gw/anthropic/v1/messages'
    flow.request.url = flow.request.pretty_url
    flow.request.method = 'POST'

    proxy.request(flow)

    assert flow.request.headers.get('cf-aig-authorization') == 'Bearer pipeline-tok'
    assert 'cf-aig-metadata' in flow.request.headers
