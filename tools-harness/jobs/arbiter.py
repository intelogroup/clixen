"""Global alert dedup + batching + critical-word priority gate.

In-memory cooldown (acceptable — restart clears it, avoids 2AM dupes within
a single sleep session). 60-second batch window merges concurrent alerts
into one digest. simple critical-word regex flags priority (no ML).
"""
import re
import threading
import time

_COOLDOWN_SECS = 300
_BATCH_WINDOW = 60
_CRITICAL_WORDS = re.compile(
    r"\b(down|error|exception|timeout|unauthorized|crash|fail(ed|ure)?|critical|"
    r"emergency|unreachable|unresponsive|dead|killed|segfault|abort)\b",
    re.IGNORECASE,
)

_last_alert: dict[str, float] = {}
_pending: list[dict] = []
_lock = threading.Lock()


def _sig(source: str, message: str) -> str:
    return f"{source}_{hash(message) & 0xffffffff:08x}"


def is_critical(message: str) -> bool:
    return bool(_CRITICAL_WORDS.search(message))


def _should_send(signature: str) -> bool:
    now = time.time()
    last = _last_alert.get(signature, 0.0)
    if now - last < _COOLDOWN_SECS:
        return False
    _last_alert[signature] = now
    return True


def _register_sig(signature: str) -> None:
    _last_alert[signature] = time.time()


def enqueue(source: str, message: str) -> None:
    """Queue an alert for the next flush cycle."""
    with _lock:
        _pending.append({"source": source, "message": message, "ts": time.time()})


def flush() -> list[str]:
    """Flush pending alerts into deduped, batched digest messages.

    Returns list of message strings ready for send_telegram (or whatever
    output channel). Call once per worker poll cycle, after all workflows
    have been dispatched.
    """
    with _lock:
        if not _pending:
            return []
        batch = _pending[:]
        _pending.clear()

    now = time.time()
    messages: list[str] = []

    inside = [a for a in batch if now - a["ts"] < _BATCH_WINDOW]
    outside = [a for a in batch if now - a["ts"] >= _BATCH_WINDOW]

    if inside:
        _add_digest(inside, messages)

    for a in outside:
        s = _sig(a["source"], a["message"])
        if _should_send(s):
            messages.append(a["message"])
        _register_sig(s)

    return messages


def _add_digest(alerts: list[dict], out: list[str]) -> None:
    if len(alerts) == 1:
        a = alerts[0]
        s = _sig(a["source"], a["message"])
        if _should_send(s):
            out.append(a["message"])
        _register_sig(s)
        return

    critical = [a for a in alerts if is_critical(a["message"])]
    info = [a for a in alerts if not is_critical(a["message"])]
    parts = []
    if critical:
        parts.append(f"{len(critical)} critical — " + "; ".join(a["message"][:120] for a in critical))
    if info:
        parts.append(f"{len(info)} info — " + "; ".join(a["message"][:120] for a in info))
    digest = " | ".join(parts)
    s = f"digest_{hash(digest) & 0xffffffff:08x}"
    if _should_send(s):
        out.append(digest)
        # Register individual sigs so subsequent same-item enqueues hit cooldown
        for a in alerts:
            _register_sig(_sig(a["source"], a["message"]))
