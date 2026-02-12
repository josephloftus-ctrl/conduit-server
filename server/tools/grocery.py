"""Grocery list tools — manage named grocery lists with auto-categorization."""

import json
import logging
from datetime import date
from pathlib import Path

from . import register
from .definitions import ToolDefinition

log = logging.getLogger("conduit.tools.grocery")

DATA_DIR = Path.home() / ".conduit" / "data"
DATA_FILE = DATA_DIR / "grocery.json"

# Keyword → category mapping for auto-detection
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "produce": [
        "apple", "banana", "orange", "lemon", "lime", "grape", "berry",
        "strawberry", "blueberry", "raspberry", "avocado", "tomato", "potato",
        "onion", "garlic", "pepper", "lettuce", "spinach", "kale", "broccoli",
        "carrot", "celery", "cucumber", "zucchini", "squash", "corn", "mushroom",
        "cilantro", "basil", "parsley", "ginger", "fruit", "vegetable", "salad",
        "cabbage", "pear", "peach", "plum", "mango", "pineapple", "watermelon",
    ],
    "dairy": [
        "milk", "cheese", "yogurt", "butter", "cream", "egg", "eggs",
        "sour cream", "cottage cheese", "whipped cream", "half and half",
        "creamer", "mozzarella", "cheddar", "parmesan",
    ],
    "meat": [
        "chicken", "beef", "pork", "steak", "ground beef", "turkey", "bacon",
        "sausage", "ham", "lamb", "veal", "ribs", "roast", "brisket",
        "ground turkey", "hot dog", "deli meat",
    ],
    "seafood": [
        "fish", "salmon", "shrimp", "tuna", "tilapia", "cod", "crab",
        "lobster", "scallop", "clam", "mussel", "oyster", "sardine",
    ],
    "bakery": [
        "bread", "bagel", "muffin", "croissant", "roll", "bun", "tortilla",
        "pita", "cake", "pie", "donut", "pastry", "baguette",
    ],
    "frozen": [
        "frozen", "ice cream", "pizza", "frozen dinner", "popsicle",
        "frozen vegetable", "frozen fruit", "frozen meal",
    ],
    "pantry": [
        "rice", "pasta", "noodle", "flour", "sugar", "salt", "oil",
        "vinegar", "sauce", "ketchup", "mustard", "mayo", "mayonnaise",
        "syrup", "honey", "peanut butter", "jelly", "jam", "cereal",
        "oatmeal", "granola", "bean", "can", "canned", "soup", "broth",
        "stock", "spice", "seasoning", "pepper", "cinnamon",
    ],
    "beverages": [
        "water", "juice", "soda", "coffee", "tea", "beer", "wine",
        "sparkling", "lemonade", "kombucha", "energy drink", "gatorade",
    ],
    "snacks": [
        "chips", "crackers", "popcorn", "nuts", "trail mix", "granola bar",
        "protein bar", "cookie", "candy", "chocolate", "pretzel",
    ],
    "household": [
        "paper towel", "toilet paper", "trash bag", "detergent", "soap",
        "dish soap", "sponge", "foil", "plastic wrap", "ziplock", "bleach",
        "cleaner", "wipes", "laundry",
    ],
    "personal": [
        "shampoo", "conditioner", "toothpaste", "toothbrush", "deodorant",
        "lotion", "sunscreen", "razor", "floss", "mouthwash",
    ],
}


def _detect_category(item_name: str) -> str:
    """Auto-detect category from item name using keyword matching."""
    lower = item_name.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return category
    return "other"


def _load_data() -> dict:
    """Load grocery data from JSON file, creating defaults if missing."""
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to read grocery data, starting fresh: %s", e)
    return {"lists": {"Grocery": {"items": []}}}


def _save_data(data: dict) -> None:
    """Save grocery data to JSON file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=2))


def _fuzzy_match(name: str, items: list[dict]) -> dict | None:
    """Find an item by name (case-insensitive substring match)."""
    lower = name.strip().lower()
    # Exact match first
    for item in items:
        if item["name"].lower() == lower:
            return item
    # Substring match
    for item in items:
        if lower in item["name"].lower() or item["name"].lower() in lower:
            return item
    return None


async def _view_grocery_list(list_name: str = "", show_checked: bool = False) -> str:
    """View a grocery list, optionally including checked items."""
    data = _load_data()
    lists = data.get("lists", {})

    if not lists:
        return "No grocery lists exist yet. Use update_grocery_list to add items."

    # If no list name given and multiple lists exist, show overview
    if not list_name:
        if len(lists) == 1:
            list_name = next(iter(lists))
        else:
            lines = ["Grocery Lists:\n"]
            for name, lst in lists.items():
                items = lst.get("items", [])
                unchecked = sum(1 for i in items if not i.get("checked"))
                total = len(items)
                lines.append(f"  - {name}: {unchecked} unchecked / {total} total")
            return "\n".join(lines)

    if list_name not in lists:
        available = ", ".join(lists.keys())
        return f"List '{list_name}' not found. Available lists: {available}"

    items = lists[list_name].get("items", [])
    if not items:
        return f"{list_name} list is empty."

    if not show_checked:
        items = [i for i in items if not i.get("checked")]
        if not items:
            return f"All items on {list_name} are checked off! Use show_checked=true to see them."

    # Group by category
    by_cat: dict[str, list[dict]] = {}
    for item in items:
        cat = item.get("category", "other")
        by_cat.setdefault(cat, []).append(item)

    lines = [f"{list_name}:\n"]
    for cat in sorted(by_cat.keys()):
        lines.append(f"  [{cat.title()}]")
        for item in by_cat[cat]:
            check = "[x]" if item.get("checked") else "[ ]"
            qty = f" ({item['quantity']})" if item.get("quantity") else ""
            lines.append(f"    {check} {item['name']}{qty}")
        lines.append("")

    total = len(items)
    checked = sum(1 for i in items if i.get("checked"))
    lines.append(f"{total} items ({checked} checked)")
    return "\n".join(lines).strip()


async def _update_grocery_list(
    action: str,
    list_name: str = "Grocery",
    items: str = "",
    quantities: str = "",
) -> str:
    """Add, check, uncheck, remove, or clear checked items."""
    data = _load_data()
    lists = data.get("lists", {})

    # Auto-create list if adding to a non-existent list
    if list_name not in lists:
        if action == "add":
            lists[list_name] = {"items": []}
            data["lists"] = lists
        else:
            available = ", ".join(lists.keys()) or "(none)"
            return f"List '{list_name}' not found. Available: {available}"

    lst = lists[list_name]["items"]

    if action == "add":
        if not items:
            return "No items specified. Provide a comma-separated list of items."
        names = [n.strip() for n in items.split(",") if n.strip()]
        qtys = [q.strip() for q in quantities.split(",")] if quantities else []

        added = []
        for i, name in enumerate(names):
            qty = qtys[i] if i < len(qtys) else ""
            category = _detect_category(name)
            lst.append({
                "name": name,
                "category": category,
                "quantity": qty,
                "checked": False,
                "added": date.today().isoformat(),
            })
            cat_label = f" [{category}]" if category != "other" else ""
            qty_label = f" ({qty})" if qty else ""
            added.append(f"{name}{cat_label}{qty_label}")

        _save_data(data)
        return f"Added to {list_name}: {', '.join(added)}"

    elif action == "check":
        if not items:
            return "No items specified to check off."
        names = [n.strip() for n in items.split(",") if n.strip()]
        results = []
        for name in names:
            match = _fuzzy_match(name, lst)
            if match:
                match["checked"] = True
                results.append(f"Checked: {match['name']}")
            else:
                results.append(f"Not found: {name}")
        _save_data(data)
        return "\n".join(results)

    elif action == "uncheck":
        if not items:
            return "No items specified to uncheck."
        names = [n.strip() for n in items.split(",") if n.strip()]
        results = []
        for name in names:
            match = _fuzzy_match(name, lst)
            if match:
                match["checked"] = False
                results.append(f"Unchecked: {match['name']}")
            else:
                results.append(f"Not found: {name}")
        _save_data(data)
        return "\n".join(results)

    elif action == "remove":
        if not items:
            return "No items specified to remove."
        names = [n.strip() for n in items.split(",") if n.strip()]
        results = []
        for name in names:
            match = _fuzzy_match(name, lst)
            if match:
                lst.remove(match)
                results.append(f"Removed: {match['name']}")
            else:
                results.append(f"Not found: {name}")
        _save_data(data)
        return "\n".join(results)

    elif action == "clear_checked":
        before = len(lst)
        lists[list_name]["items"] = [i for i in lst if not i.get("checked")]
        after = len(lists[list_name]["items"])
        removed = before - after
        _save_data(data)
        return f"Cleared {removed} checked item(s) from {list_name}."

    else:
        return f"Unknown action: {action}. Use add, check, uncheck, remove, or clear_checked."


async def _manage_grocery_lists(
    action: str,
    list_name: str = "",
    new_name: str = "",
) -> str:
    """Create, delete, or rename grocery lists."""
    data = _load_data()
    lists = data.get("lists", {})

    if action == "create":
        if not list_name:
            return "Provide a list_name to create."
        if list_name in lists:
            return f"List '{list_name}' already exists."
        lists[list_name] = {"items": []}
        _save_data(data)
        return f"Created list: {list_name}"

    elif action == "delete":
        if not list_name:
            return "Provide a list_name to delete."
        if list_name not in lists:
            return f"List '{list_name}' not found."
        count = len(lists[list_name].get("items", []))
        del lists[list_name]
        _save_data(data)
        return f"Deleted list '{list_name}' ({count} items removed)."

    elif action == "rename":
        if not list_name or not new_name:
            return "Provide both list_name and new_name to rename."
        if list_name not in lists:
            return f"List '{list_name}' not found."
        if new_name in lists:
            return f"List '{new_name}' already exists."
        lists[new_name] = lists.pop(list_name)
        _save_data(data)
        return f"Renamed '{list_name}' → '{new_name}'."

    else:
        return f"Unknown action: {action}. Use create, delete, or rename."


def register_all():
    """Register grocery list tools."""
    register(ToolDefinition(
        name="view_grocery_list",
        description=(
            "View a grocery list. Shows items grouped by category with quantities "
            "and check status. If no list_name given and multiple lists exist, "
            "shows an overview of all lists."
        ),
        parameters={
            "type": "object",
            "properties": {
                "list_name": {
                    "type": "string",
                    "description": "Name of the list to view (default: 'Grocery'). Leave empty to see all lists.",
                },
                "show_checked": {
                    "type": "boolean",
                    "description": "Include checked-off items (default: false)",
                },
            },
            "required": [],
        },
        handler=_view_grocery_list,
        permission="none",
    ))

    register(ToolDefinition(
        name="update_grocery_list",
        description=(
            "Add, check, uncheck, remove items, or clear checked items from a grocery list. "
            "Items are auto-categorized (produce, dairy, meat, etc.). "
            "Use comma-separated names for multiple items."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "check", "uncheck", "remove", "clear_checked"],
                    "description": "Action to perform",
                },
                "list_name": {
                    "type": "string",
                    "description": "Target list (default: 'Grocery'). Auto-created on 'add' if missing.",
                },
                "items": {
                    "type": "string",
                    "description": "Comma-separated item names (for add/check/uncheck/remove)",
                },
                "quantities": {
                    "type": "string",
                    "description": "Comma-separated quantities matching items (e.g. '2 gallons,1 dozen')",
                },
            },
            "required": ["action"],
        },
        handler=_update_grocery_list,
        permission="write",
    ))

    register(ToolDefinition(
        name="manage_grocery_lists",
        description="Create, delete, or rename grocery lists.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "delete", "rename"],
                    "description": "Action to perform",
                },
                "list_name": {
                    "type": "string",
                    "description": "Name of the list",
                },
                "new_name": {
                    "type": "string",
                    "description": "New name (for rename action)",
                },
            },
            "required": ["action", "list_name"],
        },
        handler=_manage_grocery_lists,
        permission="write",
    ))
