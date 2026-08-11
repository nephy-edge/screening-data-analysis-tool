import pandas as pd
import numpy as np


def _agg(grouped, col):
    if col in grouped.obj.columns:
        return grouped[col].sum()
    return pd.Series(float("nan"), index=list(grouped.indices.keys()))


def build_cohorts(df: pd.DataFrame):
    grouped = df.groupby("Cohort", dropna=False)

    cohorts = pd.DataFrame({
        "Cohort": grouped["Cohort"].first(),
        "Loan Count": grouped["Loan ID"].count(),
        "Total Principal": grouped["Principal Value"].sum(),
        "Total Interest": grouped["Expected Interest"].sum(),
        "Total Fee": grouped["Expected Fee"].sum(),
        "Total Due": _agg(grouped, "Total Due"),
        "Total Paid": grouped["Total Paid"].sum(),
        "Avg Term (days)": grouped["Term (days)"].mean(),
        "Weighted Avg Term": (
            grouped.apply(
                lambda g: (g["Term (days)"] * g["Principal Value"]).sum()
                / g["Principal Value"].sum()
            )
        ),
    })

    matured = df[df["Reached T+3?"] == True]
    if not matured.empty:
        mat_grouped = matured.groupby("Cohort", dropna=False)
        cohorts["Matured Count"] = mat_grouped["Loan ID"].count()

        def _loss_rate(g):
            if "Total Due" not in g.columns or "Total Paid" not in g.columns:
                return float("nan")
            due = g["Total Due"]
            paid = g["Total Paid"]
            denom = due.sum()
            if denom == 0:
                return float("nan")
            return (due - paid).sum() / denom

        cohorts["Loss Rate"] = mat_grouped.apply(_loss_rate)
    else:
        cohorts["Matured Count"] = 0
        cohorts["Loss Rate"] = float("nan")

    cohorts["PvD Ratio"] = (
        cohorts["Total Paid"] / cohorts["Total Due"]
    ).where(cohorts["Total Due"] > 0)

    return cohorts.reset_index(drop=True)
