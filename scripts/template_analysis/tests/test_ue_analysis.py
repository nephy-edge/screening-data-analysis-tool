import math

import pandas as pd
import pytest

from template_analysis.ue_analysis import UeAnalysis


def _df():
    return pd.DataFrame(
        {
            "Cohort": pd.to_datetime(["2023-04-01", "2023-05-01", "2025-11-01", "2026-03-01"]),
            "Principal Value": [100.0, 100.0, 100.0, 1000.0],
            "Expected Interest": [10.0, 50.0, 70.0, 5.0],
        }
    )


def test_avg_interest_pct_defaults_to_whole_book():
    assert UeAnalysis(_df()).avg_interest_pct() == pytest.approx(135 / 1300)


def test_avg_interest_pct_cohort_range_is_inclusive_by_month():
    # "YYYY-MM" strings, as the web's month pickers send them.
    ue = UeAnalysis(_df())
    assert ue.avg_interest_pct("2023-05", "2025-11") == pytest.approx(120 / 200)


def test_avg_interest_pct_open_ended_range():
    ue = UeAnalysis(_df())
    assert ue.avg_interest_pct(cohort_from="2025-11") == pytest.approx(75 / 1100)
    assert ue.avg_interest_pct(cohort_to="2023-05") == pytest.approx(60 / 200)


def test_avg_interest_pct_empty_range_is_nan():
    assert math.isnan(UeAnalysis(_df()).avg_interest_pct("2030-01", "2030-12"))
