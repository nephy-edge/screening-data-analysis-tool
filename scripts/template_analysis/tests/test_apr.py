import numpy_financial as npf
import pandas as pd
import pytest

from template_analysis.apr import compute_loan_rates, principal_weighted_average_rates


def test_owed_ignores_implausible_total_due_and_uses_principal_interest_fee():
    """Regression: a "Total Due" column that's actually a to-date collections
    snapshot (less than Principal Value alone - impossible for a genuine
    lifetime amount owed) used to be preferred over Principal + Interest +
    Fee, forcing numpy_financial.rate() to solve a nonsensical negative APR.
    Real file seen: Total Due averaged less than Principal on 86% of loans
    because it was actually "Total EMI due till date" (a to-date figure),
    not the full lifetime payoff amount."""
    df = pd.DataFrame({
        "Principal Value": [100_000.0],
        "Expected Interest": [80_000.0],
        "Expected Fee": [2_000.0],
        "Term (days)": [365.0],
        # Implausibly low - less than Principal alone, like the real file.
        "Total Due": [40_000.0],
    })
    rates = compute_loan_rates(df)
    assert rates["APR"].iloc[0] > 0


def test_apr_matches_hand_computed_rate_for_a_simple_flat_loan():
    """Principal 1,000, owed 1,100 (10% flat interest, no fee), paid back in
    12 equal monthly installments over a 365-day term - verifies the solve
    against numpy_financial.rate() called directly on the same inputs,
    rather than re-deriving the formula."""
    df = pd.DataFrame({
        "Principal Value": [1_000.0],
        "Expected Interest": [100.0],
        "Expected Fee": [0.0],
        "Term (days)": [365.0],
    })
    rates = compute_loan_rates(df)
    expected_monthly_rate = npf.rate(nper=12, pmt=1_100.0 / 12, pv=-1_000.0, fv=0)
    assert rates["APR"].iloc[0] == pytest.approx(expected_monthly_rate * 12)
    assert rates["EAR"].iloc[0] == pytest.approx((1 + expected_monthly_rate) ** 12 - 1)


def test_real_payment_per_period_overrides_synthetic_estimate():
    """When a real installment and frequency are given, nper is derived from
    owed/payment (the actual contracted schedule), not from Term(days) - a
    different, wrong nper if the two disagree."""
    df = pd.DataFrame({
        "Principal Value": [1_000.0],
        "Expected Interest": [100.0],
        "Expected Fee": [0.0],
        "Term (days)": [3650.0],  # would imply 120 monthly periods if used
        "Payment per Period": [110.0],  # owed(1100)/110 = 10 real periods
        "Payment Frequency": ["monthly"],
    })
    rates = compute_loan_rates(df)
    expected_rate = npf.rate(nper=10, pmt=110.0, pv=-1_000.0, fv=0)
    assert rates["APR"].iloc[0] == pytest.approx(expected_rate * 12)


def test_loan_with_zero_term_does_not_solve():
    df = pd.DataFrame({
        "Principal Value": [1_000.0],
        "Expected Interest": [100.0],
        "Expected Fee": [0.0],
        "Term (days)": [0.0],
    })
    rates = compute_loan_rates(df)
    assert pd.isna(rates["APR"].iloc[0])
    assert rates.attrs["n_valid_inputs"] == 0


def test_principal_weighted_average_rates_reports_coverage():
    df = pd.DataFrame({
        "Principal Value": [1_000.0, 2_000.0, 500.0],
        "Expected Interest": [100.0, 200.0, 0.0],
        "Expected Fee": [0.0, 0.0, 0.0],
        "Term (days)": [365.0, 365.0, 0.0],  # third loan can't solve (zero term)
    })
    result = principal_weighted_average_rates(df)
    assert result["n_total"] == 3
    assert result["n_solved"] == 2
    assert result["n_valid_inputs"] == 2
    assert result["APR"] > 0
    assert "has_total_due" not in result
