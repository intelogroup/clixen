"""Weekly SaaS pain miner — public complaints about a niche, tagged, clustered and ranked.

Collects HN comments and Reddit snippets for the configured queries, keeps the
on-niche software pains, groups near-duplicates, and writes the ranking to
data/demand/ranked_<date>.json (one file per run, so weekly runs build a history
that counts and trends can be computed from), then refreshes the weekly HN mention series and writes
data/demand/forecast_<date>.json (attention level and share growth per tracked term).
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def handle(instance: dict) -> dict:
    """instance["config"] keys: niche (str, required), queries (list[str], required),
    keywords (list[str], optional: short terms for sources that match every word, e.g. softwarerecs)."""
    cfg = instance.get("config") or {}
    niche, queries = cfg.get("niche"), cfg.get("queries")
    if not niche or not queries:
        return {"success": False, "items_processed": 0, "error": "config needs 'niche' and 'queries'"}
    try:
        from demand.pain import run
        ranked = run(niche, list(queries), keywords=cfg.get("keywords"))
    except Exception as exc:
        _log.error("demand.pain_miner: %s", exc)
        return {"success": False, "items_processed": 0, "error": str(exc)}
    try:  # the scorecard is a side product; its failure must not fail the pain run
        from demand.scorecard import run as scorecard
        scorecard()
    except Exception as exc:
        _log.warning("demand.pain_miner: scorecard failed: %s", exc)
    return {"success": True, "items_processed": sum(r["count"] for r in ranked)}
