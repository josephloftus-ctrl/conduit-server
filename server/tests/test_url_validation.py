"""Tests for SSRF protection — URL validation blocks private/internal networks."""

import socket
from unittest.mock import patch

import pytest

from server.tools.url_validation import is_url_blocked


class TestBlockedURLs:
    """URLs that must be blocked."""

    def test_localhost(self):
        assert is_url_blocked("http://localhost:8080/api/settings/tools") is not None

    def test_localhost_localdomain(self):
        assert is_url_blocked("http://localhost.localdomain/foo") is not None

    def test_loopback_ip(self):
        assert is_url_blocked("http://127.0.0.1:8080/api/settings/tools") is not None

    def test_loopback_ip_variant(self):
        assert is_url_blocked("http://127.0.0.2/foo") is not None

    def test_private_10(self):
        assert is_url_blocked("http://10.0.0.1/admin") is not None

    def test_private_172(self):
        assert is_url_blocked("http://172.16.0.1/admin") is not None

    def test_private_192(self):
        assert is_url_blocked("http://192.168.1.1/admin") is not None

    def test_link_local(self):
        assert is_url_blocked("http://169.254.1.1/metadata") is not None

    def test_no_hostname(self):
        assert is_url_blocked("not-a-url") is not None

    def test_dns_resolves_to_private(self):
        """A hostname that DNS-resolves to a private IP must be blocked."""
        with patch.object(socket, "gethostbyname", return_value="192.168.1.100"):
            result = is_url_blocked("http://evil.example.com/steal")
            assert result is not None
            assert "private" in result.lower() or "blocked" in result.lower()

    def test_dns_resolution_failure(self):
        """Fail closed — unresolvable hostnames are blocked."""
        with patch.object(socket, "gethostbyname", side_effect=socket.gaierror("nope")):
            result = is_url_blocked("http://nonexistent.invalid/foo")
            assert result is not None


class TestAllowedURLs:
    """URLs that must be allowed through."""

    def test_public_https(self):
        with patch.object(socket, "gethostbyname", return_value="151.101.1.140"):
            assert is_url_blocked("https://example.com/page") is None

    def test_public_ip(self):
        assert is_url_blocked("http://8.8.8.8/dns") is None

    def test_clawhub(self):
        with patch.object(socket, "gethostbyname", return_value="104.21.32.1"):
            assert is_url_blocked("https://clawhub.com/api/skills/weather/download") is None


class TestKillChainScenarios:
    """Specific attack vectors that triggered this fix."""

    def test_self_escalation_settings_api(self):
        """LLM trying to hit localhost settings API to grant itself auto_approve."""
        result = is_url_blocked("http://localhost:8080/api/settings/tools")
        assert result is not None

    def test_self_escalation_via_ip(self):
        """Same attack via 127.0.0.1."""
        result = is_url_blocked("http://127.0.0.1:8080/api/settings/tools")
        assert result is not None

    def test_probe_router(self):
        """LLM trying to probe the home router."""
        result = is_url_blocked("http://192.168.1.1/admin")
        assert result is not None

    def test_probe_searxng(self):
        """LLM trying to probe internal SearXNG."""
        result = is_url_blocked("http://localhost:8888/search?q=secrets")
        assert result is not None
