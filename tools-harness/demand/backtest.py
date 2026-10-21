"""Rolling-origin backtest. A candidate model is kept only if it beats the baseline."""
from __future__ import annotations

from typing import Callable

import numpy as np

Model = Callable[[np.ndarray, int], np.ndarray]


def mase(actual: np.ndarray, pred: np.ndarray, train: np.ndarray) -> float:
    """MAE scaled by the in-sample one-step naive MAE (1.0 = as good as naive)."""
    scale = np.mean(np.abs(np.diff(train)))
    return float(np.mean(np.abs(actual - pred)) / scale) if scale > 0 else float("nan")


def rolling_mase(y: np.ndarray, model: Model, h: int = 4, origins: int = 12,
                 min_train: int = 26) -> float:
    """Mean MASE over the last `origins` forecast origins, each predicting h weeks."""
    scores = []
    for k in range(origins):
        cut = len(y) - h - (origins - 1 - k)
        if cut < min_train:
            continue
        scores.append(mase(y[cut:cut + h], model(y[:cut], h), y[:cut]))
    return float(np.nanmean(scores)) if scores else float("nan")


def gate(series: dict[str, np.ndarray], candidate: Model, baseline: Model,
         h: int = 4, margin: float = 0.10) -> dict:
    """Pass when candidate's mean MASE across series is >= `margin` below baseline's."""
    c = np.array([rolling_mase(y, candidate, h) for y in series.values()])
    b = np.array([rolling_mase(y, baseline, h) for y in series.values()])
    ok = ~(np.isnan(c) | np.isnan(b))
    cm, bm = float(c[ok].mean()), float(b[ok].mean())
    return {"candidate": cm, "baseline": bm, "improvement": 1 - cm / bm,
            "n_series": int(ok.sum()), "passed": cm <= bm * (1 - margin)}
