"""Tests for receipt-driven baby cost hunter + heartbeat plugin dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_rch_state(monkeypatch, tmp_path):
    from server import receipt_cost_hunter as rch

    monkeypatch.setattr(rch, "DATA_DIR", tmp_path)
    monkeypatch.setattr(rch, "STATE_FILE", tmp_path / "baby_cost_hunter_state.json")
    return rch


def test_setup_wizard_auto_infers_stores_and_brands(isolated_rch_state):
    rch = isolated_rch_state
    state = rch.get_state()
    state["receipts"] = [
        {
            "id": "1",
            "store": "target",
            "baby_related": True,
            "amount": 39.99,
            "brands": {"diaper": ["pampers"], "formula": []},
            "received_at": "2026-02-10T12:00:00Z",
            "zip_codes": ["11201"],
        },
        {
            "id": "2",
            "store": "bjs",
            "baby_related": True,
            "amount": 42.50,
            "brands": {"diaper": [], "formula": ["similac"]},
            "received_at": "2026-02-12T12:00:00Z",
            "zip_codes": ["11201"],
        },
        {
            "id": "3",
            "store": "walmart",
            "baby_related": True,
            "amount": 35.00,
            "brands": {"diaper": ["huggies"], "formula": []},
            "received_at": "2026-02-13T12:00:00Z",
            "zip_codes": ["11211"],
        },
    ]
    rch._save_state(state)

    result = rch.run_setup_wizard(mode="auto", force=True)
    setup = result["setup"]
    assert setup["completed"] is True
    assert "target" in setup["primary_stores"]
    assert "bjs" in setup["primary_stores"]
    assert "walmart" in setup["challenger_stores"]
    assert "pampers" in setup["preferred_brands"]["diaper"]
    assert "similac" in setup["preferred_brands"]["formula"]


@pytest.mark.asyncio
async def test_ingest_outlook_receipts_creates_records(isolated_rch_state, monkeypatch):
    rch = isolated_rch_state

    async def fake_get_inbox(count: int = 10, unread_only: bool = False):
        return [
            {
                "id": "m1",
                "subject": "Target order confirmation",
                "bodyPreview": "Total: $29.99 Pampers Swaddlers",
                "receivedDateTime": "2026-02-15T12:00:00Z",
                "from": {"emailAddress": {"name": "Target", "address": "orders@target.com"}},
            }
        ]

    async def fake_get_message(message_id: str):
        return {
            "body": {
                "contentType": "text",
                "content": "Thank you for your order. Pampers Swaddlers 84 count. Total $29.99",
            }
        }

    class FakeOutlook:
        @staticmethod
        def is_configured():
            return True

        @staticmethod
        def get_access_token():
            return "token"

        @staticmethod
        async def get_inbox(count: int = 10, unread_only: bool = False):
            return await fake_get_inbox(count=count, unread_only=unread_only)

        @staticmethod
        async def get_message(message_id: str):
            return await fake_get_message(message_id)

    monkeypatch.setattr(rch, "_get_outlook_module", lambda: FakeOutlook)

    result = await rch.ingest_outlook_receipts(scan_count=20)
    assert result["status"] == "ok"
    assert result["new_receipts"] == 1

    state = rch.get_state()
    assert len(state["receipts"]) == 1
    assert state["receipts"][0]["store"] == "target"
    assert state["receipts"][0]["baby_related"] is True


@pytest.mark.asyncio
async def test_heartbeat_dispatches_plugin_tick(monkeypatch):
    from server import heartbeat
    from server.plugins import _hooks

    calls = []

    async def on_tick(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    _hooks.clear()
    _hooks["heartbeat_tick"] = [on_tick]

    monkeypatch.setattr(heartbeat, "_in_active_hours", lambda: False)

    class DummyManager:
        pass

    await heartbeat.check(DummyManager())
    assert len(calls) == 1

    _hooks.clear()


def test_plugin_bundle_loads_and_registers_hooks():
    from server.plugins import load_plugin

    plugin_path = Path(__file__).resolve().parents[2] / "plugins" / "receipt-cost-hunter"
    result = load_plugin(plugin_path)
    assert result is not None
    tools, hooks, skills = result
    assert any(event == "heartbeat_tick" for event, _handler in hooks)
    assert any(tool.name == "baby_cost_setup_wizard" for tool in tools)
    assert skills == []
