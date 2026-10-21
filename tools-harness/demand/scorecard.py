"""Scorecard: attention level and growth per tracked term.

`recent_avg` (mean of the last 13 weeks) is also the forecast for the next 4-8 weeks: backtests showed a flat
13-week moving average is the best model here (see forecast.moving_average)."""
from __future__ import annotations

import json
from datetime import date

import numpy as np

from .mention_series import CONTROL, OUT as SERIES, build
from .trends import growth


def _share_growth(y: np.ndarray, ctl: np.ndarray, weeks: int = 13) -> dict:
    """Growth of the term's share of all comments (term / control), not of its raw count."""
    def share(lo, hi):
        return y[lo:hi].sum() / ctl[lo:hi].sum()
    out = {"share_qoq": None, "share_yoy": None}
    if len(y) >= 2 * weeks and ctl[-2 * weeks:-weeks].sum() > 0 and share(-2 * weeks, -weeks) > 0:
        out["share_qoq"] = float(share(-weeks, len(y)) / share(-2 * weeks, -weeks) - 1)
    if len(y) >= 52 + weeks and share(-52 - weeks, -52) > 0:
        out["share_yoy"] = float(share(-weeks, len(y)) / share(-52 - weeks, -52) - 1)
    return out


def score(series: dict[str, np.ndarray]) -> list[dict]:
    """One row per term, sorted by recent weekly mentions. Sparse series (under 70% non-zero weeks) are flagged.

    Raw qoq/yoy follow the count; share_qoq/share_yoy follow the term's share of all HN comments
    (needs the CONTROL series), which removes HN-wide volume shifts."""
    ctl = series.get(CONTROL)
    rows = []
    for term, y in series.items():
        if term == CONTROL:
            continue
        g = growth(y)
        row = {"term": term, "recent_avg": round(g["recent_avg"], 1), "qoq": g["qoq"], "yoy": g["yoy"],
               "sparse": bool((y > 0).mean() < 0.7)}
        row.update(_share_growth(y, ctl) if ctl is not None and len(ctl) == len(y)
                   else {"share_qoq": None, "share_yoy": None})
        rows.append(row)
    return sorted(rows, key=lambda r: -r["recent_avg"])


def run(out_dir=SERIES.parent, refresh: bool = True) -> list[dict]:
    """Refresh the weekly mention series (cached, so only new weeks hit the network) and write forecast_<date>.json."""
    series = build(log=lambda _m: None) if refresh else dict(np.load(SERIES))
    rows = score(series)
    (out_dir / f"forecast_{date.today()}.json").write_text(json.dumps(rows, indent=1))
    return rows
