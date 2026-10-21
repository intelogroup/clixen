"""Forecasters: each is `(history: 1-D array, h: int) -> array of length h`."""
from __future__ import annotations

import numpy as np

SEASON = 52


def naive(y: np.ndarray, h: int) -> np.ndarray:
    return np.repeat(y[-1], h)


def seasonal_naive(y: np.ndarray, h: int) -> np.ndarray:
    # shortcut: needs a full year of history, else falls back to naive.
    if len(y) < SEASON:
        return naive(y, h)
    return np.array([y[len(y) - SEASON + (i % SEASON)] for i in range(h)])


def slope(y: np.ndarray, h: int, window: int = 4) -> np.ndarray:
    """Last value plus the mean weekly change of the last `window` weeks, floored at 0."""
    w = min(window, len(y) - 1)
    d = (y[-1] - y[-1 - w]) / w if w > 0 else 0.0
    return np.maximum(y[-1] + d * np.arange(1, h + 1), 0.0)


_chronos = None


def chronos(y: np.ndarray, h: int) -> np.ndarray:
    """Chronos-Bolt zero-shot median. Needs `pip install chronos-forecasting`."""
    global _chronos
    import torch
    if _chronos is None:
        from chronos import BaseChronosPipeline
        _chronos = BaseChronosPipeline.from_pretrained(
            "amazon/chronos-bolt-small", device_map="cpu", torch_dtype=torch.float32)
    _, mean = _chronos.predict_quantiles(
        torch.tensor(y, dtype=torch.float32), prediction_length=h, quantile_levels=[0.5])
    return np.maximum(mean[0].numpy()[:h], 0.0)


MODELS = {"naive": naive, "snaive": seasonal_naive, "slope": slope}
