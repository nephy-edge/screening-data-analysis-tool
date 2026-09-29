"""The General Inputs LTV cohort range limits only the LTV Calculator's
interest input - every other figure still covers the whole book."""

import sys

import pandas as pd
import pytest

sys.path.insert(0, "scripts")
import web_api.main as m  # noqa: E402


def _book():
    # One old, well-accrued cohort and one brand-new cohort with full
    # principal but almost no interest yet - the Salmon pattern.
    return pd.DataFrame(
        {
            "Loan ID": [f"L{i}" for i in range(4)],
            "Disbursement Date": pd.to_datetime(
                ["2023-05-10", "2023-05-20", "2026-03-05", "2026-03-15"]
            ),
            "Expected Completion Date": pd.to_datetime(
                ["2023-11-10", "2023-11-20", "2026-09-05", "2026-09-15"]
            ),
            "Principal Value": [100.0, 100.0, 1000.0, 1000.0],
            "Expected Interest": [50.0, 60.0, 5.0, 5.0],
            "Expected Fee": [0.0, 0.0, 0.0, 0.0],
            "Total Paid": [140.0, 150.0, 50.0, 50.0],
        }
    )


def test_no_range_means_whole_book_interest():
    c = m._compute_lending(_book(), m.GeneralInputsBody(extractionDate="2026-08-31"))
    assert c["ltv_interest"] == pytest.approx(120 / 2200)
    assert c["ltv_interest"] == pytest.approx(c["ue"].as_dict()["Average Interest %"])


def test_range_limits_ltv_interest_only():
    gi = m.GeneralInputsBody(
        extractionDate="2026-08-31", ltvCohortFrom="2023-05", ltvCohortTo="2025-11"
    )
    c = m._compute_lending(_book(), gi)
    assert c["ltv_interest"] == pytest.approx(110 / 200)
    # The rest of the analysis is unchanged by the range.
    assert c["ue"].as_dict()["Average Interest %"] == pytest.approx(120 / 2200)
    assert len(c["processed"]) == 4


def test_blank_strings_are_treated_as_no_limit():
    gi = m.GeneralInputsBody(extractionDate="2026-08-31", ltvCohortFrom="", ltvCohortTo="")
    c = m._compute_lending(_book(), gi)
    assert c["ltv_interest"] == pytest.approx(120 / 2200)


def test_response_echoes_the_range_it_used():
    gi = m.GeneralInputsBody(extractionDate="2026-08-31", ltvCohortFrom="2023-05", ltvCohortTo="")
    df = _book()
    out = m._run_lending_engine(df, m._compute_lending(df, gi))
    assert out["ltvInterestRange"] == {"from": "2023-05", "to": None}
    # Open-ended from 2023-05 covers every loan in _book().
    assert out["ltvInterest"] == pytest.approx(120 / 2200)
