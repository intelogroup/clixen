"""Drift guard: every orchestrator prompt fragment module must actually be wired
into harness._FRAGMENT_TRIGGERS and injectable via the {fragments} placeholder.
Catches a fragment file added/edited but never reaching the live prompt."""
import importlib
import pkgutil

import harness
import skills_data.orchestrator_fragments as frag_pkg


def _fragment_module_names():
    return [
        name for _, name, _ in pkgutil.iter_modules(frag_pkg.__path__)
        if name != "__init__"
    ]


def test_every_fragment_module_has_a_fragment_constant():
    for name in _fragment_module_names():
        mod = importlib.import_module(f"skills_data.orchestrator_fragments.{name}")
        assert hasattr(mod, "FRAGMENT") and mod.FRAGMENT.strip(), \
            f"{name} missing a non-empty FRAGMENT constant"


def test_every_fragment_wired_into_trigger_table():
    wired_texts = {text for _, _, text in harness._FRAGMENT_TRIGGERS}
    for name in _fragment_module_names():
        mod = importlib.import_module(f"skills_data.orchestrator_fragments.{name}")
        assert mod.FRAGMENT in wired_texts, \
            f"{name}.FRAGMENT exists but is not referenced in harness._FRAGMENT_TRIGGERS"


def test_orchestrator_prompt_has_fragments_placeholder():
    assert "{fragments}" in harness.ORCHESTRATOR_SYSTEM_PROMPT


def test_fragment_triggers_fire_on_representative_queries():
    samples = {
        "schedule_fanout": "do I have anything due today",
        "web_search_dispatch": "what's the latest news on this",
        "automation_dsl": "set up an automation for this",
        "remember_action": "remember that I like coffee",
    }
    by_name = {name: (pattern, text) for pattern, name, text in harness._FRAGMENT_TRIGGERS}
    for name, query in samples.items():
        pattern, _ = by_name[name]
        assert pattern.search(query), f"{name}'s trigger regex no longer matches {query!r}"
