"""Registry of live web-API time-series sources.

Each source is a `Source` describing *how* to fetch a family of series and
*how* to turn the response into normalised `(series_id, ts, value)` rows.

Why a registry rather than a pile of scripts: the whole point of this package
is to be able to ask "what is out there?" — so every source must be
self-describing (domain, license, frequency, whether it needs a key) and
uniformly harvestable. `harvest_all` walks the registry; adding a source
means adding one entry, not writing a new script.

Normalised record shape (a "Series"):
    series_id : stable string, unique within a dataset
    title     : human-readable
    ts        : ISO-8601 UTC timestamp
    value     : float (NaN allowed for gaps)

Sources that need credentials are still registered, but flagged `needs_key`,
and `harvest_all` skips them unless the env var named in `env_key` is set.
"""
from __future__ import annotations

import calendar
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .http import BROWSER_HEADERS, Fetcher


@dataclass
class Series:
    """One harvested univariate series."""

    series_id: str
    title: str
    points: list[tuple[str, float]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Source:
    """A harvestable family of time series from one API."""

    key: str
    name: str
    domain: str
    freq: str
    license: str = ""
    source_url: str = ""
    needs_key: bool = False
    env_key: str = ""
    notes: str = ""
    harvest: Callable[[Fetcher], Iterable[Series]] = lambda f: []

    def available(self) -> bool:
        return (not self.needs_key) or bool(os.environ.get(self.env_key))


# ----------------------------------------------------------------------
# helpers shared by the source implementations
# ----------------------------------------------------------------------
def _iso_day(s: str) -> str:
    """Normalise a `M/D/YY` or `M/D/YYYY` day string to `YYYY-MM-DD`.

    JHU CSSE columns are `1/22/20`; ISO parsing rejects that, and the raw
    string would otherwise land in the lake unparseable.
    """
    try:
        mm, dd, yy = s.strip().split("/")
        year = int(yy)
        if year < 100:
            year += 2000
        return f"{year:04d}-{int(mm):02d}-{int(dd):02d}"
    except (ValueError, AttributeError):
        return s


def _iso(ts: Any) -> str:
    """Normalise a timestamp to ISO-8601 UTC.

    Accepts: datetime, unix-seconds float/int, unix-milliseconds (if too
    large for the year range), and ISO-8601 strings (with Z or +00:00).
    """
    import datetime as _dt

    if isinstance(ts, str):
        s = ts.replace("Z", "+00:00")
        try:
            d = _dt.datetime.fromisoformat(s)
        except ValueError:
            return ts
    elif isinstance(ts, (int, float)):
        val = float(ts)
        # unix milliseconds (> 1e12) vs seconds (< 1e12)
        if val > 1e12:
            val /= 1000.0
        d = _dt.datetime.fromtimestamp(val, tz=_dt.timezone.utc)
    elif isinstance(ts, _dt.datetime):
        d = ts
    else:
        return str(ts)
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc).isoformat()


def _f(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return f


def _clean(points: Iterable[tuple[str, float]]) -> list[tuple[str, float]]:
    """Drop NaN values and de-duplicate on timestamp, keeping order."""
    seen: dict[str, float] = {}
    out: list[tuple[str, float]] = []
    for ts, val in points:
        fv = _f(val)
        if fv != fv:  # NaN
            continue
        key = _iso(ts)
        if key in seen:
            continue
        seen[key] = fv
        out.append((key, fv))
    out.sort(key=lambda p: p[0])
    return out


def _day_windows(start_epoch: int, end_epoch: int) -> list[tuple[int, int]]:
    """Split an epoch range into UTC day windows.

    Used by APIs that cap a single query at a fixed number of hits — Algolia
    serves at most 1000 per request, so a multi-year window has to be
    fetched a day at a time.
    """
    import datetime as _dt
    cur = _dt.datetime.fromtimestamp(start_epoch, tz=_dt.timezone.utc)
    day0 = cur.replace(hour=0, minute=0, second=0, microsecond=0)
    end = _dt.datetime.fromtimestamp(end_epoch, tz=_dt.timezone.utc)
    windows: list[tuple[int, int]] = []
    while day0 < end:
        nxt = day0 + _dt.timedelta(days=1)
        windows.append((int(day0.timestamp()), int(nxt.timestamp())))
        day0 = nxt
    return windows


def _month_windows(start_epoch: int, end_epoch: int) -> list[tuple[int, int]]:
    """Split an epoch range into calendar-month windows (UTC).

    Used by APIs that report a wrong `nbPages` for wide windows — Algolia
    computes it against the page size, so a 6-year window claims one page.
    Monthly windows keep the reported page count meaningful.
    """
    import datetime as _dt
    cur = _dt.datetime.fromtimestamp(start_epoch, tz=_dt.timezone.utc)
    end = _dt.datetime.fromtimestamp(end_epoch, tz=_dt.timezone.utc)
    windows: list[tuple[int, int]] = []
    while cur < end:
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1, day=1)
        else:
            nxt = cur.replace(month=cur.month + 1, day=1)
        win_end = min(int(nxt.timestamp()), end_epoch)
        windows.append((int(cur.timestamp()), win_end))
        cur = nxt
    return windows

# ======================================================================
# Climate / weather / environment
# ======================================================================
def _open_meteo(f: Fetcher, lat: float, lon: float, start: str, end: str,
                 hourly: list[str], daily: list[str] | None = None) -> list[Series]:
    """Open-Meteo archive (ERA5 reanalysis) — hourly history back to 1940."""
    params: dict[str, Any] = {
        "latitude": lat, "longitude": lon,
        "start_date": start, "end_date": end,
        "hourly": ",".join(hourly), "timezone": "UTC",
    }
    if daily:
        params["daily"] = ",".join(daily)
    data = f.get_json("https://archive-api.open-meteo.com/v1/archive", params=params)
    out: list[Series] = []
    h = data.get("hourly") or {}
    hts = h.get("time") or []
    for var in hourly:
        vals = h.get(var) or []
        pts = [(_iso(t), v) for t, v in zip(hts, vals)]
        out.append(Series(
            series_id=f"openmeteo_era5_{var}_{lat}_{lon}",
            title=f"ERA5 {var} @ {lat},{lon}",
            points=_clean(pts),
            meta={"latitude": lat, "longitude": lon, "variable": var, "provider": "open-meteo"},
        ))
    d = data.get("daily") or {}
    dts = d.get("time") or []
    for var in (daily or []):
        vals = d.get(var) or []
        pts = [(_iso(t), v) for t, v in zip(dts, vals)]
        out.append(Series(
            series_id=f"openmeteo_era5_daily_{var}_{lat}_{lon}",
            title=f"ERA5 daily {var} @ {lat},{lon}",
            points=_clean(pts),
            meta={"latitude": lat, "longitude": lon, "variable": var, "provider": "open-meteo"},
        ))
    return out


def _nasa_power(f: Fetcher, lat: float, lon: float, start: str, end: str,
                params_list: list[str]) -> list[Series]:
    """NASA POWER — satellite/derived meteorology, 1940+."""
    url = f"https://power.larc.nasa.gov/api/temporal/daily/point"
    data = f.get_json(url, params={
        "parameters": ",".join(params_list), "community": "RE",
        "longitude": lon, "latitude": lat,
        "start": start, "end": end, "format": "JSON",
    })
    pdata = (data.get("properties") or {}).get("parameter") or {}
    out: list[Series] = []
    for var, series_map in pdata.items():
        pts = [(_iso(k), v) for k, v in series_map.items()]
        out.append(Series(
            series_id=f"nasa_power_daily_{var}_{lat}_{lon}",
            title=f"POWER {var} @ {lat},{lon}",
            points=_clean(pts),
            meta={"latitude": lat, "longitude": lon, "variable": var, "provider": "nasa-power"},
        ))
    return out


def _noaa_tides(f: Fetcher, station: str, begin: str, end: str) -> list[Series]:
    """NOAA CO-OPS tide/water levels.

    The datagetter API enforces a hard 31-day range limit per request and
    answers anything longer with a 400 ("Range Limit Exceeded"). The
    requested window is therefore split into <=31-day chunks and the rows
    are concatenated.
    """
    import datetime as _dt

    def _parse(s: str) -> _dt.date:
        s = str(s).strip()
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return _dt.datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"unparseable date: {s!r}")

    start, stop = _parse(begin), _parse(end)
    pts: list[tuple[str, float]] = []
    cur = start
    while cur <= stop:
        chunk_end = min(cur + _dt.timedelta(days=30), stop)
        data = f.get_json(
            "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter",
            params={
                "begin_date": cur.strftime("%Y%m%d"),
                "end_date": chunk_end.strftime("%Y%m%d"),
                "station": station,
                "datum": "MLLW", "product": "water_level",
                "time_zone": "gmt", "units": "metric", "format": "json",
            },
        )
        for d in data.get("data", []) or []:
            if d.get("v") and d.get("t"):
                pts.append((d["t"], d["v"]))
        cur = chunk_end + _dt.timedelta(days=1)

    return [Series(
        series_id=f"noaa_tides_{station}",
        title=f"NOAA water level {station}",
        points=_clean(pts),
        meta={"station": station, "provider": "noaa-coops"},
    )]


def _usgs_water(f: Fetcher, sites: str, start: str, end: str) -> list[Series]:
    """USGS NWIS streamflow / gage height."""
    data = f.get_json("https://waterservices.usgs.gov/nwis/dv/", params={
        "format": "json", "sites": sites, "startDT": start, "endDT": end,
        "parameterCd": "00060,00065",
    })
    out: list[Series] = []
    for ts in (data.get("value") or {}).get("timeSeries", []):
        var = ts.get("variable", {})
        code = var.get("variableCode", [{}])[0].get("value", "unknown")
        site = ts.get("sourceInfo", {}).get("siteCode", [{}])[0].get("value", "site")
        pts = []
        for v in ts.get("values", [{}])[0].get("value", []):
            pts.append((v.get("dateTime"), v.get("value")))
        out.append(Series(
            series_id=f"usgs_water_{site}_{code}",
            title=f"USGS {code} @ {site}",
            points=_clean(pts),
            meta={"site": site, "variable": code, "provider": "usgs-nwis"},
        ))
    return out


def _usgs_quakes(f: Fetcher) -> list[Series]:
    """USGS earthquake feed — event timestamps bucketed by magnitude band."""
    data = f.get_json("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson")
    buckets: dict[str, list[tuple[str, float]]] = {}
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        mag = props.get("mag")
        t = props.get("time")
        if mag is None or t is None:
            continue
        band = "all"
        for thr in (4.5, 2.5, 1.0):
            if mag >= thr:
                band = f"m{thr}+"
        buckets.setdefault(band, []).append((_iso(t), 1.0))
    out = []
    for band, pts in buckets.items():
        out.append(Series(
            series_id=f"usgs_quakes_{band.replace('+', 'plus')}",
            title=f"USGS earthquakes {band} (event count)",
            points=_clean(pts),
            meta={"provider": "usgs-earthquakes", "band": band},
        ))
    return out

# ======================================================================
# Macro / finance / energy
# ======================================================================
def _world_bank(f: Fetcher, indicator: str, countries: str = "all") -> list[Series]:
    """World Bank WDI — long-run annual macro per country.

    Two things the original got wrong:

    * The envelope's first element is a *dict* of pagination metadata
      (`{"page": 1, "pages": 2, "total": 9310, ...}`), not a list — indexing
      it with `[0]` raised `KeyError: 0`.
    * `per_page=5000` still leaves multiple pages (9310 rows for the GDP
      indicator), so only the first slice was ever parsed. The page count is
      now read from the metadata and every page is fetched.
    """
    rows: list[dict] = []
    meta: dict = {}
    page = 1
    while page <= 20:  # hard stop; WDI never needs more
        data = f.get_json(
            f"https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}",
            params={"format": "json", "per_page": 5000, "date": "1990:2024",
                    "page": page},
        )
        if not isinstance(data, list) or len(data) < 2:
            break
        if isinstance(data[0], dict):
            meta = data[0]
        chunk = data[1] or []
        rows.extend(chunk)
        pages = int(meta.get("pages") or 1) if isinstance(meta, dict) else 1
        if not chunk or page >= pages:
            break
        page += 1

    by_country: dict[str, list[tuple[str, float]]] = {}
    names: dict[str, str] = {}
    indicator_name = ""
    for r in rows:
        cc = r.get("countryiso3code") or r.get("country", {}).get("id")
        year = r.get("date")
        val = r.get("value")
        if not cc or not year or val is None:
            continue
        by_country.setdefault(cc, []).append((f"{year}-01-01", val))
        names[cc] = r.get("country", {}).get("value", cc)
        if not indicator_name:
            indicator_name = (r.get("indicator") or {}).get("value", "")
    return [
        Series(
            series_id=f"worldbank_{indicator}_{cc}",
            title=f"{indicator} — {names.get(cc, cc)}",
            points=_clean(pts),
            meta={"country": cc, "indicator": indicator, "provider": "worldbank-wdi",
                  "indicator_name": indicator_name},
        )
        for cc, pts in sorted(by_country.items())
    ]


def _ecb_fx(f: Fetcher, currency: str, start: str, end: str) -> list[Series]:
    """ECB euro reference exchange rates — daily since 1999."""
    csv = f.get_text(
        f"https://data-api.ecb.europa.eu/service/data/EXR/D.{currency}.EUR.SP00.A",
        params={"format": "csvdata", "startPeriod": start, "endPeriod": end},
    )
    lines = csv.strip().splitlines()
    if not lines:
        return []
    header = lines[0].split(",")
    try:
        ti = header.index("TIME_PERIOD")
        vi = header.index("OBS_VALUE")
    except ValueError:
        return []
    pts = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) <= max(ti, vi):
            continue
        pts.append((parts[ti], parts[vi]))
    return [Series(
        series_id=f"ecb_fx_{currency}",
        title=f"ECB EUR/{currency} reference rate",
        points=_clean(pts),
        meta={"currency": currency, "provider": "ecb-sdmx"},
    )]


def _frankfurter_fx(f: Fetcher, base: str, quote: str, start: str, end: str) -> list[Series]:
    """Frankfurter (ECB-derived) FX — free, no key."""
    data = f.get_json(
        f"https://api.frankfurter.app/{start}..{end}",
        params={"from": base, "to": quote},
    )
    rates = data.get("rates") or {}
    pts = [(d, v.get(quote)) for d, v in rates.items()]
    return [Series(
        series_id=f"frankfurter_{base}_{quote}",
        title=f"{base}/{quote} FX",
        points=_clean(pts),
        meta={"base": base, "quote": quote, "provider": "frankfurter"},
    )]


def _crypto_candles(f: Fetcher, symbol: str, start: str, end: str) -> list[Series]:
    """Kraken OHLC — daily crypto candles, long history."""
    import datetime as _dt
    s = int(_dt.datetime.fromisoformat(start).replace(tzinfo=_dt.timezone.utc).timestamp())
    data = f.get_json("https://api.kraken.com/0/public/OHLC", params={
        "pair": symbol, "interval": 1440, "since": s,
    })
    result = data.get("result") or {}
    key = next(iter(result), None)
    if not key:
        return []
    rows = result[key]
    out: list[Series] = []
    for field_name, idx in (("open", 1), ("high", 2), ("low", 3), ("close", 4), ("volume", 5)):
        pts = [(row[0], row[idx]) for row in rows if len(row) > idx]
        out.append(Series(
            series_id=f"kraken_{key}_{field_name}",
            title=f"Kraken {key} {field_name}",
            points=_clean(pts),
            meta={"pair": key, "field": field_name, "provider": "kraken"},
        ))
    return out


def _treasury_rates(f: Fetcher) -> list[Series]:
    """US Treasury yield curve — daily."""
    data = f.get_json(
        "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/avg_interest_rates",
        params={"page[size]": 5000, "sort": "record_date"},
    )
    by_desc: dict[str, list[tuple[str, float]]] = {}
    for r in data.get("data", []):
        d = r.get("record_date")
        desc = r.get("security_desc")
        val = r.get("avg_interest_rate_amt")
        if not d or not desc or val is None:
            continue
        by_desc.setdefault(desc, []).append((d, val))
    return [
        Series(
            series_id=f"treasury_{desc.lower().replace(' ', '_').replace('-', '_')}",
            title=f"Treasury avg interest rate — {desc}",
            points=_clean(pts),
            meta={"security": desc, "provider": "us-treasury-fiscaldata"},
        )
        for desc, pts in sorted(by_desc.items())
    ]


def _nyfed_rates(f: Fetcher, start: str, end: str) -> list[Series]:
    """NY Fed SOFR — daily."""
    data = f.get_json(
        "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json",
        params={"startDate": start, "endDate": end, "type": "rate"},
    )
    pts = [(r.get("effectiveDate"), r.get("percentRate"))
           for r in data.get("refRates", [])]
    return [Series(
        series_id="nyfed_sofr",
        title="SOFR (Secured Overnight Financing Rate)",
        points=_clean(pts),
        meta={"provider": "ny-fed-markets"},
    )]


def _bls(f: Fetcher, series_ids: list[str], start: str, end: str) -> list[Series]:
    """BLS labour statistics — monthly (v2 POST endpoint)."""
    payload = {"seriesid": series_ids, "startyear": start[:4], "endyear": end[:4]}
    r = f.client.post(
        "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    r.raise_for_status()
    data = r.json()
    out: list[Series] = []
    for s in (data.get("Results") or {}).get("series", []):
        sid = s.get("seriesID")
        pts = []
        for d in s.get("data", []):
            y = d.get("year")
            m = str(d.get("period", "")).replace("M", "")
            if not y or not m.isdigit():
                continue
            pts.append((f"{y}-{int(m):02d}-01", d.get("value")))
        out.append(Series(
            series_id=f"bls_{sid}",
            title=f"BLS {sid}",
            points=_clean(pts),
            meta={"series_id": sid, "provider": "bls"},
        ))
    return out


def _yahoo_ohlc(f: Fetcher, symbol: str, range_: str = "5y") -> list[Series]:
    """Yahoo Finance chart API — daily OHLCV for equities/ETFs."""
    data = f.get_json(
        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"range": range_, "interval": "1d"},
        headers=BROWSER_HEADERS,
    )
    res = ((data.get("chart") or {}).get("result") or [None])[0]
    if not res:
        return []
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    out = []
    for field_name in ("open", "high", "low", "close", "volume"):
        vals = q.get(field_name) or []
        pts = list(zip(ts, vals))
        out.append(Series(
            series_id=f"yahoo_{symbol.replace('^', '').replace('=', '').replace('.', '_')}_{field_name}",
            title=f"{symbol} {field_name}",
            points=_clean(pts),
            meta={"symbol": symbol, "field": field_name, "provider": "yahoo-finance"},
        ))
    return out

# ======================================================================
# Web / social / health / transport / sport
# ======================================================================
def _hackernews(f: Fetcher, start: int, end: int) -> list[Series]:
    """Hacker News via Algolia — daily point counts (attention signal).

    Algolia caps a single query at 1000 hits: at `hitsPerPage=1000` it
    serves exactly one page and reports `nbPages: 1`, so a 6-year window
    collapsed to a single page of 1000 stories (two days of data). And
    `hitsPerPage=100` caps at 10 pages, so a month (25k hits) is still
    truncated.

    The robust approach is to query day by day: HN never exceeds ~1.1k
    stories in a day, so one page of 1000 is not always enough either —
    a day that overflows is detected (`nbHits > hits returned`) and
    re-queried in half-day slices. Days that fit are fetched in a single
    request, which keeps the whole 6-year harvest to ~2200 requests.
    """
    buckets: dict[str, list[tuple[str, float]]] = {}

    def _fetch(lo: int, hi: int) -> tuple[int, list[dict]]:
        data = f.get_json("https://hn.algolia.com/api/v1/search_by_date", params={
            "tags": "story",
            "numericFilters": f"created_at_i>{lo},created_at_i<{hi}",
            "hitsPerPage": 1000, "page": 0,
        })
        return int(data.get("nbHits") or 0), (data.get("hits") or [])

    def _absorb(hits: list[dict]) -> None:
        for h in hits:
            ts, pts = h.get("created_at"), h.get("points")
            if not ts or pts is None:
                continue
            day = f"{ts[:10]}T00:00:00Z"
            buckets.setdefault("points", []).append((day, pts))
            buckets.setdefault("stories", []).append((day, 1.0))

    for day_start, day_end in _day_windows(start, end):
        n_hits, hits = _fetch(day_start, day_end)
        if n_hits <= len(hits):
            _absorb(hits)
            continue
        # Overflow: the day holds more than one page can carry, so split it.
        mid = (day_start + day_end) // 2
        for lo, hi in ((day_start, mid), (mid, day_end)):
            n2, h2 = _fetch(lo, hi)
            if n2 <= len(h2):
                _absorb(h2)
            else:
                quarter = (hi - lo) // 2
                for a, b in ((lo, lo + quarter), (lo + quarter, hi)):
                    _, h3 = _fetch(a, b)
                    _absorb(h3)

    out = []
    for name, pts in buckets.items():
        agg: dict[str, float] = {}
        for ts, v in pts:
            agg[ts] = agg.get(ts, 0.0) + v
        out.append(Series(
            series_id=f"hn_{name}_daily",
            title=f"Hacker News {name} (daily total)",
            points=_clean(sorted(agg.items())),
            meta={"provider": "hn-algolia", "metric": name},
        ))
    return out


def _nyc_311(f: Fetcher, start: str, end: str) -> list[Series]:
    """NYC 311 service requests — daily counts (urban demand proxy)."""
    rows: list[dict] = []
    offset = 0
    while len(rows) < 20000:
        data = f.get_json(
            "https://data.cityofnewyork.us/resource/erm2-nwe9.json",
            params={
                "$limit": 2000, "$offset": offset,
                "$where": f"created_date between '{start}T00:00:00' and '{end}T23:59:59'",
                "$order": "created_date",
            },
        )
        if not isinstance(data, list) or not data:
            break
        rows.extend(data)
        if len(data) < 2000:
            break
        offset += 2000
    agg: dict[str, float] = {}
    for r in rows:
        cd = r.get("created_date")
        if cd:
            agg[cd[:10]] = agg.get(cd[:10], 0.0) + 1.0
    return [Series(
        series_id="nyc311_requests_daily",
        title="NYC 311 service requests (daily count)",
        points=_clean(sorted(agg.items())),
        meta={"provider": "nyc-open-data", "rows": len(rows)},
    )]


def _football(f: Fetcher, season: str) -> list[Series]:
    """football-data.co.uk — English Premier League results.

    The season path segment is a 4-digit code where the first two digits are
    the century+decade of the *start* year: "2122" = 2021/22, "2425" =
    2024/25. A plain "2024" is a 300 error. The `Date` column is dd/mm/yy.
    """
    import csv as _csv
    import io
    text = f.get_text(f"https://www.football-data.co.uk/mmz4281/{season}/E0.csv")
    # The CSV ships a UTF-8 BOM and the first column is a stray "Div".
    text = text.lstrip("﻿")
    rows = list(_csv.DictReader(io.StringIO(text)))
    out: list[Series] = []
    for side, label in (("FTHG", "home"), ("FTAG", "away")):
        pts = []
        for r in rows:
            d, v = r.get("Date"), r.get(side)
            if not d or not v:
                continue
            try:
                dd, mm, yy = d.split("/")
            except ValueError:
                continue
            year = 2000 + int(yy) if len(yy) == 2 else int(yy)
            pts.append((f"{year:04d}-{int(mm):02d}-{int(dd):02d}T00:00:00Z", v))
        out.append(Series(
            series_id=f"football_e0_{season}_{label}_goals",
            title=f"EPL {season} {label} goals",
            points=_clean(pts),
            meta={"season": season, "provider": "football-data.co.uk"},
        ))
    return out


def _covid(f: Fetcher) -> list[Series]:
    """COVID-19 daily cases/deaths by country (JHU CSSE time series).

    The old disease.sh `/v3/covid-19/countries` endpoint no longer carries a
    `timeline` object — WHO stopped publishing daily country counts, and the
    mirror now returns only 30 trailing days, so the adapter produced nothing
    at all. The JHU CSSE GitHub time-series CSVs are the canonical archive
    (daily columns from 2020-01-22 to 2023-03-09) and are still served.
    """
    import csv as _csv
    import io

    base = ("https://raw.githubusercontent.com/CSSEGISandData/COVID-19/master/"
            "csse_covid_19_data/csse_covid_19_time_series/")
    files = {
        "cases": "time_series_covid19_confirmed_global.csv",
        "deaths": "time_series_covid19_deaths_global.csv",
    }
    out: list[Series] = []
    for metric, fname in files.items():
        text = f.get_text(base + fname)
        rows = list(_csv.reader(io.StringIO(text)))
        if not rows:
            continue
        header = rows[0]
        day_cols = header[4:]
        by_country: dict[str, list[tuple[str, float]]] = {}
        for row in rows[1:]:
            if len(row) < 5:
                continue
            country = row[1].strip()
            if not country:
                continue
            for day, cell in zip(day_cols, row[4:]):
                try:
                    val = float(cell)
                except (TypeError, ValueError):
                    continue
                by_country.setdefault(country, []).append((_iso_day(day), val))
        for country, pts in sorted(by_country.items()):
            if len(pts) < 30:
                continue
            # JHU lists one row per province; several countries have many, so
            # the same day appears more than once. Sum per day, then let
            # _clean() de-duplicate.
            per_day: dict[str, float] = {}
            for day, val in pts:
                per_day[day] = per_day.get(day, 0.0) + val
            slug = country.lower().replace(" ", "_").replace("*", "")
            out.append(Series(
                series_id=f"covid_{slug}_{metric}",
                title=f"COVID-19 {metric} — {country}",
                points=_clean(sorted(per_day.items())),
                meta={"country": country, "metric": metric, "provider": "JHU-CSSE"},
            ))
    return out


def _who_gho(f: Fetcher, indicator: str = "WHOSIS_000001") -> list[Series]:
    """WHO Global Health Observatory — annual country indicators.

    The `$filter=TimeDim eq 2020` variant returned a single year, so every
    series held exactly one observation and the lake had no usable time axis.
    Fetching the indicator unfiltered returns the full year range in one
    request, and the country rows are picked out by `SpatialDimType` so the
    series are comparable across countries.
    """
    data = f.get_json(f"https://ghoapi.azureedge.net/api/{indicator}")
    rows = data.get("value") or []
    by_country: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        cc, val, year = r.get("SpatialDim"), r.get("NumericValue"), r.get("TimeDim")
        if not cc or val is None or not year:
            continue
        if r.get("SpatialDimType") not in (None, "COUNTRY"):
            continue
        by_country.setdefault(cc, []).append((f"{year}-01-01", val))
    return [
        Series(
            series_id=f"who_{indicator}_{cc}",
            title=f"WHO {indicator} — {cc}",
            points=_clean(pts),
            meta={"country": cc, "indicator": indicator, "provider": "who-gho"},
        )
        for cc, pts in sorted(by_country.items())
    ]


def _openalex(f: Fetcher, start: str, end: str) -> list[Series]:
    """OpenAlex scholarly output — monthly publication counts.

    Cursor paging needs a paid API key, and `group_by` refuses to group by a
    date field, so the old "page through every work and count them locally"
    approach could not run without credentials. `meta.count` is the total
    matching the filter, though, so one cheap request per month
    (`per-page=1`) gives the exact count without paging at all.
    """
    months: list[str] = []
    y0, m0 = (int(x) for x in start.split("-")[:2])
    end_parts = end.split("-")
    y1, m1 = int(end_parts[0]), int(end_parts[1])
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1

    agg: dict[str, float] = {}
    for month in months:
        y, m = int(month[:4]), int(month[5:7])
        last_day = calendar.monthrange(y, m)[1]
        lo = f"{month}-01"
        hi = f"{month}-{last_day:02d}"
        data = f.get_json("https://api.openalex.org/works", params={
            "filter": f"from_publication_date:{lo},to_publication_date:{hi}",
            "per-page": 1,
        })
        count = (data.get("meta") or {}).get("count")
        if count is not None:
            agg[month] = float(count)

    pts = [(f"{m}-01", v) for m, v in sorted(agg.items())]
    return [Series(
        series_id="openalex_publications_monthly",
        title="OpenAlex publication count (monthly)",
        points=_clean(pts),
        meta={"provider": "openalex", "method": "meta.count per month"},
    )]


def _gdelt(f: Fetcher, query: str) -> list[Series]:
    """GDELT 2.1 DOC timeline — daily news volume for a query."""
    data = f.get_json("https://api.gdeltproject.org/api/v2/doc/doc", params={
        "query": query, "mode": "TimelineVolRaw", "format": "json",
        "maxrecords": 250, "timespan": "1y",
    })
    timeline = (data.get("timeline") or [{}])[0]
    pts = []
    for k, v in (timeline.get("data") or []):
        day = k[:8]
        pts.append((f"{day[:4]}-{day[4:6]}-{day[6:8]}", v))
    return [Series(
        series_id=f"gdelt_{query.replace(' ', '_')[:40]}",
        title=f"GDELT news volume — {query}",
        points=_clean(pts),
        meta={"query": query, "provider": "gdelt-2.1"},
    )]

# ======================================================================
# The registry
# ======================================================================
#: Default harvest window for sources that accept a date range. Five years
#: keeps the payload sane while still giving enough history for a 24-month
#: forecast backtest.
DEFAULT_START = "2019-01-01"
DEFAULT_END = "2024-12-31"

#: A spread of cities across climate zones — forecasting needs the seasonal
#: and daily patterns to actually differ across regimes.
_DEFAULT_CITIES: list[tuple[float, float]] = [
    (52.52, 13.41),    # Berlin
    (40.71, -74.01),   # New York
    (1.35, 103.82),    # Singapore
    (51.51, -0.13),    # London
    (-23.55, -46.63),  # São Paulo
    (-1.29, 36.82),    # Nairobi
    (35.68, 139.69),   # Tokyo
    (-33.87, 151.21),  # Sydney
    (34.05, -118.24),  # Los Angeles
    (29.76, -95.37),   # Houston
    (19.43, -99.13),   # Mexico City
    (6.52, 3.38),      # Lagos
    (30.04, 31.24),    # Cairo
    (55.76, 37.62),    # Moscow
    (19.08, 72.88),    # Mumbai
    (-6.21, 106.85),   # Jakarta
    (-33.45, -70.67),  # Santiago
    (43.65, -79.38),   # Toronto
    (48.86, 2.35),     # Paris
    (50.11, 8.68),     # Frankfurt
    (40.42, -3.70),    # Madrid
    (41.90, 12.50),    # Rome
    (41.01, 28.98),    # Istanbul
    (37.57, 126.98),   # Seoul
    (22.54, 114.06),   # Shenzhen
    (25.20, 55.27),    # Dubai
    (-26.20, 28.05),   # Johannesburg
    (-12.05, -77.04),  # Lima
    (4.71, -74.07),    # Bogotá
    (-36.85, 174.76),  # Auckland
]


SOURCES: list[Source] = [
    Source(
        key="openmeteo_era5",
        name="Open-Meteo ERA5 reanalysis",
        domain="climate",
        freq="hourly",
        license="CC BY 4.0 (ERA5: Copernicus)",
        source_url="https://open-meteo.com/en/docs/historical-weather-api",
        notes="Hourly reanalysis 1940+. Multi-city grid spanning tropical, "
              "arid, temperate, subarctic and oceanic regimes.",
        harvest=lambda f: [
            s
            for lat, lon in _DEFAULT_CITIES
            for s in _open_meteo(
                f, lat, lon, "2019-01-01", "2024-12-31",
                ["temperature_2m", "precipitation", "wind_speed_10m",
                 "relative_humidity_2m", "surface_pressure"],
                ["temperature_2m_max", "temperature_2m_min", "precipitation_sum"],
            )
        ],
    ),
    Source(
        key="nasa_power",
        name="NASA POWER climatology",
        domain="climate",
        freq="daily",
        license="Public domain (NASA)",
        source_url="https://power.larc.nasa.gov/docs/",
        notes="Satellite + derived meteorology, 1981+.",
        harvest=lambda f: [
            s
            for lat, lon in _DEFAULT_CITIES
            for s in _nasa_power(
                f, lat, lon, "20000101", "20241231",
                ["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR", "PS", "WS2M", "RH2M"],
            )
        ],
    ),
    Source(
        key="noaa_tides",
        name="NOAA CO-OPS tide gauges",
        domain="climate",
        freq="6min",
        license="Public domain (NOAA)",
        source_url="https://tidesandcurrents.noaa.gov/",
        notes="Water level at US coastal gauges.",
        harvest=lambda f: [
            s for st in ("9414290", "8443970", "8518750", "8729108", "9455920")
            for s in _noaa_tides(f, st, "20230101", "20241231")
        ],
    ),
    Source(
        key="usgs_water",
        name="USGS streamflow",
        domain="hydro",
        freq="daily",
        license="Public domain (USGS)",
        source_url="https://waterservices.usgs.gov/nwis/",
        harvest=lambda f: _usgs_water(
            f, "01646500,14211720,12134500,11264500,00000042",
            "2020-01-01", "2024-12-31"),
    ),
    Source(
        key="usgs_quakes",
        name="USGS earthquake feed",
        domain="geophysics",
        freq="event",
        license="Public domain (USGS)",
        source_url="https://earthquake.usgs.gov/earthquakes/feed/",
        harvest=_usgs_quakes,
    ),
    Source(
        key="worldbank_gdp",
        name="World Bank GDP (current US$)",
        domain="macro",
        freq="annual",
        license="CC BY 4.0 (World Bank)",
        source_url="https://api.worldbank.org/v2/",
        harvest=lambda f: _world_bank(f, "NY.GDP.MKTP.CD"),
    ),
    Source(
        key="worldbank_pop",
        name="World Bank population",
        domain="macro",
        freq="annual",
        license="CC BY 4.0 (World Bank)",
        source_url="https://api.worldbank.org/v2/",
        harvest=lambda f: _world_bank(f, "SP.POP.TOTL"),
    ),
    Source(
        key="worldbank_gdp_pc",
        name="World Bank GDP per capita",
        domain="macro",
        freq="annual",
        license="CC BY 4.0 (World Bank)",
        source_url="https://api.worldbank.org/v2/",
        harvest=lambda f: _world_bank(f, "NY.GDP.PCAP.CD"),
    ),
    Source(
        key="ecb_fx",
        name="ECB euro reference FX rates",
        domain="finance",
        freq="daily",
        license="ECB terms of use (free reuse with attribution)",
        source_url="https://data.ecb.europa.eu/help/api/overview",
        harvest=lambda f: [
            s for c in ("USD", "GBP", "JPY", "CHF", "SEK", "NOK", "PLN", "CZK",
                        "AUD", "CAD", "CNY", "INR", "BRL", "MXN", "ZAR", "TRY")
            for s in _ecb_fx(f, c, "2019-01-01", "2024-12-31")
        ],
    ),
    Source(
        key="frankfurter_fx",
        name="Frankfurter FX (ECB-derived)",
        domain="finance",
        freq="daily",
        license="MIT (open)",
        source_url="https://www.frankfurter.app/",
        harvest=lambda f: [
            s for pair in (("USD", "EUR"), ("USD", "GBP"), ("USD", "JPY"),
                           ("EUR", "GBP"), ("GBP", "JPY"), ("USD", "CHF"))
            for s in _frankfurter_fx(f, pair[0], pair[1], "2019-01-01", "2024-12-31")
        ],
    ),
    Source(
        key="kraken_crypto",
        name="Kraken crypto OHLC",
        domain="finance",
        freq="daily",
        license="Kraken public API terms",
        source_url="https://docs.kraken.com/api/",
        harvest=lambda f: [
            s
            for pair in ("XBTUSD", "ETHUSD", "XTZUSD")
            for s in _crypto_candles(f, pair, "2019-01-01", "2024-12-31")
        ],
    ),
    Source(
        key="yahoo_equities",
        name="Yahoo Finance daily OHLCV",
        domain="finance",
        freq="daily",
        license="Yahoo Finance terms (personal use)",
        source_url="https://query2.finance.yahoo.com/",
        notes="Index ETFs + sector ETFs as broad market proxies, plus VIX and "
              "the Treasury yield curve as rates series.",
        harvest=lambda f: [
            s
            for sym in ("SPY", "QQQ", "IWM", "EEM", "XLK", "XLE", "XLF", "XLV",
                        "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "^VIX", "^TNX",
                        "^TYX", "^FVX", "DX-Y.NYB", "GC=F", "CL=F", "BTC-USD", "ETH-USD")
            for s in _yahoo_ohlc(f, sym, "10y")
        ],
    ),
    Source(
        key="treasury_rates",
        name="US Treasury interest rates",
        domain="macro",
        freq="monthly",
        license="Public domain (US Treasury)",
        source_url="https://fiscaldata.treasury.gov/",
        harvest=_treasury_rates,
    ),
    Source(
        key="nyfed_sofr",
        name="NY Fed SOFR",
        domain="macro",
        freq="daily",
        license="Public domain (NY Fed)",
        source_url="https://markets.newyorkfed.org/",
        harvest=lambda f: _nyfed_rates(f, "2019-01-01", "2024-12-31"),
    ),
    Source(
        key="bls_labor",
        name="BLS labour statistics",
        domain="macro",
        freq="monthly",
        license="Public domain (BLS)",
        source_url="https://www.bls.gov/developers/",
        notes="Unemployment rate, payrolls, CPI, average hourly earnings.",
        harvest=lambda f: _bls(
            f,
            ["LNS14000000", "LNS12300000", "LNS12000000", "CES0000000001",
             "CUUR0000SA0", "CES0500000003", "LNS11300000", "LNS12032189"],
            "2019-01-01", "2024-12-31",
        ),
    ),
    Source(
        key="hackernews",
        name="Hacker News attention",
        domain="web",
        freq="daily",
        license="HN Algolia public API",
        source_url="https://hn.algolia.com/api",
        harvest=lambda f: _hackernews(f, 1546300800, 1735689600),
    ),
    Source(
        key="nyc311",
        name="NYC 311 service requests",
        domain="urban",
        freq="daily",
        license="NYC Open Data terms",
        source_url="https://data.cityofnewyork.us/City-Government/311-Service-Requests-from-2020-to-Present/erm2-nwe9",
        harvest=lambda f: _nyc_311(f, "2023-01-01", "2024-12-31"),
    ),
    Source(
        key="football_epl",
        name="English Premier League results",
        domain="sport",
        freq="event",
        license="football-data.co.uk (free for personal use)",
        source_url="https://www.football-data.co.uk/englandm.php",
        harvest=lambda f: [
            s
            for season in ("2122", "2223", "2324", "2425")
            for s in _football(f, season)
        ],
    ),
    Source(
        key="covid",
        name="WHO COVID-19 by country",
        domain="health",
        freq="daily",
        license="WHO data (public)",
        source_url="https://disease.sh/",
        harvest=_covid,
    ),
    Source(
        key="who_gho",
        name="WHO Global Health Observatory",
        domain="health",
        freq="annual",
        license="CC BY-NC-SA 3.0 IGO (WHO)",
        source_url="https://www.who.int/data/gho",
        harvest=lambda f: _who_gho(f, "WHOSIS_000001"),
    ),
    Source(
        key="openalex",
        name="OpenAlex publication counts",
        domain="science",
        freq="monthly",
        license="CC0 (OpenAlex)",
        source_url="https://docs.openalex.org/",
        harvest=lambda f: _openalex(f, "2019-01-01", "2024-12-31"),
    ),
    Source(
        key="gdelt_climate",
        name="GDELT climate news volume",
        domain="media",
        freq="daily",
        license="GDELT 2.1 terms (open)",
        source_url="https://www.gdeltproject.org/",
        harvest=lambda f: _gdelt(f, 'climate OR "climate change"'),
    ),
]


def get_source(key: str) -> Source:
    for s in SOURCES:
        if s.key == key:
            return s
    raise KeyError(f"unknown source: {key} (have: {', '.join(s.key for s in SOURCES)})")


def sources_by_domain() -> dict[str, list[Source]]:
    out: dict[str, list[Source]] = {}
    for s in SOURCES:
        out.setdefault(s.domain, []).append(s)
    return out
