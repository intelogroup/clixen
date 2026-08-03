"""Data-analysis specialist: outliers, PCA, ROC, full/time-series/survival/meta analysis + tool registration."""

from __future__ import annotations

import time

from tools.registry import EXECUTORS

from ._helpers import CHARTS_DIR, _ANALYSIS_DEPS_MSG, _chart_url, _view_url, _load_dataframe, _analyze_dataset, _query_data, _plot_chart, _correlate
from ._stats import _run_stats_test, _run_regression, _make_summary_table, _plot_interactive


def _detect_outliers(path: str, method: str = "iqr",
                     column: str = "", group: str = "") -> str:
    """Detect outliers using IQR or z-score method. Returns flagged rows."""
    try:
        import numpy as np
    except ImportError:
        return _ANALYSIS_DEPS_MSG

    try:
        df = _load_dataframe(path)
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if column and column in numeric_cols:
            numeric_cols = [column]

        if method.lower() == "iqr":
            lines = ["Outlier detection (IQR method, 1.5×IQR threshold):"]
            for col in numeric_cols[:10]:
                q1, q3 = df[col].quantile(0.25), df[col].quantile(0.75)
                iqr = q3 - q1
                lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                mask = (df[col] < lower) | (df[col] > upper)
                n = mask.sum()
                if n > 0:
                    vals = df.loc[mask, col].tolist()
                    lines.append(f"  {col}: {n} outlier(s), range ({lower:.2f}, {upper:.2f}): {vals[:5]}{'...' if len(vals) > 5 else ''}")
                else:
                    lines.append(f"  {col}: 0 outliers")
            return "\n".join(lines)
        else:
            from scipy.stats import zscore
            lines = ["Outlier detection (z-score, |z| > 3 threshold):"]
            for col in numeric_cols[:10]:
                z = zscore(df[col].dropna())
                n = (abs(z) > 3).sum()
                if n > 0:
                    idx = np.where(abs(z) > 3)[0]
                    vals = df[col].iloc[idx].tolist()
                    lines.append(f"  {col}: {n} outlier(s): {vals[:5]}{'...' if len(vals) > 5 else ''}")
                else:
                    lines.append(f"  {col}: 0 outliers")
            return "\n".join(lines)
    except Exception as e:
        return f"detect_outliers failed: {type(e).__name__}: {e}"


def _run_pca(path: str, n_components: int = 2,
             columns: str = "", group: str = "", format: str = "png") -> str:
    """Run PCA on numeric columns and return loadings + explained variance + plot."""
    try:
        from sklearn.decomposition import PCA
        from sklearn.preprocessing import StandardScaler
        import numpy as np
    except ImportError:
        return "sklearn not installed. Run: pip install -e \".[data]\""

    try:
        df = _load_dataframe(path)
        if columns:
            cols = [c.strip() for c in columns.split(",")]
            numeric = df[[c for c in cols if c in df.columns]]
        else:
            numeric = df.select_dtypes(include=[np.number])
        if numeric.shape[1] < 2:
            return "Need at least 2 numeric columns for PCA"

        scaled = StandardScaler().fit_transform(numeric.dropna())
        n_comp = min(n_components, numeric.shape[1])
        pca = PCA(n_components=n_comp)
        components = pca.fit_transform(scaled)
        loadings = pca.components_.T
        explained = pca.explained_variance_ratio_

        lines = [f"PCA ({n_comp} components):"]
        lines.append(f"Explained variance: {', '.join(f'{v:.3f}' for v in explained)}")
        lines.append(f"Cumulative: {explained.sum():.3f}")
        lines.append("")
        lines.append("Component loadings (correlation):")
        for i, col in enumerate(numeric.columns):
            row = f"  {col:20s}"
            for j in range(n_comp):
                row += f"  PC{j+1}:{loadings[i, j]: .3f}"
            lines.append(row)

        # Generate PCA biplot
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 8))
        colors = None
        if group and group in df.columns:
            groups = df.loc[numeric.dropna().index, group]
            unique_groups = groups.unique()
            from matplotlib.cm import tab10
            color_map = {g: tab10(i / len(unique_groups)) for i, g in enumerate(unique_groups)}
            colors = groups.map(color_map)
        scatter = ax.scatter(components[:, 0], components[:, 1], c=colors, alpha=0.6, edgecolors="k", linewidth=0.5)
        ax.set_xlabel(f"PC1 ({explained[0]:.1%})")
        ax.set_ylabel(f"PC2 ({explained[1]:.1%})" if n_comp >= 2 else f"PC1")
        ax.set_title("PCA Biplot")
        ax.axhline(0, color="gray", linestyle="--", alpha=0.3)
        ax.axvline(0, color="gray", linestyle="--", alpha=0.3)
        if group and group in df.columns:
            ax.legend(handles=[plt.Line2D([0], [0], marker='o', color='w',
                                          markerfacecolor=color_map[g], markersize=8, label=g)
                               for g in unique_groups], title=group)

        fmt = format.lower() if format else "png"
        if fmt not in {"png", "svg", "pdf"}:
            fmt = "png"

        fname = f"g4l_pca_{int(time.time())}.{fmt}"
        fig.tight_layout()
        fig.savefig(str(CHARTS_DIR / fname), dpi=100)
        plt.close(fig)

        url = _chart_url(fname)
        if fmt == "pdf":
            chart_markup = f"[Download PDF PCA Biplot]({url})"
        else:
            chart_markup = f"![PCA Biplot]({url})"

        return "\n".join(lines) + f"\n\n{chart_markup}"
    except Exception as e:
        return f"run_pca failed: {type(e).__name__}: {e}"


def _plot_roc(path: str, y_true: str, y_score: str,
              title: str = "ROC Curve", format: str = "png") -> str:
    """Plot ROC curve from true labels and predicted probabilities/scores."""
    try:
        from sklearn.metrics import roc_curve, auc, roc_auc_score
    except ImportError:
        return "sklearn not installed. Run: pip install -e \".[data]\""

    try:
        df = _load_dataframe(path)
        if y_true not in df.columns or y_score not in df.columns:
            return f"Columns {y_true} or {y_score} not found"

        fpr, tpr, thresholds = roc_curve(df[y_true], df[y_score])
        auc_val = auc(fpr, tpr)

        import plotly.graph_objects as go
        import plotly.io as pio
        pio.kaleido.scope.default_format = "png"

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=fpr, y=tpr, mode="lines",
                                  name=f"AUC = {auc_val:.3f}",
                                  line=dict(width=2, color="darkorange")))
        fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines",
                                  name="Chance", line=dict(dash="dash", color="gray")))
        fig.update_layout(title=title, xaxis_title="False Positive Rate",
                          yaxis_title="True Positive Rate",
                          width=600, height=600, showlegend=True)

        fmt = format.lower() if format else "png"
        if fmt not in {"png", "svg", "pdf"}:
            fmt = "png"

        ts = int(time.time())
        html_fname = f"g4l_roc_{ts}.html"
        img_fname = f"g4l_roc_{ts}.{fmt}"

        fig.write_html(str(CHARTS_DIR / html_fname))
        img_saved = False
        try:
            fig.write_image(str(CHARTS_DIR / img_fname), width=600, height=600)
            img_saved = True
        except Exception:
            pass

        lines = [f"ROC analysis: AUC = {auc_val:.3f}"]
        if img_saved:
            url = _chart_url(img_fname)
            if fmt == "pdf":
                lines.append(f"[Download PDF ROC Curve]({url})")
            else:
                lines.append(f"![ROC Curve]({url})")
        lines.append(f"[Open Interactive ROC]({_view_url(html_fname)})")
        return "\n\n".join(lines)
    except Exception as e:
        return f"plot_roc failed: {type(e).__name__}: {e}"


def _run_full_analysis(path: str, group_col: str = "") -> str:
    """Run a complete automated analysis pipeline: EDA → tests → tables."""
    try:
        lines = []
        lines.append(f"# Full Analysis: {path}\n")

        # 1. EDA
        lines.append("## 1. Exploratory Data Analysis")
        lines.append(_analyze_dataset(path))
        lines.append("")

        # 2. Outliers
        lines.append("## 2. Outlier Detection")
        lines.append(_detect_outliers(path, method="iqr"))
        lines.append("")

        # 3. Normality
        lines.append("## 3. Normality Tests (Shapiro-Wilk)")
        lines.append(_run_stats_test(path, "normality"))
        lines.append("")

        # 4. Group comparisons (if group provided)
        if group_col:
            lines.append(f"## 4. Group Analysis ({group_col})")

            # Homogeneity
            df = _load_dataframe(path)
            numeric_cols = df.select_dtypes(include="number").columns.tolist()
            if numeric_cols:
                val_col = numeric_cols[0]
                lines.append(_run_stats_test(path, "homogeneity", group_col=group_col, value_col=val_col))

            # ANOVA
            if numeric_cols:
                val_col = numeric_cols[0]
                lines.append(_run_stats_test(path, "anova", group_col=group_col, value_col=val_col))

            # Effect size (compare first two groups within the group column)
            grp_vals = df[group_col].unique()
            if len(grp_vals) >= 2 and len(numeric_cols) >= 1:
                g1, g2 = str(grp_vals[0]), str(grp_vals[1])
                v = numeric_cols[0]
                lines.append(f"> Effect size ({group_col}: {g1} vs {g2}, {v})")
                a = df.loc[df[group_col] == g1, v].astype(float)
                b = df.loc[df[group_col] == g2, v].astype(float)
                import pingouin as pg
                d = pg.compute_effsize(a, b, eftype="cohen")
                lines.append(f"Cohen's d = {d:.3f}")

            lines.append("")

        # 5. Correlation matrix
        lines.append("## 5. Correlation Matrix")
        lines.append(_correlate(path))
        lines.append("")

        # 6. Summary table
        lines.append("## 6. Descriptive Statistics Table")
        result = _make_summary_table(path, group=group_col)
        lines.append(result)
        lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"run_full_analysis failed: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Time series analysis
# ---------------------------------------------------------------------------

def _run_time_series(path: str, date_col: str, value_col: str,
                     order: str = "", group: str = "", forecast_steps: int = 0, format: str = "png") -> str:
    """Time series analysis: ACF/PACF, ARIMA, seasonal decomposition, forecast."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from statsmodels.tsa.stattools import acf, pacf
        from statsmodels.graphics.tsaplots import plot_acf, plot_pacf
        import statsmodels.api as sm
        import pandas as pd
        import numpy as np
    except ImportError:
        return _ANALYSIS_DEPS_MSG

    try:
        df = _load_dataframe(path)
        if date_col not in df.columns or value_col not in df.columns:
            return f"Columns {date_col} or {value_col} not found"

        ts_data = df.set_index(date_col)
        if not isinstance(ts_data.index, pd.DatetimeIndex):
            ts_data.index = pd.to_datetime(ts_data.index)

        series = ts_data[value_col].dropna().astype(float)
        lines = []

        if group and group in df.columns:
            lines.append(f"Time series: {value_col} grouped by {group}")

        # Basic stats
        lines.append(f"\nObservations: {len(series)}")
        lines.append(f"Mean: {series.mean():.3f}, SD: {series.std():.3f}")
        lines.append(f"Min: {series.min():.3f}, Max: {series.max():.3f}")

        # Stationarity (ADF test)
        from statsmodels.tsa.stattools import adfuller
        adf = adfuller(series.dropna())
        lines.append(f"\nADF test: stat={adf[0]:.4f}, p={adf[1]:.4f}")
        lines.append(f"  Stationary: {'Yes' if adf[1] < 0.05 else 'No'}")

        # Seasonal decomposition
        fig, axes = plt.subplots(4, 1, figsize=(12, 10))
        try:
            decomp = sm.tsa.seasonal_decompose(series.dropna(), model="additive", period=min(12, len(series) // 2))
            decomp.observed.plot(ax=axes[0], title="Observed")
            decomp.trend.plot(ax=axes[1], title="Trend")
            decomp.seasonal.plot(ax=axes[2], title="Seasonal")
            decomp.resid.plot(ax=axes[3], title="Residual")
            fig.tight_layout()
            fmt = format.lower() if format else "png"
            if fmt not in {"png", "svg", "pdf"}:
                fmt = "png"

            fname_dec = f"g4l_ts_decomp_{int(time.time())}.{fmt}"
            fig.savefig(str(CHARTS_DIR / fname_dec), dpi=100)
            plt.close(fig)
            if fmt == "pdf":
                lines.append(f"\n[Download PDF Seasonal Decomposition]({_chart_url(fname_dec)})")
            else:
                lines.append(f"\n![Seasonal Decomposition]({_chart_url(fname_dec)})")
            lines.append(f"  Trend SD: {decomp.trend.std():.3f}, Seasonal SD: {decomp.seasonal.std():.3f}, Residual SD: {decomp.resid.std():.3f}")
        except Exception:
            plt.close(fig)
            lines.append("\n  Seasonal decomposition skipped (insufficient data)")

        fmt = format.lower() if format else "png"
        if fmt not in {"png", "svg", "pdf"}:
            fmt = "png"

        # ACF/PACF
        ts_key = f"g4l_ts_acf_{int(time.time())}.{fmt}"
        fig, axes = plt.subplots(1, 2, figsize=(12, 4))
        plot_acf(series.dropna(), lags=min(40, len(series) // 3), ax=axes[0])
        plot_pacf(series.dropna(), lags=min(40, len(series) // 3), ax=axes[1])
        fig.tight_layout()
        fig.savefig(str(CHARTS_DIR / ts_key), dpi=100)
        plt.close(fig)
        if fmt == "pdf":
            lines.append(f"[Download PDF ACF/PACF]({_chart_url(ts_key)})")
        else:
            lines.append(f"![ACF/PACF]({_chart_url(ts_key)})")

        # ARIMA modeling
        if order:
            try:
                p, d, q = [int(x.strip()) for x in order.replace(",", " ").split()]
                model = sm.tsa.ARIMA(series.dropna(), order=(p, d, q)).fit()
                lines.append(f"\nARIMA({p},{d},{q}) results:")
                lines.append(f"  AIC: {model.aic:.2f}, BIC: {model.bic:.2f}")
                for param, coef, pval in zip(model.params.index, model.params, model.pvalues):
                    lines.append(f"  {param}: coef={coef:.4f}, p={pval:.4f}")

                if forecast_steps > 0:
                    forecast = model.forecast(steps=forecast_steps)
                    fc_fname = f"g4l_ts_forecast_{int(time.time())}.{fmt}"
                    fig, ax = plt.subplots(figsize=(10, 5))
                    ax.plot(series.index, series.values, label="Observed")
                    fc_idx = pd.date_range(series.index[-1], periods=forecast_steps + 1, freq="D")[1:]
                    ax.plot(fc_idx, forecast, label="Forecast", linestyle="--", color="red")
                    ax.legend()
                    ax.set_title(f"ARIMA({p},{d},{q}) Forecast ({forecast_steps} steps)")
                    fig.tight_layout()
                    fig.savefig(str(CHARTS_DIR / fc_fname), dpi=100)
                    plt.close(fig)
                    if fmt == "pdf":
                        lines.append(f"\n[Download PDF Forecast]({_chart_url(fc_fname)})")
                    else:
                        lines.append(f"\n![Forecast]({_chart_url(fc_fname)})")
                    lines.append(f"  Forecast values: {', '.join(f'{v:.2f}' for v in forecast[:10])}")
            except Exception as e:
                lines.append(f"\n  ARIMA failed: {e}")

        return "\n".join(lines)
    except Exception as e:
        return f"run_time_series failed: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Survival analysis
# ---------------------------------------------------------------------------

def _run_survival(path: str, time_col: str, event_col: str,
                  group: str = "", formula: str = "", format: str = "png") -> str:
    """Survival analysis: Kaplan-Meier, log-rank test, Cox PH model."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from lifelines import KaplanMeierFitter, CoxPHFitter
        from lifelines.statistics import logrank_test
        import pandas as pd
        import numpy as np
    except ImportError:
        return "lifelines not installed. Run: pip install lifelines"

    try:
        df = _load_dataframe(path)
        if time_col not in df.columns or event_col not in df.columns:
            return f"Columns {time_col} or {event_col} not found"

        T = df[time_col].astype(float).values
        E = df[event_col].astype(bool).values
        lines = []

        # Event summary
        n_events = int(E.sum())
        n_censored = int((~E).sum())
        lines.append(f"Survival analysis: {len(df)} subjects")
        lines.append(f"  Events: {n_events}, Censored: {n_censored}")
        lines.append(f"  Event rate: {n_events / len(df):.1%}")
        median_time = np.median(T[E]) if n_events > 0 else float("inf")
        lines.append(f"  Median time to event (observed): {median_time:.1f}")

        fname_base = f"g4l_survival_{int(time.time())}"

        if group and group in df.columns:
            # Kaplan-Meier by group
            fig, ax = plt.subplots(figsize=(10, 7))
            groups = df[group].unique()
            kmf_dict = {}
            for i, g in enumerate(sorted(groups)):
                mask = df[group] == g
                kmf = KaplanMeierFitter()
                kmf.fit(T[mask], event_observed=E[mask], label=str(g))
                kmf.plot_survival_function(ax=ax)
                kmf_dict[str(g)] = kmf
                median = kmf.median_survival_time_
                lines.append(f"\n  Group {g}: median survival={median:.1f}, "
                            f"n={mask.sum()}, events={int(E[mask].sum())}")

            ax.set_title("Kaplan-Meier Survival Curves")
            ax.set_xlabel("Time")
            ax.set_ylabel("Survival Probability")
            ax.legend()
            fig.tight_layout()
            fmt = format.lower() if format else "png"
            if fmt not in {"png", "svg", "pdf"}:
                fmt = "png"

            fname_km = f"{fname_base}_km.{fmt}"
            fig.savefig(str(CHARTS_DIR / fname_km), dpi=100)
            plt.close(fig)
            if fmt == "pdf":
                lines.append(f"\n[Download PDF Kaplan-Meier Curves]({_chart_url(fname_km)})")
            else:
                lines.append(f"\n![Kaplan-Meier Curves]({_chart_url(fname_km)})")

            # Log-rank test between first two groups
            grp_list = sorted(groups)
            if len(grp_list) >= 2:
                g1 = df[group] == grp_list[0]
                g2 = df[group] == grp_list[1]
                lr = logrank_test(T[g1], T[g2], event_observed_A=E[g1], event_observed_B=E[g2])
                lines.append(f"\n  Log-rank test ({grp_list[0]} vs {grp_list[1]}): "
                            f"chi2={lr.test_statistic:.2f}, p={lr.p_value:.4f}")

            # Cox PH model
            cox_df = df[[time_col, event_col, group]].copy()
            if formula:
                cox_df = df.copy()
                try:
                    cph = CoxPHFitter()
                    cph.fit(cox_df, duration_col=time_col, event_col=event_col, formula=formula)
                    lines.append(f"\n  Cox PH model ({formula}):")
                    lines.append(f"  Concordance: {cph.concordance_index_:.3f}")
                    lines.append(f"  Log-likelihood: {cph.log_likelihood_:.2f}")
                    for col in cph.params_.index:
                        hr = np.exp(cph.params_[col])
                        ci_low = np.exp(cph.confidence_intervals_.iloc[:, 0].loc[col])
                        ci_high = np.exp(cph.confidence_intervals_.iloc[:, 1].loc[col])
                        pv = cph.summary.loc[col, 'p']
                        sig = '***' if pv < 0.001 else ('**' if pv < 0.01 else ('*' if pv < 0.05 else ''))
                        lines.append(f"    {col}: HR={hr:.3f} [{ci_low:.3f}, {ci_high:.3f}], p={pv:.4f} {sig}")
                except Exception as e:
                    lines.append(f"\n  Cox PH failed: {e}")
        else:
            # Single KM curve
            fig, ax = plt.subplots(figsize=(10, 7))
            kmf = KaplanMeierFitter()
            kmf.fit(T, event_observed=E, label="All")
            kmf.plot_survival_function(ax=ax)
            ax.set_title("Kaplan-Meier Survival Curve")
            ax.set_xlabel("Time")
            ax.set_ylabel("Survival Probability")
            fig.tight_layout()
            fmt = format.lower() if format else "png"
            if fmt not in {"png", "svg", "pdf"}:
                fmt = "png"

            fname_km = f"{fname_base}_km.{fmt}"
            fig.savefig(str(CHARTS_DIR / fname_km), dpi=100)
            plt.close(fig)
            if fmt == "pdf":
                lines.append(f"\n[Download PDF Kaplan-Meier Curve]({_chart_url(fname_km)})")
            else:
                lines.append(f"\n![Kaplan-Meier Curve]({_chart_url(fname_km)})")

        return "\n".join(lines)
    except Exception as e:
        return f"run_survival failed: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Meta-analysis
# ---------------------------------------------------------------------------

def _run_meta_analysis(path: str, effect_col: str, variance_col: str = "",
                       se_col: str = "", study_col: str = "",
                       method: str = "random", format: str = "png") -> str:
    """Meta-analysis: fixed/random effects, forest plot, funnel plot, heterogeneity."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from statsmodels.stats.meta_analysis import combine_effects
        import pandas as pd
        import numpy as np
    except ImportError:
        return _ANALYSIS_DEPS_MSG

    try:
        df = _load_dataframe(path)
        if effect_col not in df.columns:
            return f"Effect column '{effect_col}' not found"
        if not variance_col and not se_col:
            return "Provide variance_col or se_col"

        effects = df[effect_col].astype(float).dropna().values
        study_names = df[study_col].astype(str).values if study_col and study_col in df.columns else None

        if variance_col and variance_col in df.columns:
            variances = df[variance_col].astype(float).dropna().values
        elif se_col and se_col in df.columns:
            variances = (df[se_col].astype(float).dropna().values) ** 2
        else:
            return "Could not compute variances"

        if len(effects) < 2:
            return "Need at least 2 studies for meta-analysis"

        method_re = "iterated" if method == "random" else "chi2"
        res = combine_effects(effects, variances, method_re=method_re,
                              row_names=study_names.tolist() if study_names is not None else None)

        lines = []
        lines.append(f"Meta-analysis: {len(effects)} studies ({method} effects)")
        lines.append("")

        # Study-level table
        sf = res.summary_frame()
        lines.append("Study-level results:")
        lines.append(sf.to_string())
        lines.append("")

        # Heterogeneity
        i2 = max(0, res.i2)
        lines.append(f"Heterogeneity: Q = {res.q:.3f}, I² = {i2:.1%}, τ² = {res.tau2:.4f}")
        if hasattr(res, 'p'):
            lines.append(f"  Heterogeneity p = {res.p:.4f}")
        lines.append("")

        # Overall estimates
        if method == "random":
            effect_size = res.mean_effect_re
            ci = res.conf_int()[3]  # REML CI
            lines.append(f"Random effects model:")
        else:
            effect_size = res.mean_effect_fe
            ci = res.conf_int()[0]
        lines.append(f"  Effect: {effect_size:.4f}  95% CI: [{ci[0]:.4f}, {ci[1]:.4f}]")
        hr = np.exp(effect_size)
        hr_ci = np.exp(ci)
        lines.append(f"  OR/HR: {hr:.3f}  95% CI: [{hr_ci[0]:.3f}, {hr_ci[1]:.3f}]")

        fmt = format.lower() if format else "png"
        if fmt not in {"png", "svg", "pdf"}:
            fmt = "png"

        # Forest plot
        ts = int(time.time())
        fig = res.plot_forest(use_exp=False)
        if fig is not None:
            fname = f"g4l_forest_{ts}.{fmt}"
            fig.savefig(str(CHARTS_DIR / fname), dpi=100, bbox_inches="tight")
            plt.close(fig)
            if fmt == "pdf":
                lines.append(f"\n[Download PDF Forest Plot]({_chart_url(fname)})")
            else:
                lines.append(f"\n![Forest Plot]({_chart_url(fname)})")

        # Funnel plot
        fname_funnel = f"g4l_funnel_{ts}.{fmt}"
        fig, ax = plt.subplots(figsize=(8, 8))
        ses = np.sqrt(variances)
        ax.scatter(effects, ses, alpha=0.7, s=40)
        ax.set_xlabel("Effect size")
        ax.set_ylabel("Standard error")
        ax.set_title("Funnel Plot")
        ax.invert_yaxis()
        ax.axvline(x=effect_size, color="red", linestyle="--", alpha=0.5)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(str(CHARTS_DIR / fname_funnel), dpi=100)
        plt.close(fig)
        if fmt == "pdf":
            lines.append(f"[Download PDF Funnel Plot]({_chart_url(fname_funnel)})")
        else:
            lines.append(f"![Funnel Plot]({_chart_url(fname_funnel)})")

        return "\n".join(lines)
    except Exception as e:
        return f"run_meta_analysis failed: {type(e).__name__}: {e}"


# Register built-in data tools in EXECUTORS if not already there
_BUILTIN_DATA_EXECUTORS = {
    "analyze_dataset": lambda args: _analyze_dataset(args["path"]),
    "query_data": lambda args: _query_data(args["path"], args["sql"]),
    "plot_chart": lambda args: _plot_chart(
        path=args["path"],
        chart_type=args.get("type", "bar"),
        x=args.get("x", ""),
        y=args.get("y", ""),
        title=args.get("title", ""),
        output_path=args.get("output_path", ""),
        format=args.get("format", "png"),
    ),
    "correlate": lambda args: _correlate(
        path=args["path"],
        columns=args.get("columns", ""),
        format=args.get("format", "png"),
    ),
    "run_stats_test": lambda args: _run_stats_test(
        path=args["path"],
        test=args["test"],
        group_col=args.get("group_col", ""),
        value_col=args.get("value_col", ""),
        x=args.get("x", ""),
        y=args.get("y", ""),
        target=args.get("target", ""),
    ),
    "run_regression": lambda args: _run_regression(
        path=args["path"],
        formula=args.get("formula", ""),
        x=args.get("x", ""),
        y=args.get("y", ""),
        kind=args.get("kind", "ols"),
        format=args.get("format", "png"),
    ),
    "make_summary_table": lambda args: _make_summary_table(
        path=args["path"],
        columns=args.get("columns", ""),
        group=args.get("group", ""),
    ),
    "plot_interactive": lambda args: _plot_interactive(
        path=args["path"],
        chart_type=args.get("type", "scatter"),
        x=args.get("x", ""),
        y=args.get("y", ""),
        title=args.get("title", ""),
        color=args.get("color", ""),
    ),
    "detect_outliers": lambda args: _detect_outliers(
        path=args["path"],
        method=args.get("method", "iqr"),
        column=args.get("column", ""),
        group=args.get("group", ""),
    ),
    "run_pca": lambda args: _run_pca(
        path=args["path"],
        n_components=args.get("n_components", 2),
        columns=args.get("columns", ""),
        group=args.get("group", ""),
        format=args.get("format", "png"),
    ),
    "plot_roc": lambda args: _plot_roc(
        path=args["path"],
        y_true=args["y_true"],
        y_score=args["y_score"],
        title=args.get("title", "ROC Curve"),
        format=args.get("format", "png"),
    ),
    "run_full_analysis": lambda args: _run_full_analysis(
        path=args["path"],
        group_col=args.get("group_col", ""),
    ),
    "run_time_series": lambda args: _run_time_series(
        path=args["path"],
        date_col=args["date_col"],
        value_col=args["value_col"],
        order=args.get("order", ""),
        group=args.get("group", ""),
        forecast_steps=args.get("forecast_steps", 0),
        format=args.get("format", "png"),
    ),
    "run_survival": lambda args: _run_survival(
        path=args["path"],
        time_col=args["time_col"],
        event_col=args["event_col"],
        group=args.get("group", ""),
        formula=args.get("formula", ""),
        format=args.get("format", "png"),
    ),
    "run_meta_analysis": lambda args: _run_meta_analysis(
        path=args["path"],
        effect_col=args["effect_col"],
        variance_col=args.get("variance_col", ""),
        se_col=args.get("se_col", ""),
        study_col=args.get("study_col", ""),
        method=args.get("method", "random"),
        format=args.get("format", "png"),
    ),
}

# Merge into registry EXECUTORS if not already present
for _name, _fn in _BUILTIN_DATA_EXECUTORS.items():
    if _name not in EXECUTORS:
        EXECUTORS[_name] = _fn


