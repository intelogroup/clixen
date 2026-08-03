"""Self-contained ringback voice-call connector. One function call: place a real
SIP call (via the ringback MCP server's own docker image) and speak a line.

ringback-voice speaks MCP over stdio (see ~/Developer/clixen/tools-harness/ringback/).
Claude Code's own `ringback-voice` MCP registration is for the CLI session only —
this module lets the harness agent (chat_ui.py/harness.py) place a call from inside
its own tool loop, by acting as a one-shot MCP stdio client against the same
docker image, using the mcp python SDK already installed on the host.
"""
from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger("connector_ringback")

_RINGBACK_DIR = Path(__file__).resolve().parent.parent / "ringback"
_ENV_FILE = _RINGBACK_DIR / "voice.docker.env"

# Every caller (main orchestrator, notify_gate/science_scout, etc.) funnels through
# call_my_phone(), so the cooldown lives here once instead of per-caller — a burst of
# several qualifying findings in one job run would otherwise ring back-to-back.
_COOLDOWN_FILE = _RINGBACK_DIR / ".last_call_ts"
COOLDOWN_SECONDS = int(os.environ.get("RINGBACK_CALL_COOLDOWN_SECONDS", "900"))


def _check_and_update_cooldown() -> tuple[bool, float, float]:
    """Atomic (flock-guarded) check-and-set so two near-simultaneous callers can't
    both pass the check before either records a timestamp. Returns
    (allowed, seconds_since_last, previous_ts) — previous_ts lets a failed call
    restore the old timestamp instead of burning the cooldown window on nothing."""
    _COOLDOWN_FILE.touch(exist_ok=True)
    with open(_COOLDOWN_FILE, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            content = f.read().strip()
            last = float(content) if content else 0.0
            now = time.time()
            elapsed = now - last
            if elapsed < COOLDOWN_SECONDS:
                return False, elapsed, last
            f.seek(0)
            f.truncate()
            f.write(str(now))
            return True, elapsed, last
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _restore_cooldown(previous_ts: float) -> None:
    """Undo the reservation from _check_and_update_cooldown when the call itself
    failed — a docker/network flake shouldn't burn the cooldown window on nothing."""
    with open(_COOLDOWN_FILE, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.truncate()
            f.write(str(previous_ts))
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "call_my_phone",
            "description": (
                "Place a real SIP voice call (via the ringback Linphone account) and speak "
                "an opening line, then end the call. Use for reminders/alerts the user wants "
                "spoken over a call rather than texted. You may also call this proactively, "
                "unprompted, any time you judge something is important or urgent enough — "
                "use your judgment. Rings the Linphone app, not a real cellular number — "
                "the callee must have Linphone installed and online. "
                "If the result starts with '[ringback cooldown]', no call was placed — quote "
                "the exact 'callable again in Ns' number from the result verbatim, never "
                "estimate or round it, and never phrase the reply as if a call attempt "
                "happened (e.g. do not say 'the call is in cooldown, I couldn't ring you "
                "again' — say plainly that no call was made and when the next one can go out)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The line to speak when the call connects.",
                    },
                },
                "required": ["message"],
            },
        },
    },
]


async def _run_call(message: str) -> str:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k] = v
    env["VOICE_STUN"] = env.get("VOICE_STUN", "stun.l.google.com:19302")

    params = StdioServerParameters(
        command="docker",
        args=["run", "-i", "--rm", "--network", "host",
              "-e", f"VOICE_STUN={env['VOICE_STUN']}",
              "--env-file", str(_ENV_FILE), "ringback"],
        env=None,  # docker run reads VOICE_* from --env-file, not our env
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            start_result = await session.call_tool("call_start", {"opening_line": message, "play_only": True})
            end_result = await session.call_tool("call_end", {})
            start_text = "".join(getattr(c, "text", "") for c in start_result.content)
            end_text = "".join(getattr(c, "text", "") for c in end_result.content)
            return f"{start_text} {end_text}".strip()


_MD_STRIP_RE = re.compile(r"[*_`#]+|^\s*[-•]\s+", re.MULTILINE)


def _strip_markdown(text: str) -> str:
    """Every caller's message may carry markdown (bold/headers/bullets meant
    for Telegram's rendered text) — TTS reads the literal symbols aloud
    ('asterisk asterisk') if they aren't stripped before speaking."""
    return _MD_STRIP_RE.sub("", text)


def call_my_phone(message: str = "") -> str:
    if not message:
        return "[invalid arguments] call_my_phone requires message"
    message = _strip_markdown(message)
    if not _ENV_FILE.exists():
        return f"[ringback error] missing {_ENV_FILE} — run the ringback docker setup first"
    allowed, elapsed, previous_ts = _check_and_update_cooldown()
    if not allowed:
        remaining = max(0, COOLDOWN_SECONDS - elapsed)
        return (
            f"[ringback cooldown] last call was {elapsed:.0f}s ago "
            f"(cooldown={COOLDOWN_SECONDS}s) — callable again in {remaining:.0f}s. "
            f"Tell the user exactly when you can call back, and offer text/telegram as alternatives."
        )
    error: Exception | None = None
    try:
        return asyncio.run(_run_call(message))
    except* Exception as eg:
        error = eg.exceptions[0]
        while isinstance(error, BaseExceptionGroup):
            error = error.exceptions[0]
        log.warning("ringback call failed: %s", error, exc_info=error)
    _restore_cooldown(previous_ts)
    return f"[ringback error] {error}"
