#!/usr/bin/env python3
"""
Crash watchdog: tails the service logs for native-crash / process-exit
signatures and pushes a Telegram alert immediately, instead of a crash sitting
unnoticed until someone manually greps logs (as happened 2026-07-07).

Runs as its own supervised process (com.clixen.crash_watchdog.plist) so
it keeps working even if every other service it's watching goes down.
"""
from __future__ import annotations

import hashlib
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from tools.telegram_send import send_telegram
from store import event_log

_HARNESS_DIR = Path(__file__).parent
_WATCHED_LOGS = [
    _HARNESS_DIR / "messaging_stderr.log",
    _HARNESS_DIR / "messaging_stdout.log",
    _HARNESS_DIR / "task_worker.log",
    _HARNESS_DIR / "chat_ui.log",
    _HARNESS_DIR / "core_stderr.log",
    _HARNESS_DIR / "whatsapp_bot.log",
]

_CRASH_RE = re.compile(
    r"Abort trap|Segmentation fault|Bus error|Illegal instruction|"
    r"Process \d+ exited|Traceback \(most recent call last\)"
)

_POLL_SECS = 5
_DEBOUNCE_SECS = 60  # don't re-alert on the same crash more than once a minute

# ponytail: log_config.py's setup_logging() wires every module's file handler
# onto the shared root logger, so in core.py's single multi-threaded process
# one log record (e.g. from `harness`) fans out into every registered log
# file, not just the one the producing thread owns — confirmed live, the same
# crash appeared in task_worker.log/chat_ui.log/core_stderr.log in the same
# poll cycle. Debouncing by file path meant one real crash produced 3-4
# separate Telegram alerts. Dedupe by crash-content hash instead — fixes the
# alert-layer symptom without touching the higher-risk root-logger fan-out
# design. Add per-file debounce back too if a genuinely new crash type starts
# hiding behind an old one's hash within the window.


def _crash_key(line: str, key_text: str) -> str:
    normalized = re.sub(r"\s+", " ", f"{line} {key_text[:300]}".strip())
    return hashlib.sha256(normalized.encode()).hexdigest()


def check_new_text(path: Path, new_text: str, last_alert: dict[str, float], now: float | None = None) -> bool:
    """Check one file's newly-appended text for a crash signature and alert
    if it's not a dedupe-window repeat of an already-alerted crash. Mutates
    `last_alert` in place. Returns True if a Telegram alert was sent.
    Extracted from watch()'s loop body so tests can drive it directly instead
    of re-implementing the same offset/match/dedupe logic inline.
    """
    match = _CRASH_RE.search(new_text)
    if not match:
        return False

    line = match.group(0)
    snippet = new_text[max(0, match.start() - 100):match.start() + 200].strip()
    # Dedupe key uses only text AFTER the match (the actual exception body) —
    # the pre-match context in `snippet` above can include a log-line
    # timestamp, which differs by a second or two between files even for the
    # same fanned-out crash record and would otherwise break the hash match.
    key_text = new_text[match.end():match.end() + 300]
    key = _crash_key(line, key_text)

    now = time.time() if now is None else now
    if now - last_alert.get(key, 0) < _DEBOUNCE_SECS:
        print(f"[watchdog] crash signature in {path.name}: {line} (deduped, already alerted)", flush=True)
        return False
    last_alert[key] = now

    print(f"[watchdog] crash signature in {path.name}: {line}", flush=True)
    send_telegram(f"Crash detected in {path.name}: {line}\n\n{snippet[:500]}")
    return True


def check_new_events(events: list[dict], last_alert: dict[str, float], now: float | None = None) -> int:
    """Structured counterpart to check_new_text: reads job/workflow failure
    events written by jobs/worker.py at the point of failure, instead of
    relying on the failure's traceback text matching _CRASH_RE. Catches any
    failure shape immediately (no regex to miss), independent of whether the
    module doing the failing bothered to log anything at all. Returns alert count.
    """
    now = time.time() if now is None else now
    sent = 0
    for ev in events:
        if ev["status"] != "failed":
            continue
        detail = ev["detail"]
        ident = detail.get("task_name") or detail.get("automation_id") or detail.get("job_id") or detail.get("workflow_id")
        line = f"{ev['kind']} failed: {ident}"
        error = detail.get("error", "")
        key = _crash_key(line, error)
        if now - last_alert.get(key, 0) < _DEBOUNCE_SECS:
            print(f"[watchdog] structured failure: {line} (deduped, already alerted)", flush=True)
            continue
        last_alert[key] = now
        print(f"[watchdog] structured failure: {line} {error[:200]}", flush=True)
        send_telegram(f"{line}\n\n{error[:500]}")
        sent += 1
    return sent


def watch() -> None:
    offsets: dict[Path, int] = {}
    last_alert: dict[str, float] = {}

    # Start at end-of-file — only alert on new lines from here on.
    for path in _WATCHED_LOGS:
        offsets[path] = path.stat().st_size if path.exists() else 0

    last_event_id = event_log.latest_id()

    print(f"[watchdog] watching {len(_WATCHED_LOGS)} log files + structured event log...", flush=True)

    while True:
        for path in _WATCHED_LOGS:
            if not path.exists():
                continue
            size = path.stat().st_size
            if size < offsets[path]:
                offsets[path] = 0  # log was rotated/truncated
            if size == offsets[path]:
                continue

            with path.open("r", errors="replace") as f:
                f.seek(offsets[path])
                new_text = f.read()
            offsets[path] = size

            check_new_text(path, new_text, last_alert)

        new_events = event_log.events_since(last_event_id)
        if new_events:
            last_event_id = new_events[-1]["id"]
            check_new_events(new_events, last_alert)

        time.sleep(_POLL_SECS)


if __name__ == "__main__":
    watch()
