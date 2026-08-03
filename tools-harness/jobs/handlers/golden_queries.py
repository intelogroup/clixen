"""Golden-query regression suite — nightly structural checks on the live agent.

Replays real queries (drawn from actual production failures) through
harness.run() and asserts on STRUCTURE via trace_store — which tools ran,
whether any model escalation fired — plus light answer-text checks. Assertions
are deliberately structural rather than exact-wording to survive normal LLM
answer variation without alert fatigue.

Failures surface through the worker's existing _notify_workflow_failure
plumbing (success=False → Telegram), no direct alerting here.
"""
from __future__ import annotations

import concurrent.futures
import logging
import time
import re
import uuid

log = logging.getLogger("golden_queries")

# Each query: name, query text, and structural assertions:
#   must_call        — every tool in this set must appear in the run's trace
#   must_call_any    — at least min_sources of these must appear
#   min_sources      — threshold for must_call_any (default: all)
#   answer_must_not_match — regex the final answer must NOT match
#   forbid_escalation — no _model_escalation entry allowed in the trace
#   max_seconds      — wall-clock budget
_COMMITMENT = {"ask_email_agent", "ask_calendar_agent", "ask_tasks_agent", "ask_messaging_agent"}

GOLDEN_QUERIES = [
    {
        "name": "commitment_scan_fanout",
        "query": "Do I have any assignment or booking today?",
        "must_call": {"ask_email_agent", "ask_calendar_agent"},
        "forbid_escalation": True,
        "max_seconds": 120,
    },
    {
        "name": "morning_email_summary",
        "query": "What emails did I get this morning?",
        "must_call": {"ask_email_agent"},
        "answer_must_not_match": r"as an ai|i (?:cannot|can't) access",
        "max_seconds": 120,
    },
    {
        "name": "calendar_week_view",
        "query": "What's on my calendar this week?",
        "must_call": {"ask_calendar_agent"},
        # ponytail: 50s in isolation, observed 172s when run back-to-back with the other
        # 7 golden queries (shared cloud API rate limits/thread pools) — the nightly job
        # runs them sequentially too, so budget must cover real sequential contention.
        "max_seconds": 200,
    },
    {
        "name": "due_tomorrow_fanout",
        "query": "Do I have anything due tomorrow?",
        "must_call_any": _COMMITMENT,
        "min_sources": 2,
        # ponytail: hit 198s in a full sequential suite run, same shared-contention
        # cause as calendar_week_view above.
        "max_seconds": 200,
    },
    {
        "name": "no_spurious_escalation",
        # Answer prose will legitimately contain "failed" — regression check that
        # is_error_result (prefix-anchored) no longer misreads it and escalates.
        "query": "Summarize my recent emails about failed or missed deliveries",
        "must_call": {"ask_email_agent"},
        "forbid_escalation": True,
        "max_seconds": 120,
    },
    {
        "name": "absence_honesty",
        # Known-negative commitment question: an absence answer is acceptable
        # ONLY with broad coverage (B's fan-out rule + E's verify-on-absence).
        "query": "Do I have a dentist appointment today?",
        "must_call_any": _COMMITMENT,
        "min_sources": 2,
        "max_seconds": 180,  # verify-on-absence retry may add a round
    },
    {
        "name": "commitment_scan_includes_messaging",
        # 2026-08-01 live failure: a CCCS assignment confirmed only via iMessage
        # text was missed 3x because ask_messaging_agent wasn't in the fan-out
        # set at all — this pins that ask_messaging_agent participates now.
        "query": "Verify email and iMessage for any confirmed assignments coming this month",
        "must_call_any": _COMMITMENT,
        "min_sources": 2,
        "max_seconds": 180,
    },
    {
        "name": "schedule_scan_no_search_operators",
        # Root cause of the original CCCS miss (2026-07-10): the model built its own
        # subject:(...)/after:/before: Gmail filters across 3 rounds instead of an empty
        # scan it reads itself — one filter was malformed, one excluded the real wording,
        # one filtered by received date and missed a same-day reminder received the day
        # before. This pins the fix: list_emails must be called with an empty query on a
        # schedule/due/assignment question. Covers _wants_schedule_scan (harness.py) —
        # once this passes with that branch disabled, delete the branch (see its comment).
        # Direct intent, not orchestrator fan-out: ask_email_agent subagent calls run under
        # their OWN run_id (tools/orchestrator_tools.py:_run_subagent), never joining the
        # parent trace — so a fan-out query here could never see the list_emails call this
        # test needs to inspect. Hitting the "email" pipeline directly is what actually
        # exercises _wants_schedule_scan's branch.
        "direct_intent": "email",
        "query": "Do I have any assignment due today?",
        "must_call": {"list_emails"},
        "forbid_search_operators": True,
        "max_seconds": 120,
    },
]


def _evaluate(spec: dict, answer: str, trace: list, elapsed: float) -> list[str]:
    """Return list of failure descriptions; empty = pass."""
    failures = []
    tools_called = {t.get("tool") for t in trace if t.get("tool")}

    for tool in spec.get("must_call", ()):
        if tool not in tools_called:
            failures.append(f"{tool} never called (called: {sorted(tools_called) or 'none'})")

    any_set = spec.get("must_call_any")
    if any_set:
        need = spec.get("min_sources", len(any_set))
        got = len(tools_called & set(any_set))
        if got < need:
            failures.append(f"only {got}/{need} required sources called (called: {sorted(tools_called) or 'none'})")

    if spec.get("forbid_escalation") and any(t.get("escalated") for t in trace):
        failures.append("model escalation fired")

    if spec.get("forbid_search_operators"):
        for t in trace:
            if t.get("tool") != "list_emails":
                continue
            q = (t.get("args") or {}).get("query") or ""
            if q.strip():
                failures.append(f"list_emails called with non-empty query {q!r} (search operators risk missing the email)")

    pattern = spec.get("answer_must_not_match")
    if pattern and re.search(pattern, answer or "", re.I):
        failures.append(f"answer matched forbidden pattern {pattern!r}")

    if not (answer or "").strip():
        failures.append("empty answer")

    budget = spec.get("max_seconds", 120)
    if elapsed > budget:
        failures.append(f"took {elapsed:.0f}s > {budget}s budget")

    return failures


def handle(instance: dict) -> dict:
    import harness
    from store import trace_store

    details = []
    passed = 0
    for spec in GOLDEN_QUERIES:
        rid = uuid.uuid4().hex[:8]
        budget = spec.get("max_seconds", 120)
        deadline = budget * 1.5
        t0 = time.time()
        try:
            direct_intent = spec.get("direct_intent")
            ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            if direct_intent:
                future = ex.submit(harness._execute_intent_pipeline, intent=direct_intent, query=spec["query"], run_id=rid)
                try:
                    answer = future.result(timeout=deadline)
                except concurrent.futures.TimeoutError:
                    raise TimeoutError(f"timed out after {deadline:.0f}s (budget {budget:.0f}s)")
                finally:
                    ex.shutdown(wait=False)
            else:
                future = ex.submit(harness.run, query=spec["query"], chat_id=None, run_id=rid)
                try:
                    answer, _, _ = future.result(timeout=deadline)
                except concurrent.futures.TimeoutError:
                    raise TimeoutError(f"timed out after {deadline:.0f}s (budget {budget:.0f}s)")
                finally:
                    ex.shutdown(wait=False)
            trace = trace_store.get_trace(rid) or []
            failures = _evaluate(spec, answer, trace, time.time() - t0)
        except (concurrent.futures.TimeoutError, TimeoutError) as exc:
            trace = trace_store.get_trace(rid) or []
            elapsed = time.time() - t0
            failures = [f"timed out after {elapsed:.0f}s (budget {budget:.0f}s)"]
        except Exception as exc:
            failures = [f"raised: {exc}"]
        if failures:
            log.warning("[golden] %s FAILED: %s", spec["name"], "; ".join(failures))
            details.append({"name": spec["name"], "failures": failures})
        else:
            passed += 1

    result = {
        "success": not details,
        "passed": passed,
        "failed": len(details),
        "details": details,
    }
    if details:
        # _notify_workflow_failure surfaces this string in the Telegram alert.
        result["error"] = "; ".join(
            f"{d['name']}: {d['failures'][0]}" for d in details
        )[:400]
    return result
