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


def test_exact_dedup_drops_duplicate_content_within_one_scan(clean_db):
    first = {"id": "https://example.test/1", "title": "Same paper", "url": "https://example.test/paper", "niche": "microplastics"}
    duplicate = {"id": "https://example.test/2", "title": "Same paper", "url": "https://example.test/paper", "niche": "microplastics"}

    fresh = science_scout.exact_dedup([first, duplicate])

    assert fresh == [first]


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


def test_automatic_query_batch_rotates_with_cooldown(clean_db):
    queries = [f"query {i}" for i in range(18)]
    first = store.select_query_batch(queries, scan_number=1, count=3, cooldown_scans=5)
    second = store.select_query_batch(queries, scan_number=2, count=3, cooldown_scans=5)
    for scan in range(3, 7):
        store.select_query_batch(queries, scan_number=scan, count=3, cooldown_scans=5)
    again = store.select_query_batch(queries, scan_number=7, count=3, cooldown_scans=5)

    assert len(set(first) & set(second)) == 0
    assert len(set(first) & set(again)) > 0


def test_query_discovery_planner_returns_fresh_diverse_candidates(clean_db, monkeypatch):
    monkeypatch.setattr(
        "clients.cloud_client.chat",
        lambda **kwargs: '["malaria vaccine updates 2026", "NASA discoveries 2026", "algorithms detecting minerals under Earth"]',
    )

    found = science_scout._discover_query_candidates(["old seed"], scan_number=4)

    assert found == [
        "malaria vaccine updates 2026",
        "NASA discoveries 2026",
        "algorithms detecting minerals under Earth",
    ]


def test_query_pool_self_review_retires_and_adds(clean_db, monkeypatch):
    store.select_query_batch(["weak query"], 1, count=1, cooldown_scans=5)
    monkeypatch.setattr(
        "clients.cloud_client.chat",
        lambda **kwargs: '{"retire": ["weak query"], "add": ["deep Earth mineral discovery"]}',
    )

    science_scout._self_review_query_pool()

    assert store.select_query_batch(["weak query"], 2, count=1, cooldown_scans=5) == []
    assert "deep Earth mineral discovery" in store.discovered_queries()


def test_paperqa_extraction_failure_falls_back_to_snippet(monkeypatch):
    def boom(url, niche):
        raise RuntimeError("paper-qa unavailable")
    monkeypatch.setattr(science_scout, "_paperqa_extract_async", lambda url, niche: boom(url, niche))
    assert science_scout.extract_claim("https://example.com/paper", "microplastics") == ""


def _strong_paper(i: int, niche: str, call_detail: str) -> dict:
    return {
        "id": f"https://arxiv.org/abs/9{i}",
        "title": f"Distinct paper {i}",
        "snippet": f"snippet {i}",
        "url": f"https://arxiv.org/abs/9{i}",
        "niche": niche,
    }


def test_strong_evidence_notify_capped_per_niche(clean_db, kb, monkeypatch):
    calls = []
    monkeypatch.setattr("jobs.notify_gate.decide_and_notify", lambda **kw: calls.append(kw) or True)

    cap = science_scout.NOTIFY_CAP_PER_NICHE_PER_DAY
    # Distinct call_details so the cross-source semantic dedup (Jaccard) does
    # NOT collapse them — each is a genuinely different paper in the same niche.
    details = [
        "Enzyme degrades PET bottles at ambient temperature in controlled trials.",
        "Quantum annealing accelerates protein folding prediction on benchmarks.",
        "CRISPR delivered via lipid nanoparticles reaches brain tissue in mice.",
        "Graphene aerogel filter removes heavy metals from contaminated water.",
        "Organoid model recapitulates early human neural development in vitro.",
    ]
    for i, detail in enumerate(details):
        science_scout.apply_decision(
            _strong_paper(i, "microplastics", detail),
            {"decision": "CREATE", "summary": f"Distinct claim {i}", "evidence_level": "Observed", "call_detail": detail},
            kb,
        )

    assert len(calls) == cap, f"expected {cap} notifications for one niche, got {len(calls)}"

    # A different niche is not blocked by the first niche's cap.
    science_scout.apply_decision(
        _strong_paper(99, "organoids", "Distinct organoid detail about a kidney scaffold."),
        {"decision": "CREATE", "summary": "Other claim", "evidence_level": "Observed", "call_detail": "Distinct organoid detail about a kidney scaffold."},
        kb,
    )
    assert len(calls) == cap + 1

    # The rate-limited findings were still persisted as claims for the weekly digest.
    assert len(store.list_active_claims()) == len(details) + 1


def test_niche_notify_count_increments_and_is_per_niche(clean_db):
    assert store.niche_notify_count("microplastics") == 0
    store.record_niche_notify("microplastics")
    assert store.niche_notify_count("microplastics") == 1
    assert store.niche_notify_count("other") == 0
    store.record_niche_notify("microplastics")
    assert store.niche_notify_count("microplastics") == 2
    assert store.niche_notify_count("other") == 0
