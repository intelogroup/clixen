"""Dual-write contract for science_scout_store.

Local SQLite is the source of truth; the Turso replica must never be able to
break a scan. Pinned here:
  * every write function mirrors exactly one statement, with the same values
    it committed locally (drift = silent divergence),
  * a mirror that raises leaves the local row intact,
  * with Turso unconfigured, nothing changes for the local write.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from store import science_scout_store as store
from store import turso_client


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "ss.db")
    return tmp_path / "ss.db"


@pytest.fixture
def mirrored(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    def fake(sql, params=()):
        calls.append((sql, tuple(params)))
        return True

    monkeypatch.setattr(turso_client, "mirror", fake)
    return calls


def test_insert_paper_mirrors_exact_statement(db, mirrored):
    store.insert_paper("p1", "aging", "Title", "snip", "http://x")
    assert len(mirrored) == 1
    sql, params = mirrored[0]
    assert "INSERT OR IGNORE INTO raw_papers" in sql
    assert params[0] == "p1"
    assert params[5] == "http://x"
    assert params[6] == store.content_hash("Title", "http://x")


def test_claim_bump_and_link_mirror(db, mirrored):
    claim_id = store.create_claim("Senolytics reduce X", "Observed", ["aging"])
    store.bump_claim(claim_id, "longevity", "Replicated")
    store.link("p1", claim_id, "CREATE")

    assert len(mirrored) == 3
    assert "INSERT INTO claims" in mirrored[0][0]
    bump_sql, bump_params = mirrored[1]
    assert "UPDATE claims" in bump_sql
    assert bump_params[0] == 2                    # evidence_count grew
    assert bump_params[2] == "Replicated"         # level is the 3rd arg
    assert "INSERT INTO paper_claim_links" in mirrored[2][0]
    assert mirrored[2][1][2] == "CREATE"


def test_bump_without_level_uses_short_statement(db, mirrored):
    claim_id = store.create_claim("no level", "Observed", [])
    store.bump_claim(claim_id, "aging")
    sql, params = mirrored[-1]
    assert "evidence_level" not in sql
    assert len(params) == 4


def test_meta_niche_perf_alerts_and_prompt_mirror(db, mirrored):
    store.set_meta("k", "v")
    store.record_niche_perf("aging", True)
    store.log_suppressed_alert("science_scout", "finding", "dup")
    store.set_prompt("merge_decision", "prompt text")

    kinds = [sql.split("(")[0].strip() for sql, _ in mirrored]
    assert any("INSERT INTO meta" in k for k in kinds)
    assert any("INSERT INTO niche_perf" in k for k in kinds)
    assert any("INSERT INTO suppressed_alerts" in k for k in kinds)
    assert any("INSERT INTO subagent_prompts" in k for k in kinds)
    niche = next(p for s, p in mirrored if "niche_perf" in s)
    assert niche[0] == "aging" and niche[1] == 1 and niche[2] == 0


def test_query_batch_mirrors_every_reservation(db, mirrored):
    selected = store.select_query_batch(["q1", "q2", "q3"], scan_number=1, count=2)
    assert len(selected) == 2
    assert len(mirrored) == 2
    assert all("query_usage" in sql for sql, _ in mirrored)


def test_mirror_failure_never_breaks_local_writes(db, monkeypatch):
    def boom(sql, params=()):
        raise RuntimeError("turso down")

    monkeypatch.setattr(turso_client, "mirror", boom)
    claim_id = store.create_claim("claim survives", "Observed", ["aging"])
    store.insert_paper("p2", "aging", "T", "s", "u")

    assert store.get_claim(claim_id)["summary"] == "claim survives"
    assert store.paper_exists("p2")


def test_mirror_hook_swallows_import_level_failure(db, monkeypatch):
    """Even if turso_client itself is broken, the local write must land."""
    def explode(sql, params=()):
        raise ImportError("simulated missing module")

    monkeypatch.setattr(turso_client, "mirror", explode)
    claim_id = store.create_claim("still local", "Speculative", [])
    assert store.get_claim(claim_id) is not None


def test_unconfigured_turso_writes_locally_only(db, monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TURSO_DISABLE", raising=False)
    monkeypatch.setattr(turso_client, "_creds_loaded", True)
    monkeypatch.setattr(turso_client, "_ENV_FILE", Path("/nonexistent/.env"))

    claim_id = store.create_claim("offline ok", "Speculative", [])
    assert store.get_claim(claim_id)["id"] == claim_id
    assert turso_client.enabled() is False
