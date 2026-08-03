#!/usr/bin/env python3
"""Generate synthetic clinical trial data: 800 patients, 3 arms, 20 variables."""

import numpy as np
import pandas as pd
from pathlib import Path

rng = np.random.default_rng(seed=42)

N_PLACEBO = 320
N_LOW_DOSE = 240
N_HIGH_DOSE = 240
TOTAL = N_PLACEBO + N_LOW_DOSE + N_HIGH_DOSE


def _assign_treatment(rng):
    arms = (["placebo"] * N_PLACEBO + ["low_dose"] * N_LOW_DOSE + ["high_dose"] * N_HIGH_DOSE)
    rng.shuffle(arms)
    return arms


def _generate_patient(arm: str, idx: int, rng):
    age = rng.normal(54, 12)
    age = max(22, min(80, age))
    age_factor = (age - 50) / 50

    sex = "F" if rng.random() < 0.52 else "M"

    bmi = rng.normal(28.5 + age_factor * 3, 5.5)
    bmi = max(16, min(45, bmi))

    smoker = "Yes" if rng.random() < 0.18 else "No"

    # Correlated baselines (r=0.45-0.55 between biomarkers)
    means = [8.2 + age_factor * 0.3, 135 + age_factor * 10 + (bmi - 28) * 0.3,
             120 + (bmi - 28) * 1.5, 0.8 + age_factor * 0.5]
    cov = [[1.44, 0.50 * 1.2 * 15, 0.50 * 1.2 * 30, 0.15 * 1.2 * 0.5],
           [0.50 * 15 * 1.2, 225, 0.50 * 15 * 30, 0.10 * 15 * 0.5],
           [0.50 * 30 * 1.2, 0.50 * 30 * 15, 900, 0.10 * 30 * 0.5],
           [0.15 * 0.5 * 1.2, 0.10 * 0.5 * 15, 0.10 * 0.5 * 30, 0.25]]
    bl = rng.multivariate_normal(means, cov, size=1)[0]
    baseline_hba1c = max(5.5, min(14.0, bl[0]))
    baseline_sbp = max(95, min(190, bl[1]))
    baseline_ldl = max(50, min(250, bl[2]))
    baseline_crp = max(0.1, min(30, bl[3]))

    diabetes_duration = rng.gamma(3, 3) + age * 0.05
    diabetes_duration = max(0, min(30, diabetes_duration))

    # Treatment effects
    if arm == "placebo":
        hba1c_effect = 0.9
        sbp_effect = 0.3
        ldl_effect = 0.3
        ae_lambda = 0.3
        sae_p = 0.05
        qol_mu = 2.0
    elif arm == "low_dose":
        hba1c_effect = 1.6
        sbp_effect = 1.2
        ldl_effect = 1.1
        ae_lambda = 0.6
        sae_p = 0.08
        qol_mu = 4.5
    else:  # high_dose
        hba1c_effect = 2.3
        sbp_effect = 1.8
        ldl_effect = 1.4
        ae_lambda = 0.9
        sae_p = 0.12
        qol_mu = 6.5

    noise_hba1c = rng.normal(0, 0.4) + (baseline_hba1c - 8.2) * 0.15
    noise_sbp = rng.normal(0, 3)
    noise_ldl = rng.normal(0, 6)

    reduction_hba1c = hba1c_effect + noise_hba1c
    reduction_sbp = sbp_effect * 4 + noise_sbp
    reduction_ldl = ldl_effect * 10 + noise_ldl

    week12_hba1c = baseline_hba1c - reduction_hba1c
    week12_hba1c = max(4.0, min(14.0, week12_hba1c))

    week12_sbp = baseline_sbp - reduction_sbp
    week12_sbp = max(80, min(190, week12_sbp))

    week12_ldl = baseline_ldl - reduction_ldl
    week12_ldl = max(30, min(250, week12_ldl))

    hba1c_change = week12_hba1c - baseline_hba1c

    ae_count = rng.poisson(ae_lambda)
    serious_ae = 1 if rng.random() < sae_p else 0
    dropout = 1 if rng.random() < 0.08 else 0

    compliance_pct = rng.normal(92, 8) if not dropout else 0
    compliance_pct = max(0, min(100, compliance_pct))

    qol_change = rng.normal(qol_mu, 3)

    return {
        "patient_id": f"P{idx:04d}",
        "arm": arm,
        "age": round(age, 1),
        "sex": sex,
        "bmi": round(bmi, 1),
        "smoker": smoker,
        "baseline_hba1c": round(baseline_hba1c, 1),
        "baseline_sbp": round(baseline_sbp),
        "baseline_ldl": round(baseline_ldl),
        "baseline_crp": round(baseline_crp, 2),
        "diabetes_duration": round(diabetes_duration, 1),
        "week12_hba1c": round(week12_hba1c, 1),
        "week12_sbp": round(week12_sbp),
        "week12_ldl": round(week12_ldl),
        "hba1c_change": round(hba1c_change, 2),
        "ae_count": ae_count,
        "serious_ae": serious_ae,
        "dropout": dropout,
        "compliance_pct": round(compliance_pct),
        "qol_change": round(qol_change, 1),
    }


def generate(output_path: str = "synthetic_trial_800.csv"):
    arms = _assign_treatment(rng)
    patients = [_generate_patient(arms[i], i + 1, rng) for i in range(TOTAL)]
    df = pd.DataFrame(patients)

    outcome_cols = ["hba1c_change", "week12_hba1c", "week12_sbp", "week12_ldl", "qol_change"]
    df.to_csv(output_path, index=False)
    return df


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "synthetic_trial_800.csv")
    df = generate(out)
    print(f"Generated {len(df)} patients -> {out}")
    print(f"Columns: {list(df.columns)}")
    print(f"Arms: {df['arm'].value_counts().to_dict()}")
    print(f"Outcome (hba1c_change): mean={df['hba1c_change'].mean():.2f}, sd={df['hba1c_change'].std():.2f}")
