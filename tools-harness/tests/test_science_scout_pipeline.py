"""Smallest check that would fail if the science_scout dedup/merge/evidence-level
logic regresses: exact dupes never re-enter raw_papers, an UPDATE bumps
evidence_count instead of creating a new claim row, paper_claim_links only ever
grows (append-only), and all six evidence levels round-trip through the store.

Embedding calls are stubbed (no live Ollama dependency), and paper-qa extraction
is never exercised here (network+LLM) — extract_claim's own try/except is what
keeps a paper-qa failure from blocking the pipeline, tested via handle()'s
config-supplied collect_new_papers stub instead.
"""
import json
import os

import pytest

from store import science_scout_store as store
from store import world_monitor_store as wm_store
from store.knowledge_base import KnowledgeBase
from jobs.handlers import science_scout


@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    db_path = tmp_path / "science_scout_test.db"
    monkeypatch.setattr(store, "DB_PATH", db_path)
    # apply_decision's CREATE path checks the shared cross-source dedup ledger
    # (world_monitor_store) — without isolating it too, this test hits the real
    # prod DB and silently starts failing once a matching finding is recorded there.
    monkeypatch.setattr(wm_store, "DB_PATH", tmp_path / "world_monitor_test.db")
    wm_store.init()
    yield
    if db_path.exists():
        os.remove(db_path)


@pytest.fixture
def kb(tmp_path, monkeypatch):
    def fake_embed(text: str) -> list[float]:
        import hashlib
        h = hashlib.sha256(text.strip().lower().encode()).digest()
        vec = [b / 255.0 for b in h] * 96
        return vec[:768]

    monkeypatch.setattr("store.knowledge_base._embed", fake_embed)
    return KnowledgeBase(db_path=str(tmp_path / "science_scout_test.lance"))


@pytest.mark.parametrize("resp,expected", [
    ('"gut microbiome longevity"', "gut microbiome longevity"),
    ('["gut microbiome longevity"]', "gut microbiome longevity"),
    ("gut microbiome longevity", "gut microbiome longevity"),
    ('```json\n{"query": "gut microbiome longevity"}\n```', "gut microbiome longevity"),
    ('{"new_query": "gut microbiome longevity"}', "gut microbiome longevity"),
    ("null", ""),
])
def test_extract_query_string_normalizes_llm_response(resp, expected):
    assert science_scout._extract_query_string(resp) == expected


def test_exact_dedup_never_reenters_raw_papers(clean_db):
    paper = {"id": "https://arxiv.org/abs/1", "title": "New enzyme found", "url": "https://arxiv.org/abs/1", "niche": "microplastics"}
    fresh = science_scout.exact_dedup([paper])
    assert len(fresh) == 1
    store.insert_paper(paper["id"], paper["niche"], paper["title"], "", paper["url"])

    fresh2 = science_scout.exact_dedup([paper])
    assert fresh2 == []

    dupe_content = {**paper, "id": "https://arxiv.org/abs/1?utm=x"}
    # same title+url content_hash inputs differ only by id -> not an exact content dupe
    # (content_hash keys off title+url, so change url too to prove distinct papers pass)
    distinct = {**paper, "id": "https://arxiv.org/abs/2", "url": "https://arxiv.org/abs/2"}
    fresh3 = science_scout.exact_dedup([distinct])
    assert len(fresh3) == 1


def test_update_decision_bumps_evidence_not_new_row(clean_db, kb):
    claim_id = store.create_claim("PETase variant degrades PET faster", "Observed", ["microplastics"])
    kb.store("PETase variant degrades PET faster", source="science_claim", query=claim_id)

    paper = {
        "id": "https://arxiv.org/abs/9", "title": "Confirms PETase degrades PET faster",
        "snippet": "", "url": "https://arxiv.org/abs/9", "niche": "enzymes",
        "matched_claim": store.get_claim(claim_id),
    }
    science_scout.apply_decision(paper, {"decision": "UPDATE", "evidence_level": "Replicated"}, kb)

    active = store.list_active_claims()
    assert len(active) == 1
    assert active[0]["evidence_count"] == 2
    assert active[0]["evidence_level"] == "Replicated"
    assert json.loads(active[0]["niches_json"]) == ["enzymes", "microplastics"]


def test_create_decision_adds_new_claim_with_evidence_level(clean_db, kb):
    paper = {"id": "https://arxiv.org/abs/5", "title": "Novel enzyme", "snippet": "", "url": "https://arxiv.org/abs/5", "niche": "microplastics"}
    science_scout.apply_decision(
        paper, {"decision": "CREATE", "summary": "Novel enzyme degrades PET", "evidence_level": "Mechanistically supported"}, kb,
    )

    active = store.list_active_claims()
    assert len(active) == 1
    assert active[0]["evidence_level"] == "Mechanistically supported"


def test_create_with_strong_evidence_triggers_immediate_notify(clean_db, kb, monkeypatch):
    calls = []
    monkeypatch.setattr("jobs.notify_gate.decide_and_notify", lambda **kw: calls.append(kw) or True)
    paper = {"id": "https://arxiv.org/abs/6", "title": "Strong finding", "snippet": "", "url": "https://arxiv.org/abs/6", "niche": "microplastics"}
    science_scout.apply_decision(
        paper, {"decision": "CREATE", "summary": "Big finding", "evidence_level": "Observed"}, kb,
    )
    assert len(calls) == 1
    assert calls[0]["source"] == "science_scout"


def test_create_with_weak_evidence_skips_immediate_notify(clean_db, kb, monkeypatch):
    calls = []
    monkeypatch.setattr("jobs.notify_gate.decide_and_notify", lambda **kw: calls.append(kw) or True)
    paper = {"id": "https://arxiv.org/abs/7", "title": "Weak finding", "snippet": "", "url": "https://arxiv.org/abs/7", "niche": "microplastics"}
    science_scout.apply_decision(
        paper, {"decision": "CREATE", "summary": "Speculative finding", "evidence_level": "Speculative"}, kb,
    )
    assert calls == []


@pytest.mark.parametrize("level", list(store.VALID_EVIDENCE_LEVELS))
def test_all_evidence_levels_round_trip(clean_db, level):
    claim_id = store.create_claim("finding", level, ["niche"])
    assert store.get_claim(claim_id)["evidence_level"] == level


def test_invalid_evidence_level_falls_back_to_speculative(clean_db):
    claim_id = store.create_claim("finding", "not-a-real-level", ["niche"])
    assert store.get_claim(claim_id)["evidence_level"] == "Speculative"


def test_links_are_append_only(clean_db):
    claim_id = store.create_claim("X", "Observed", ["niche"])
    store.link("p1", claim_id, "CREATE")
    store.link("p2", claim_id, "UPDATE")
    store.link("p3", claim_id, "UPDATE")
    links = store.list_links_by_decision("UPDATE", since="2000-01-01T00:00:00")
    assert len(links) == 2


def test_handle_uses_config_niche_queries(clean_db, kb, monkeypatch):
    monkeypatch.setattr(science_scout, "collect_new_papers", lambda niches: [])
    result = science_scout.handle({"id": "wf-cfg", "config": {"niche_queries": ["custom niche"]}})
    assert result["niches_scanned"] == ["custom niche"]


def test_paperqa_extraction_failure_falls_back_to_snippet(monkeypatch):
    def boom(url, niche):
        raise RuntimeError("paper-qa unavailable")
    monkeypatch.setattr(science_scout, "_paperqa_extract_async", lambda url, niche: boom(url, niche))
    assert science_scout.extract_claim("https://example.com/paper", "microplastics") == ""
