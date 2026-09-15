"""PluginClient timeout defaults for JSON RPC vs content streams."""

import httpx

from plugins.client import CONTENT_STREAM_TIMEOUT, DEFAULT_TIMEOUT_SECONDS


def test_content_stream_timeout_has_unlimited_read() -> None:
    """Large cross-storage copies must not inherit the 10s JSON-RPC read cap."""
    assert DEFAULT_TIMEOUT_SECONDS == 10.0
    assert isinstance(CONTENT_STREAM_TIMEOUT, httpx.Timeout)
    assert CONTENT_STREAM_TIMEOUT.read is None
    assert CONTENT_STREAM_TIMEOUT.connect == DEFAULT_TIMEOUT_SECONDS
