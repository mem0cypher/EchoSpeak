"""Safe GET hydration paths must not consume the mutation rate budget."""

from __future__ import annotations


def test_rate_limit_exempt_helpers():
    from api import server as server_mod

    assert server_mod._rate_limit_exempt("/health", "GET") is True
    assert server_mod._rate_limit_exempt("/threads/abc/state", "GET") is True
    assert server_mod._rate_limit_exempt("/video/documents/x", "GET") is True
    assert server_mod._rate_limit_exempt("/pending-action", "GET") is True
    # Mutations still limited
    assert server_mod._rate_limit_exempt("/query", "POST") is False
    assert server_mod._rate_limit_exempt("/query", "GET") is False
    assert server_mod._rate_limit_exempt("/approvals/id/confirm", "POST") is False
    assert server_mod._rate_limit_exempt("/memory/rebuild-index", "POST") is False
