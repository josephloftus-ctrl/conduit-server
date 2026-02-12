"""Expense tracker tools — log expenses, view summaries, manage budgets."""

import json
import logging
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from . import register
from .definitions import ToolDefinition

log = logging.getLogger("conduit.tools.expenses")

DATA_DIR = Path.home() / ".conduit" / "data"
DATA_FILE = DATA_DIR / "expenses.json"

# Keyword → category mapping for auto-detection from descriptions
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "food": [
        "lunch", "dinner", "breakfast", "restaurant", "grocery", "groceries",
        "coffee", "cafe", "pizza", "burger", "takeout", "doordash", "ubereats",
        "grubhub", "chipotle", "starbucks", "panera", "chick-fil-a",
        "mcdonald", "wendy", "taco bell", "food", "eat", "meal",
    ],
    "transportation": [
        "gas", "fuel", "uber", "lyft", "parking", "toll", "transit",
        "metro", "bus", "train", "car wash", "oil change", "mechanic",
        "tire", "registration", "inspection",
    ],
    "utilities": [
        "electric", "water", "internet", "phone", "cable", "wifi",
        "power", "gas bill", "utility", "sewage", "trash pickup",
    ],
    "entertainment": [
        "movie", "netflix", "spotify", "hulu", "disney", "game",
        "concert", "ticket", "theater", "bar", "club", "bowling",
        "arcade", "museum", "amusement",
    ],
    "shopping": [
        "amazon", "target", "walmart", "costco", "clothing", "shoes",
        "clothes", "shirt", "pants", "jacket", "online order", "ebay",
        "best buy", "home depot", "lowes", "ikea",
    ],
    "health": [
        "doctor", "pharmacy", "gym", "medicine", "prescription",
        "dentist", "hospital", "urgent care", "copay", "therapy",
        "vitamin", "supplement", "cvs", "walgreens",
    ],
    "subscriptions": [
        "subscription", "renewal", "membership", "annual", "monthly plan",
        "premium", "pro plan",
    ],
    "work": [
        "office", "supplies", "equipment", "work expense", "business",
        "laptop", "software", "license",
    ],
}


def _detect_category(description: str) -> str:
    """Auto-detect expense category from description."""
    lower = description.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return category
    return "other"


def _load_data() -> dict:
    """Load expense data from JSON file, creating defaults if missing."""
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to read expense data, starting fresh: %s", e)
    return {"transactions": [], "budgets": {}}


def _save_data(data: dict) -> None:
    """Save expense data to JSON file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=2))


def _parse_period(period: str) -> tuple[date, date]:
    """Return (start_date, end_date) for a named period."""
    today = date.today()
    if period == "today":
        return today, today
    elif period == "week":
        start = today - timedelta(days=today.weekday())  # Monday
        return start, today
    elif period == "month":
        start = today.replace(day=1)
        return start, today
    elif period == "year":
        start = today.replace(month=1, day=1)
        return start, today
    else:  # "all"
        return date(2000, 1, 1), today


def _format_amount(amount: float) -> str:
    return f"${amount:,.2f}"


def _month_total(transactions: list[dict], category: str | None = None) -> float:
    """Sum transactions for the current month, optionally filtered by category."""
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    total = 0.0
    for t in transactions:
        if t["date"] >= month_start:
            if category is None or t.get("category") == category:
                total += t["amount"]
    return total


async def _log_expense(
    amount: float,
    description: str,
    category: str = "",
    date: str = "",
    recurring: bool = False,
) -> str:
    """Log an expense transaction."""
    from datetime import date as _date_cls
    data = _load_data()

    if not category:
        category = _detect_category(description)

    expense_date = date if date else _date_cls.today().isoformat()

    # Validate date format
    try:
        datetime.strptime(expense_date, "%Y-%m-%d")
    except ValueError:
        return f"Invalid date format: {expense_date}. Use YYYY-MM-DD."

    txn = {
        "id": uuid.uuid4().hex[:8],
        "amount": round(amount, 2),
        "description": description,
        "category": category,
        "date": expense_date,
        "recurring": recurring,
    }
    data["transactions"].append(txn)
    _save_data(data)

    month = _month_total(data["transactions"], category)
    budget = data.get("budgets", {}).get(category, {}).get("monthly")
    budget_note = ""
    if budget:
        remaining = budget - month
        if remaining > 0:
            budget_note = f" ({_format_amount(remaining)} left in {category} budget)"
        else:
            budget_note = f" (OVER {category} budget by {_format_amount(-remaining)})"

    return (
        f"Logged {_format_amount(amount)} — {category} ({description}). "
        f"{_date_cls.today().strftime('%B')} {category} total: {_format_amount(month)}{budget_note}"
    )


async def _view_expenses(
    period: str = "month",
    category: str = "",
    summary: bool = False,
) -> str:
    """View expense transactions or summary for a period."""
    data = _load_data()
    transactions = data.get("transactions", [])

    if not transactions:
        return "No expenses recorded yet."

    start, end = _parse_period(period)
    filtered = [
        t for t in transactions
        if start.isoformat() <= t["date"] <= end.isoformat()
    ]
    if category:
        filtered = [t for t in filtered if t.get("category") == category]

    if not filtered:
        cat_label = f" in {category}" if category else ""
        return f"No expenses{cat_label} for {period}."

    if summary:
        # Category breakdown
        by_cat: dict[str, float] = {}
        for t in filtered:
            cat = t.get("category", "other")
            by_cat[cat] = by_cat.get(cat, 0.0) + t["amount"]

        budgets = data.get("budgets", {})
        total = sum(by_cat.values())

        lines = [f"Expense Summary ({period}):\n"]
        for cat in sorted(by_cat.keys(), key=lambda c: by_cat[c], reverse=True):
            amt = by_cat[cat]
            budget = budgets.get(cat, {}).get("monthly")
            if budget:
                pct = (amt / budget) * 100
                status = f" ({pct:.0f}% of {_format_amount(budget)} budget)"
            else:
                status = ""
            lines.append(f"  {cat:15s}  {_format_amount(amt):>10s}{status}")

        lines.append(f"\n  {'TOTAL':15s}  {_format_amount(total):>10s}")
        return "\n".join(lines)

    else:
        # Line items
        lines = [f"Expenses ({period}):\n"]
        total = 0.0
        for t in sorted(filtered, key=lambda x: x["date"], reverse=True):
            total += t["amount"]
            recur = " (recurring)" if t.get("recurring") else ""
            lines.append(
                f"  {t['date']}  {_format_amount(t['amount']):>10s}  "
                f"{t['category']:12s}  {t['description']}{recur}"
            )
        lines.append(f"\n  Total: {_format_amount(total)}")
        return "\n".join(lines)


async def _manage_budget(
    action: str,
    category: str = "",
    monthly_amount: float = 0.0,
) -> str:
    """Set, view, or delete monthly budgets per category."""
    data = _load_data()
    budgets = data.setdefault("budgets", {})

    if action == "set":
        if not category:
            return "Provide a category to set a budget for."
        if monthly_amount <= 0:
            return "monthly_amount must be positive."
        budgets[category] = {"monthly": round(monthly_amount, 2)}
        _save_data(data)
        return f"Budget set: {category} → {_format_amount(monthly_amount)}/month"

    elif action == "view":
        if not budgets:
            return "No budgets set. Use action='set' to create one."

        transactions = data.get("transactions", [])
        lines = ["Monthly Budgets:\n"]

        cats = [category] if category else sorted(budgets.keys())
        for cat in cats:
            if cat not in budgets:
                return f"No budget set for '{cat}'."
            budget = budgets[cat]["monthly"]
            spent = _month_total(transactions, cat)
            remaining = budget - spent
            pct = (spent / budget) * 100 if budget else 0
            bar_len = min(int(pct / 5), 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            status = "OK" if remaining >= 0 else "OVER"
            lines.append(
                f"  {cat:15s}  {_format_amount(spent):>10s} / {_format_amount(budget):>10s}  "
                f"[{bar}] {pct:.0f}%  {status}"
            )

        return "\n".join(lines)

    elif action == "delete":
        if not category:
            return "Provide a category to delete the budget for."
        if category not in budgets:
            return f"No budget set for '{category}'."
        del budgets[category]
        _save_data(data)
        return f"Deleted budget for '{category}'."

    else:
        return f"Unknown action: {action}. Use set, view, or delete."


def register_all():
    """Register expense tracker tools."""
    register(ToolDefinition(
        name="log_expense",
        description=(
            "Log an expense. Amount and description are required. "
            "Category is auto-detected if not provided (food, transportation, "
            "utilities, entertainment, shopping, health, subscriptions, work, other)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "description": "Amount spent (e.g. 45.00)",
                },
                "description": {
                    "type": "string",
                    "description": "What the expense was for (e.g. 'Lunch at Panera')",
                },
                "category": {
                    "type": "string",
                    "description": "Category (auto-detected if omitted): food, transportation, utilities, entertainment, shopping, health, subscriptions, work, other",
                },
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format (defaults to today)",
                },
                "recurring": {
                    "type": "boolean",
                    "description": "Mark as a recurring expense (default: false)",
                },
            },
            "required": ["amount", "description"],
        },
        handler=_log_expense,
        permission="write",
    ))

    register(ToolDefinition(
        name="view_expenses",
        description=(
            "View expense transactions or a summary for a time period. "
            "Can filter by category. Use summary=true for category totals with budget comparison."
        ),
        parameters={
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["today", "week", "month", "year", "all"],
                    "description": "Time period to view (default: 'month')",
                },
                "category": {
                    "type": "string",
                    "description": "Filter to a specific category",
                },
                "summary": {
                    "type": "boolean",
                    "description": "Show category totals instead of line items (default: false)",
                },
            },
            "required": [],
        },
        handler=_view_expenses,
        permission="none",
    ))

    register(ToolDefinition(
        name="manage_budget",
        description=(
            "Set, view, or delete monthly budgets per expense category. "
            "View shows spending vs budget with progress bars."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["set", "view", "delete"],
                    "description": "Action to perform",
                },
                "category": {
                    "type": "string",
                    "description": "Expense category (required for set/delete, optional for view)",
                },
                "monthly_amount": {
                    "type": "number",
                    "description": "Monthly budget amount (for 'set' action)",
                },
            },
            "required": ["action"],
        },
        handler=_manage_budget,
        permission="write",
    ))
