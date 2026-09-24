"""
Stateless run loop (M1) — the extracted "one round at a time" agent loop.

Contract (docs/plans/2026-09-24-long-horizon-agent-upgrade.md WS1):

    load journal → one model round → append → repeat

The journal is the source of truth: this module keeps NO state between calls
beyond what it reads back from run_store. Crash at any point → the next call
resumes from the last event. Replay safety:

  * tool_call ids already answered by a tool_result in the journal are NEVER
    re-executed (replay cache — read from the journal, not memory);
  * a dangling tool_call (crash between call and result) on a non-replayable
    tool fails the run instead of blindly re-executing it (full approval/
    classification registry lands with M3 — the door is marked here).

model_fn is injected so tests run without providers and the live adapter can
wrap clients.cloud_client.chat() later without touching this file.
"""
from __future__ import annotations

import time

from store import run_store

# Tools known-safe to re-execute when a resume hits a dangling call.
REPLAYABLE_TOOLS = frozenset({
    "browser_tree", "browser_status", "browser_capture", "browser_eval",
    "get_current_time", "web_search", "browser_wait_for",
})


def replay_messages(run_id: str, system: str = ""):
    """Rebuild model messages from the journal (the loop's only memory).

    Returns (messages, answered_ids, known_call_ids) — answered_ids drives the
    replay cache; known_call_ids detects duplicate appends + dangling calls.
    """
    msgs: list[dict] = [{"role": "system", "content": system}] if system else []
    answered: set[str] = set()
    known_calls: set[str] = set()
    for ev in run_store.get_events(run_id):
        p = ev["payload"]
        if ev["kind"] == "user_msg":
            msgs.append({"role": "user", "content": p.get("text", "")})
        elif ev["kind"] == "assistant_msg":
            msgs.append({"role": "assistant", "content": p.get("text", "")})
        elif ev["kind"] == "tool_call":
            cid = p.get("id")
            known_calls.add(cid)
            msgs.append({"role": "assistant", "content": None, "tool_calls": [{
                "id": cid, "name": p.get("name"), "args": p.get("args", {}),
            }]})
        elif ev["kind"] == "tool_result":
            answered.add(p.get("id"))
            msgs.append({"role": "tool", "tool_call_id": p.get("id"),
                         "content": p.get("result", "")})
    return msgs, answered, known_calls


def _execute_tool(name: str, args: dict) -> str:
    try:
        from tools.registry import EXECUTORS  # lazy: avoids registry import cycles
    except Exception as exc:  # noqa: BLE001 — registry may be mid-refactor
        return f"[error] tool registry unavailable: {exc}"
    fn = EXECUTORS.get(name)
    if fn is None:
        return f"[error] unknown tool {name!r}"
    try:
        out = fn(args)
        return out if isinstance(out, str) else str(out)
    except Exception as exc:  # noqa: BLE001 — surfaced to the model, not the caller
        return f"[error] {name} failed: {exc}"


def run_rounds(run_id: str, model_fn, *, tools: list[str], system: str = "",
               max_rounds: int = 20, on_event=None) -> str:
    """Drive the journal until the run terminalizes.

    model_fn(messages, tools) -> {"text": str,
                                  "tool_calls": [{"id","name","args"}]}
    Returns the final assistant text. Sets status: succeeded | budget-exceeded
    | failed. Re-entrant: call again after a crash and it resumes (replay
    cache skips already-answered calls).

    Budget note: deadline_s bounds a single invocation's wall-clock (resume
    gets a fresh window); cost_guard round/cost checking integrates at the
    live adapter layer.
    """
    run = run_store.get_run(run_id)
    if run is None:
        raise KeyError(f"no run {run_id!r}")
    if run["status"] == "succeeded":
        raise ValueError(f"run {run_id!r} already terminal")
    if run["status"] in ("paused", "steer-waiting"):
        raise ValueError(f"run {run_id!r} is {run['status']} — resume explicitly first")

    policy = run.get("policy") or {}
    max_rounds = int(policy.get("max_rounds", max_rounds))
    deadline_s = float(policy.get("deadline_s", 0))
    started = time.monotonic()

    # Round-boundary state carried across lives (plan WS1 issue 1): the next
    # round continues the journaled round index and escalation state, so a
    # resume neither re-grants the round budget nor silently un-escalates.
    state = run_store.latest_round(run_id) or {}
    round_idx = int(state.get("round_idx", -1)) + 1
    escalated = bool(state.get("escalated", False))
    consecutive_errors = int(state.get("consecutive_errors", 0))
    force_tool_consumed = bool(state.get("force_tool_consumed", False))
    model_name = state.get("model", "")
    # Control cursor: which control intents this run has already consumed.
    # Persisted as a heartbeat marker per consumed control, so a resume (or a
    # stop mid-drain) never re-applies one.
    control_cursor = max(
        int((state.get("extra") or {}).get("last_control_seq", 0)),
        run_store.latest_control_cursor(run_id),
    )

    if run["status"] == "queued":
        run_store.set_status(run_id, "running")
    elif run["status"] in ("failed", "killed", "budget-exceeded", "retrying"):
        run_store.set_status(run_id, "running")  # resume transition

    final_text = ""
    # Fail fast on an empty journal: a provider 400 ("Input required: specify
    # ...") is a confusing way to learn the run has no input. This happens
    # when a run is created without a user_msg (e.g. a hand-rolled run, or a
    # resume after a truncated journal).
    probe_msgs, _answered, _known = replay_messages(run_id, system=system)
    if not any(m.get("role") in ("user", "assistant", "tool")
               and (m.get("content") or m.get("tool_calls"))
               for m in probe_msgs):
        reason = "run journal has no user message — nothing to execute"
        run_store.append_event(run_id, "status", {"status": "failed",
                                                   "reason": reason})
        run_store.set_status(run_id, "failed")
        return ""

    while round_idx < max_rounds:
        # ── Control intents (M2) — drained at every round boundary, never
        # mid-round. The requester only journals intent; transitions happen
        # here, keeping the journal single-writer.
        for c in run_store.pending_controls(run_id, after_seq=control_cursor):
            control_cursor = c["seq"]
            # Durably record consumption BEFORE acting, so a stop/crash mid-drain
            # cannot re-apply this control on the next life.
            run_store.append_event(run_id, "heartbeat",
                                   {"control_cursor": control_cursor})
            if c["action"] == "steer":
                run_store.append_event(run_id, "user_msg",
                                       {"text": c.get("text", ""), "via": "steer"})
            elif c["action"] == "pause":
                run_store.set_status(run_id, "paused")
                return final_text
            elif c["action"] == "kill":
                run_store.set_status(run_id, "killed")
                return final_text
            # "resume" is a no-op here: a paused run is not executing, so the
            # resume path re-enters run_rounds instead.
        if deadline_s and time.monotonic() - started > deadline_s:
            run_store.append_event(run_id, "budget",
                                   {"reason": f"deadline {deadline_s}s exceeded"})
            run_store.set_status(run_id, "budget-exceeded")
            return final_text

        msgs, answered, known_calls = replay_messages(run_id, system=system)
        result = model_fn(msgs, tools) or {}
        text = result.get("text") or ""
        tool_calls = result.get("tool_calls") or []

        if not tool_calls:
            if text:
                run_store.append_event(run_id, "assistant_msg", {"text": text})
            run_store.append_round_snapshot(
                run_id, round_idx=round_idx, model=model_name,
                escalated=escalated, consecutive_errors=consecutive_errors,
                force_tool_consumed=force_tool_consumed,
                extra={"last_control_seq": control_cursor} if control_cursor else None)
            run_store.set_status(run_id, "succeeded")
            return text

        if text:
            run_store.append_event(run_id, "assistant_msg", {"text": text})
        for call in tool_calls:
            cid, name, args = call.get("id"), call.get("name"), call.get("args", {})
            if cid in answered:
                continue  # REPLAY CACHE: answered pre-crash — never re-run
            if name not in (tools or []):
                # Restricted toolset (M5): a run may only call what it was
                # offered — an empty allowlist means NO tools. A hallucinated
                # name must never execute.
                denied = (f"[error] tool {name!r} is not available to this run — "
                          f"use one of: {', '.join(sorted(tools))}")
                run_store.append_event(run_id, "tool_result",
                                       {"id": cid, "result": denied})
                answered.add(cid)
                consecutive_errors += 1
                if on_event:
                    on_event({"kind": "tool_denied", "id": cid, "name": name})
                continue
            if cid not in known_calls:
                run_store.append_event(run_id, "tool_call",
                                       {"id": cid, "name": name, "args": args})
                known_calls.add(cid)
            elif name not in REPLAYABLE_TOOLS:
                # Dangling side-effecting call from a previous life: crash hit
                # between call and result. Fail safely — journal preserved.
                run_store.append_event(run_id, "status", {
                    "status": "failed",
                    "reason": f"dangling side-effecting call {name!r} — "
                              "needs approval before re-execution"})
                run_store.set_status(run_id, "failed")
                return final_text
            result_str = _execute_tool(name, args)
            run_store.append_event(run_id, "tool_result",
                                   {"id": cid, "result": result_str})
            answered.add(cid)
            consecutive_errors = consecutive_errors + 1 if result_str.startswith("[error]") else 0
            if on_event:
                on_event({"kind": "tool_result", "id": cid, "name": name})
        final_text = text
        # Round completed — journal its boundary state for the next life.
        run_store.append_round_snapshot(
            run_id, round_idx=round_idx, model=model_name,
            escalated=escalated, consecutive_errors=consecutive_errors,
            force_tool_consumed=force_tool_consumed,
            extra={"last_control_seq": control_cursor} if control_cursor else None)
        round_idx += 1

    run_store.append_event(run_id, "budget", {"reason": f"max_rounds {max_rounds} hit"})
    run_store.set_status(run_id, "budget-exceeded")
    return final_text