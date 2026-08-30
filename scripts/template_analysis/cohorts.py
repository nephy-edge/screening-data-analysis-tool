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


def build_cohorts(df: pd.DataFrame, min_matured: int | None = None, matured_only: bool = True):
    """Mirrors the Excel template's "Cohorts" pivot table, which has a page
    filter restricting it to Reached T+3? = TRUE - every aggregate below
    (not just Loss Rate) is therefore computed over matured loans only by
    default, and a cohort with zero matured loans has no row at all, exactly
    as the Excel pivot would show none.

    Pass matured_only=False to instead aggregate every column over ALL loans
    (matured or not) per cohort - useful for seeing true origination volume
    rather than the Excel-faithful matured-lagged view. Loss Rate is always
    computed from matured loans only regardless, since it's not meaningful
    any other way."""
    matured = df[df["Reached T+3?"] == True]
    population = matured if matured_only else df
    grouped = population.groupby("Cohort", dropna=False)

    term_days = _num(population["Term (days)"])
    principal = _num(population["Principal Value"])
    weighted_term_num = (term_days * principal).groupby(
        population["Cohort"], dropna=False
    ).sum()
    weighted_term_den = principal.groupby(population["Cohort"], dropna=False).sum()
    weighted_avg_term = (weighted_term_num / weighted_term_den).where(
        weighted_term_den != 0
    ).reindex(grouped["Cohort"].first().index)

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

    if matured_only:
        cohorts["Matured Count"] = cohorts["Loan Count"]
    else:
        mat_grouped = matured.groupby("Cohort", dropna=False)
        cohorts["Matured Count"] = mat_grouped["Loan ID"].count().reindex(
            cohorts.index
        ).fillna(0)

    if not matured.empty and "Total Paid" in matured.columns:
        # Sum each column per cohort independently (via groupby().sum(), which
        # skips NaN within a group) before combining them - NOT row-wise
        # addition/subtraction followed by a groupby sum. A column that's
        # entirely blank for every loan (e.g. no Expected Fee provided at all)
        # makes every row's row-wise combination NaN, and groupby().sum() of
        # an all-NaN group silently returns 0 rather than NaN - collapsing
        # loss_den to 0 and blanking out every cohort's Loss Rate. Summing
        # per-column first treats a missing column as contributing 0, exactly
        # like Excel's own SUMIF-per-column formula and ue_analysis.py's
        # avg_loss(), which never hit this failure mode.
        #
        # Always uses Principal+Interest+Fee, never Total Due, even when a
        # Total Due column is present: verified against every deal's actual
        # Excel Cohorts-pivot Loss formula this session (R2, Qardas, Hypefast,
        # UangCermat, Teclogi, BRKZ, Discovery, BL Financing) - none of them
        # base the Cohorts-level Loss on Total Due, even the ones that have
        # the column. A prior version of this function switched to Total Due
        # whenever present, which both diverged from Excel's actual formula
        # and silently zeroed every cohort's Loss Rate on files where Total
        # Due exists as a column but is left blank.
        paid_sum = _num(matured["Total Paid"]).groupby(matured["Cohort"], dropna=False).sum()
        loss_den = (
            _num(matured["Principal Value"]).groupby(matured["Cohort"], dropna=False).sum()
            + _num(matured["Expected Interest"]).groupby(matured["Cohort"], dropna=False).sum()
            + _num(matured["Expected Fee"]).groupby(matured["Cohort"], dropna=False).sum()
        )
        loss_num = loss_den - paid_sum
        loss_rate = (loss_num / loss_den).where(loss_den != 0).reindex(cohorts.index)
        if min_matured is not None:
            loss_rate = loss_rate.where(cohorts["Matured Count"] >= min_matured)
        cohorts["Loss Rate"] = loss_rate
    else:
        cohorts["Loss Rate"] = float("nan")

    cohort_paid = _num(cohorts["Total Paid"])
    cohort_due = _num(cohorts["Total Due"])
    cohorts["PvD Ratio"] = (cohort_paid / cohort_due).where(cohort_due > 0)

    return cohorts.reset_index(drop=True)
