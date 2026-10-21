"""Weekly interest/adoption series from keyless public APIs."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd

from tsdata.http import Fetcher

CACHE = Path(__file__).resolve().parent.parent / "data" / "demand" / "raw"


def _weekly(daily: pd.Series) -> pd.Series:
    """Daily -> weekly sums (weeks ending Sunday), dropping the partial last week."""
    wk = daily.resample("W-SUN").sum()
    if len(daily) and daily.index[-1].dayofweek != 6:
        wk = wk.iloc[:-1]
    return wk


def wikipedia_weekly(f: Fetcher, article: str, weeks: int = 156) -> pd.Series:
    end = date.today() - timedelta(days=1)
    start = end - timedelta(weeks=weeks)
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
           f"en.wikipedia/all-access/user/{quote(article, safe='')}/daily/"
           f"{start:%Y%m%d}/{end:%Y%m%d}")
    items = f.get_json(url, force=True)["items"]
    s = pd.Series({pd.Timestamp(i["timestamp"][:8]): float(i["views"]) for i in items})
    return _weekly(s)


def npm_weekly(f: Fetcher, package: str, weeks: int = 70) -> pd.Series:
    # npm range endpoint caps at 18 months per request.
    end = date.today() - timedelta(days=1)
    start = end - timedelta(weeks=weeks)
    url = f"https://api.npmjs.org/downloads/range/{start}:{end}/{package}"
    rows = f.get_json(url, force=True)["downloads"]
    s = pd.Series({pd.Timestamp(r["day"]): float(r["downloads"]) for r in rows})
    return _weekly(s)


def get_series(kind: str, key: str, f: Fetcher | None = None) -> np.ndarray:
    f = f or Fetcher(CACHE)
    fn = {"wiki": wikipedia_weekly, "npm": npm_weekly}[kind]
    return fn(f, key).to_numpy(dtype=float)
