"""
Agent fan-out (M5) — one run spawning restricted child agents.

    child = spawn_agent(parent_run_id, "find X", tools=["web_search"])

What makes this safe:
  * the child gets its OWN run/journal and its own tool allowlist; the loop
    refuses any tool that wasn't offered (see run_loop),
  * the child's answer is delivered into the PARENT's journal as a user_msg
    tagged `via: agent`, so the parent simply sees it on its next round —
    no side channel, no polling, and it survives a restart like everything
    else,
  * parent/child links live in the child's `run` event, so a fan-out tree is
    reconstructable from the journal alone.
"""
from __future__ import annotations

from store import run_store as rs


def spawn_agent(parent_run_id: str, goal: str, tools: list[str], *,
                model: str = "deepseek/deepseek-v4-flash", system: str = "",
                label: str = "", execute=None) -> str:
    """Create + start a child run owned by `parent_run_id`. Returns child id."""
    parent = rs.get_run(parent_run_id)
    if parent is None:
        raise KeyError(f"no parent run {parent_run_id!r}")
    child = rs.create_run(goal, trigger="agent-mail", policy={"tools": list(tools)})
    rs.append_event(child, "run", {
        "goal": goal, "trigger": "agent-mail",
        "parent_run_id": parent_run_id,
        "label": label or child[:8],
        "tools": list(tools),
    })
    (execute or _child_executor(model, tools, system))(child)
    return child


def _child_executor(model: str, tools: list[str], system: str):
    def run(child_id: str) -> None:
        _execute_child(child_id, _parent_of(child_id), model, tools, system)
    return run


def _execute_child(child_id: str, parent: str | None, model: str,
                   tools: list[str], system: str) -> None:
    from agents import cloud_adapter, run_loop

    try:
        from tools._registry.schemas import ALL_TOOLS
    except Exception:  # noqa: BLE001 — tests run without the heavy registry
        ALL_TOOLS = []
    names = set(tools or [])
    schemas = [t for t in ALL_TOOLS if t.get("function", {}).get("name") in names]
    model_fn = cloud_adapter.make_model_fn(model, schemas) if schemas else \
        (lambda messages, tools: {"text": "", "tool_calls": []})
    answer = run_loop.run_rounds(child_id, model_fn, tools=list(names), system=system)
    if parent:
        deliver_to_parent(parent, child_id, answer)


def _parent_of(run_id: str) -> str | None:
    for ev in rs.get_events(run_id):
        if ev["kind"] == "run" and ev["payload"].get("parent_run_id"):
            return ev["payload"]["parent_run_id"]
    return None


def deliver_to_parent(parent_run_id: str, child_run_id: str, answer: str) -> bool:
    """Post a child's answer into the parent's journal as the next user turn.
    Refuses when the parent is gone or already finished."""
    parent = rs.get_run(parent_run_id)
    if parent is None or parent["status"] in rs.TERMINAL:
        return False
    rs.append_event(parent_run_id, "user_msg", {
        "via": "agent", "from": child_run_id, "text": answer or "(no answer)"})
    return True