"""Offline structural checks for tools/registry.py — no LLM, no network, no auth.

Catches the classic registry-drift bug: a tool schema added to ALL_TOOLS with
no matching EXECUTORS entry (breaks at call time), or an EXECUTORS entry left
behind after its schema was removed (dead code, or worse, a callable the
model can never reach but that still runs if invoked some other way).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.registry import ALL_TOOLS, EXECUTORS


def _schema_names():
    names = []
    for schema in ALL_TOOLS:
        assert schema.get("type") == "function", f"unexpected schema shape: {schema}"
        fn = schema.get("function", {})
        name = fn.get("name")
        assert name, f"schema missing function.name: {schema}"
        names.append(name)
    return names


def test_every_schema_name_is_unique():
    names = _schema_names()
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate tool schema names: {dupes}"


def test_every_schema_has_an_executor():
    names = set(_schema_names())
    missing = names - set(EXECUTORS)
    assert not missing, f"tool schemas with no EXECUTORS entry: {missing}"


def test_every_executor_has_a_schema():
    names = set(_schema_names())
    orphaned = set(EXECUTORS) - names
    assert not orphaned, f"EXECUTORS entries with no matching schema in ALL_TOOLS: {orphaned}"


def test_every_executor_is_callable():
    for name, fn in EXECUTORS.items():
        assert callable(fn), f"EXECUTORS[{name!r}] is not callable"


if __name__ == "__main__":
    test_every_schema_name_is_unique()
    test_every_schema_has_an_executor()
    test_every_executor_has_a_schema()
    test_every_executor_is_callable()
    print(f"OK — {len(ALL_TOOLS)} schemas, {len(EXECUTORS)} executors, all paired.")
