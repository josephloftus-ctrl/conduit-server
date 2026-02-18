"""Receipt-driven baby cost hunter state + setup wizard.

This module ingests Outlook receipts, infers store/brand preferences, and
maintains a lightweight profile for cost-hunting workflows.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("conduit.receipt_cost_hunter")

DATA_DIR = Path.home() / ".conduit" / "data"
STATE_FILE = DATA_DIR / "baby_cost_hunter_state.json"
STATE_VERSION = 1

DEFAULT_PRIMARY_STORES = ["local_grocery", "target", "bjs"]
DEFAULT_CHALLENGER_STORES = ["walmart", "costco", "aldi"]

STORE_ALIASES: dict[str, tuple[str, ...]] = {
    "target": ("target", "target.com"),
    "bjs": ("bj", "bjs", "bj's", "bjs.com", "bj wholesale", "bj's wholesale"),
    "walmart": ("walmart", "walmart.com"),
    "costco": ("costco", "costco.com"),
    "aldi": ("aldi", "aldi.us"),
    "amazon": ("amazon", "amazon.com"),
    "instacart": ("instacart",),
    "local_grocery": ("grocery", "market", "supermarket"),
}

DIAPER_TERMS = ("diaper", "diapers", "pull-up", "pullups", "overnight", "swaddlers")
FORMULA_TERMS = ("formula", "infant formula", "toddler formula", "powder formula")
RECEIPT_TERMS = (
    "receipt",
    "invoice",
    "order confirmation",
    "order confirmed",
    "your order",
    "purchase",
    "payment",
    "charged",
    "subtotal",
    "total",
)

DIAPER_BRANDS = (
    "pampers",
    "huggies",
    "honest",
    "luv",
    "dyper",
    "cuties",
)

FORMULA_BRANDS = (
    "similac",
    "enfamil",
    "gerber",
    "kendamil",
    "bobbie",
    "parent's choice",
)

MONEY_RE = re.compile(r"\$?\s?(\d{1,4}(?:,\d{3})*(?:\.\d{2}))")
ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _get_outlook_module():
    try:
        from . import outlook as outlook_mod
        return outlook_mod
    except Exception:
        return None


def _default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "updated_at": _utc_now_iso(),
        "last_processed_received_at": "",
        "seen_message_ids": [],
        "receipts": [],
        "setup": {
            "completed": False,
            "needs_review": True,
            "primary_stores": [],
            "challenger_stores": [],
            "store_locations": [],
            "preferred_brands": {"diaper": [], "formula": []},
            "updated_at": "",
        },
    }


def _normalize_store(text: str) -> str | None:
    lowered = text.lower()
    for canonical, aliases in STORE_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return canonical
    return None


def _store_display(store: str) -> str:
    mapping = {
        "bjs": "BJ's",
        "local_grocery": "Local Grocery",
    }
    return mapping.get(store, store.title())


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<script[^>]*>.*?</script>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"&nbsp;", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _extract_sender(msg: dict) -> str:
    sender = msg.get("from", {}).get("emailAddress", {})
    name = _safe_text(sender.get("name"))
    addr = _safe_text(sender.get("address"))
    return f"{name} {addr}".strip()


def _extract_amount(text: str) -> float | None:
    if not text:
        return None
    candidates: list[float] = []

    for line in re.split(r"[\n\r]+", text):
        lowered = line.lower()
        if "total" not in lowered and "charged" not in lowered and "amount" not in lowered:
            continue
        for raw in MONEY_RE.findall(line):
            try:
                candidates.append(float(raw.replace(",", "")))
            except ValueError:
                continue

    if not candidates:
        for raw in MONEY_RE.findall(text):
            try:
                candidates.append(float(raw.replace(",", "")))
            except ValueError:
                continue

    if not candidates:
        return None

    # Prefer realistic consumer totals.
    realistic = [value for value in candidates if 0.5 <= value <= 2000]
    if realistic:
        return round(max(realistic), 2)
    return round(max(candidates), 2)


def _infer_brands(text: str) -> dict[str, list[str]]:
    lowered = text.lower()
    diaper_hits = sorted([brand for brand in DIAPER_BRANDS if brand in lowered])
    formula_hits = sorted([brand for brand in FORMULA_BRANDS if brand in lowered])
    return {"diaper": diaper_hits, "formula": formula_hits}


def _has_baby_terms(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in DIAPER_TERMS + FORMULA_TERMS + DIAPER_BRANDS + FORMULA_BRANDS)


def _looks_like_receipt(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in RECEIPT_TERMS) or _has_baby_terms(lowered)


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return _default_state()

    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("Failed reading %s, reinitializing.", STATE_FILE)
        return _default_state()

    if not isinstance(payload, dict):
        return _default_state()
    return payload


def _save_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state["version"] = STATE_VERSION
    state["updated_at"] = _utc_now_iso()
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _extract_body_text(message: dict | None) -> str:
    if not message:
        return ""
    body = message.get("body", {})
    if not isinstance(body, dict):
        return ""
    content = _safe_text(body.get("content"))
    if not content:
        return ""
    body_type = _safe_text(body.get("contentType")).lower()
    if body_type == "html":
        return _strip_html(content)
    return content


def _make_receipt_record(msg: dict, body_text: str) -> dict[str, Any] | None:
    message_id = _safe_text(msg.get("id"))
    if not message_id:
        return None

    subject = _safe_text(msg.get("subject"))
    preview = _safe_text(msg.get("bodyPreview"))
    sender = _extract_sender(msg)
    received_at = _safe_text(msg.get("receivedDateTime"))

    combined = "\n".join(filter(None, [subject, preview, body_text, sender]))
    if not _looks_like_receipt(combined):
        return None

    store = _normalize_store(" ".join([subject, sender, preview])) or "unknown"
    amount = _extract_amount(combined)
    brands = _infer_brands(combined)
    baby_related = _has_baby_terms(combined)
    zip_codes = sorted(set(ZIP_RE.findall(combined)))[:3]

    return {
        "id": message_id,
        "source": "outlook",
        "subject": subject,
        "sender": sender,
        "received_at": received_at,
        "store": store,
        "amount": amount,
        "baby_related": baby_related,
        "brands": brands,
        "zip_codes": zip_codes,
    }


def _trim_state(state: dict[str, Any]) -> None:
    receipts = state.get("receipts", [])
    if isinstance(receipts, list) and len(receipts) > 2000:
        state["receipts"] = receipts[-2000:]

    seen_ids = state.get("seen_message_ids", [])
    if isinstance(seen_ids, list) and len(seen_ids) > 5000:
        state["seen_message_ids"] = seen_ids[-5000:]


def _summarize_spend(receipts: list[dict[str, Any]], days: int = 30) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    by_store: dict[str, float] = defaultdict(float)
    baby_by_store: dict[str, float] = defaultdict(float)
    baby_total = 0.0
    total = 0.0

    for row in receipts:
        received = _parse_iso(row.get("received_at"))
        if not received or received < cutoff:
            continue
        amount = row.get("amount")
        if not isinstance(amount, (int, float)):
            continue
        store = _safe_text(row.get("store")) or "unknown"
        total += float(amount)
        by_store[store] += float(amount)
        if row.get("baby_related"):
            baby_total += float(amount)
            baby_by_store[store] += float(amount)

    return {
        "window_days": days,
        "total_spend": round(total, 2),
        "baby_spend": round(baby_total, 2),
        "spend_by_store": {k: round(v, 2) for k, v in sorted(by_store.items(), key=lambda item: item[1], reverse=True)},
        "baby_spend_by_store": {
            k: round(v, 2) for k, v in sorted(baby_by_store.items(), key=lambda item: item[1], reverse=True)
        },
    }


def _infer_setup_from_receipts(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    store_counter: Counter[str] = Counter()
    diaper_brand_counter: Counter[str] = Counter()
    formula_brand_counter: Counter[str] = Counter()
    store_zip_hints: dict[str, str] = {}

    for row in receipts:
        if not row.get("baby_related"):
            continue
        store = _safe_text(row.get("store"))
        if store:
            store_counter[store] += 1
            zips = row.get("zip_codes") or []
            if isinstance(zips, list) and zips and store not in store_zip_hints:
                store_zip_hints[store] = _safe_text(zips[0])

        brands = row.get("brands", {})
        for brand in brands.get("diaper", []):
            diaper_brand_counter[brand] += 1
        for brand in brands.get("formula", []):
            formula_brand_counter[brand] += 1

    observed_stores = [name for name, _count in store_counter.most_common()]
    # Keep user's usual-store defaults stable unless manually overridden.
    primary: list[str] = DEFAULT_PRIMARY_STORES.copy()

    challengers: list[str] = [s for s in DEFAULT_CHALLENGER_STORES if s not in primary]
    for store in observed_stores:
        if len(challengers) >= 4:
            break
        if store not in primary and store not in challengers:
            challengers.append(store)

    if not primary:
        primary = DEFAULT_PRIMARY_STORES.copy()
    if not challengers:
        challengers = DEFAULT_CHALLENGER_STORES.copy()

    store_locations = []
    for store in primary + challengers:
        store_locations.append(
            {
                "store": store,
                "zip_hint": store_zip_hints.get(store, ""),
            }
        )

    diaper_brands = [name for name, _ in diaper_brand_counter.most_common(5)]
    formula_brands = [name for name, _ in formula_brand_counter.most_common(5)]

    completed = bool(primary) and bool(diaper_brands or formula_brands)
    return {
        "completed": completed,
        "needs_review": True,
        "primary_stores": primary[:4],
        "challenger_stores": challengers[:5],
        "store_locations": store_locations[:8],
        "preferred_brands": {
            "diaper": diaper_brands,
            "formula": formula_brands,
        },
        "updated_at": _utc_now_iso(),
    }


def _apply_manual_setup(
    state: dict[str, Any],
    primary_stores: list[str] | None,
    challenger_stores: list[str] | None,
    diaper_brands: list[str] | None,
    formula_brands: list[str] | None,
    zip_code: str | None,
) -> dict[str, Any]:
    setup = state.setdefault("setup", {})
    if primary_stores:
        setup["primary_stores"] = [s.strip().lower() for s in primary_stores if s.strip()]
    if challenger_stores:
        setup["challenger_stores"] = [s.strip().lower() for s in challenger_stores if s.strip()]

    pref = setup.setdefault("preferred_brands", {"diaper": [], "formula": []})
    if diaper_brands is not None:
        pref["diaper"] = [b.strip().lower() for b in diaper_brands if b.strip()]
    if formula_brands is not None:
        pref["formula"] = [b.strip().lower() for b in formula_brands if b.strip()]

    if zip_code:
        locations = []
        for store in setup.get("primary_stores", []) + setup.get("challenger_stores", []):
            locations.append({"store": store, "zip_hint": zip_code})
        setup["store_locations"] = locations

    setup["completed"] = bool(setup.get("primary_stores")) and bool(
        pref.get("diaper") or pref.get("formula")
    )
    setup["needs_review"] = False
    setup["updated_at"] = _utc_now_iso()
    return setup


def get_state() -> dict[str, Any]:
    return _load_state()


async def ingest_outlook_receipts(scan_count: int = 60) -> dict[str, Any]:
    """Ingest recent Outlook messages and store receipt candidates."""
    state = _load_state()
    outlook_mod = _get_outlook_module()
    if outlook_mod is None:
        return {"status": "skipped", "reason": "outlook_import_failed", "new_receipts": 0}

    if not outlook_mod.is_configured() or not outlook_mod.get_access_token():
        return {"status": "skipped", "reason": "outlook_not_ready", "new_receipts": 0}

    scan_count = max(10, min(int(scan_count), 120))
    inbox = await outlook_mod.get_inbox(count=scan_count, unread_only=False)
    if not inbox:
        return {"status": "ok", "new_receipts": 0, "scanned_messages": 0}

    seen_ids = set(state.get("seen_message_ids", []))
    receipts = state.setdefault("receipts", [])
    last_processed = _parse_iso(state.get("last_processed_received_at"))

    new_msgs: list[dict] = []
    for msg in inbox:
        message_id = _safe_text(msg.get("id"))
        if not message_id or message_id in seen_ids:
            continue
        received = _parse_iso(msg.get("receivedDateTime"))
        if last_processed and received and received <= last_processed:
            continue
        new_msgs.append(msg)

    new_msgs.sort(key=lambda row: _parse_iso(row.get("receivedDateTime")) or datetime.now(UTC))

    new_receipts = 0
    newest_received = last_processed
    for msg in new_msgs:
        message_id = _safe_text(msg.get("id"))
        if not message_id:
            continue

        subject_preview = " ".join([_safe_text(msg.get("subject")), _safe_text(msg.get("bodyPreview"))])
        body_text = ""
        if _looks_like_receipt(subject_preview):
            full = await outlook_mod.get_message(message_id)
            body_text = _extract_body_text(full)

        record = _make_receipt_record(msg, body_text)
        if record is not None:
            receipts.append(record)
            new_receipts += 1

        seen_ids.add(message_id)
        received = _parse_iso(_safe_text(msg.get("receivedDateTime")))
        if received and (newest_received is None or received > newest_received):
            newest_received = received

    state["seen_message_ids"] = sorted(seen_ids)
    if newest_received:
        state["last_processed_received_at"] = newest_received.isoformat()
    _trim_state(state)
    _save_state(state)

    summary_30d = _summarize_spend(state.get("receipts", []), days=30)
    return {
        "status": "ok",
        "scanned_messages": len(inbox),
        "new_messages": len(new_msgs),
        "new_receipts": new_receipts,
        "summary_30d": summary_30d,
    }


def run_setup_wizard(
    mode: str = "auto",
    primary_stores: list[str] | None = None,
    challenger_stores: list[str] | None = None,
    diaper_brands: list[str] | None = None,
    formula_brands: list[str] | None = None,
    zip_code: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Auto-infer or manually set baby cost hunter profile."""
    state = _load_state()
    setup = state.setdefault("setup", {})

    mode = mode.lower().strip()
    if mode not in {"auto", "set", "status"}:
        raise ValueError("mode must be one of: auto, set, status")

    if mode == "status":
        return {"mode": mode, "setup": setup, "summary_30d": _summarize_spend(state.get("receipts", []), days=30)}

    if mode == "set":
        setup = _apply_manual_setup(
            state=state,
            primary_stores=primary_stores,
            challenger_stores=challenger_stores,
            diaper_brands=diaper_brands,
            formula_brands=formula_brands,
            zip_code=zip_code,
        )
        _save_state(state)
        return {"mode": mode, "setup": setup, "summary_30d": _summarize_spend(state.get("receipts", []), days=30)}

    # mode == auto
    already_complete = bool(setup.get("completed"))
    if already_complete and not force:
        return {"mode": mode, "setup": setup, "skipped": "already_configured", "summary_30d": _summarize_spend(state.get("receipts", []), days=30)}

    inferred = _infer_setup_from_receipts(state.get("receipts", []))
    state["setup"] = inferred
    _save_state(state)
    return {"mode": mode, "setup": inferred, "summary_30d": _summarize_spend(state.get("receipts", []), days=30)}


def build_cost_report(days: int = 30) -> dict[str, Any]:
    state = _load_state()
    setup = state.get("setup", {})
    receipts = state.get("receipts", [])
    summary = _summarize_spend(receipts, days=days)

    primary = setup.get("primary_stores", []) or []
    challengers = setup.get("challenger_stores", []) or []
    baby_spend_by_store = summary.get("baby_spend_by_store", {})

    primary_total = sum(float(baby_spend_by_store.get(store, 0.0)) for store in primary)
    challenger_total = sum(float(baby_spend_by_store.get(store, 0.0)) for store in challengers)

    recommendation = "insufficient_data"
    if primary_total > 0 and challenger_total > 0:
        recommendation = "consider_challengers" if challenger_total < primary_total else "stay_primary"

    return {
        "setup": setup,
        "summary": summary,
        "comparison": {
            "primary_stores": primary,
            "challenger_stores": challengers,
            "primary_baby_spend": round(primary_total, 2),
            "challenger_baby_spend": round(challenger_total, 2),
            "recommendation": recommendation,
        },
    }


def format_setup_summary(setup: dict[str, Any]) -> str:
    primary = ", ".join(_store_display(s) for s in setup.get("primary_stores", [])) or "not set"
    challengers = ", ".join(_store_display(s) for s in setup.get("challenger_stores", [])) or "not set"
    diaper = ", ".join(setup.get("preferred_brands", {}).get("diaper", [])) or "not set"
    formula = ", ".join(setup.get("preferred_brands", {}).get("formula", [])) or "not set"
    status = "complete" if setup.get("completed") else "incomplete"
    return (
        f"Baby cost setup is {status}. "
        f"Primary stores: {primary}. Challengers: {challengers}. "
        f"Diaper brands: {diaper}. Formula brands: {formula}."
    )


def format_report_text(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    comparison = report.get("comparison", {})
    setup = report.get("setup", {})

    lines = [
        format_setup_summary(setup),
        (
            f"Last {summary.get('window_days', 30)} days: total spend ${summary.get('total_spend', 0):.2f}, "
            f"baby spend ${summary.get('baby_spend', 0):.2f}."
        ),
    ]

    baby_by_store = summary.get("baby_spend_by_store", {})
    if baby_by_store:
        parts = [f"{_store_display(store)} ${amount:.2f}" for store, amount in baby_by_store.items()]
        lines.append("Baby spend by store: " + ", ".join(parts) + ".")

    rec = comparison.get("recommendation", "insufficient_data")
    if rec == "consider_challengers":
        lines.append(
            "Receipts suggest challenger stores are currently cheaper for baby-category purchases; "
            "switching some items is likely worth testing."
        )
    elif rec == "stay_primary":
        lines.append("Receipts suggest your primary stores are competitive right now.")
    else:
        lines.append("Need more receipt coverage before recommending a switch.")

    return " ".join(lines)
