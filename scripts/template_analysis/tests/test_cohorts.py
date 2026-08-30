import numpy as np
import pandas as pd
import pytest

from template_analysis.cohorts import build_cohorts
from template_analysis.data_input import process_data_input

EXTRACTION_DATE = pd.Timestamp("2024-06-01")


def _make_df(expected_fee=5, total_due=None):
    """5 fully-matured Jan loans, 4 unmatured Feb loans, 3 unmatured May loans -
    the same shape used to verify matured-only filtering throughout this
    project's development."""
    n = 12
    raw = pd.DataFrame({
        "Loan ID": range(1, n + 1),
        "Disbursement Date": (
            [pd.Timestamp("2024-01-05")] * 5
            + [pd.Timestamp("2024-02-10")] * 4
            + [pd.Timestamp("2024-05-20")] * 3
        ),
        "Expected Completion Date": (
            [pd.Timestamp("2024-02-05")] * 5
            + [pd.Timestamp("2024-03-10")] * 2 + [pd.Timestamp("2024-05-10")] * 2
            + [pd.Timestamp("2024-08-20")] * 3
        ),
        "Principal Value": [100] * n,
        "Expected Interest": [10] * n,
        "Expected Fee": [expected_fee] * n if not isinstance(expected_fee, list) else expected_fee,
        "Total Paid": [110] * 5 + [95, 90, 50, 50] + [0, 0, 0],
    })
    if total_due is not None:
        raw["Total Due"] = total_due
    return process_data_input(raw, EXTRACTION_DATE, days_after_term=90)


def test_matured_only_default_hides_cohorts_with_no_matured_loans():
    df = _make_df()
    cohorts = build_cohorts(df, min_matured=1)
    # Only the Jan cohort has any matured loan - Feb/May shouldn't appear at
    # all, matching the Excel template's pivot page filter on Reached T+3?.
    assert list(cohorts["Cohort"]) == [pd.Timestamp("2024-01-01")]
    assert cohorts.loc[0, "Loan Count"] == 5
    assert cohorts.loc[0, "Loss Rate"] == pytest.approx((575 - 550) / 575)


def test_matured_only_false_restores_all_cohorts():
    df = _make_df()
    cohorts = build_cohorts(df, min_matured=1, matured_only=False)
    assert list(cohorts["Cohort"]) == [
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01"), pd.Timestamp("2024-05-01"),
    ]
    feb = cohorts[cohorts["Cohort"] == pd.Timestamp("2024-02-01")].iloc[0]
    assert feb["Loan Count"] == 4
    assert feb["Matured Count"] == 0
    assert pd.isna(feb["Loss Rate"])


def test_min_matured_threshold_nulls_loss_rate_not_the_whole_row():
    df = _make_df()
    cohorts = build_cohorts(df, min_matured=10)
    # The cohort still shows up (it has matured loans), just with Loss Rate
    # suppressed for being under the sample-size threshold.
    assert len(cohorts) == 1
    assert cohorts.loc[0, "Loan Count"] == 5
    assert pd.isna(cohorts.loc[0, "Loss Rate"])


def test_blank_fee_column_does_not_collapse_loss_rate_to_nan():
    """Regression for a real bug: when Expected Fee is entirely blank, the
    row-wise sum P+I+Fee became NaN for every row, and groupby().sum() over
    an all-NaN group silently returns 0 - zeroing the denominator and
    blanking every cohort's Loss Rate. Caught in production against two
    Pakistan EWA/IF deals and an EWA+Payroll deal, all with a fully-blank
    Expected Fee column."""
    df = _make_df(expected_fee=np.nan)
    cohorts = build_cohorts(df, min_matured=1)
    assert len(cohorts) == 1
    owed = 100 * 5 + 10 * 5  # Fee contributes 0, not NaN
    paid = 110 * 5
    assert cohorts.loc[0, "Loss Rate"] == pytest.approx((owed - paid) / owed)


def test_total_due_column_present_but_unused_for_loss_rate():
    """Regression for a real bug: Loss Rate switched to a Total Due column
    whenever one was present, but no deal's actual Excel Cohorts-pivot
    formula ever bases Loss on Total Due - it's always Principal+Interest+
    Fee-Paid. A blank Total Due column used to zero out Loss Rate entirely
    (caught against a real deal); a populated-but-different Total Due column
    would otherwise silently give a different number than Excel's own."""
    df = _make_df(total_due=[999] * 5 + [0] * 4 + [0] * 3)  # would give a wrong answer if used
    cohorts = build_cohorts(df, min_matured=1)
    assert len(cohorts) == 1
    owed = 100 * 5 + 10 * 5 + 5 * 5
    paid = 110 * 5
    assert cohorts.loc[0, "Loss Rate"] == pytest.approx((owed - paid) / owed)

    blank_due_df = _make_df(total_due=[np.nan] * 12)
    blank_due_cohorts = build_cohorts(blank_due_df, min_matured=1)
    assert blank_due_cohorts.loc[0, "Loss Rate"] == pytest.approx((owed - paid) / owed)
