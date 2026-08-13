import pandas as pd
import numpy as np


def _agg(grouped, col):
    if col in grouped.obj.columns:
        return grouped[col].sum()
    return pd.Series(float("nan"), index=list(grouped.indices.keys()))


def build_cohorts(df: pd.DataFrame):
    grouped = df.groupby("Cohort", dropna=False)

    weighted_term_num = (df["Term (days)"] * df["Principal Value"]).groupby(
        df["Cohort"], dropna=False
    ).sum()
    weighted_term_den = df["Principal Value"].groupby(df["Cohort"], dropna=False).sum()
    weighted_avg_term = (weighted_term_num / weighted_term_den).reindex(
        grouped["Cohort"].first().index
    )

    cohorts = pd.DataFrame({
        "Cohort": grouped["Cohort"].first(),
        "Loan Count": grouped["Loan ID"].count(),
        "Total Principal": grouped["Principal Value"].sum(),
        "Total Interest": grouped["Expected Interest"].sum(),
        "Total Fee": grouped["Expected Fee"].sum(),
        "Total Due": _agg(grouped, "Total Due"),
        "Total Paid": grouped["Total Paid"].sum(),
        "Avg Term (days)": grouped["Term (days)"].mean(),
        "Weighted Avg Term": weighted_avg_term,
    })

    matured = df[df["Reached T+3?"] == True]
    if not matured.empty and "Total Due" in matured.columns and "Total Paid" in matured.columns:
        mat_grouped = matured.groupby("Cohort", dropna=False)
        cohorts["Matured Count"] = mat_grouped["Loan ID"].count()

        loss_num = (matured["Total Due"] - matured["Total Paid"]).groupby(
            matured["Cohort"], dropna=False
        ).sum()
        loss_den = matured["Total Due"].groupby(matured["Cohort"], dropna=False).sum()
        loss_rate = (loss_num / loss_den).where(loss_den != 0).reindex(cohorts.index)
        cohorts["Loss Rate"] = loss_rate
    else:
        cohorts["Matured Count"] = 0
        cohorts["Loss Rate"] = float("nan")

    cohorts["PvD Ratio"] = (
        cohorts["Total Paid"] / cohorts["Total Due"]
    ).where(cohorts["Total Due"] > 0)

    return cohorts.reset_index(drop=True)
