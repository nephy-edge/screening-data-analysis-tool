"""TS Covenants sheet: proposed performance and recoverability covenants,
read off the Unit Economics / LTV outputs and the repayment curve."""

import numpy as np


def build_ts_covenants(ue, ltv, curve):
    ts = {
        "recovery_rate_observed": ltv["loss_non_recoverability"],
        "option2_observed": ue["monthly_observed_repayment"],
    }
    for m in (6, 12, 24):
        row = curve[curve["mob"] == m]
        ts[f"paid_at_{m}m_over_cost"] = row["pct_paid_over_cost"].iloc[0] if len(row) else np.nan
    return ts
