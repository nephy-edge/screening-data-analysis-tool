"""Lease Cohorts sheet: monthly cohort performance (value, defaults,
recoveries, PvD, active/defaulted leases in month, churn rate)."""

import numpy as np
import pandas as pd


def build_cohorts(df, gi):
    cohorts = sorted(df["contract_cohort"].dropna().unique())
    rows = []
    for c in cohorts:
        sub = df[df["contract_cohort"] == c]
        defaulted = (sub["defaulted_gt_nmo"] == True).sum()  # noqa: E712
        recovered = ((sub["defaulted_gt_nmo"] == True) & sub["redeployed"]).sum()  # noqa: E712
        active_mask = (df["contract_cohort"] <= c) & (
            (df["last_active_date"] >= c) | df["last_active_date"].isna()
        )
        default_mask = (df["contract_cohort"] <= c) & (df["last_active_date"] == c)
        active = active_mask.sum()
        defaulted_in_month = default_mask.sum()
        rows.append({
            "cohort": c,
            "value_of_leases": sub["total_contract_value"].sum(),
            "value_of_new_vehicles": sub[sub["first_asset_lease"]]["cost_of_asset"].sum(),
            "new_leases": len(sub),
            "defaulted": defaulted,
            "recovered": recovered,
            "pct_recovered": recovered / defaulted if defaulted else np.nan,
            "pvd": sub["total_paid"].sum() / sub["amount_expected_to_date"].sum(),
            "avg_days_to_recovery": sub["days_to_recovery"].mean(),
            "active_leases_in_month": active,
            "defaulted_leases_in_month": defaulted_in_month,
            "churn_rate": defaulted_in_month / active if active else np.nan,
        })
    return pd.DataFrame(rows)
