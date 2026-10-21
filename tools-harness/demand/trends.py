"""Growth of a weekly series: last `weeks` weeks vs the prior block and vs the same block a year ago."""
from __future__ import annotations

import numpy as np


def growth(y: np.ndarray, weeks: int = 13) -> dict:
    """Ratios are change as a fraction (0.2 = +20%); None when history is too short or the base is 0."""
    def ratio(now: np.ndarray, then: np.ndarray):
        return float(now.sum() / then.sum() - 1) if len(then) == weeks and then.sum() > 0 else None
    now = y[-weeks:]
    return {"recent_avg": float(now.mean()),
            "qoq": ratio(now, y[-2 * weeks:-weeks]),
            "yoy": ratio(now, y[-52 - weeks:-52] if len(y) >= 52 + weeks else y[:0])}
