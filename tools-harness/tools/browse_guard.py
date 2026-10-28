"""Runtime guards for browse runs, enforced in code instead of asked for in the prompt.

Found by tracing real orb runs (Amazon, Walmart):
- a read tool navigated the page, and nothing noticed;
- the agent repeated one call (web.read x4, open_url x3) instead of changing approach;
- `act` is classed WRITE whatever it clicks, so "Add to cart" was as free as "Close".

Hooked from tools.registry.execute_tool, so every caller (orb, orchestrator, Telegram) gets it.
"""
from __future__ import annotations

import json
import os
from collections import Counter

# Tools that read the page but must never change what page it is.
READ_WATCH = frozenset({"observe", "read_page_text", "get_screen_context", "find_on_page", "page_probe"})

# Tools the agent re-calls verbatim when stuck. observe/act repeat legitimately between actions, so
# they are not counted.
LOOP_WATCH = frozenset({"open_url", "app_task", "find_on_page", "read_page_text", "ask_web_search"})
LOOP_LIMIT = 3

# Explicit phrases, not a pattern: a click on a label containing one of these spends money or commits
# an order. Lowercase substring match on the visible label.
RISKY_LABELS = (
    "add to cart", "add to basket", "add to bag", "buy now", "place your order", "place order",
    "proceed to checkout", "checkout", "pay now", "complete purchase", "confirm purchase",
)

_MAX_RUNS = 64
_calls: dict[str, Counter] = {}


def _run_id() -> str:
    try:
        from log_config import CURRENT_RUN_ID

        return CURRENT_RUN_ID.get() or "-"
    except Exception:  # noqa: BLE001 - a guard must never break a tool call
        return "-"


def risky_label(label: str) -> str | None:
    """The risky phrase `label` contains, or None."""
    low = (label or "").lower()
    return next((p for p in RISKY_LABELS if p in low), None)


def target_label(name: str, arguments: dict) -> str:
    """What a click would press: click_text's text, or the label an act target id resolves to."""
    if name == "click_text":
        return str(arguments.get("text", ""))
    target = str(arguments.get("target", ""))
    if target.startswith("e") and "." in target:
        try:
            from tools import screen_v2

            rev = int(target.split(".", 1)[1])
            return screen_v2._state["hist"].get(rev, {}).get(target, {}).get("fp", ("", ""))[1]
        except Exception:  # noqa: BLE001
            return ""
    return target  # act also takes a free-text label


def pre_check(name: str, arguments: dict) -> str | None:
    """A refusal string when this call must not run, else None."""
    if name in ("act", "click_text") and os.environ.get("CLIXEN_ALLOW_PURCHASE_CLICKS", "") in ("", "0", "false"):
        label = target_label(name, arguments)
        if (phrase := risky_label(label)):
            return (
                f"[blocked] '{label[:80]}' would {phrase} — a purchase step. It was NOT clicked. "
                "Do not retry or look for another route: tell the user what you found and ask them to confirm."
            )
    if name in LOOP_WATCH:
        key = json.dumps([name, arguments], sort_keys=True, default=str)
        runs = _calls.setdefault(_run_id(), Counter())
        if len(_calls) > _MAX_RUNS:
            _calls.pop(next(iter(_calls)))
        runs[key] += 1
        if runs[key] >= LOOP_LIMIT:
            return (
                f"[blocked] {name} was already called {runs[key] - 1} times with these exact arguments. "
                "Repeating it will not help: change the query or approach, or answer with what you have."
            )
    return None


def url_before(name: str, arguments: dict) -> str | None:
    """The front page URL before a read tool runs (None when the tool is not watched)."""
    if name not in READ_WATCH:
        return None
    try:
        from tools import ax_context

        return ax_context.front_url(str(arguments.get("app", "")))
    except Exception:  # noqa: BLE001
        return None


def after(name: str, arguments: dict, before: str | None, result: str) -> str:
    """Append a warning when a read-only tool left the page on a different URL."""
    if before is None:
        return result
    try:
        from tools import ax_context

        now = ax_context.front_url(str(arguments.get("app", "")))
    except Exception:  # noqa: BLE001
        return result
    if now and before and now != before:
        return (
            f"{result}\n[warning] {name} is read-only but the page changed: {before[:100]} -> {now[:100]}. "
            "Observe again before acting, and do not trust earlier element ids."
        )
    return result


def reset() -> None:
    _calls.clear()
