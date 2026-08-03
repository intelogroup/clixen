"""
Google Tasks tools — list, create, complete, and delete tasks.

Requires tasks scope — re-auth if not yet granted:
    python tools/google_auth.py --auth
"""

from tools.google_auth import execute_with_google_auth_retry, get_service

# ---------------------------------------------------------------------------
# Date parsing helper — handles ISO formats + common relative expressions
# ---------------------------------------------------------------------------

_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _parse_due_date(text: str):
    """
    Parse a due date string into a datetime object.
    Uses dateparser for natural language + ISO format handling (200+ languages),
    with regex fallback for day-name patterns that dateparser sometimes misses.
    Returns datetime or None.
    """
    import re
    import dateparser
    from datetime import datetime, timedelta

    text = text.strip()
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Try strict ISO first (most reliable)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass

    # dateparser handles most formats
    result = dateparser.parse(text, settings={'PREFER_DATES_FROM': 'future'})
    if result:
        return result

    # Fallback for day-name patterns dateparser sometimes misses
    low = text.lower().strip()
    m = re.search(r"(\w+day)", low)
    if m:
        target = m.group(1)
        if target in _DAYS:
            target_idx = _DAYS.index(target)
            days_ahead = (target_idx - today.weekday() + 7) % 7
            if days_ahead == 0:
                days_ahead = 7
            return today + timedelta(days=days_ahead)

    return None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

LIST_TASKS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_tasks",
        "description": (
            "List tasks from Google Tasks. Returns all incomplete tasks by default. "
            "Call this first whenever the user asks about tasks, urgency, counts, or reminders "
            "— always fetch the real list before reasoning or answering."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_list": {
                    "type": "string",
                    "description": (
                        "Name of the task list to read (default: 'My Tasks'). "
                        "Use 'all' to list tasks across all lists."
                    ),
                    "default": "My Tasks",
                },
                "include_completed": {
                    "type": "boolean",
                    "description": "Include completed tasks (default false)",
                    "default": False,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max tasks to return (default 20)",
                    "default": 20,
                },
            },
            "required": [],
        },
    },
}

CREATE_TASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_task",
        "description": (
            "Add a new task to Google Tasks. "
            "Use for to-do items, action points, or anything to track and check off later."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Task title",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional task notes or details",
                    "default": "",
                },
                "due_iso": {
                    "type": "string",
                    "description": (
                        "Optional due date in ISO 8601 format. "
                        "Infer from relative expressions ('tomorrow', 'next Friday', 'in 3 days') "
                        "using get_current_time if needed — do NOT ask the user for the date. "
                        "Example: 2026-04-15 or 2026-04-15T10:00:00"
                    ),
                    "default": "",
                },
                "task_list": {
                    "type": "string",
                    "description": "Task list to add to (default: 'My Tasks')",
                    "default": "My Tasks",
                },
            },
            "required": ["title"],
        },
    },
}

COMPLETE_TASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "complete_task",
        "description": "Mark a Google Task as completed by its task ID (from list_tasks).",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID from list_tasks",
                },
                "task_list": {
                    "type": "string",
                    "description": "Task list the task belongs to (default: 'My Tasks')",
                    "default": "My Tasks",
                },
            },
            "required": ["task_id"],
        },
    },
}

DELETE_TASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_task",
        "description": "Delete a Google Task permanently by its task ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID from list_tasks",
                },
                "task_list": {
                    "type": "string",
                    "description": "Task list the task belongs to (default: 'My Tasks')",
                    "default": "My Tasks",
                },
            },
            "required": ["task_id"],
        },
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_task_list_id(svc, name: str) -> str | None:
    """Resolve a task list name → ID. Returns None if not found."""
    result = execute_with_google_auth_retry(
        lambda: get_service("tasks", "v1").tasklists().list(maxResults=20).execute()
    )
    lists = result.get("items", [])
    for tl in lists:
        if tl["title"].lower() == name.lower():
            return tl["id"]
    # If only one list exists, return it regardless of name
    if len(lists) == 1:
        return lists[0]["id"]
    return None


def _list_all_task_lists(svc) -> list[dict]:
    return execute_with_google_auth_retry(
        lambda: get_service("tasks", "v1").tasklists().list(maxResults=20).execute()
    ).get("items", [])


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


def list_tasks(
    task_list: str = "My Tasks",
    include_completed: bool = False,
    max_results: int = 20,
) -> str:
    svc = get_service("tasks", "v1")
    if svc is None:
        return "[tasks error] Not authenticated. Run: python tools/google_auth.py --auth"
    try:
        if task_list.lower() == "all":
            all_lists = _list_all_task_lists(svc)
        else:
            tid = _get_task_list_id(svc, task_list)
            if not tid:
                return f"[tasks error] Task list '{task_list}' not found."
            all_lists = [{"id": tid, "title": task_list}]

        lines = []
        for tl in all_lists:
            kwargs = dict(
                tasklist=tl["id"],
                maxResults=min(max_results, 100),
                showCompleted=include_completed,
                showHidden=include_completed,
            )
            result = execute_with_google_auth_retry(
                lambda call_kwargs=dict(kwargs): get_service("tasks", "v1")
                .tasks()
                .list(**call_kwargs)
                .execute()
            )
            tasks = result.get("items", [])
            if not tasks:
                lines.append(f"[{tl['title']}] No tasks.")
                continue
            lines.append(f"[{tl['title']}]")
            for t in tasks:
                status = "x" if t.get("status") == "completed" else " "
                due = ""
                if t.get("due"):
                    from datetime import datetime
                    try:
                        due = " (due " + datetime.fromisoformat(t["due"].replace("Z", "+00:00")).strftime("%b %d") + ")"
                    except Exception:
                        due = f" (due {t['due']})"
                notes = f"\n    {t['notes'][:80]}" if t.get("notes") else ""
                lines.append(f"  [{status}] {t['title']}{due} — ID: {t['id']}{notes}")

        return "\n".join(lines) if lines else "No tasks found."
    except Exception as e:
        return f"[tasks error] list_tasks failed: {e}"


def create_task(
    title: str,
    notes: str = "",
    due_iso: str = "",
    task_list: str = "My Tasks",
) -> str:
    svc = get_service("tasks", "v1")
    if svc is None:
        return "[tasks error] Not authenticated. Run: python tools/google_auth.py --auth"
    try:
        tid = _get_task_list_id(svc, task_list)
        if not tid:
            return f"[tasks error] Task list '{task_list}' not found."

        body: dict = {"title": title, "status": "needsAction"}
        if notes:
            body["notes"] = notes
        if due_iso:
            dt = _parse_due_date(due_iso)
            if dt is None:
                return f"[tasks error] Could not parse due date '{due_iso}'. Use ISO format like 2026-04-09."
            body["due"] = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        created = execute_with_google_auth_retry(
            lambda: get_service("tasks", "v1")
            .tasks()
            .insert(tasklist=tid, body=body)
            .execute()
        )
        due_str = f" (due {due_iso})" if due_iso else ""
        return f"Task created: '{title}'{due_str} — ID: {created['id']}"
    except Exception as e:
        return f"[tasks error] create_task failed: {e}"


def complete_task(task_id: str, task_list: str = "My Tasks") -> str:
    svc = get_service("tasks", "v1")
    if svc is None:
        return "[tasks error] Not authenticated. Run: python tools/google_auth.py --auth"
    try:
        tid = _get_task_list_id(svc, task_list)
        if not tid:
            return f"[tasks error] Task list '{task_list}' not found."

        task = execute_with_google_auth_retry(
            lambda: get_service("tasks", "v1")
            .tasks()
            .get(tasklist=tid, task=task_id)
            .execute()
        )
        task["status"] = "completed"
        execute_with_google_auth_retry(
            lambda: get_service("tasks", "v1")
            .tasks()
            .update(tasklist=tid, task=task_id, body=task)
            .execute()
        )
        return f"Task '{task.get('title', task_id)}' marked as completed."
    except Exception as e:
        return f"[tasks error] complete_task failed: {e}"


def delete_task(task_id: str, task_list: str = "My Tasks") -> str:
    svc = get_service("tasks", "v1")
    if svc is None:
        return "[tasks error] Not authenticated. Run: python tools/google_auth.py --auth"
    try:
        tid = _get_task_list_id(svc, task_list)
        if not tid:
            return f"[tasks error] Task list '{task_list}' not found."

        execute_with_google_auth_retry(
            lambda: get_service("tasks", "v1")
            .tasks()
            .delete(tasklist=tid, task=task_id)
            .execute()
        )
        return f"Task {task_id} deleted."
    except Exception as e:
        return f"[tasks error] delete_task failed: {e}"


def get_pending_tasks_raw_status() -> tuple[list[dict], str]:
    """Return all non-completed tasks plus an error string when unavailable.

    Each dict has keys: id, title, due (ISO str or ""), notes (str),
    task_list (str).  Max 50 tasks total.
    """
    svc = get_service("tasks", "v1")
    if svc is None:
        return [], "Not authenticated. Run: python tools/google_auth.py --auth"
    try:
        all_lists = _list_all_task_lists(svc)
        out: list[dict] = []
        for tl in all_lists:
            if len(out) >= 50:
                break
            remaining = 50 - len(out)
            result = execute_with_google_auth_retry(
                lambda tl_id=tl["id"], max_count=min(remaining, 100): get_service("tasks", "v1")
                .tasks()
                .list(
                    tasklist=tl_id,
                    maxResults=max_count,
                    showCompleted=False,
                    showHidden=False,
                )
                .execute()
            )
            for t in result.get("items", []):
                if t.get("status") == "completed":
                    continue
                raw_due = t.get("due", "")
                # Normalise Google's RFC-3339 "Z" suffix to proper ISO
                if raw_due and raw_due.endswith("Z"):
                    raw_due = raw_due[:-1] + "+00:00"
                out.append({
                    "id": t.get("id", ""),
                    "title": t.get("title", ""),
                    "due": raw_due,
                    "notes": t.get("notes", ""),
                    "task_list": tl.get("title", ""),
                })
                if len(out) >= 50:
                    break
        return out, ""
    except Exception as exc:
        return [], str(exc)


def get_pending_tasks_raw() -> list[dict]:
    """Return all non-completed tasks across every task list as a list of dicts.

    Returns [] on auth failure or API error.
    """
    tasks, _ = get_pending_tasks_raw_status()
    return tasks
