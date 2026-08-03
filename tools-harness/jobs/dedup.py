"""Shared dedup primitives for workflows/automations.

Every workflow that emits items (news, papers, emails, attachments) needs to
avoid re-sending what it already sent. Two strategies, both persist their seen
state back into the workflow_instance config so the next run can skip repeats:

  URL dedup      — simplest, per-workflow. Stores the seen URL list.
                   Use when each item has a stable unique URL.

  content-hash   — sha256(title|date) (+ optional stable id like a PMID).
                   Cross-workflow safe: the same paper/article seen by two
                   different workflows dedups against one hash.

The helper functions return (kept_items, updated_seen_state) and a convenience
persist() that writes the seen state back into workflow_store so handlers stay
one line. Capped at 1000 entries to bound config size.

Handler usage:
    from jobs.dedup import dedup_by_url, dedup_by_hash, persist_seen

    kept, seen_urls = dedup_by_url(items, seen=config.get("last_sent_urls", []),
                                   url_of=lambda it: it["url"])
    persist_seen(instance, last_sent_urls=seen_urls)
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Iterable, Sequence

_SEEN_CAP = 1000


def _cap(seq: Sequence[Any]) -> list:
    return list(seq)[-_SEEN_CAP:]


def dedup_by_url(
    items: Iterable[dict],
    seen: Sequence[str] | None = None,
    url_of: Callable[[dict], str] = lambda it: it.get("url", ""),
) -> tuple[list[dict], list[str]]:
    """Drop items whose URL is already in `seen`. Returns (kept, updated_seen).

    `updated_seen` = previous seen + the URLs of kept items (capped).
    """
    seen_set = set(seen or [])
    kept: list[dict] = []
    added: list[str] = []
    for it in items:
        url = (url_of(it) or "").strip()
        if not url or url in seen_set:
            continue
        seen_set.add(url)
        added.append(url)
        kept.append(it)
    # previous seen first, then newly seen (most-recent at the tail)
    updated = list(seen or []) + added
    return kept, _cap(updated)


def content_hash(title: str, date: str = "", extra: str = "") -> str:
    """Stable sha256 of an item. Combine title + date (+ optional id) so the
    same content dedups even if the URL differs (mirror sites, redirects)."""
    payload = f"{title}|{date}|{extra}".strip("|").lower().encode("utf-8", "ignore")
    return hashlib.sha256(payload).hexdigest()


def dedup_by_hash(
    items: Iterable[dict],
    seen_hashes: Sequence[str] | None = None,
    stable_ids: Sequence[str] | None = None,
    hash_of: Callable[[dict], str] | None = None,
    id_of: Callable[[dict], str] = lambda it: "",
) -> tuple[list[dict], list[str], list[str]]:
    """Drop items whose content-hash (or stable id) is already seen.

    Returns (kept, updated_hashes, updated_ids). Both seen lists are capped.
    `hash_of` overrides the default title|date hashing when an item has a
    precomputed fingerprint.
    """
    seen_h = set(seen_hashes or [])
    seen_i = set(stable_ids or [])
    kept: list[dict] = []
    new_h: list[str] = []
    new_i: list[str] = []
    for it in items:
        h = hash_of(it) if hash_of else content_hash(
            it.get("title", ""), it.get("published_date", "") or it.get("date", "")
        )
        if h and h in seen_h:
            continue
        sid = (id_of(it) or "").strip()
        if sid and sid in seen_i:
            continue
        if h:
            seen_h.add(h)
            new_h.append(h)
        if sid:
            seen_i.add(sid)
            new_i.append(sid)
        kept.append(it)
    return kept, _cap(list(seen_hashes or []) + new_h), _cap(list(stable_ids or []) + new_i)


def persist_seen(instance: dict, **seen_fields: Sequence[str]) -> None:
    """Write seen-state fields back into the workflow_instance config.

    Pass the field names/values returned by the dedup helpers, e.g.
        persist_seen(instance, last_sent_urls=seen_urls)
        persist_seen(instance, seen_hashes=hashes, seen_pmids=ids)
    Always merges over the existing config so other keys survive.
    """
    from store import workflow_store

    if not seen_fields:
        return
    config = dict(instance.get("config") or {})
    for key, val in seen_fields.items():
        config[key] = list(val)
    workflow_store.update_workflow_instance(instance["id"], config=config)
