"""
Regression test for the 2026-07-11 entity-scoped near-duplicate dedupe in
tools/orchestrator_tools.py — closes the gap where the exact-text subagent
cache let a "don't leave anything out" style query re-search the same
entities across many rounds with differently-worded queries per attribute
(confirmed live: 12+ ask_web_search calls, 50s+ before any answer streamed).
"""
from __future__ import annotations

from tools.orchestrator_tools import (
    _ENTITY_CACHE,
    _near_duplicate_entity_query,
    _record_entity_query,
)


def test_attribute_varied_requery_of_same_entities_is_blocked():
    run = "test_dedupe_run_same_entity"
    _ENTITY_CACHE.pop(run, None)
    try:
        q1 = "geothermal vs micro hydro home electricity comparison 2025 2026"
        q2 = "geothermal heat pump vs geothermal electricity generation home"
        q3 = "micro hydro turbine home cost per watt installed 2024 2025"

        assert not _near_duplicate_entity_query(run, q1)
        _record_entity_query(run, q1)
        assert _near_duplicate_entity_query(run, q2)
        _record_entity_query(run, q2)
        assert _near_duplicate_entity_query(run, q3)
    finally:
        _ENTITY_CACHE.pop(run, None)


def test_genuinely_different_entities_are_not_blocked():
    run = "test_dedupe_run_diff_entity"
    _ENTITY_CACHE.pop(run, None)
    try:
        q1 = "geothermal vs micro hydro home electricity comparison 2025 2026"
        q2 = "solar panel cost per watt 2026 federal tax credit"
        q3 = "residential wind turbine cost effectiveness 2026"

        _record_entity_query(run, q1)
        assert not _near_duplicate_entity_query(run, q2)
        _record_entity_query(run, q2)
        assert not _near_duplicate_entity_query(run, q3)
    finally:
        _ENTITY_CACHE.pop(run, None)


def test_different_runs_do_not_cross_contaminate():
    run_a, run_b = "test_dedupe_run_a", "test_dedupe_run_b"
    _ENTITY_CACHE.pop(run_a, None)
    _ENTITY_CACHE.pop(run_b, None)
    try:
        q1 = "geothermal vs micro hydro home electricity comparison 2025 2026"
        q2 = "geothermal heat pump vs geothermal electricity generation home"

        _record_entity_query(run_a, q1)
        assert not _near_duplicate_entity_query(run_b, q2)
    finally:
        _ENTITY_CACHE.pop(run_a, None)
        _ENTITY_CACHE.pop(run_b, None)
