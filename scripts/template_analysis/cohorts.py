import pandas as pd
import numpy as np


def _num(series):
    """Coerce to a plain float64 Series regardless of source dtype backend.

    Columns can arrive numpy-backed (e.g. Total Due computed via a numpy
    payment-schedule fallback) or pyarrow-backed (e.g. Total Paid parsed from
    uploaded text via pandas' pyarrow dtype backend). Arithmetic between the
    two backends raises TypeError, so every column used in arithmetic below
    is normalized to the same backend first.
    """
    return pd.to_numeric(series, errors="coerce").astype("float64")


def _agg(grouped, col):
    if col in grouped.obj.columns:
        return grouped[col].sum()
    return pd.Series(float("nan"), index=list(grouped.indices.keys()))


def build_cohorts(df: pd.DataFrame):
    grouped = df.groupby("Cohort", dropna=False)

    term_days = _num(df["Term (days)"])
    principal = _num(df["Principal Value"])
    weighted_term_num = (term_days * principal).groupby(
        df["Cohort"], dropna=False
    ).sum()
    weighted_term_den = principal.groupby(df["Cohort"], dropna=False).sum()
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
    has_total_due = "Total Due" in df.columns and "Total Paid" in df.columns
    if not matured.empty and "Total Paid" in matured.columns:
        mat_grouped = matured.groupby("Cohort", dropna=False)
        cohorts["Matured Count"] = mat_grouped["Loan ID"].count().reindex(
            cohorts.index
        ).fillna(0)

        matured_paid = _num(matured["Total Paid"])
        if has_total_due:
            matured_due = _num(matured["Total Due"])
            loss_num = (matured_due - matured_paid).groupby(
                matured["Cohort"], dropna=False
            ).sum()
            loss_den = matured_due.groupby(matured["Cohort"], dropna=False).sum()
        else:
            owed = (
                _num(matured["Principal Value"])
                + _num(matured["Expected Interest"])
                + _num(matured["Expected Fee"])
            )
            loss_num = (owed - matured_paid).groupby(
                matured["Cohort"], dropna=False
            ).sum()
            loss_den = owed.groupby(matured["Cohort"], dropna=False).sum()
        loss_rate = (loss_num / loss_den).where(loss_den != 0).reindex(cohorts.index)
        cohorts["Loss Rate"] = loss_rate
    else:
        cohorts["Matured Count"] = 0
        cohorts["Loss Rate"] = float("nan")

    cohort_paid = _num(cohorts["Total Paid"])
    cohort_due = _num(cohorts["Total Due"])
    cohorts["PvD Ratio"] = (cohort_paid / cohort_due).where(cohort_due > 0)

    return cohorts.reset_index(drop=True)
