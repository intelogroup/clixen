"""Data-analysis specialist: statistical tests, regression, summary tables."""

from __future__ import annotations

import time

from ._helpers import CHARTS_DIR, _ANALYSIS_DEPS_MSG, _chart_url, _view_url, _load_dataframe


def _run_stats_test(path: str, test: str,
                    group_col: str = "", value_col: str = "",
                    x: str = "", y: str = "",
                    target: str = "") -> str:
    """Run a statistical test using pingouin and return formatted results."""
    try:
        import pingouin as pg
        pg.options['round'] = 3
        import pandas as pd
    except ImportError:
        return _ANALYSIS_DEPS_MSG

    try:
        df = _load_dataframe(path)
        t = test.lower()

        # ── Parametric group comparison ──

        if t == "ttest":
            if not x or not y:
                return "ttest requires x (group col) and y (value col)"
            result = pg.ttest(df[x], df[y], paired=False)
            return f"Independent t-test:\n{result.to_string()}"

        elif t == "paired_ttest":
            if not x or not y:
                return "paired_ttest requires x and y columns"
            result = pg.ttest(df[x], df[y], paired=True)
            return f"Paired t-test:\n{result.to_string()}"

        elif t == "anova":
            if not group_col or not value_col:
                return "anova requires group_col and value_col"
            result = pg.anova(data=df, dv=value_col, between=group_col, detailed=True)
            return f"One-way ANOVA:\n{result.to_string()}"

        elif t == "repeated_measures":
            if not group_col or not value_col:
                return "repeated_measures requires group_col and value_col"
            result = pg.rm_anova(data=df, dv=value_col, within=group_col, subject="subject", detailed=True)
            return f"Repeated measures ANOVA:\n{result.to_string()}"

        elif t == "manova":
            if not group_col or not value_col:
                return "manova requires group_col and comma-separated value_col(s)"
            dv_cols = [c.strip() for c in value_col.split(",")]
            result = pg.multivariate_ttest(data=df, dv=dv_cols, group=group_col)
            return f"MANOVA ({group_col} on {dv_cols}):\n{result.to_string()}"

        # ── Post-hoc tests ──

        elif t == "tukey":
            if not group_col or not value_col:
                return "tukey requires group_col and value_col"
            result = pg.pairwise_tukey(data=df, dv=value_col, between=group_col)
            return f"Tukey HSD post-hoc ({group_col} by {value_col}):\n{result.to_string()}"

        elif t == "games_howell":
            if not group_col or not value_col:
                return "games_howell requires group_col and value_col"
            result = pg.pairwise_gameshowell(data=df, dv=value_col, between=group_col)
            return f"Games-Howell post-hoc ({group_col} by {value_col}):\n{result.to_string()}"

        # ── Non-parametric tests ──

        elif t == "mannwhitney" or t == "mwu":
            if not group_col or not value_col:
                return "mannwhitney requires group_col and value_col"
            groups = df[group_col].unique()
            if len(groups) < 2:
                return "Need at least 2 groups"
            g1, g2 = str(groups[0]), str(groups[1])
            a = df.loc[df[group_col] == g1, value_col].astype(float)
            b = df.loc[df[group_col] == g2, value_col].astype(float)
            result = pg.mwu(a, b)
            return f"Mann-Whitney U test ({group_col}: {g1} vs {g2}, {value_col}):\n{result.to_string()}"

        elif t == "wilcoxon":
            if not x or not y:
                return "wilcoxon requires x and y columns (paired numeric)"
            result = pg.wilcoxon(df[x].astype(float), df[y].astype(float))
            return f"Wilcoxon signed-rank ({x} vs {y}):\n{result.to_string()}"

        elif t == "kruskal":
            if not group_col or not value_col:
                return "kruskal requires group_col and value_col"
            result = pg.kruskal(data=df, dv=value_col, between=group_col)
            return f"Kruskal-Wallis H test:\n{result.to_string()}"

        elif t == "friedman":
            if not value_col:
                return "friedman requires comma-separated value_col (repeated measures)"
            cols = [c.strip() for c in value_col.split(",")]
            if len(cols) < 2:
                cols = df.select_dtypes(include="number").columns[:3].tolist()
            import pandas as pd
            melted = pd.melt(df[cols].reset_index(), id_vars=["index"], value_vars=cols,
                            var_name="condition", value_name="measure")
            melted["index"] = melted["index"].astype(str)
            result = pg.friedman(data=melted, dv="measure", within="condition", subject="index")
            return f"Friedman test ({len(cols)} repeated measures):\n{result.to_string()}"

        # ── Correlation ──

        elif t == "correlation" or t == "corr":
            if not x or not y:
                return "correlation requires x and y columns"
            result = pg.corr(df[x], df[y])
            return f"Correlation ({x} vs {y}):\n{result.to_string()}"

        elif t == "partial_corr" or t == "pcorr":
            if not x or not y:
                return "partial_corr requires x and y columns"
            covariates = [c.strip() for c in (target or "").split(",")] if target else []
            if not covariates:
                covariates = [c for c in df.select_dtypes(include="number").columns if c not in (x, y)][:3]
            result = pg.partial_corr(data=df, x=x, y=y, covar=covariates)
            return f"Partial correlation ({x} vs {y}, controlling for {covariates}):\n{result.to_string()}"

        elif t == "distance_corr":
            if not x or not y:
                return "distance_corr requires x and y columns"
            result = pg.distance_corr(df[x], df[y])
            if hasattr(result, 'to_string'):
                return f"Distance correlation ({x} vs {y}):\n{result.to_string()}"
            r = result[0] if isinstance(result, (tuple, list)) else result
            return f"Distance correlation ({x} vs {y}): r = {r:.4f}"

        # ── Effect sizes ──

        elif t == "effsize" or t == "cohen_d":
            efftype = (target or "cohen").lower()
            if not x or not y:
                return "effsize requires x and y columns"
            if "paired" in efftype or efftype == "cohen_dz":
                result = pg.compute_effsize(df[x], df[y], eftype="cohen_dz")
                return f"Cohen's dz (paired) = {result:.3f}"
            elif "hedges" in efftype:
                result = pg.compute_effsize(df[x], df[y], eftype="hedges")
                return f"Hedges' g = {result:.3f}"
            else:
                result = pg.compute_effsize(df[x], df[y], eftype="cohen")
                return f"Cohen's d = {result:.3f}"

        elif t == "eta_squared":
            if not group_col or not value_col:
                return "eta_squared requires group_col and value_col"
            groups = df[group_col].unique()
            if len(groups) < 2:
                return "Need at least 2 groups"
            g1, g2 = groups[0], groups[1]
            result = pg.compute_effsize(df.loc[df[group_col] == g1, value_col],
                                        df.loc[df[group_col] == g2, value_col],
                                        eftype="eta_square")
            return f"Eta-squared = {result:.3f}"

        # ── Power analysis ──

        elif t == "power":
            if not x:
                return "power requires x column name"
            power_type = (target or "ttest").lower()
            if power_type == "ttest":
                d = pg.compute_effsize(df[x], df[y]) if y else 0.5
                pw = pg.power_ttest(d=d, n=len(df), power=None)
                return f"Power analysis (t-test): d={d:.3f}, n={len(df)}, power={pw:.3f}"
            elif power_type == "anova":
                groups = df[group_col].nunique() if group_col else 3
                pw = pg.power_anova(eta_squared=0.06, k=groups, n=len(df), power=None)
                return f"Power analysis (ANOVA): eta2=0.06, k={groups}, n={len(df)}, power={pw:.3f}"
            elif power_type == "correlation":
                r = pg.corr(df[x], df[y]).iloc[0]['r'] if y else 0.3
                pw = pg.power_corr(r=r, n=len(df), power=None)
                return f"Power analysis (correlation): r={r:.3f}, n={len(df)}, power={pw:.3f}"
            else:
                return f"Unknown power type: {power_type}. Options: ttest, anova, correlation"

        # ── Reliability ──

        elif t == "cronbach" or t == "cronbach_alpha":
            cols = [c.strip() for c in (target or value_col or "").split(",")] if (target or value_col) else []
            if not cols:
                cols = df.select_dtypes(include="number").columns.tolist()[:10]
            alpha, ci = pg.cronbach_alpha(data=df[cols])
            return f"Cronbach's alpha ({len(cols)} items): alpha={alpha:.3f}, 95% CI=[{ci[0]:.3f}, {ci[1]:.3f}]"

        elif t == "icc":
            cols = [c.strip() for c in (target or value_col or "").split(",")] if (target or value_col) else []
            if not cols:
                cols = df.select_dtypes(include="number").columns.tolist()[:4]
            if len(cols) < 2:
                return "icc requires at least 2 numeric columns (raters/measurements)"
            import pandas as pd
            melted = df[cols].reset_index().melt(id_vars=["index"], value_vars=cols,
                                                  var_name="rater", value_name="rating")
            melted = melted.rename(columns={"index": "target"})
            result = pg.intraclass_corr(data=melted, targets="target", raters="rater", ratings="rating")
            return f"Intraclass correlation ({len(cols)} raters/measures):\n{result.to_string()}"

        # ── Mediation ──

        elif t == "mediation":
            if not x or not y:
                return "mediation requires x (IV) and y (DV) columns"
            m = target or ""
            if not m and group_col:
                m = group_col
            if not m:
                numeric_cols = [c for c in df.select_dtypes(include="number").columns if c not in (x, y)]
                m = numeric_cols[0] if numeric_cols else ""
            if not m:
                return "mediation requires a mediator column via target="
            med_df = df.copy()
            # Encode categorical columns to numeric
            for col in [x, m, y]:
                if col in med_df.columns and med_df[col].dtype == object:
                    try:
                        med_df[col] = pd.Categorical(med_df[col]).codes
                    except Exception:
                        pass
            result = pg.mediation_analysis(data=med_df, x=x, m=m, y=y)
            return f"Mediation analysis ({x} -> {m} -> {y}):\n{result.to_string()}"

        # ── Categorical / contingency ──

        elif t == "chi2" or t == "chi_square":
            if not x or not y:
                return "chi2 requires x and y column names (categorical)"
            ct = pd.crosstab(df[x], df[y])
            from scipy.stats import chi2_contingency
            chi2, p, dof, expected = chi2_contingency(ct)
            return (
                f"Chi-square test: {x} vs {y}\n"
                f"Chi2 = {chi2:.4f}, df = {dof}, p = {p:.4f}\n"
                f"Contingency table:\n{ct.to_string()}"
            )

        # ── Assumption checks ──

        elif t == "normality":
            cols = [c.strip() for c in (target or value_col or "").split(",")]
            if not cols:
                numeric_cols = df.select_dtypes(include="number").columns.tolist()
                cols = numeric_cols[:5]
            lines = ["Normality tests (Shapiro-Wilk):"]
            for c in cols:
                if c in df.columns:
                    stat, p = pg.normality(df[c])
                    lines.append(f"  {c}: W={stat.iloc[0]:.4f}, p={p.iloc[0]:.4f}")
            return "\n".join(lines)

        elif t == "homogeneity" or t == "homoscedasticity":
            if not group_col or not value_col:
                return "homogeneity requires group_col and value_col"
            result = pg.homoscedasticity(data=df, dv=value_col, group=group_col)
            return f"Homogeneity of variance ({group_col} by {value_col}):\n{result.to_string()}"

        elif t == "vif" or t == "multicollinearity":
            if value_col:
                cols = [c.strip() for c in value_col.split(",")]
            else:
                cols = df.select_dtypes(include="number").columns.tolist()[:10]
            import statsmodels.api as sm
            X = sm.add_constant(df[cols])
            from statsmodels.stats.outliers_influence import variance_inflation_factor
            lines = ["VIF (Variance Inflation Factor):"]
            for i, name in enumerate(X.columns):
                vif_val = variance_inflation_factor(X.values, i)
                flag = "  HIGH" if vif_val > 10 else ("  MODERATE" if vif_val > 5 else "")
                lines.append(f"  {name}: {vif_val:.2f}{flag}")
            return "\n".join(lines)

        # ── Bootstrap ──

        elif t == "bootci" or t == "bootstrap":
            if not x:
                return "bootci requires x column"
            y_col = y or None
            ci = pg.compute_bootci(df[x], func="mean", confidence=0.95, method="bca",
                                   seed=None, n_boot=2000)
            return (
                f"Bootstrap CI (BCa) for '{x}'"
                + (f" and '{y_col}'" if y_col else "")
                + f":\nMean: {df[x].mean():.3f}\n95% CI: [{ci[0]:.3f}, {ci[1]:.3f}]"
            )

        else:
            return (
                f"Unknown test: {test}. Options:\n"
                f"  Parametric: ttest, paired_ttest, anova, repeated_measures, manova\n"
                f"  Post-hoc: tukey, games_howell\n"
                f"  Non-parametric: mannwhitney, wilcoxon, kruskal, friedman\n"
                f"  Correlation: correlation, partial_corr, distance_corr\n"
                f"  Effect size: effsize, eta_squared\n"
                f"  Power: power\n"
                f"  Reliability: cronbach, icc\n"
                f"  Mediation: mediation\n"
                f"  Categorical: chi2\n"
                f"  Diagnostics: normality, homogeneity, vif\n"
                f"  Bootstrap: bootci"
            )

    except Exception as e:
        return f"run_stats_test failed: {type(e).__name__}: {e}"


def _run_regression(path: str, formula: str = "",
                    x: str = "", y: str = "",
                    kind: str = "ols", format: str = "png") -> str:
    """Run a regression model using statsmodels."""
    try:
        import statsmodels.api as sm
        import statsmodels.formula.api as smf
    except ImportError:
        return _ANALYSIS_DEPS_MSG

    try:
        df = _load_dataframe(path)
        k = kind.lower()

        if k == "ols":
            if formula:
                model = smf.ols(formula=formula, data=df).fit()
            elif x and y:
                X = df[[x]] if isinstance(x, str) else df[list(x)]
                X = sm.add_constant(X)
                model = sm.OLS(df[y], X).fit()
            else:
                return "ols requires formula= or x+y columns"

            # Generate residual plot
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, axes = plt.subplots(1, 2, figsize=(12, 5))
            axes[0].scatter(model.fittedvalues, model.resid, alpha=0.5)
            axes[0].axhline(y=0, color="r", linestyle="--")
            axes[0].set_xlabel("Fitted values")
            axes[0].set_ylabel("Residuals")
            axes[0].set_title("Residuals vs Fitted")

            from statsmodels.graphics.gofplots import qqplot
            qqplot(model.resid, line="s", ax=axes[1])
            axes[1].set_title("Q-Q Plot")

            fmt = format.lower() if format else "png"
            if fmt not in {"png", "svg", "pdf"}:
                fmt = "png"

            fname = f"g4l_reg_{int(time.time())}.{fmt}"
            fig.tight_layout()
            fig.savefig(str(CHARTS_DIR / fname), dpi=100)
            plt.close(fig)

            url = _chart_url(fname)
            if fmt == "pdf":
                chart_markup = f"[Download PDF Residual Diagnostics]({url})"
            else:
                chart_markup = f"![Residual Diagnostics]({url})"

            return (
                f"Regression results ({kind}):\n"
                f"{model.summary()}\n\n"
                f"{chart_markup}"
            )

        elif k == "logit":
            if not formula:
                return "logit requires formula= (e.g., 'y ~ x1 + x2')"
            model = smf.logit(formula=formula, data=df).fit(disp=False)
            return f"Logistic regression:\n{model.summary()}"

        elif k == "glm":
            if not formula:
                return "glm requires formula="
            from statsmodels.genmod.families import Poisson, Binomial, Gaussian
            model = smf.glm(formula=formula, data=df, family=Gaussian()).fit()
            return f"GLM (Gaussian):\n{model.summary()}"

        else:
            return f"Unknown regression kind: {kind}. Options: ols, logit, glm"

    except Exception as e:
        return f"run_regression failed: {type(e).__name__}: {e}"


def _apa_summary_row(col_name: str, values) -> dict:
    """Build one row of an APA-style summary: N, M (SD), Min, Max, Skew, Kurtosis."""
    import numpy as np
    clean = values.dropna()
    n = len(clean)
    if n == 0:
        return {"Variable": col_name, "N": 0, "M (SD)": "", "Min": "", "Max": "", "Skew": "", "Kurtosis": ""}
    m = clean.mean()
    s = clean.std()
    mn = clean.min()
    mx = clean.max()
    sk = clean.skew()
    ku = clean.kurtosis()
    return {
        "Variable": col_name,
        "N": n,
        "M (SD)": f"{m:.2f} ({s:.2f})",
        "Min": f"{mn:.2f}",
        "Max": f"{mx:.2f}",
        "Skew": f"{sk:.2f}",
        "Kurtosis": f"{ku:.2f}",
    }


def _make_summary_table(path: str, columns: str = "",
                        group: str = "") -> str:
    """Generate an APA-style descriptive statistics table."""
    try:
        from great_tables import GT, md
        import pandas as pd
        import numpy as np
    except ImportError:
        return _ANALYSIS_DEPS_MSG

    try:
        df = _load_dataframe(path)

        if columns:
            cols = [c.strip() for c in columns.split(",")]
            df = df[[c for c in cols if c in df.columns]]

        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if not numeric_cols:
            return "No numeric columns to summarize"

        if group and group in df.columns:
            rows = []
            for g, grp in df.groupby(group):
                for col in numeric_cols:
                    row = _apa_summary_row(col, grp[col])
                    row["Group"] = str(g)
                    rows.append(row)
            summary = pd.DataFrame(rows)
            # Reorder: Group, Variable, N, M (SD), ...
            cols_order = ["Group", "Variable", "N", "M (SD)", "Min", "Max", "Skew", "Kurtosis"]
            summary = summary[[c for c in cols_order if c in summary.columns]]
        else:
            rows = [_apa_summary_row(col, df[col]) for col in numeric_cols]
            summary = pd.DataFrame(rows)

        gt_table = GT(summary).tab_header(
            title=md(f"**Descriptive Statistics**{' by ' + group if group else ''}")
        )

        desc_html = gt_table.as_raw_html()

        fname = f"g4l_table_{int(time.time())}.html"
        html_path = CHARTS_DIR / fname
        html_path.write_text(desc_html, encoding="utf-8")

        return f"Summary table saved.\n\n[View Full Table]({_view_url(fname)})"
    except Exception as e:
        return f"make_summary_table failed: {type(e).__name__}: {e}"


def _plot_interactive(path: str, chart_type: str, x: str = "", y: str = "",
                      title: str = "", color: str = "") -> str:
    """Generate an interactive Plotly chart + static PNG."""
    try:
        import plotly.express as px
        import plotly.io as pio
    except ImportError:
        return _ANALYSIS_DEPS_MSG

    try:
        df = _load_dataframe(path)
        ct = chart_type.lower()

        pio.kaleido.scope.default_format = "png"

        fig = None
        if ct == "scatter":
            fig = px.scatter(df, x=x, y=y, color=color or None, title=title)
        elif ct == "line":
            fig = px.line(df, x=x, y=y, color=color or None, title=title)
        elif ct == "bar":
            fig = px.bar(df, x=x, y=y, color=color or None, title=title)
        elif ct == "hist" or ct == "histogram":
            fig = px.histogram(df, x=x or df.select_dtypes(include="number").columns[0], title=title)
        elif ct == "box":
            fig = px.box(df, x=x, y=y, color=color or None, title=title)
        elif ct == "violin":
            fig = px.violin(df, x=x, y=y, color=color or None, title=title)
        elif ct == "density" or ct == "density_heatmap":
            fig = px.density_heatmap(df, x=x, y=y, title=title)
        else:
            fig = px.scatter(df, x=x or df.columns[0], y=y or df.columns[1] if len(df.columns) > 1 else df.columns[0],
                             title=title or "Plot")

        ts = int(time.time())
        html_fname = f"g4l_chart_{ts}.html"
        png_fname = f"g4l_chart_{ts}.png"

        # Save interactive HTML
        fig.write_html(str(CHARTS_DIR / html_fname))

        # Save static PNG (may fail if kaleido missing)
        png_saved = False
        try:
            fig.write_image(str(CHARTS_DIR / png_fname), width=1000, height=600)
            png_saved = True
        except Exception:
            pass

        lines = []
        if png_saved:
            lines.append(f"![Chart]({_chart_url(png_fname)})")
        lines.append(f"[Open Interactive Chart]({_view_url(html_fname)})")

        return "\n\n".join(lines)
    except Exception as e:
        return f"plot_interactive failed: {type(e).__name__}: {e}"


