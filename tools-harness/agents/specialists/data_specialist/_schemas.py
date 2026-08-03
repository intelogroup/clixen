"""Data-analysis specialist: tool schemas registered into ALL_TOOLS."""

from __future__ import annotations

from tools.registry import ALL_TOOLS, EXECUTORS
from ._advanced import _BUILTIN_DATA_EXECUTORS


_DATA_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_dataset",
            "description": (
                "Analyze a structured data file (CSV, JSON, Parquet, Excel). "
                "Returns shape, column names, dtypes, null counts, and numeric summary statistics. "
                "Always run this first before any other data operation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file",
                    }
                },
            "required": ["path"],
        },
    },
    },
    {
        "type": "function",
        "function": {
            "name": "query_data",
            "description": (
                "Run a DuckDB SQL query on a structured data file (CSV, JSON, Parquet). "
                "Use for filtering, grouping, aggregation, and joins. Faster than pandas for large files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file",
                    },
                    "sql": {
                        "type": "string",
                        "description": "SQL query to run against the file",
                    },
                },
                "required": ["path", "sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plot_chart",
            "description": (
                "Generate a chart (bar, line, scatter, histogram, heatmap, pie) from a data file. "
                "Saves the chart as a static PNG image and returns it as an inline markdown image."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["bar", "line", "scatter", "hist", "heatmap", "pie"],
                        "description": "Chart type",
                        "default": "bar",
                    },
                    "x": {
                        "type": "string",
                        "description": "Column name for x-axis",
                        "default": "",
                    },
                    "y": {
                        "type": "string",
                        "description": "Column name for y-axis",
                        "default": "",
                    },
                    "title": {
                        "type": "string",
                        "description": "Chart title",
                        "default": "",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Output image path (optional)",
                        "default": "",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["png", "svg", "pdf"],
                        "description": "Output chart format. Vector formats ('svg', 'pdf') are fully scalable for premium journals.",
                        "default": "png",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "correlate",
            "description": (
                "Compute correlation matrix for numeric columns in a data file. "
                "Returns the matrix + saves a heatmap PNG inline."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file",
                    },
                    "columns": {
                        "type": "string",
                        "description": "Comma-separated column names to include (default: all numeric)",
                        "default": "",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["png", "svg", "pdf"],
                        "description": "Output chart format. Vector formats ('svg', 'pdf') are fully scalable for premium journals.",
                        "default": "png",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_stats_test",
            "description": (
                "Run any statistical test on a data file. "
                "Parametric: ttest, paired_ttest, anova, repeated_measures, manova. "
                "Post-hoc: tukey, games_howell. "
                "Non-parametric: mannwhitney, wilcoxon, kruskal, friedman. "
                "Correlation: correlation, partial_corr, distance_corr. "
                "Effect sizes: effsize, eta_squared. "
                "Power analysis: power. "
                "Reliability: cronbach, icc. "
                "Mediation: mediation. "
                "Diagnostics: normality, homogeneity, vif. "
                "Categorical: chi2. "
                "Bootstrap: bootci."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file",
                    },
                    "test": {
                        "type": "string",
                        "enum": [
                            "ttest", "paired_ttest", "anova", "repeated_measures", "manova",
                            "tukey", "games_howell",
                            "mannwhitney", "wilcoxon", "kruskal", "friedman",
                            "correlation", "partial_corr", "distance_corr",
                            "effsize", "eta_squared",
                            "power",
                            "cronbach", "icc",
                            "mediation",
                            "normality", "homogeneity", "vif",
                            "chi2",
                            "bootci",
                        ],
                        "description": "Statistical test to run",
                    },
                    "group_col": {
                        "type": "string",
                        "description": "Grouping/categorical column (for anova, tukey, kruskal, homogeneity, manova, power, eta_squared)",
                        "default": "",
                    },
                    "value_col": {
                        "type": "string",
                        "description": "Value/dependent column or comma-separated list (for anova, tukey, manova, friedman, vif)",
                        "default": "",
                    },
                    "x": {
                        "type": "string",
                        "description": "First column (IV for ttest, correlation, chi2, bootci; IV for mediation)",
                        "default": "",
                    },
                    "y": {
                        "type": "string",
                        "description": "Second column (DV for ttest, correlation, chi2; DV for mediation)",
                        "default": "",
                    },
                    "target": {
                        "type": "string",
                        "description": "Extra param: mediator for mediation, eftype for effsize, power_type for power, columns for cronbach/icc/normality",
                        "default": "",
                    },
                },
                "required": ["path", "test"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_regression",
            "description": (
                "Run a regression model on a data file using statsmodels. "
                "Supports ols (linear), logit (logistic), and glm. "
                "Returns coefficient table, R-squared, p-values, and residual diagnostic plots."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file",
                    },
                    "formula": {
                        "type": "string",
                        "description": "Formula string (e.g., 'y ~ x1 + x2'). Required for logit and glm.",
                        "default": "",
                    },
                    "x": {
                        "type": "string",
                        "description": "Predictor column name (for simple OLS without formula)",
                        "default": "",
                    },
                    "y": {
                        "type": "string",
                        "description": "Target column name",
                        "default": "",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["ols", "logit", "glm"],
                        "description": "Type of regression model",
                        "default": "ols",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["png", "svg", "pdf"],
                        "description": "Output residual diagnostic plot format (resembles nature/science quality when svg/pdf is used).",
                        "default": "png",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_summary_table",
            "description": (
                "Generate an APA-style descriptive statistics HTML table "
                "from a data file. Returns a link to the full interactive HTML table."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file",
                    },
                    "columns": {
                        "type": "string",
                        "description": "Comma-separated column names to include (default: all)",
                        "default": "",
                    },
                    "group": {
                        "type": "string",
                        "description": "Column name to group by for per-group statistics",
                        "default": "",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plot_interactive",
            "description": (
                "Generate an interactive Plotly chart from a data file. "
                "Supports scatter, line, bar, histogram, box, violin, and density_heatmap. "
                "Returns both a static PNG preview (inline) and a link to the interactive chart."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["scatter", "line", "bar", "hist", "box", "violin", "density_heatmap"],
                        "description": "Chart type",
                        "default": "scatter",
                    },
                    "x": {
                        "type": "string",
                        "description": "Column name for x-axis",
                        "default": "",
                    },
                    "y": {
                        "type": "string",
                        "description": "Column name for y-axis",
                        "default": "",
                    },
                    "title": {
                        "type": "string",
                        "description": "Chart title",
                        "default": "",
                    },
                    "color": {
                        "type": "string",
                        "description": "Column name for color grouping",
                        "default": "",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_outliers",
            "description": (
                "Detect outliers in numeric columns using IQR (Interquartile Range, 1.5x) "
                "or z-score (|z|>3) methods. Returns flagged values per column."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["iqr", "zscore"],
                        "description": "Outlier detection method",
                        "default": "iqr",
                    },
                    "column": {
                        "type": "string",
                        "description": "Specific column to check (default: all numeric)",
                        "default": "",
                    },
                    "group": {
                        "type": "string",
                        "description": "Group column for per-group detection",
                        "default": "",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_pca",
            "description": (
                "Run Principal Component Analysis on numeric columns. "
                "Returns loadings, explained variance, cumulative variance, "
                "and a biplot PNG. Optionally color by group column."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file",
                    },
                    "n_components": {
                        "type": "integer",
                        "description": "Number of PCA components (default 2)",
                        "default": 2,
                    },
                    "columns": {
                        "type": "string",
                        "description": "Comma-separated column names (default: all numeric)",
                        "default": "",
                    },
                    "group": {
                        "type": "string",
                        "description": "Group column for coloring points in biplot",
                        "default": "",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["png", "svg", "pdf"],
                        "description": "Output chart format. Vector formats ('svg', 'pdf') are fully scalable for premium journals.",
                        "default": "png",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plot_roc",
            "description": (
                "Plot an interactive ROC (Receiver Operating Characteristic) curve. "
                "Requires columns with true binary labels and predicted scores/probabilities. "
                "Returns AUC value, static PNG, and interactive HTML."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file",
                    },
                    "y_true": {
                        "type": "string",
                        "description": "Column name with true binary labels (0/1)",
                    },
                    "y_score": {
                        "type": "string",
                        "description": "Column name with predicted probability or score",
                    },
                    "title": {
                        "type": "string",
                        "description": "Chart title",
                        "default": "ROC Curve",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["png", "svg", "pdf"],
                        "description": "Output chart format. Vector formats ('svg', 'pdf') are fully scalable for premium journals.",
                        "default": "png",
                    },
                },
                "required": ["path", "y_true", "y_score"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_full_analysis",
            "description": (
                "Run a COMPLETE automated analysis pipeline: "
                "EDA → outlier detection → normality tests → "
                "group comparisons (if group_col provided) → "
                "correlation matrix → APA summary table. "
                "This is the one-shot PhD-level analysis tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file",
                    },
                    "group_col": {
                        "type": "string",
                        "description": "Group column for per-group analysis (optional)",
                        "default": "",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_time_series",
            "description": (
                "Time series analysis: ARIMA modeling, ACF/PACF plots, "
                "seasonal decomposition, ADF stationarity test, and optional "
                "forecasting. Requires a date column and a value column."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file",
                    },
                    "date_col": {
                        "type": "string",
                        "description": "Column name with dates/timestamps",
                    },
                    "value_col": {
                        "type": "string",
                        "description": "Column name with time series values",
                    },
                    "order": {
                        "type": "string",
                        "description": "ARIMA order as 'p,d,q' (e.g., '1,1,1')",
                        "default": "",
                    },
                    "group": {
                        "type": "string",
                        "description": "Optional group column",
                        "default": "",
                    },
                    "forecast_steps": {
                        "type": "integer",
                        "description": "Number of steps to forecast (requires order)",
                        "default": 0,
                    },
                    "format": {
                        "type": "string",
                        "enum": ["png", "svg", "pdf"],
                        "description": "Output chart format. Vector formats ('svg', 'pdf') are fully scalable for premium journals.",
                        "default": "png",
                    },
                },
                "required": ["path", "date_col", "value_col"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_survival",
            "description": (
                "Survival analysis: Kaplan-Meier curves, log-rank test, "
                "Cox proportional hazards model. Requires a time-to-event "
                "column and an event indicator column."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file",
                    },
                    "time_col": {
                        "type": "string",
                        "description": "Column with follow-up time",
                    },
                    "event_col": {
                        "type": "string",
                        "description": "Column with event indicator (1=event, 0=censored)",
                    },
                    "group": {
                        "type": "string",
                        "description": "Optional group column for stratified curves",
                        "default": "",
                    },
                    "formula": {
                        "type": "string",
                        "description": "Cox model formula (e.g., 'age + sex + group')",
                        "default": "",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["png", "svg", "pdf"],
                        "description": "Output chart format. Vector formats ('svg', 'pdf') are fully scalable for premium journals.",
                        "default": "png",
                    },
                },
                "required": ["path", "time_col", "event_col"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_meta_analysis",
            "description": (
                "Meta-analysis: combine effect sizes across studies. "
                "Fixed or random effects (DerSimonian-Laird / Paule-Mandel). "
                "Returns forest plot, funnel plot, heterogeneity stats (I², Q, τ²), "
                "and overall effect estimate with 95% CI."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the data file with study-level data",
                    },
                    "effect_col": {
                        "type": "string",
                        "description": "Column name with effect sizes (e.g., Cohen's d, log OR, mean difference)",
                    },
                    "variance_col": {
                        "type": "string",
                        "description": "Column name with variance of effect sizes",
                        "default": "",
                    },
                    "se_col": {
                        "type": "string",
                        "description": "Column name with standard error (alternative to variance_col)",
                        "default": "",
                    },
                    "study_col": {
                        "type": "string",
                        "description": "Column name with study names for the forest plot",
                        "default": "",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["random", "fixed"],
                        "description": "Random effects (Paule-Mandel) or fixed effects",
                        "default": "random",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["png", "svg", "pdf"],
                        "description": "Output chart format. Vector formats ('svg', 'pdf') are fully scalable for premium journals.",
                        "default": "png",
                    },
                },
                "required": ["path", "effect_col"],
            },
        },
    },
]

# Register schemas and executors in the global registry
for _schema in _DATA_TOOL_SCHEMAS:
    _name = _schema["function"]["name"]
    # Update/override executor in EXECUTORS
    EXECUTORS[_name] = _BUILTIN_DATA_EXECUTORS[_name]
    # Update or add to ALL_TOOLS in-place
    existing_idx = next((i for i, t in enumerate(ALL_TOOLS) if t["function"]["name"] == _name), None)
    if existing_idx is not None:
        ALL_TOOLS[existing_idx] = _schema
    else:
        ALL_TOOLS.append(_schema)


