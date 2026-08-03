"""Data-analysis specialist: chart/dataframe helpers, DataResult, system prompt."""


from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import ollama
from pydantic import BaseModel, Field

from tools.registry import ALL_TOOLS, EXECUTORS, execute_tool, tools_with_tags

_log = logging.getLogger("data_specialist")

# ---------------------------------------------------------------------------
# Chart output directory
# ---------------------------------------------------------------------------

CHARTS_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)
CHARTS_URL_PREFIX = "/static/charts"
VIEW_CHART_URL_PREFIX = "/view/chart"

# ---------------------------------------------------------------------------
# Toolset
# ---------------------------------------------------------------------------

DATA_TOOL_NAMES = tools_with_tags("spec_data")

_STRUCTURED_SUFFIXES = {".csv", ".json", ".tsv", ".parquet", ".xlsx", ".feather", ".arrow"}

# Analysis libraries — imported lazily inside tool functions
_ANALYSIS_DEPS_MSG = (
    "Analysis libraries not installed. Run: pip install -e \".[data]\""
)


def _data_tool_schemas() -> list[dict]:
    # Only include tools that exist in ALL_TOOLS
    names = {t["function"]["name"] for t in ALL_TOOLS}
    available = DATA_TOOL_NAMES & names
    return [t for t in ALL_TOOLS if t["function"]["name"] in available]


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

class DataResult(BaseModel):
    text: str = ""
    paths: list[str] = Field(default_factory=list)
    chart_path: str = ""
    tool_trace: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are DATA ANALYST (PhD-level). Your job: analyze structured data files and deliver publication-quality statistical analysis with numbers, charts, tables, and insights.

TOOLS:
  analyze_dataset(path)      — EDA: schema, shape, nulls, skew/kurtosis, numeric summary
  detect_outliers(path)       — flag outliers via IQR or z-score
  query_data(path, sql)      — DuckDB SQL on CSV/JSON/Parquet (fast grouping/filtering)
  run_stats_test(path, test, ...) — 20+ tests: parametric (ttest, anova, manova), non-parametric (mwu, kruskal, friedman, wilcoxon), post-hoc (tukey, games_howell), correlation (pearson, partial, distance), effect sizes (cohen_d, hedges_g, eta_squared), power analysis, reliability (cronbach_alpha, icc), mediation, bootstrap CI (BCa), diagnostics (normality, homogeneity, vif), chi-square
  run_regression(path, formula, ...) — OLS/logistic/GLM with residual diagnostics
  run_pca(path, n_components) — PCA with loadings, explained variance, biplot
  plot_roc(path, y_true, y_score) — ROC curve with AUC
  correlate(path)            — correlation matrix + heatmap
  plot_chart(path, type, x, y) — matplotlib PNG (bar, line, scatter, hist, heatmap, pie)
  plot_interactive(path, type, ...) — Plotly HTML + PNG (scatter, line, bar, hist, box, violin, density_heatmap)
  make_summary_table(path)   — APA-style table: N, M(SD), Min, Max, Skew, Kurtosis
  run_full_analysis(path, group_col) — ONE-SHOT comprehensive pipeline: EDA → outliers → normality → group stats → correlation → table
  run_time_series(path, date_col, value_col, ...) — ARIMA, ACF/PACF, seasonal decomposition, forecast
  run_survival(path, time_col, event_col, ...) — Kaplan-Meier, log-rank, Cox PH model
  run_meta_analysis(path, effect_col, ...) — forest plot, funnel plot, heterogeneity, fixed/random effects
  run_python(code)           — custom Python analysis
  parse_file(path)           — preview first rows

WORKFLOW (follow this order for a comprehensive analysis):
  1. analyze_dataset — always start here to understand the data
  2. detect_outliers — flag anomalies before statistical testing
  3. run_stats_test(normality) — check normality assumption
  4. run_stats_test(homogeneity) — check variance homogeneity (if groups exist)
  5. run_stats_test(anova/ttest) — group comparisons with appropriate test
  6. run_stats_test(tukey) — post-hoc if ANOVA significant
  7. run_stats_test(effsize) — report effect sizes
  8. correlate — correlation matrix
  9. run_regression — regression modeling with covariates
  10. make_summary_table — publication-ready APA table
  Or just use run_full_analysis(path, group_col) for steps 1-8 in one call.

RULES:
- Always start with analyze_dataset.
- For **comprehensive analysis in one call**, use run_full_analysis with group_col= if you have groups.
- Check assumptions before parametric tests: normality + homogeneity.
- Use non-parametric fallbacks (kruskal, mannwhitney) when assumptions fail.
- Report effect sizes and confidence intervals alongside p-values.
- For publication-ready output, use make_summary_table.
- Stop after answering — don't re-analyze the same file repeatedly.
- You have {max_steps} steps available — plan your analysis to use them wisely."""


def _build_system_prompt(max_steps: int) -> str:
    return _SYSTEM_PROMPT.format(max_steps=max_steps)


# ---------------------------------------------------------------------------
# Built-in data tools (pure Python — no LLM needed)
# ---------------------------------------------------------------------------

def _analyze_dataset(path: str) -> str:
    """Return schema, shape, dtypes, nulls, summary stats."""
    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        return "pandas/numpy not installed"

    try:
        p = Path(path)
        suffix = p.suffix.lower()
        if suffix == ".csv":
            df = pd.read_csv(path)
        elif suffix == ".tsv":
            df = pd.read_csv(path, sep="\t")
        elif suffix == ".json":
            df = pd.read_json(path)
        elif suffix in {".parquet", ".feather", ".arrow"}:
            df = pd.read_parquet(path)
        elif suffix in {".xlsx", ".xls"}:
            df = pd.read_excel(path)
        else:
            return f"Unsupported format: {suffix}"

        lines = [
            f"Shape: {df.shape[0]} rows x {df.shape[1]} columns",
            f"Columns: {list(df.columns)}",
            f"Dtypes:\n{df.dtypes.to_string()}",
            f"Null counts:\n{df.isnull().sum().to_string()}",
        ]

        # Numeric summary
        numeric = df.select_dtypes(include=[np.number])
        if not numeric.empty:
            lines.append(f"\nNumeric summary ({len(numeric.columns)} columns):")
            desc = numeric.describe().to_string()
            lines.append(desc)
            # Skewness and kurtosis
            skew_vals = numeric.skew()
            kurt_vals = numeric.kurtosis()
            skew_line = "  ".join(f"{c}: {skew_vals[c]:.3f}" for c in skew_vals.index)
            kurt_line = "  ".join(f"{c}: {kurt_vals[c]:.3f}" for c in kurt_vals.index)
            lines.append(f"\nSkewness:\n  {skew_line}")
            lines.append(f"Kurtosis:\n  {kurt_line}")

        # Categorical summary
        categorical = df.select_dtypes(include=["object", "category"])
        if not categorical.empty:
            cats = []
            for col in categorical.columns[:10]:
                uniq = df[col].nunique()
                cats.append(f"  {col}: {uniq} unique values")
            if cats:
                lines.append(f"\nCategorical columns:")
                lines.extend(cats)

        return "\n".join(lines)
    except Exception as e:
        return f"analyze_dataset failed: {type(e).__name__}: {e}"


def _query_data(path: str, sql: str) -> str:
    """Run DuckDB SQL on the file."""
    try:
        import duckdb
    except ImportError:
        return "duckdb not installed. Run: pip install duckdb"

    try:
        con = duckdb.connect()
        result = con.execute(sql).fetchdf()
        con.close()
        return result.to_string()
    except Exception as e:
        return f"query_data failed: {type(e).__name__}: {e}"


def _chart_url(filename: str) -> str:
    """Convert a filename in CHARTS_DIR to its HTTP URL path."""
    return f"{CHARTS_URL_PREFIX}/{filename}"


def _view_url(filename: str) -> str:
    """Convert a HTML filename to its view endpoint URL."""
    return f"{VIEW_CHART_URL_PREFIX}/{filename}"


def _load_dataframe(path: str, use_polars: bool = True):
    """Load a structured data file into a pandas DataFrame.
    
    Uses Polars for parsing (15-50x faster for CSV/Parquet), then converts
    to pandas for compatibility with plotting and stats APIs.
    Seaborn 0.13+ can accept Polars DataFrames natively via the dataframe
    exchange protocol, so .to_pandas() calls before seaborn are unnecessary.
    """
    suffix = Path(path).suffix.lower()
    if use_polars:
        try:
            import polars as pl
            if suffix == ".csv":
                return pl.read_csv(path).to_pandas()
            elif suffix == ".tsv":
                return pl.read_csv(path, separator="\t").to_pandas()
            elif suffix == ".json":
                return pl.read_json(path).to_pandas()
            elif suffix in {".parquet", ".feather", ".arrow"}:
                return pl.read_parquet(path).to_pandas()
            elif suffix in {".xlsx", ".xls"}:
                return pl.read_excel(path).to_pandas()
        except ImportError:
            pass
    import pandas as pd
    if suffix == ".csv":
        return pd.read_csv(path)
    elif suffix == ".tsv":
        return pd.read_csv(path, sep="\t")
    elif suffix == ".json":
        return pd.read_json(path)
    elif suffix in {".parquet", ".feather", ".arrow"}:
        return pd.read_parquet(path)
    elif suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    else:
        return pd.read_csv(path)


def _plot_chart(path: str, chart_type: str, x: str = "", y: str = "",
                title: str = "", output_path: str = "", format: str = "png") -> str:
    """Generate a chart using matplotlib/seaborn and save to file."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import pandas as pd
        import seaborn as sns
    except ImportError:
        return "matplotlib/pandas/seaborn not installed"

    try:
        df = _load_dataframe(path)

        fig, ax = plt.subplots(figsize=(10, 6))

        ct = chart_type.lower()
        if ct == "bar":
            if x and y:
                df.groupby(x)[y].mean().plot(kind="bar", ax=ax)
            else:
                df.iloc[:, 0].value_counts().plot(kind="bar", ax=ax)
        elif ct == "line":
            if x and y:
                df.plot(x=x, y=y, kind="line", ax=ax)
            else:
                df.iloc[:, :10].plot(ax=ax)
        elif ct == "scatter":
            if x and y:
                df.plot.scatter(x=x, y=y, ax=ax)
            else:
                cols = df.select_dtypes(include="number").columns[:2]
                if len(cols) >= 2:
                    df.plot.scatter(x=cols[0], y=cols[1], ax=ax)
        elif ct == "hist":
            df.hist(ax=ax, bins=20)
        elif ct == "heatmap":
            numeric = df.select_dtypes(include="number")
            if not numeric.empty and len(numeric.columns) > 1:
                sns.heatmap(numeric.corr(), annot=True, cmap="coolwarm", ax=ax)
        elif ct == "pie":
            if x:
                df[x].value_counts().plot.pie(autopct="%1.1f%%", ax=ax)
            else:
                df.iloc[:, 0].value_counts().plot.pie(autopct="%1.1f%%", ax=ax)
        else:
            df.hist(ax=ax, bins=20)

        if title:
            ax.set_title(title)

        fmt = format.lower() if format else "png"
        if fmt not in {"png", "svg", "pdf"}:
            fmt = "png"

        if output_path:
            out = output_path
            if format:
                p = Path(out)
                if p.suffix.lower().lstrip(".") != fmt:
                    out = str(p.with_suffix(f".{fmt}"))
        else:
            fname = f"g4l_chart_{int(time.time())}.{fmt}"
            out = str(CHARTS_DIR / fname)
        fig.tight_layout()
        fig.savefig(out, dpi=100)
        plt.close(fig)
        url = _chart_url(Path(out).name)
        if fmt == "pdf":
            return f"[Download PDF Chart]({url})"
        else:
            return f"![Chart]({url})"
    except Exception as e:
        return f"plot_chart failed: {type(e).__name__}: {e}"


def _correlate(path: str, columns: str = "", format: str = "png") -> str:
    """Compute correlation matrix and optionally generate heatmap."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import pandas as pd
        import seaborn as sns
        import numpy as np
    except ImportError:
        return "pandas/matplotlib/seaborn not installed"

    try:
        df = _load_dataframe(path)

        if columns:
            cols = [c.strip() for c in columns.split(",")]
            df = df[[c for c in cols if c in df.columns]]

        numeric = df.select_dtypes(include=[np.number])
        if numeric.empty:
            return "No numeric columns to correlate"

        corr = numeric.corr()

        # Generate heatmap
        fig, ax = plt.subplots(figsize=(10, 8))
        sns.heatmap(corr, annot=True, fmt=".2f", cmap="coolwarm", center=0, ax=ax)
        ax.set_title("Correlation Matrix")

        fmt = format.lower() if format else "png"
        if fmt not in {"png", "svg", "pdf"}:
            fmt = "png"

        fname = f"g4l_corr_{int(time.time())}.{fmt}"
        chart_path = str(CHARTS_DIR / fname)
        fig.tight_layout()
        fig.savefig(chart_path, dpi=100)
        plt.close(fig)

        url = _chart_url(fname)
        if fmt == "pdf":
            chart_markup = f"[Download PDF Heatmap]({url})"
        else:
            chart_markup = f"![Heatmap]({url})"
        return f"Correlation matrix:\n{corr.to_string()}\n\n{chart_markup}"
    except Exception as e:
        return f"correlate failed: {type(e).__name__}: {e}"


