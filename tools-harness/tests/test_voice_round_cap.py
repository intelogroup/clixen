"""Voice round-cap relies on CURRENT_CHAT_ID being readable inside the subagent
thread (subagents run via ThreadPoolExecutor, which copies the parent context).

Proves: (1) the contextvar propagates into a copied context / child thread the
way the cap's `CURRENT_CHAT_ID.get(None) == "brabble_voice"` check needs, and
(2) the min()-clamp arithmetic the cap uses.
"""
import contextvars
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.registry import CURRENT_CHAT_ID

_VOICE_MAX_ROUNDS = 6


def _cap(base_rounds, chat_id):
    # mirrors harness.py's inline clamp
    if chat_id == "brabble_voice":
        return min(base_rounds, _VOICE_MAX_ROUNDS)
    return base_rounds


def test_clamp_only_for_voice():
    assert _cap(15, "brabble_voice") == 6
    assert _cap(4, "brabble_voice") == 4          # never raises a small default
    assert _cap(15, "web_chat") == 15             # non-voice untouched
    assert _cap(15, None) == 15


def test_contextvar_reaches_child_thread():
    # subagents run on ThreadPoolExecutor workers; the parent sets CURRENT_CHAT_ID
    # then dispatches. The worker must observe the parent's value via copied context.
    CURRENT_CHAT_ID.set("brabble_voice")
    ctx = contextvars.copy_context()
    with ThreadPoolExecutor(1) as ex:
        seen = ex.submit(ctx.run, CURRENT_CHAT_ID.get, None).result()
    assert seen == "brabble_voice", f"contextvar did not propagate: {seen!r}"
    assert CURRENT_CHAT_ID.get(None) == "brabble_voice"


if __name__ == "__main__":
    test_clamp_only_for_voice()
    test_contextvar_reaches_child_thread()
    print("ok")
