"""
Rolling per-model call outcomes (success/error/latency), in-process only.

core.py runs chat_ui/telegram_bot/worker as threads in one process — no
cross-process state needed, so a plain dict + lock beats a sqlite file here.
Feeds model_for_intent()'s fallback-default path only; pinned intents in
_INTENT_MODEL_OVERRIDES are untouched.
"""
import threading
import time
from collections import deque

_WINDOW = 50
_lock = threading.Lock()
_stats: dict[str, deque] = {}

_DECISIONS_MAX = 100
_decisions: deque = deque(maxlen=_DECISIONS_MAX)


def record(model: str, ok: bool, latency_ms: float = 0.0) -> None:
    with _lock:
        dq = _stats.setdefault(model, deque(maxlen=_WINDOW))
        dq.append((ok, latency_ms))


def success_rate(model: str) -> float | None:
    """None means no data yet — caller should treat that as "don't know", not "bad"."""
    with _lock:
        dq = _stats.get(model)
        if not dq:
            return None
        return sum(1 for ok, _ in dq if ok) / len(dq)


def is_unhealthy(model: str, min_samples: int = 5, threshold: float = 0.5) -> bool:
    with _lock:
        dq = _stats.get(model)
        if not dq or len(dq) < min_samples:
            return False
        return (sum(1 for ok, _ in dq if ok) / len(dq)) < threshold


def record_decision(intent: str, picked: str, reason: str) -> None:
    """Log why model_for_intent() picked this model — 'pinned'/'normal'/
    'budget_blocked'/'unhealthy_fallback'. Outcome-only success_rate() above
    can't answer "why did this land on local" — this fills that gap for the
    routing-decision dashboard panel."""
    with _lock:
        _decisions.append({
            "ts": time.time(), "intent": intent, "picked": picked, "reason": reason,
        })


def recent_decisions(limit: int = 50) -> list[dict]:
    with _lock:
        return list(_decisions)[-limit:]
