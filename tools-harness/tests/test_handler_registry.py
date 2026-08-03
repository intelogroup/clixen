"""
Tests for handler_registry — register, dispatch, and error handling.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from jobs import handler_registry


@pytest.fixture(autouse=True)
def clear_registry():
    handler_registry._REGISTRY.clear()
    yield
    handler_registry._REGISTRY.clear()


def _sample_handler(instance: dict) -> dict:
    return {"success": True, "items_processed": 1}


def test_register_and_dispatch():
    handler_registry.register("test.workflow", _sample_handler)
    result = handler_registry.dispatch({"automation_id": "test.workflow"})
    assert result["success"] is True
    assert result["items_processed"] == 1


def test_dispatch_unknown_raises_keyerror():
    with pytest.raises(KeyError, match="test.missing"):
        handler_registry.dispatch({"automation_id": "test.missing"})


def test_dispatch_no_automation_id():
    with pytest.raises(KeyError):
        handler_registry.dispatch({})


def test_registered_ids():
    handler_registry.register("a", _sample_handler)
    handler_registry.register("b", _sample_handler)
    ids = handler_registry.registered_ids()
    assert "a" in ids
    assert "b" in ids


def test_register_overwrites_existing():
    def handler_a(inst):
        return {"from": "a"}

    def handler_b(inst):
        return {"from": "b"}

    handler_registry.register("dup", handler_a)
    handler_registry.register("dup", handler_b)
    result = handler_registry.dispatch({"automation_id": "dup"})
    assert result["from"] == "b"


def test_handler_receives_full_instance():
    captured = {}

    def capture(inst):
        captured.update(inst)
        return {"success": True}

    handler_registry.register("capture", capture)
    handler_registry.dispatch({"automation_id": "capture", "config": {"key": "val"}, "id": "abc123"})
    assert captured["automation_id"] == "capture"
    assert captured["config"]["key"] == "val"
    assert captured["id"] == "abc123"
