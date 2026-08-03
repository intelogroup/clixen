"""
In-app notification queue.

Notifications are stored in memory for the lifetime of the server process.
Producers (pipeline jobs, health checks) call push(); the UI polls /notifications.

Levels:
    "info"    — informational, no action required
    "warning" — something degraded but non-fatal
    "error"   — something failed that the user should act on
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Literal

Level = Literal["info", "warning", "error"]

_lock = threading.Lock()
_store: list[dict] = []
_MAX = 100  # keep at most N notifications in memory


def push(
    message: str,
    level: Level = "info",
    source: str = "",
    action_label: str = "",
    action_url: str = "",
    action_type: str = "",
    action_payload: dict | None = None,
) -> dict:
    """
    Add a notification.

    Args:
        message:      Human-readable description.
        level:        "info" | "warning" | "error"
        source:       Origin label, e.g. "inbox_monitor", "ollama_health"
        action_label: Optional CTA button text shown in the UI.
        action_url:   URL or JS handler the CTA triggers.
        action_type:  Optional action type identifier for structured UI actions.
        action_payload: Optional structured action payload.

    Returns:
        The notification dict (includes generated id and timestamp).
    """
    notif = {
        "id": uuid.uuid4().hex[:12],
        "ts": int(time.time()),
        "level": level,
        "source": source,
        "message": message,
        "action_label": action_label,
        "action_url": action_url,
        "action_type": action_type,
        "action_payload": action_payload or {},
        "read": False,
    }
    with _lock:
        _store.append(notif)
        # Trim oldest beyond cap
        if len(_store) > _MAX:
            del _store[: len(_store) - _MAX]
    return notif


def list_all(unread_only: bool = False) -> list[dict]:
    with _lock:
        items = list(_store)
    if unread_only:
        items = [n for n in items if not n["read"]]
    return sorted(items, key=lambda n: n["ts"], reverse=True)


def mark_read(notif_id: str) -> bool:
    with _lock:
        for n in _store:
            if n["id"] == notif_id:
                n["read"] = True
                return True
    return False


def dismiss(notif_id: str) -> bool:
    with _lock:
        for i, n in enumerate(_store):
            if n["id"] == notif_id:
                del _store[i]
                return True
    return False


def dismiss_all() -> int:
    with _lock:
        count = len(_store)
        _store.clear()
    return count


def unread_count() -> int:
    with _lock:
        return sum(1 for n in _store if not n["read"])
