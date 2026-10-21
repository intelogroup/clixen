"""Build weekly HN mention-count series for a term list and save them as an .npz (one array per term)."""
from __future__ import annotations

import sys

import numpy as np

from tsdata.http import Fetcher

from .backfill import hn_weekly_counts
from .pain import CACHE

OUT = CACHE.parent / "mention_series.npz"

TERMS = [
    "invoicing", "invoice", "e-invoicing", "peppol", "quickbooks", "xero", "freshbooks", "stripe", "billing",
    "time tracking", "accounting software", "bookkeeping", "late payment", "freelance", "freelancer",
    "payment processing", "crm", "project management", "helpdesk", "analytics", "no-code", "automation",
    "email marketing", "payroll", "expense", "scheduling", "chatbot", "observability", "feature flags", "low-code",
]


def build(terms: list[str] = TERMS, weeks: int = 104, out=OUT, log=print) -> dict[str, np.ndarray]:
    f = Fetcher(CACHE)
    series = {}
    for i, t in enumerate(terms, 1):
        try:
            series[t] = np.array(hn_weekly_counts(f, t, weeks), dtype=float)
        except Exception as e:
            log(f"{t!r} failed: {type(e).__name__}")
            continue
        log(f"{i}/{len(terms)} {t}: mean {series[t].mean():.1f}/wk, nonzero {np.mean(series[t] > 0):.0%}")
    np.savez(out, **series)
    return series


if __name__ == "__main__":
    build(weeks=int(sys.argv[1]) if len(sys.argv) > 1 else 104)
