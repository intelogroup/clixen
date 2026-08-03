"""Unit tests for tools/plan_tool.py — the update_task_plan executor."""
from log_config import CURRENT_RUN_ID
from tools.plan_tool import update_task_plan
from store import plan_store


def test_no_active_run_returns_error():
    token = CURRENT_RUN_ID.set("")
    try:
        assert update_task_plan(steps=["a"]).startswith("[error]")
    finally:
        CURRENT_RUN_ID.reset(token)


def test_set_steps_then_mark_done_round_trip():
    token = CURRENT_RUN_ID.set("plantool-test-run")
    try:
        msg = update_task_plan(steps=["find email", "reply", "confirm"])
        assert "3 step" in msg
        msg2 = update_task_plan(mark_done=[0])
        assert "1/3" in msg2
        assert plan_store.get_plan("plantool-test-run")["done"] == [0]
    finally:
        CURRENT_RUN_ID.reset(token)


def test_mark_done_without_existing_plan_errors():
    token = CURRENT_RUN_ID.set("plantool-test-run-2")
    try:
        assert update_task_plan(mark_done=[0]).startswith("[error]")
    finally:
        CURRENT_RUN_ID.reset(token)


def test_no_args_errors():
    token = CURRENT_RUN_ID.set("plantool-test-run-3")
    try:
        assert update_task_plan().startswith("[error]")
    finally:
        CURRENT_RUN_ID.reset(token)
