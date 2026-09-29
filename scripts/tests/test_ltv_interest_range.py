"""The General Inputs LTV cohort range limits only the LTV Calculator's
interest and 95th %ile loss inputs - every other figure still covers the
whole book."""

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
    assert out["ltvCohortRange"] == {"from": "2023-05", "to": None}
    # Open-ended from 2023-05 covers every loan in _book().
    assert out["ltvInterest"] == pytest.approx(120 / 2200)


def _matured_book():
    # Two fully matured cohorts of 10 loans each: a bad launch month (50% loss)
    # and a later one (10% loss).
    n = 10
    return pd.DataFrame(
        {
            "Loan ID": [f"L{i}" for i in range(2 * n)],
            "Disbursement Date": pd.to_datetime(["2022-12-15"] * n + ["2024-01-15"] * n),
            "Expected Completion Date": pd.to_datetime(["2023-06-15"] * n + ["2024-07-15"] * n),
            "Principal Value": [100.0] * (2 * n),
            "Expected Interest": [0.0] * (2 * n),
            "Expected Fee": [0.0] * (2 * n),
            "Total Paid": [50.0] * n + [90.0] * n,
        }
    )


def test_range_limits_ltv_loss_only():
    df = _matured_book()
    whole = m._compute_lending(df, m.GeneralInputsBody(extractionDate="2026-08-31"))
    ranged = m._compute_lending(
        df, m.GeneralInputsBody(extractionDate="2026-08-31", ltvCohortFrom="2023-05")
    )
    assert whole["ltv_loss"] == pytest.approx(pd.Series([0.5, 0.1]).quantile(0.95))
    assert ranged["ltv_loss"] == pytest.approx(0.1)
    # The engine's own whole-book percentile (Summary/UE) is untouched.
    assert ranged["ltv"].as_dict()["95th Percentile Losses"] == pytest.approx(whole["ltv_loss"])
    out = m._run_lending_engine(df, ranged)
    assert out["ltvLoss"] == pytest.approx(0.1)


def test_response_carries_the_all_loans_loss():
    # _book(): 2023-05 loans paid 290 of 310 owed; 2026-03 loans paid 100 of
    # 2010 and are unfinished. With min 2 loans, both cohorts count.
    df = _book()
    c = m._compute_lending(
        df, m.GeneralInputsBody(extractionDate="2026-08-31", minLoansPerCohort=2)
    )
    out = m._run_lending_engine(df, c)
    losses = sorted(r["Loss Rate"] for r in out["cohortLossesAllLoans"])
    assert losses == pytest.approx(sorted([1 - 290 / 310, 1 - 100 / 2010]))
    assert out["ltvLossAllLoans"] == pytest.approx(pd.Series(losses).quantile(0.95))
