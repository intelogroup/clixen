import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import harness
from clients.router import Classification

# Both tests below exercise harness.run(orchestrated=False) — the classic per-intent
# path where the SVG-formatting instruction actually lives. orchestrated=True (run()'s
# default since the 2026-07 cloud-first revamp) routes through ORCHESTRATOR_SYSTEM_PROMPT
# + ask_* tool-calling instead and never reaches this code, so classify_message()/
# local_chat() patches have no effect there. orchestrated=False is still real, live
# code — used by _execute_intent_pipeline() for pre-classified subagent dispatch.
#
# 2026-07-11: run()'s model=None path now calls classify_message() (LLM-primary,
# regex-fallback) instead of the bare regex classify() — patch that instead.

_CASUAL = Classification(model="mistral-nemo", intent="casual", specialist_hint=None, source="test")


def test_visual_request_adds_svg_instruction_to_system_prompt():
    with (
        patch("harness.classify_message", return_value=_CASUAL),
        patch("harness.local_chat", return_value="```svg\n<svg></svg>\n```") as mock_local_chat,
    ):
        harness.run("design a simple status card svg for server uptime", orchestrated=False)

    prompt = mock_local_chat.call_args.kwargs["system_prompt"]
    assert "```svg ... ```" in prompt
    assert "Do not use JavaScript" in prompt


def test_non_visual_request_does_not_add_svg_instruction():
    with (
        patch("harness.classify_message", return_value=_CASUAL),
        patch("harness.local_chat", return_value="plain answer") as mock_local_chat,
    ):
        harness.run("explain closures in javascript", orchestrated=False)

    prompt = mock_local_chat.call_args.kwargs["system_prompt"]
    assert "```svg ... ```" not in prompt


def test_normalize_svg_reply_unwraps_malformed_nested_fences():
    raw = """Here's a simple status card SVG for server uptime:

```
```svg
<svg width="200" height="100" viewBox="0 0 200 100"></svg>
```
```"""

    normalized = harness._normalize_svg_reply(raw)

    assert normalized == """Here's a simple status card SVG for server uptime:

```svg
<svg width="200" height="100" viewBox="0 0 200 100"></svg>
```"""
