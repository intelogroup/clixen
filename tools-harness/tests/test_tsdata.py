"""Tests for the tsdata lake, HTTP layer, and the "god's eye" aggregator.

These cover the bugs that were actually hit during the first full harvest:

* the CLI's `--source` was single-value, so a multi-source invocation
  silently harvested only the last one;
* `get_json()` wrapped `get()` in a second retry loop (5x5 = 25 requests)
  and a Yahoo 429 turned one symbol into a multi-minute stall;
* the disk cache stored *decoded* bodies but replayed the
  `content-encoding` header, so cache hits raised
  "brotli: decoder failed" / "zlib error";
* the football adapter produced timestamps like `202020-09-12`;
* the OpenAlex adapter needed a paid API key for cursor paging;
* Algolia serves at most 1000 hits per query, so a 6-year Hacker News
  window collapsed to two days of data;
* the CLI's relative `--root` default created a second, nested lake.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tsdata import eye as eye_mod  # noqa: E402
from tsdata import http as http_mod  # noqa: E402
from tsdata import sources as sources_mod  # noqa: E402
from tsdata.cli import main as cli_main  # noqa: E402
from tsdata.lake import DEFAULT_ROOT, Lake  # noqa: E402



# ----------------------------------------------------------------------
# http layer
# ----------------------------------------------------------------------
def test_cache_replay_does_not_double_decode(tmp_path):
    """A cached body is already decoded; replaying content-encoding breaks it.

    The upstream sends `content-encoding: br` with a compressed payload.
    httpx decompresses it for us, so the bytes we cache are plain. Replaying
    the header makes httpx try to decompress again -> DecodingError.
    """
    f = http_mod.Fetcher(tmp_path)
    f._last_response_content = b'{"ok": true}'
    f._last_response_url = "https://example.test/x"
    f._last_response_status = 200
    f._last_response_headers = {
        "content-type": "application/json",
        "content-encoding": "br",
        "content-length": "11",
    }
    f._write_cache("https://example.test/x", None)

    meta_files = list(tmp_path.rglob("*.meta.json"))
    assert meta_files, "cache meta not written"
    meta = json.loads(meta_files[0].read_text())
    assert "content-encoding" not in {k.lower() for k in meta["headers"]}
    assert "content-length" not in {k.lower() for k in meta["headers"]}


def test_get_json_does_not_multiply_retries():
    """get_json must not wrap get() in another retry loop.

    get() already retries 5x. Nesting a second 5x loop meant up to 25
    requests against a throttled host, with backoff sleeps between each.
    """
    calls = {"n": 0}

    class FakeFetcher(http_mod.Fetcher):
        def get(self, url, **kw):  # noqa: D102
            calls["n"] += 1
            raise RuntimeError("boom")

    f = FakeFetcher("/tmp/tsdata-test-unused")
    with pytest.raises(Exception):
        f.get_json("https://example.test/x")
    # 5 attempts inside get() + at most 2 json-parse attempts, not 25.
    assert calls["n"] <= 6, f"too many attempts: {calls['n']}"


def test_per_host_rate_limit_is_separate():
    """Two different hosts must not share a rate-limit slot."""
    f = http_mod.Fetcher("/tmp/tsdata-test-unused")
    f._thin("https://a.example/x")
    f._thin("https://b.example/x")  # would sleep if the slot were global
    assert set(f._last_request) >= {"a.example", "b.example"}



def test_ua_downgrade_on_429():
    """A 429 caused by our long Chrome UA is retried with a short UA."""
    seen: list[str | None] = []

    class FakeResponse:
        def __init__(self, status, ua):
            self.status_code = status
            self.headers = {}
            self._ua = ua

    class FakeClient:
        def get(self, url, params=None, headers=None):
            ua = (headers or {}).get("User-Agent")
            seen.append(ua)
            if ua and ua.startswith("Mozilla/5.0 ("):
                return FakeResponse(429, ua)
            return FakeResponse(200, ua)

        def close(self):
            pass

    f = http_mod.Fetcher("/tmp/tsdata-test-unused")
    f._client = FakeClient()
    browser = {"User-Agent": http_mod.BROWSER_HEADERS["User-Agent"]}
    r = f.get("https://query2.finance.yahoo.com/v8/finance/chart/SPY",
              headers=browser)
    assert r.status_code == 200
    assert seen[0].startswith("Mozilla/5.0 (")
    assert seen[-1] == "Mozilla/5.0"


# ----------------------------------------------------------------------
# source adapters
# ----------------------------------------------------------------------
def test_iso_day_normalises_us_dates():
    assert sources_mod._iso_day("1/22/20") == "2020-01-22"
    assert sources_mod._iso_day("12/5/21") == "2021-12-05"
    assert sources_mod._iso_day("3/9/23") == "2023-03-09"


def test_day_windows_cover_every_day():
    wins = sources_mod._day_windows(1546300800, 1548979200)
    assert len(wins) == 31  # January 2019
    assert all(b - a == 86400 for a, b in wins)
    assert wins[0][0] == 1546300800


def test_month_windows_are_contiguous():
    wins = sources_mod._month_windows(1546300800, 1735689600)
    assert len(wins) == 72  # 6 years


def test_football_season_codes_and_dates():
    """The football adapter must use 4-digit season codes and dd/mm/yy dates."""
    csv_text = (
        "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR\r\n"
        "E0,16/08/24,15:00,Arsenal,Chelsea,2,1,H\r\n"
    )

    class FakeFetcher:
        def get_text(self, url, **kw):
            assert "/mmz4281/2425/" in url, url
            return csv_text

    out = sources_mod._football(FakeFetcher(), "2425")
    assert out, "no series produced"
    ts, val = out[0].points[0]
    assert ts.startswith("2024-08-16"), ts
    assert val == 2.0
    assert out[0].series_id == "football_e0_2425_home_goals"


def test_openalex_uses_meta_count_not_cursor_paging():
    """OpenAlex cursor paging needs a paid key; meta.count does not."""
    calls: list[dict] = []

    class FakeFetcher:
        def get_json(self, url, params=None, **kw):
            calls.append(params or {})
            return {"meta": {"count": 42}}

    out = sources_mod._openalex(FakeFetcher(), "2019-01-01", "2019-03-31")
    assert len(out) == 1
    assert len(out[0].points) == 3
    assert all(v == 42.0 for _ts, v in out[0].points)
    for params in calls:
        assert "cursor" not in params
        assert params["per-page"] == 1


def test_covid_uses_jhu_csv_and_aggregates_provinces():
    csv_text = (
        "Province/State,Country/Region,Lat,Long,1/22/20,1/23/20,1/24/20\r\n"
        "Alberta,Canada,53.9,-116.5,1,2,3\r\n"
        "Ontario,Canada,56.0,-106.0,10,20,30\r\n"
    )

    class FakeFetcher:
        def get_text(self, url, **kw):
            return csv_text


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def test_cli_source_flag_is_repeatable(monkeypatch):
    """--source must accept several values, not just one.

    The original was a plain (non-append) argument, so
    `--source a --source b` silently harvested only `b`.
    """
    seen: list[str] = []

    class FakeLake:
        def __init__(self, root=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def harvest(self, key, force=False):
            seen.append(key)
            return {"source": key, "status": "ok", "n_series": 1, "n_obs": 1}

        def close(self):
            pass

    monkeypatch.setattr("tsdata.cli.Lake", FakeLake)
    rc = cli_main(["harvest", "--source", "ecb_fx", "--source", "usgs_quakes"])
    assert rc == 0
    assert seen == ["ecb_fx", "usgs_quakes"], seen


def test_cli_default_root_is_absolute():
    """A relative --root default created a second, nested lake."""


# ----------------------------------------------------------------------
# the god's eye aggregator
# ----------------------------------------------------------------------
def _make_lake(tmp_path, rows):
    """rows: (dataset_id, domain, freq, n_series, n_obs, first, last)"""
    lake = Lake(root=tmp_path)
    conn = sqlite3.connect(tmp_path / "catalog.db")
    for ds_id, domain, freq, n_s, n_o, first, last in rows:
        conn.execute(
            "INSERT OR REPLACE INTO datasets (dataset_id, source, domain, title,"
            " source_url, license, freq, n_series, n_obs, first_ts, last_ts,"
            " acquired_at, meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ds_id, ds_id, domain, ds_id, f"https://{ds_id}.test", "public",
             freq, n_s, n_o, first, last, 0.0, "{}"),
        )
        for i in range(n_s):
            sid = f"{ds_id}_s{i}"
            conn.execute(
                "INSERT OR REPLACE INTO series (series_id, dataset_id, source,"
                " domain, freq, title, n_obs, first_ts, last_ts, meta_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, ds_id, ds_id, domain, freq, f"{ds_id} series {i}",
                 n_o, first, last, "{}"),
            )
    conn.commit()
    conn.close()
    return lake


def test_eye_marks_stale_and_empty_feeds(tmp_path):
    lake = _make_lake(tmp_path, [
        ("live_feed", "finance", "daily", 2, 100, "2026-10-01", "2026-10-20"),
        ("old_feed", "macro", "annual", 1, 5, "2000-01-01", "2005-01-01"),
        ("dead_feed", "health", "daily", 0, 0, "", ""),
    ])
    e = eye_mod.Eye(lake, now=1760000000.0)
    feeds = {f["dataset_id"]: f for f in e.feeds()}
    assert feeds["live_feed"]["status"] == "live"
    assert feeds["old_feed"]["status"] == "stale"
    assert feeds["dead_feed"]["status"] == "empty"

    st = e.status()
    assert st["totals"]["feeds"] == 3
    assert st["totals"]["live"] == 1
    assert st["totals"]["stale"] == 1
    assert st["totals"]["empty"] == 1
    lake.close()


def test_eye_coverage_reports_span_and_thin_series(tmp_path):
    lake = _make_lake(tmp_path, [
        ("a", "finance", "daily", 2, 50, "2020-01-01", "2024-01-01"),
        ("b", "macro", "annual", 1, 1, "2021-01-01", "2021-01-01"),
    ])
    e = eye_mod.Eye(lake, now=1760000000.0)
    cov = e.coverage()
    assert cov["first_ts"].startswith("2020-01-01")
    assert cov["last_ts"].startswith("2024-01-01")
    assert cov["n_series"] == 3
    assert cov["thin"], "single-observation series should be flagged"
    lake.close()


def test_eye_latest_orders_by_recency(tmp_path):
    lake = _make_lake(tmp_path, [
        ("fresh", "finance", "daily", 1, 10, "2026-10-01", "2026-10-20"),
        ("stale", "macro", "annual", 1, 10, "2001-01-01", "2005-01-01"),
    ])
    e = eye_mod.Eye(lake, now=1760000000.0)
    rows = e.latest(limit=5)
    assert rows[0]["series_id"].startswith("fresh")
    assert rows[-1]["series_id"].startswith("stale")
    lake.close()


def test_eye_handles_empty_lake(tmp_path):
    lake = Lake(root=tmp_path)
    e = eye_mod.Eye(lake)
    assert e.feeds() == []
    assert e.status()["totals"]["feeds"] == 0
    assert e.coverage()["n_series"] == 0
    assert e.correlate() == []
    lake.close()

    assert Path(DEFAULT_ROOT).is_absolute()
    assert "tools-harness/data/tsdata" not in str(DEFAULT_ROOT)


    out = sources_mod._covid(FakeFetcher())
    assert out, "no series produced"
    by_id = {s.series_id: s for s in out}
    series = by_id.get("covid_canada_cases")
    assert series is not None, sorted(by_id)
    # Provinces are summed per day: 1+10, 2+20, 3+30.
    assert [v for _ts, v in series.points] == [11.0, 22.0, 33.0]
    assert series.points[0][0].startswith("2020-01-22")

    for (a, b), (c, _d) in zip(wins, wins[1:]):
        assert b == c
