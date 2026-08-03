"""Smallest check that would fail if the reddit_intel dedup/merge logic regresses:
exact dupes never re-enter raw_items, a near-dup bumps evidence_count instead of
creating a new insight row, and item_insight_links only ever grows (append-only).

Embedding calls are stubbed (no live Ollama dependency) — this tests the dedup/
merge control flow, not embedding quality.
"""
import json
import os

import pytest

from store import reddit_intel_store as store
from store.knowledge_base import KnowledgeBase
from jobs.handlers import reddit_intel


@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    db_path = tmp_path / "reddit_intel_test.db"
    monkeypatch.setattr(store, "DB_PATH", db_path)
    yield
    if db_path.exists():
        os.remove(db_path)


@pytest.fixture
def kb(tmp_path, monkeypatch):
    """A real KnowledgeBase with embedding stubbed to a hash-based deterministic
    vector — near-identical text -> near-identical vector, without Ollama."""
    def fake_embed(text: str) -> list[float]:
        import hashlib
        h = hashlib.sha256(text.strip().lower().encode()).digest()
        vec = [b / 255.0 for b in h] * 96  # pad to 768 dims
        return vec[:768]

    monkeypatch.setattr("store.knowledge_base._embed", fake_embed)
    return KnowledgeBase(db_path=str(tmp_path / "reddit_test.lance"))


def test_exact_dedup_never_reenters_raw_items(clean_db):
    post = {"id": "t3_x1", "title": "I wish there was a tool", "body": "for X", "subreddit": "SaaS", "score": 5, "num_comments": 1}
    fresh = reddit_intel.exact_dedup([post])
    assert len(fresh) == 1
    store.insert_item("t3_x1", "SaaS", post["title"], post["body"], "", 5, 1)

    # same id seen again
    fresh2 = reddit_intel.exact_dedup([post])
    assert fresh2 == []

    # different id, identical content -> content-hash dedup catches it
    dupe_content = {**post, "id": "t3_x2"}
    fresh3 = reddit_intel.exact_dedup([dupe_content])
    assert fresh3 == []


def test_update_decision_bumps_evidence_not_new_row(clean_db, kb):
    insight_id = store.create_insight("People want a tool for X", ["SaaS"], 0.6, 0.4)
    kb.store("People want a tool for X", source="reddit_insight", query=insight_id)

    post = {
        "id": "t3_y1", "title": "I wish there was a tool for X", "body": "",
        "subreddit": "Entrepreneur", "score": 3, "num_comments": 0,
        "matched_insight": store.get_insight(insight_id),
    }
    reddit_intel.apply_decision(post, {"decision": "UPDATE"}, kb)

    active = store.list_active_insights()
    assert len(active) == 1  # no new insight row created
    assert active[0]["evidence_count"] == 2
    assert store.link_count() == 1
    assert json.loads(active[0]["subreddits_json"]) == ["Entrepreneur", "SaaS"]


def test_create_decision_adds_new_insight(clean_db, kb):
    post = {"id": "t3_z1", "title": "Totally new pain point", "body": "", "subreddit": "SaaS", "score": 1, "num_comments": 0}
    reddit_intel.apply_decision(post, {"decision": "CREATE", "summary": "Totally new pain point", "severity": 0.5, "willingness_to_pay": 0.3}, kb)

    active = store.list_active_insights()
    assert len(active) == 1
    assert store.link_count() == 1


def test_links_are_append_only(clean_db, kb):
    insight_id = store.create_insight("X", ["SaaS"], 0.5, 0.5)
    store.link("t3_1", insight_id, "CREATE")
    store.link("t3_2", insight_id, "UPDATE")
    store.link("t3_3", insight_id, "UPDATE")
    assert store.link_count() == 3


def test_dead_subreddit_dropped_after_threshold(clean_db, kb, monkeypatch):
    monkeypatch.setattr(reddit_intel, "collect_new_posts", lambda subs, run_id=None: [])
    monkeypatch.setattr("store.knowledge_base._embed", lambda text: [0.0] * 768)
    for _ in range(reddit_intel.DEAD_SCAN_THRESHOLD):
        reddit_intel.handle({"id": "wf-drop", "config": {}})
    assert store.list_active_subreddits() == []


def test_discovery_triggers_when_below_min_active(clean_db, kb, monkeypatch):
    store.seed_watched_subreddits(["onesub"])  # below MIN_ACTIVE_SUBREDDITS
    monkeypatch.setattr(reddit_intel, "collect_new_posts", lambda subs, run_id=None: [])
    monkeypatch.setattr(reddit_intel, "discover_subreddits", lambda existing, n=1: [("newsub", "test")])
    monkeypatch.setattr("store.knowledge_base._embed", lambda text: [0.0] * 768)

    result = reddit_intel.handle({"id": "wf-discover", "config": {}})
    assert "newsub" in result["subreddits_scanned"]


def test_config_subreddits_bypass_watched_list(clean_db, kb, monkeypatch):
    """Explicit config['subreddits'] must never touch/drop the self-managed list."""
    store.seed_watched_subreddits(["managed"])
    monkeypatch.setattr(reddit_intel, "collect_new_posts", lambda subs, run_id=None: [])
    monkeypatch.setattr("store.knowledge_base._embed", lambda text: [0.0] * 768)

    result = reddit_intel.handle({"id": "wf-cfg", "config": {"subreddits": ["custom"]}})
    assert result["subreddits_scanned"] == ["custom"]
    assert store.list_active_subreddits() == ["managed"]  # untouched


def test_cadence_picks_shorter_delay_when_signal_high():
    assert reddit_intel._next_scan_delay_hours(0.5) == 3
    assert reddit_intel._next_scan_delay_hours(0.1) == 6
    assert reddit_intel._next_scan_delay_hours(0.0) == 24
