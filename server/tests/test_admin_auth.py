"""Tests for admin token auth and dangerous settings protection."""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_no_token():
    """App instance with no admin token (backwards compatible mode)."""
    with patch.dict(os.environ, {"CONDUIT_ADMIN_TOKEN": ""}, clear=False):
        # Need to reimport to pick up env change
        from server.app import require_admin
        # In no-token mode, require_admin should pass through
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(require_admin(""))
        assert result is None


@pytest.fixture
def app_with_token():
    """App instance with admin token set."""
    with patch.dict(os.environ, {"CONDUIT_ADMIN_TOKEN": "test-secret-token"}, clear=False):
        yield "test-secret-token"


class TestDangerousKeysBlocked:
    """PUT /api/settings/tools must reject dangerous keys."""

    def test_auto_approve_all_rejected(self):
        """auto_approve_all cannot be set via REST API."""
        from server.app import app
        client = TestClient(app)
        resp = client.put("/api/settings/tools", json={"auto_approve_all": True})
        assert resp.status_code == 403
        assert "auto_approve_all" in resp.json()["detail"]

    def test_enabled_rejected(self):
        """tools.enabled cannot be set via REST API."""
        from server.app import app
        client = TestClient(app)
        resp = client.put("/api/settings/tools", json={"enabled": False})
        assert resp.status_code == 403
        assert "enabled" in resp.json()["detail"]

    def test_allowed_directories_rejected(self):
        """allowed_directories cannot be set via REST API."""
        from server.app import app
        client = TestClient(app)
        resp = client.put("/api/settings/tools", json={"allowed_directories": ["/"]})
        assert resp.status_code == 403
        assert "allowed_directories" in resp.json()["detail"]

    def test_safe_keys_accepted(self):
        """Non-dangerous keys should still work."""
        from server.app import app
        client = TestClient(app)
        resp = client.put("/api/settings/tools", json={"max_agent_turns": 15})
        # Should succeed (200) — not blocked
        assert resp.status_code == 200


class TestAdminTokenEnforcement:
    """When CONDUIT_ADMIN_TOKEN is set, settings endpoints require it."""

    def test_require_admin_no_token_configured_passes(self):
        """With no CONDUIT_ADMIN_TOKEN, requests pass through."""
        import asyncio
        from server import app as app_module

        original = app_module.ADMIN_TOKEN
        try:
            app_module.ADMIN_TOKEN = ""
            result = asyncio.get_event_loop().run_until_complete(
                app_module.require_admin("")
            )
            assert result is None
        finally:
            app_module.ADMIN_TOKEN = original

    def test_require_admin_rejects_missing_token(self):
        """With CONDUIT_ADMIN_TOKEN set, missing header is rejected."""
        import asyncio
        from fastapi import HTTPException
        from server import app as app_module

        original = app_module.ADMIN_TOKEN
        try:
            app_module.ADMIN_TOKEN = "my-secret"
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    app_module.require_admin("")
                )
            assert exc_info.value.status_code == 401
        finally:
            app_module.ADMIN_TOKEN = original

    def test_require_admin_rejects_wrong_token(self):
        """With CONDUIT_ADMIN_TOKEN set, wrong token is rejected."""
        import asyncio
        from fastapi import HTTPException
        from server import app as app_module

        original = app_module.ADMIN_TOKEN
        try:
            app_module.ADMIN_TOKEN = "my-secret"
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    app_module.require_admin("Bearer wrong-token")
                )
            assert exc_info.value.status_code == 403
        finally:
            app_module.ADMIN_TOKEN = original

    def test_require_admin_accepts_correct_token(self):
        """With CONDUIT_ADMIN_TOKEN set, correct token passes."""
        import asyncio
        from server import app as app_module

        original = app_module.ADMIN_TOKEN
        try:
            app_module.ADMIN_TOKEN = "my-secret"
            result = asyncio.get_event_loop().run_until_complete(
                app_module.require_admin("Bearer my-secret")
            )
            assert result is None
        finally:
            app_module.ADMIN_TOKEN = original


class TestKillChainBlocked:
    """The specific self-escalation kill chain must be blocked."""

    def test_cannot_enable_auto_approve_via_api(self):
        """The exact attack: PUT /api/settings/tools {auto_approve_all: true}."""
        from server.app import app
        client = TestClient(app)
        resp = client.put("/api/settings/tools", json={"auto_approve_all": True})
        assert resp.status_code == 403

    def test_cannot_expand_directories_via_api(self):
        """Attacker trying to expand allowed directories to /."""
        from server.app import app
        client = TestClient(app)
        resp = client.put("/api/settings/tools", json={"allowed_directories": ["/"]})
        assert resp.status_code == 403

    def test_cannot_disable_tools_via_api(self):
        """Attacker trying to disable all tools (DoS)."""
        from server.app import app
        client = TestClient(app)
        resp = client.put("/api/settings/tools", json={"enabled": False})
        assert resp.status_code == 403
