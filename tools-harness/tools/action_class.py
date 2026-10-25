"""What a tool call *does*, declared — so approval is machinery, not prose.

Ported from the cloud repo's capability contract (`capabilities/adapter/spec.py`):
every capability declares an ActionClass up front, and the class decides approval
behaviour. Their three: READ / WRITE / MONEY. Locally the same split matters for a
different reason: the orb's rule 6 ("irreversible actions need the user to have
asked for exactly that") is *prompt text* today, so it holds only as well as the
model follows it. A declarative class lets the runtime enforce it, and lets the
audit trail say what kind of thing happened rather than only which tool ran.

Two properties copied deliberately:
- a declared class, never derived from the model's intent;
- READ executes immediately, WRITE/MONEY is a *decision point*, not a hint.
"""

from __future__ import annotations

READ = "read"
WRITE = "write"
MONEY = "money"

# Explicit classes for the tools where getting this wrong is expensive. Everything
# else falls through to the name heuristics below, which are deliberately
# conservative: an unclassified name is treated as a write only if its verb says so.
_EXPLICIT: dict[str, str] = {
    # reads
    "observe": READ, "get_screen_context": READ, "read_page_text": READ, "tabs": READ,
    "get_latest_email": READ, "list_emails": READ, "read_email": READ,
    "list_calendar_events": READ, "list_tasks": READ, "list_reminders": READ,
    "list_windows": READ, "system_status": READ, "get_current_time": READ,
    "workspace_list": READ, "workspace_read": READ, "take_screenshot": READ,
    "ocr_image": READ, "ask_web_search": READ, "ask_fetch_url": READ,
    "ask_research_agent": READ, "ask_browser_agent": READ, "wait_for": READ,
    "recall_about": READ, "search_sessions": READ, "facts_related_to": READ,
    # writes — they change something the user owns
    "act": WRITE, "click_text": WRITE, "type_text": WRITE,
    "open_url": WRITE, "open_app": WRITE, "window": WRITE, "menu": WRITE,
    "app": WRITE, "finder": WRITE, "dialog": WRITE, "system": WRITE,
    "app_task": WRITE, "workspace_write": WRITE, "workspace_delete": WRITE,
    "create_calendar_event": WRITE, "complete_task": WRITE, "remember": WRITE,
    "forget": WRITE, "link_facts": WRITE, "update_task_plan": WRITE,
    "bash_exec": WRITE, "write_file": WRITE, "edit_file": WRITE,
    "append_file": WRITE, "download_url": WRITE, "send_email": MONEY,
}

# A call may carry its real action in an argument (app_task's recipe name).
_ARG_RECIPES = {
    "gmail.send": WRITE,
}

_MONEY_HINTS = ("payment", "pay_", "transfer", "purchase", "buy_", "refund", "invoice", "spend")
_WRITE_HINTS = ("send", "write", "edit", "delete", "remove", "create", "set_", "move",
                "rename", "upload", "post", "append", "truncate", "install", "kill")


def of(tool_name: str) -> str:
    """ActionClass for a tool by name alone."""
    name = (tool_name or "").strip()
    if name in _EXPLICIT:
        return _EXPLICIT[name]
    low = name.lower()
    if any(h in low for h in _MONEY_HINTS):
        return MONEY
    if any(low.startswith(h) or f"_{h}" in low for h in _WRITE_HINTS):
        return WRITE
    return READ


def of_call(tool_name: str, arguments: dict | None = None) -> str:
    """ActionClass for this specific call — recipes can be stricter than their tool."""
    base = of(tool_name)
    if tool_name == "app_task":
        recipe = str((arguments or {}).get("name", "")).strip().lower()
        return _ARG_RECIPES.get(recipe, base)
    return base


def requires_approval(tool_name: str, arguments: dict | None = None) -> bool:
    """MONEY always needs a human; WRITE needs one only when approvals are enabled.

    `CLIXEN_APPROVALS=1` turns the gate on. It is off by default because the orb has
    no approval UI yet: parking a call would leave it "[awaiting confirmation]"
    forever, which is worse than the prompt rule it would replace. Classification
    and logging still happen, so enabling it later is a switch, not a rewrite.
    """
    import os

    cls = of_call(tool_name, arguments)
    if cls == MONEY:
        return True
    return cls == WRITE and os.environ.get("CLIXEN_APPROVALS", "") not in ("", "0", "false")


def audit_fields(tool_name: str, arguments: dict | None = None) -> dict:
    """Fields every tool execution records, so the trail says what kind of act it was."""
    return {"action_class": of_call(tool_name, arguments)}


def summary(tool_names) -> str:
    """One line for a prompt or a review: how many of each class a toolset carries."""
    counts = {READ: 0, WRITE: 0, MONEY: 0}
    for name in tool_names:
        counts[of(name)] += 1
    return f"{counts[READ]} read, {counts[WRITE]} write, {counts[MONEY]} money"
