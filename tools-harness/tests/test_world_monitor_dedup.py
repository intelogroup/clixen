"""world_monitor_store dedup regression tests.

History: the store hashed the LLM's raw finding text, but the LLM rewords the
same stable source headline every scan (arXiv feed is stable, the text is
not). The same paper got notified 2-3x under different content_hashes. Dedup
now keys on a normalized lead-phrase (before the first separator), which is
stable across rewording/truncation while still separating unrelated findings.
"""
import pytest

from store import world_monitor_store as store

# Real dup groups observed in world_monitor.db (2026-08-01)
TAYLER_DUPS = [
    "Spinning down neutron-star merger remnants with the Tayler-Spruit dynamo: "
    "Global simulations reveal formation of massive disks and neutron-rich ejecta.",
    "Spinning down neutron-star merger remnants with the Tayler-Spruit dynamo.",
    "Spinning down neutron-star merger remnants with the Tayler-Spruit dynamo: "
    "Global simulations reveal the formation of massive disks and neutron-rich ejecta.",
]

BBH_DUPS = [
    "Evidence for distinct astrophysical origins of binary black hole subpopulations: "
    "hierarchical mergers versus common envelope channels",
    "Evidence for distinct astrophysical origins of binary black hole subpopulations.",
]


def _h(text: str) -> str:
    return store.content_hash(store.dedup_key(text))


def test_reworded_variants_collapse_to_one_key():
    keys = {_h(t) for t in TAYLER_DUPS}
    assert len(keys) == 1


def test_truncated_variant_collapses():
    assert len({_h(t) for t in BBH_DUPS}) == 1


def test_case_and_whitespace_variance_collapses():
    base = TAYLER_DUPS[1]
    assert _h(base) == _h(f"  {base.upper()}\n")


def test_separator_variance_collapses():
    colon = "Nuclear fusion milestone: NIF achieves record energy gain"
    dash = "Nuclear fusion milestone — NIF achieves record energy gain"
    assert _h(colon) == _h(dash)


def test_unrelated_findings_stay_distinct():
    a = "AI model predicts protein folding accuracy: AlphaFold successor achieves 92% on CASP benchmark"
    b = "Nuclear fusion milestone: NIF achieves record energy gain"
    assert _h(a) != _h(b)


def test_is_known_mark_notified_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "wm.db")
    store.init()
    assert not store.is_known(TAYLER_DUPS[0])
    store.mark_notified(TAYLER_DUPS[0], "reason", "new_research_papers")
    # reworded re-report of the same paper is now known
    assert store.is_known(TAYLER_DUPS[2])
    # unrelated finding still unknown
    assert not store.is_known("Global simulations of neutron-star merger remnants reveal "
                              "Tayler-Spruit dynamo activation and magnetorotational instability.")
