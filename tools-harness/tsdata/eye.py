"""The "god's eye" — one fused view over every source in the lake.

The `tsdata` layer is a pipe: adapters in, tidy observations in a SQLite
catalog out. That is the right shape for *harvesting*, but it is the wrong
shape for *understanding*. Answering "what is the state of the world right
now?" from 23 separate per-source datasets means stitching them together by
hand every single time.

This module is the aggregator layer that sits on top of the catalog. It
answers the cross-domain questions the per-source views cannot:

* Which sources are alive, stale, empty, or broken? (freshness)
* Which series are longest, and how do they overlap in time? (coverage)
* What is the latest value of every series, and how far behind is it? (recency)
* Which series move together? (correlation across domains)

The design deliberately mirrors the "god's eye" spatial-intelligence pattern
one layer up: many independent feeds, each self-describing with its own
license and provenance, fused into a single cockpit view where every number
can be traced back to the feed that produced it. The difference is that our
feeds are *time series* rather than *positions*, and the unit of fusion is a
common time axis rather than a common map projection.
"""
from __future__ import annotations

import math
from typing import Any

#: A series is considered "stale" if its newest observation is older than
#: this multiple of its own natural period. Sources with different cadences
#: (annual macro vs daily equities) cannot share one absolute threshold.
STALE_MULTIPLIER = 3.5

#: Frequencies mapped to an approximate day count, used for staleness.
FREQ_DAYS = {
    "daily": 1.0,
    "event": 7.0,
    "weekly": 7.0,
    "monthly": 30.0,
    "quarterly": 91.0,
    "annual": 365.0,
}


def _parse_ts(ts: str) -> float:
    """Parse an ISO-8601 timestamp to a POSIX float. 0.0 on failure."""
    import datetime as _dt
    if not ts:
        return 0.0
    try:
        d = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.0
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.timestamp()


def _days(ts: str, now: float) -> float:
    """Age of a timestamp in days (negative if in the future)."""
    t = _parse_ts(ts)
    if not t:
        return math.inf
    return (now - t) / 86400.0



class Eye:
    """A fused, cross-domain view of the lake.

    Constructed from a `Lake` (or anything exposing `datasets()` and
    `series()`); reads only, never writes.
    """

    def __init__(self, lake: Any, now: float | None = None):
        import time
        self.lake = lake
        self.now = time.time() if now is None else now
        self._datasets = list(lake.datasets())
        self._series = list(lake.series())

    # ------------------------------------------------------------------
    # per-feed status
    # ------------------------------------------------------------------
    def feeds(self) -> list[dict]:
        """One row per dataset: what it is, how big, and how fresh.

        This is the core of the "god's eye" view — every feed reports its
        own provenance (URL, license) so nothing on the panel is anonymous.
        """
        by_ds: dict[str, list[dict]] = {}
        for s in self._series:
            by_ds.setdefault(s.get("dataset_id") or "", []).append(s)

        out: list[dict] = []
        for d in self._datasets:
            ds_id = d.get("dataset_id") or ""
            kids = by_ds.get(ds_id, [])
            n_obs = d.get("n_obs") or 0
            n_series = d.get("n_series") or 0
            last_ts = d.get("last_ts") or ""
            age = _days(last_ts, self.now)
            freq = (d.get("freq") or "").lower()
            period = FREQ_DAYS.get(freq, 30.0)
            if age == math.inf or n_series == 0:
                status = "empty"
            elif age > STALE_MULTIPLIER * period:
                status = "stale"
            else:
                status = "live"
            out.append({
                "dataset_id": ds_id,
                "source": d.get("source"),
                "domain": d.get("domain") or "",
                "title": d.get("title") or "",
                "freq": freq,
                "n_series": n_series,
                "n_obs": n_obs,
                "first_ts": d.get("first_ts") or "",
                "last_ts": last_ts,
                "age_days": age,
                "status": status,
                "source_url": d.get("source_url") or "",
                "license": d.get("license") or "",
                "acquired_at": d.get("acquired_at") or 0.0,
                "series_ids": [s["series_id"] for s in kids],
            })
        out.sort(key=lambda r: (r["domain"], r["dataset_id"]))
        return out


    def status(self) -> dict:
        """One-line-per-domain summary of feed health."""
        feeds = self.feeds()
        by_domain: dict[str, dict] = {}
        for f in feeds:
            dom = by_domain.setdefault(f["domain"], {
                "domain": f["domain"], "feeds": 0, "live": 0, "stale": 0,
                "empty": 0, "series": 0, "obs": 0,
            })
            dom["feeds"] += 1
            dom[f["status"]] = dom.get(f["status"], 0) + 1
            dom["series"] += f["n_series"]
            dom["obs"] += f["n_obs"]
        return {
            "totals": {
                "feeds": len(feeds),
                "series": sum(f["n_series"] for f in feeds),
                "obs": sum(f["n_obs"] for f in feeds),
                "live": sum(1 for f in feeds if f["status"] == "live"),
                "stale": sum(1 for f in feeds if f["status"] == "stale"),
                "empty": sum(1 for f in feeds if f["status"] == "empty"),
            },
            "domains": sorted(by_domain.values(), key=lambda d: d["domain"]),
            "feeds": feeds,
        }

    # ------------------------------------------------------------------
    # recency
    # ------------------------------------------------------------------
    def latest(self, limit: int = 50, domain: str | None = None) -> list[dict]:
        """The most recent observation of every series, newest first.

        This is the "what is happening now" panel. Each row carries the
        series' own metadata so the value is never orphaned from its source.
        """
        rows: list[dict] = []
        for s in self._series:
            if domain and (s.get("domain") or "") != domain:
                continue
            n_obs = s.get("n_obs") or 0
            if not n_obs:
                continue
            rows.append({
                "series_id": s["series_id"],
                "title": s.get("title") or "",
                "domain": s.get("domain") or "",
                "source": s.get("source") or "",
                "freq": s.get("freq") or "",
                "n_obs": n_obs,
                "first_ts": s.get("first_ts") or "",
                "last_ts": s.get("last_ts") or "",
                "age_days": _days(s.get("last_ts") or "", self.now),
            })
        rows.sort(key=lambda r: r["age_days"])
        return rows[:limit]


    # ------------------------------------------------------------------
    # coverage
    # ------------------------------------------------------------------
    def coverage(self) -> dict:
        """How the feeds line up on a common time axis.

        Returns the global time span plus a per-domain span, and the list
        of series that carry the most history. Sparse or single-point series
        are called out because they are the ones that make a fused panel
        silently wrong.
        """
        spans: list[tuple[str, str, str, str]] = []
        for s in self._series:
            if not (s.get("n_obs") or 0):
                continue
            spans.append((
                s.get("first_ts") or "", s.get("last_ts") or "",
                s.get("domain") or "", s.get("series_id") or "",
            ))
        if not spans:
            return {"first_ts": "", "last_ts": "", "n_series": 0,
                    "by_domain": {}, "longest": [], "thin": []}

        first = min(s[0] for s in spans)
        last = max(s[1] for s in spans)
        by_domain: dict[str, dict] = {}
        for f, l, dom, _ in spans:
            d = by_domain.setdefault(dom, {"first_ts": f, "last_ts": l, "n": 0})
            d["n"] += 1
            if f < d["first_ts"]:
                d["first_ts"] = f
            if l > d["last_ts"]:
                d["last_ts"] = l

        ranked = sorted(self._series, key=lambda s: s.get("n_obs") or 0, reverse=True)
        longest = [{
            "series_id": s["series_id"],
            "title": s.get("title") or "",
            "domain": s.get("domain") or "",
            "n_obs": s.get("n_obs") or 0,
            "first_ts": s.get("first_ts") or "",
            "last_ts": s.get("last_ts") or "",
        } for s in ranked[:20]]
        thin = [{
            "series_id": s["series_id"],
            "domain": s.get("domain") or "",
            "n_obs": s.get("n_obs") or 0,
        } for s in ranked if (s.get("n_obs") or 0) <= 2][:20]

        return {
            "first_ts": first,
            "last_ts": last,
            "n_series": len(spans),
            "by_domain": by_domain,
            "longest": longest,
            "thin": thin,
        }


    # ------------------------------------------------------------------
    # cross-domain correlation
    # ------------------------------------------------------------------
    def correlate(self, top: int = 15, min_obs: int = 60) -> list[dict]:
        """Pairwise correlation between series across different domains.

        Fusing feeds is only interesting if they move together. This
        resamples each series onto a common daily grid and reports the
        strongest cross-domain pairs — the ones that a per-source view
        would never show you.
        """
        import pandas as pd

        buckets: dict[str, pd.Series] = {}
        for s in self._series:
            sid = s["series_id"]
            rows = self.lake.catalog.observations(sid)
            if len(rows) < min_obs:
                continue
            try:
                idx = pd.to_datetime([r[0] for r in rows], utc=True, format="mixed")
            except (ValueError, TypeError):
                continue
            ser = pd.Series([r[1] for r in rows], index=idx).sort_index()
            buckets[sid] = ser
        if len(buckets) < 2:
            return []

        # Daily grid, forward-filled: feeds have different cadences and a
        # ffill puts them on a comparable axis without inventing structure.
        frame = pd.DataFrame(buckets).resample("D").last().ffill()
        frame = frame.dropna(how="all")
        if frame.empty or len(frame) < 10:
            return []

        meta = {s["series_id"]: s for s in self._series}
        corr = frame.corr(min_periods=max(10, len(frame) // 4))
        pairs: list[dict] = []
        ids = list(corr.columns)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                dom_a = (meta.get(a) or {}).get("domain") or ""
                dom_b = (meta.get(b) or {}).get("domain") or ""
                if dom_a and dom_b and dom_a == dom_b:
                    continue  # cross-domain only — that is the point
                v = corr.loc[a, b]
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    continue
                pairs.append({
                    "a": a, "b": b,
                    "a_title": (meta.get(a) or {}).get("title") or a,
                    "b_title": (meta.get(b) or {}).get("title") or b,
                    "domain_a": dom_a, "domain_b": dom_b,
                    "corr": round(float(v), 4),
                    "abs_corr": abs(float(v)),
                })
        pairs.sort(key=lambda p: p["abs_corr"], reverse=True)
        return pairs[:top]
