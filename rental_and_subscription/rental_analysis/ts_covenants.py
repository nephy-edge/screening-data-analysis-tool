"""TS Covenants sheet: proposed performance and recoverability covenants,
read off the Unit Economics / LTV outputs and the repayment curve.

Guide-aligned: the Recovery Rate covenant now reports the actual recovery rate
(pct_recovered_last_12m - the Guide's ratio) rather than the loss rate that the
workbook's TS Covenants C20 pointed at. Asset DSLP30 and Receivables Coverage
Ratio (Guide's remaining rental covenants) are added.
"""

import numpy as np


def build_ts_covenants(ue, ltv, curve, debt_service_180d=None):
    ts = {
        # Guide Recovery Rate: repossessed/insurance-recovered contracts over
        # contracts defaulted/cancelled in the last 12 months (>= 3 months prior
        # to the testing date).
        "recovery_rate_observed": ltv["pct_recovered_last_12m"],
        "option2_observed": ue["monthly_observed_repayment"],
        # Guide Asset DSLP30: % of active asset value with no payment in 30 days.
        "asset_dslp30_observed": ltv["asset_dslp30"],
        # Guide Receivables Coverage Ratio: 180 days of performing receivables over
        # the facility's debt service due over the next 180 days. debt_service_180d
        # is a facility-level input (not in the borrower tape); n/a when absent.
        "receivables_coverage_observed": (
            ltv["receivables_value_180d"] / debt_service_180d if debt_service_180d else np.nan
        ),
    }
    for m in (6, 12, 24):
        row = curve[curve["mob"] == m]
        ts[f"paid_at_{m}m_over_cost"] = row["pct_paid_over_cost"].iloc[0] if len(row) else np.nan
    return ts
