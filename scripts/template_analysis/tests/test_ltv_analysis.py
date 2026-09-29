import math

import pandas as pd
import pytest

from template_analysis.ltv_analysis import LtvAnalysis


def _ltv():
    cohorts = pd.DataFrame(
        {
            "Cohort": pd.to_datetime(
                ["2022-12-01", "2023-05-01", "2024-01-01", "2025-11-01", "2026-01-01"]
            ),
            "Loss Rate": [0.56, 0.10, 0.20, 0.30, 0.05],
        }
    )
    return LtvAnalysis(pd.DataFrame(), cohorts)


def test_percentile_defaults_to_every_cohort():
    all_rates = pd.Series([0.56, 0.10, 0.20, 0.30, 0.05])
    assert _ltv().percentile_95_losses() == pytest.approx(all_rates.quantile(0.95))


def test_percentile_cohort_range_is_inclusive_by_month():
    # Drops the 2022-12 launch cohort and 2026-01; both 2023-05 and 2025-11 stay.
    expected = pd.Series([0.10, 0.20, 0.30]).quantile(0.95)
    assert _ltv().percentile_95_losses("2023-05", "2025-11") == pytest.approx(expected)


def test_percentile_empty_range_is_nan():
    assert math.isnan(_ltv().percentile_95_losses("2030-01", "2030-12"))


def _loans():
    # Cohort A: 2 loans, one finished (paid 60 of 100 owed) and one still
    # repaying (paid 20 of 100). Cohort B: a single loan, below min_loans=2.
    return pd.DataFrame(
        {
            "Cohort": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-02-01"]),
            "Principal Value": [80.0, 80.0, 80.0],
            "Expected Interest": [15.0, 15.0, 15.0],
            "Expected Fee": [5.0, 5.0, 5.0],
            "Total Paid": [60.0, 20.0, 0.0],
        }
    )


def test_all_loans_losses_count_every_loan_and_respect_min_loans():
    out = LtvAnalysis(_loans(), pd.DataFrame()).all_loans_cohort_losses(min_loans=2)
    assert list(out["Cohort"]) == [pd.Timestamp("2024-01-01")]
    assert out["Loss Rate"].iloc[0] == pytest.approx(1 - 80 / 200)


def test_all_loans_percentile_applies_the_cohort_range():
    ltv = LtvAnalysis(_loans(), pd.DataFrame())
    assert ltv.percentile_95_losses_all_loans(2) == pytest.approx(0.6)
    assert math.isnan(ltv.percentile_95_losses_all_loans(2, cohort_from="2024-02"))
