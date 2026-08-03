"""Tests for jobs/dedup.py — URL and content-hash dedup helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jobs import dedup


# ---------------------------------------------------------------------------
# dedup_by_url
# ---------------------------------------------------------------------------

def test_dedup_by_url_drops_previously_seen():
    items = [{"url": "a"}, {"url": "b"}]
    kept, seen = dedup.dedup_by_url(items, seen=["a"])
    assert [i["url"] for i in kept] == ["b"]
    assert seen == ["a", "b"]


def test_dedup_by_url_drops_duplicates_within_same_batch():
    items = [{"url": "a"}, {"url": "a"}, {"url": "b"}]
    kept, seen = dedup.dedup_by_url(items, seen=[])
    assert [i["url"] for i in kept] == ["a", "b"]
    assert seen == ["a", "b"]


def test_dedup_by_url_skips_empty_and_whitespace_url():
    items = [{"url": ""}, {"url": "   "}, {"url": "a"}]
    kept, seen = dedup.dedup_by_url(items, seen=[])
    assert [i["url"] for i in kept] == ["a"]
    assert seen == ["a"]


def test_dedup_by_url_caps_seen_at_1000():
    old_seen = [f"u{i}" for i in range(1000)]
    kept, seen = dedup.dedup_by_url([{"url": "new"}], seen=old_seen)
    assert len(seen) == 1000
    assert seen[-1] == "new"
    assert seen[0] == "u1"  # oldest entry dropped off the front


def test_dedup_by_url_custom_url_of():
    items = [{"link": "x"}]
    kept, seen = dedup.dedup_by_url(items, seen=[], url_of=lambda it: it["link"])
    assert seen == ["x"]


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------

def test_content_hash_stable_and_case_insensitive():
    h1 = dedup.content_hash("Title", "2026-01-01")
    h2 = dedup.content_hash("title", "2026-01-01")
    assert h1 == h2


def test_content_hash_differs_on_extra_id():
    h1 = dedup.content_hash("t", "d")
    h2 = dedup.content_hash("t", "d", "pmid123")
    assert h1 != h2


# ---------------------------------------------------------------------------
# dedup_by_hash
# ---------------------------------------------------------------------------

def test_dedup_by_hash_drops_seen_hash():
    it = {"title": "Foo", "published_date": "2026-01-01"}
    h = dedup.content_hash("Foo", "2026-01-01")
    kept, hashes, ids = dedup.dedup_by_hash([it], seen_hashes=[h])
    assert kept == []
    assert hashes == [h]


def test_dedup_by_hash_drops_seen_stable_id_even_if_hash_differs():
    it = {"title": "New Title", "published_date": "2026-02-02"}
    kept, hashes, ids = dedup.dedup_by_hash(
        [it], stable_ids=["pmid1"], id_of=lambda i: "pmid1"
    )
    assert kept == []


def test_dedup_by_hash_uses_custom_hash_of():
    it = {"fingerprint": "custom-fp"}
    kept, hashes, ids = dedup.dedup_by_hash(
        [it], seen_hashes=["custom-fp"], hash_of=lambda i: i["fingerprint"]
    )
    assert kept == []


def test_dedup_by_hash_keeps_new_items_and_records_both_lists():
    it = {"title": "A", "published_date": "d"}
    kept, hashes, ids = dedup.dedup_by_hash([it], id_of=lambda i: "id-1")
    assert kept == [it]
    assert len(hashes) == 1
    assert ids == ["id-1"]


# ---------------------------------------------------------------------------
# persist_seen
# ---------------------------------------------------------------------------

def test_persist_seen_merges_into_existing_config(monkeypatch):
    calls = {}

    def fake_update(instance_id, config=None):
        calls["id"] = instance_id
        calls["config"] = config

    monkeypatch.setattr("store.workflow_store.update_workflow_instance", fake_update)

    instance = {"id": "wf-1", "config": {"other_key": "keep-me"}}
    dedup.persist_seen(instance, last_sent_urls=["a", "b"])

    assert calls["id"] == "wf-1"
    assert calls["config"]["other_key"] == "keep-me"
    assert calls["config"]["last_sent_urls"] == ["a", "b"]


def test_persist_seen_noop_when_no_fields(monkeypatch):
    called = []
    monkeypatch.setattr(
        "store.workflow_store.update_workflow_instance",
        lambda *a, **k: called.append(1),
    )
    dedup.persist_seen({"id": "wf-1", "config": {}})
    assert called == []
