"""demand — weekly interest/adoption forecasting for SaaS candidates.

signals.py   weekly series from keyless public APIs
forecast.py  baselines + optional Chronos, all `(history, h) -> forecast`
backtest.py  rolling-origin MASE; the gate a model must pass to be kept
"""
