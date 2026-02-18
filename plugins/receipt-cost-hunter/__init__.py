"""Receipt Cost Hunter plugin.

Hooks:
- heartbeat_tick: ingest Outlook receipts + auto-run setup wizard when needed
- before_agent_start: inject setup/report context into system prompt
"""

from __future__ import annotations

from datetime import UTC, datetime

from server import config
from server.plugins import PluginAPI
from server import receipt_cost_hunter as rch


def _should_auto_setup() -> bool:
    state = rch.get_state()
    setup = state.get("setup", {})
    if not setup.get("completed"):
        return True
    raw_updated = str(setup.get("updated_at", "")).strip()
    if not raw_updated:
        return True
    if raw_updated.endswith("Z"):
        raw_updated = raw_updated[:-1] + "+00:00"
    try:
        updated = datetime.fromisoformat(raw_updated)
    except ValueError:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return (datetime.now(UTC) - updated).days >= 7


async def _heartbeat_tick(**kwargs):
    if not config.OUTLOOK_ENABLED:
        return {"status": "skipped", "reason": "outlook_disabled"}

    ingest = await rch.ingest_outlook_receipts(scan_count=60)
    auto_setup = None
    if ingest.get("status") == "ok" and _should_auto_setup():
        auto_setup = rch.run_setup_wizard(mode="auto")

    return {
        "ingest": ingest,
        "auto_setup": auto_setup,
    }


async def _before_agent_start(**kwargs):
    system_prompt = kwargs.get("system_prompt", "")
    report = rch.build_cost_report(days=30)
    summary = rch.format_report_text(report)
    inject = (
        "\n\nBaby Cost Hunter Context:\n"
        f"{summary}\n"
        "If user asks about baby shopping, use this profile first and recommend setup refinement when data is thin."
    )
    return {"system_prompt": system_prompt + inject}


async def _tool_setup_wizard(
    mode: str = "status",
    primary_stores: list[str] | None = None,
    challenger_stores: list[str] | None = None,
    diaper_brands: list[str] | None = None,
    formula_brands: list[str] | None = None,
    zip_code: str | None = None,
    force: bool = False,
) -> str:
    result = rch.run_setup_wizard(
        mode=mode,
        primary_stores=primary_stores,
        challenger_stores=challenger_stores,
        diaper_brands=diaper_brands,
        formula_brands=formula_brands,
        zip_code=zip_code,
        force=force,
    )
    setup = result.get("setup", {})
    summary = rch.format_setup_summary(setup)
    spend = result.get("summary_30d", {})
    spend_text = (
        f" 30d total ${spend.get('total_spend', 0):.2f}, "
        f"baby ${spend.get('baby_spend', 0):.2f}."
    )
    if result.get("skipped"):
        return f"{summary} Wizard skipped ({result['skipped']}).{spend_text}"
    return f"{summary}{spend_text}"


async def _tool_baby_cost_report(days: int = 30) -> str:
    days = max(7, min(int(days), 180))
    report = rch.build_cost_report(days=days)
    return rch.format_report_text(report)


async def _tool_receipt_sync(scan_count: int = 60, force_auto_setup: bool = False) -> str:
    ingest = await rch.ingest_outlook_receipts(scan_count=scan_count)
    setup_msg = ""
    if force_auto_setup:
        setup = rch.run_setup_wizard(mode="auto", force=True)
        setup_msg = " " + rch.format_setup_summary(setup.get("setup", {}))
    return (
        f"Receipt sync status: {ingest.get('status')}."
        f" Scanned {ingest.get('scanned_messages', 0)} messages,"
        f" new receipts {ingest.get('new_receipts', 0)}.{setup_msg}"
    )


def register(api: PluginAPI):
    api.register_hook("heartbeat_tick", _heartbeat_tick)
    api.register_hook("before_agent_start", _before_agent_start)

    api.register_tool(
        name="baby_cost_setup_wizard",
        description=(
            "Configure baby cost-hunter preferences for stores, local zip, and preferred diaper/formula brands. "
            "Use mode='auto' to infer from receipts."
        ),
        parameters={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "status | auto | set",
                },
                "primary_stores": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Your usual stores (e.g. local_grocery,target,bjs)",
                },
                "challenger_stores": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Alternative stores to evaluate",
                },
                "diaper_brands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Preferred diaper brands",
                },
                "formula_brands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Preferred formula brands",
                },
                "zip_code": {
                    "type": "string",
                    "description": "Primary ZIP code for local store matching",
                },
                "force": {
                    "type": "boolean",
                    "description": "Force re-inference in auto mode even if already configured",
                },
            },
            "required": [],
        },
        handler=_tool_setup_wizard,
        permission="none",
    )

    api.register_tool(
        name="baby_cost_report",
        description="Summarize 30-day baby spending by store from indexed Outlook receipts.",
        parameters={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Window size in days (7-180, default 30)",
                }
            },
            "required": [],
        },
        handler=_tool_baby_cost_report,
        permission="none",
    )

    api.register_tool(
        name="baby_receipt_sync",
        description="Manually trigger Outlook receipt ingestion and optional auto-setup refresh.",
        parameters={
            "type": "object",
            "properties": {
                "scan_count": {
                    "type": "integer",
                    "description": "How many recent inbox messages to scan (10-120)",
                },
                "force_auto_setup": {
                    "type": "boolean",
                    "description": "Force auto-setup refresh after syncing receipts",
                },
            },
            "required": [],
        },
        handler=_tool_receipt_sync,
        permission="none",
    )
