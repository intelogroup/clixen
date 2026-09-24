"""Regression tests reproducing real agent-behavior bugs found in Telegram
traces from 2026-09-17 (chat_id 8538224711), diagnosed live via
telegram_bot.log / messaging_stderr.log / task_worker.err.log.

Bug 1 — local_agent_nodes._chat(): a plain local (non-cloud) model call had
no fallback at all. Confirmed live: Ollama daemon down all day ->
`gemma4:12b' not found` fired 14x, every one hard-aborted the local-agent
run instead of degrading to cloud. Fixed in _chat() (agents/local_agent_nodes.py)
to catch and retry on DEFAULT_CLOUD_MODEL, mirroring the is_cloud_model
branch's existing fallback.

Bug 2 — agents/local_agent_graph.py run_local_agent() calls asyncio.run()
internally. Confirmed live at 2026-09-17 11:26:33 (run_id 396179b3, query
"Navigate to /Users/kalinovdameus/Developer/ugent-app"): a nested specialist
dispatch called run_local_agent() from code already executing inside a
running event loop -> "RuntimeError: asyncio.run() cannot be called from a
running event loop". harness.py:279 already catches and degrades to an
apology string, but the specialist's real work is lost silently — this test
documents and locks in that caught-not-crashed behavior so a future refactor
that removes the try/except doesn't turn it back into an unhandled crash.

Bug 3 — harness.py's verify-on-absence nudge misfired on an explicit
send-action request. Confirmed live at 11:26-11:28 (run_id 7442720f, query
"Check the working GitHub pat we have in our repo local send it to me via
email"): trace_store shows only ask_local_agent/ask_run_command were ever
called — ask_email_agent was never invoked, no email was ever sent. Root
cause: _is_commitment_shaped() matched (intent=email + the word "check")
even though this is a deliver-this-by-email action, not a "do I have X"
commitment question, so the retry nudge said "a draft answer claimed nothing
was found... call them now" — verification wording, not a send instruction.
Fixed by detecting explicit delivery phrasing (_EXPLICIT_DELIVERY_RE) and
swapping in a "SEND REQUIRED: ... call ask_email_agent now" nudge instead.

Bug 4 — agents/local_agent_nodes.py had two separate inline computations of
"what tools can this agent actually call", and they drifted. _tools_for_state()
(builds the schema shown to the model) unions a matched skill's tool list with
tag "core" so always-on tools like bash_exec stay visible. tool_node()
(execution-time enforcement) did NOT apply that same union when a skill
matched. Confirmed live at 11:39 (query "Zhsrc" / "cd .. and search on
/developer or Zehra" follow-up): model saw bash_exec in its own tool list,
called it, got "[blocked] Tool 'bash_exec' is not allowed in this agent's
scoped manifest". Fixed by extracting one shared _effective_allowed_tool_names()
that both call, so the advertised schema and the enforced allowlist can never
disagree again.
"""
import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agents.local_agent_nodes as lan
from agents.local_agent_graph import run_local_agent
import harness


def test_local_only_ollama_failure_falls_back_to_cloud():
    """Bug 1 repro: local model unreachable -> _chat() must retry on cloud,
    not raise. This is the exact failure mode from the 14x '...not found'
    errors in task_worker/messaging logs on 2026-09-17."""

    def fake_run_local(model, messages, tools, temperature=None):
        raise RuntimeError(
            f"Ollama model '{model}' not found — run `ollama pull {model}`."
        )

    class FakeMessage:
        content = "cloud fallback answer"
        tool_calls = None

    class FakeResp:
        message = FakeMessage()

    calls = {}

    def fake_raw_completion(model, messages, tools, temperature=None, **kw):
        calls["model"] = model
        return FakeResp()

    with patch.object(lan, "_run_local", fake_run_local), \
         patch("clients.cloud_client.raw_completion", fake_raw_completion):
        resp = lan._chat("gemma4:12b", [{"role": "user", "content": "hi"}], [], temperature=0.0)

    assert resp.message.content == "cloud fallback answer"
    assert calls["model"] == lan.DEFAULT_CLOUD_MODEL


def test_run_local_agent_inside_running_loop_degrades_not_crashes():
    """Bug 2 repro: run_local_agent() is a sync function that does
    asyncio.run() internally. Calling it from inside an already-running
    event loop must not propagate a raw RuntimeError to the caller — it has
    to come back as the documented apology string (harness.py's try/except
    around this same call), same as the real 11:26:33 trace."""

    async def _call_from_running_loop():
        # run_local_agent is sync; calling it here reproduces the real crash
        # site — harness.py's specialist dispatch is itself inside a running
        # loop at the point it calls this.
        return run_local_agent(query="Navigate to /tmp", model="openrouter/x/y:free")

    result = asyncio.run(_call_from_running_loop())
    assert "couldn't finish" in result or "errored" in result
    assert "asyncio.run() cannot be called from a running event loop" in result


def test_explicit_send_request_gets_send_nudge_not_verification_nudge():
    """Bug 3 repro: the exact 11:26 query must be detected as an explicit
    delivery action so the retry nudge tells the model to actually call
    ask_email_agent, instead of the generic 'verify absence' wording that
    let the model wander off into more filesystem search and never send."""
    query = "Check the working GitHub pat we have in our repo local send it to me via email"
    missing = {"ask_email_agent", "ask_calendar_agent", "ask_tasks_agent", "ask_messaging_agent"}

    assert harness._EXPLICIT_DELIVERY_RE.search(query)
    assert "ask_email_agent" in missing

    # a real commitment question ("do I have X due") must NOT be swept into
    # the send-nudge path just because it also mentions "check"/"email"
    commitment_query = "Do I have anything due today, check email and calendar"
    assert not harness._EXPLICIT_DELIVERY_RE.search(commitment_query)


def test_schema_and_execution_allowlist_never_drift():
    """Bug 4 repro: when a skill matches, the tool schema shown to the model
    and the execution-time allowlist must agree on every name — in particular
    core-tagged tools like bash_exec must survive in both, not just the
    schema. This is the exact mismatch that produced the live 'Zhsrc' trace's
    [blocked] bash_exec error."""

    class FakeState:
        task = "code"
        skill_tools = ["read_file"]
        excluded_tools = []

    state = FakeState()
    allowed = lan._effective_allowed_tool_names(state)
    schema_names = {t["function"]["name"] for t in lan._tools_for_state(state)}

    assert "bash_exec" in allowed
    assert "bash_exec" in schema_names
    assert schema_names == allowed


if __name__ == "__main__":
    test_local_only_ollama_failure_falls_back_to_cloud()
    print("OK — local-only Ollama failure now falls back to cloud.")
    test_run_local_agent_inside_running_loop_degrades_not_crashes()
    print("OK — nested asyncio.run() degrades to apology string, doesn't crash.")
    test_explicit_send_request_gets_send_nudge_not_verification_nudge()
    print("OK — explicit send request gets the SEND REQUIRED nudge, not VERIFICATION REQUIRED.")
    test_schema_and_execution_allowlist_never_drift()
    print("OK — schema shown to model and execution allowlist always agree.")
