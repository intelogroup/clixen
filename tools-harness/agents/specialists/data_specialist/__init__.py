"""
Data Analysis specialist subagent.

Mission: analyze structured data files — compute statistics, generate charts,
run SQL queries, correlate variables.

Uses the best OSS tools: pandas, duckdb, matplotlib, seaborn, scipy.

Public API:
    run_data_specialist(query, model="gemma4", known_path=None, max_steps=6) -> DataResult
"""

from __future__ import annotations

from ._helpers import DataResult
from .agent import run_data_specialist

__all__ = ["DataResult", "run_data_specialist"]
