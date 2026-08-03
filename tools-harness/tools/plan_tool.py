"""
Orchestrator-facing mid-task plan/todo tool — externalizes "what's done, what's
left" across tool rounds so a long run doesn't drift off the original ask
before verify-on-absence/claim-check ever get a chance to catch it. See
store/plan_store.py for the sqlite-backed state (mirrors trace_store's shape).

Uses CURRENT_RUN_ID (log_config.py), set once per orchestrator run() call on
the main thread before any tool dispatch — safe to read here without the
parallel-subagent-worker race that ContextVar reads hit elsewhere (see
CLAUDE.md's threaded run_id note). This tool is orchestrator-only, never
called from inside a subagent worker thread.
"""
from log_config import CURRENT_RUN_ID
from store import plan_store


def _run_id() -> str:
    return CURRENT_RUN_ID.get()


def update_task_plan(steps: list[str] = None, mark_done: list[int] = None) -> str:
    run_id = _run_id()
    if not run_id:
        return "[error] no active run — update_task_plan is orchestrator-only"
    if steps:
        plan_store.set_plan(run_id, steps)
        return f"Plan set: {len(steps)} step(s)."
    if mark_done:
        result = plan_store.mark_done(run_id, mark_done)
        if result is None:
            return "[error] no plan exists yet for this run — pass `steps` first"
        return f"Marked done: {mark_done}. {len(result['done'])}/{len(result['steps'])} complete."
    return "[error] pass either `steps` (to create/replace the plan) or `mark_done` (to tick items)"


PLAN_TASK_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "update_task_plan",
            "description": (
                "Write or update a mid-task plan for the current run. Use for any request "
                "needing 3+ tool calls: call once with `steps` up front to lay out what you'll "
                "do, then call again with `mark_done` (0-indexed) as each step finishes. The plan "
                "is shown back to you each round so you can track progress and avoid drifting off "
                "the original ask. Skip for simple 1-2 tool-call requests."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Full ordered list of steps — pass to create/replace the plan.",
                    },
                    "mark_done": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "0-indexed step numbers to mark complete.",
                    },
                },
            },
        },
    },
]

PLAN_TASK_EXECUTORS = {
    "update_task_plan": lambda args: update_task_plan(args.get("steps"), args.get("mark_done")),
}
