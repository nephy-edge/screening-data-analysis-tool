import numpy as np
import numpy_financial as npf
import pandas as pd

_PERIODS_PER_YEAR = {"weekly": 52, "biweekly": 26, "monthly": 12}


def _num(series) -> pd.Series:
    """Coerce to a plain float64 Series regardless of source dtype backend.

    Columns can arrive numpy-backed or pyarrow-backed (pandas defaults text-
    parsed columns to pyarrow-backed dtypes). numpy_financial.rate()'s Newton
    solve expects native floats - handed a pyarrow-backed scalar instead, it
    can silently fail to converge for every row, not just bad ones. Mirrors
    cohorts.py's _num() so this module is safe against the same issue.
    """
    return pd.to_numeric(series, errors="coerce").astype("float64")


def compute_loan_rates(df: pd.DataFrame) -> pd.DataFrame:
    """Per-loan implied APR (nominal) and EAR (compounded), solving the standard
    amortization equation for the periodic rate that turns Principal Value into
    the full amount owed (Principal + Interest + Fee) via a series of
    installments.

    Uses the loan's real installment (Payment per Period, at the cadence given
    by Payment Frequency) when available, deriving how many installments that
    implies from the amount owed - this is the actual contracted payment, and
    matches what the loan really schedules. Only falls back to a synthetic
    flat installment (owed / (Term(days) / period length)) when no real
    installment is given, since that's an assumption, not a fact from the data.

    A loan whose rate can't be solved (bad/zero term, non-positive payment, no
    convergence) gets NaN rather than a silently wrong value, so it drops out
    of any downstream average rather than distorting it.
    """
    principal = _num(df["Principal Value"])
    term_days = _num(df["Term (days)"])

    # Deliberately never uses "Total Due" even when mapped, only Principal +
    # Interest + Fee - mirrors cohorts.py's build_cohorts(), which made the
    # same call for the same reason. "Total Due" fields are frequently a
    # to-date collections snapshot (e.g. a column literally named "Total EMI
    # due till date", paired with "Total Collection till date"/Total Paid for
    # a Paid-vs-Due ratio) rather than the full lifetime payoff amount this
    # amortization solve needs. Confirmed on a real file: Total Due averaged
    # LESS than Principal Value alone (before adding any interest/fees) on
    # 86% of loans - mathematically impossible for a genuine total-owed
    # figure - which forced numpy_financial.rate() to solve deeply negative
    # implied rates instead of raising or flagging the mismatch.
    interest = _num(df["Expected Interest"]).fillna(0) if "Expected Interest" in df.columns else 0.0
    fee = _num(df["Expected Fee"]).fillna(0) if "Expected Fee" in df.columns else 0.0
    owed = principal + interest + fee

    if "Payment Frequency" in df.columns:
        periods_per_year = (
            df["Payment Frequency"]
            .astype(str)
            .str.lower()
            .map(_PERIODS_PER_YEAR)
            .fillna(12)
            .astype("float64")
        )
    else:
        periods_per_year = pd.Series(12.0, index=df.index, dtype="float64")
    period_days = 365 / periods_per_year

    if "Payment per Period" in df.columns:
        real_pmt = _num(df["Payment per Period"])
    else:
        real_pmt = pd.Series(np.nan, index=df.index, dtype="float64")

    has_real_pmt = real_pmt.notna() & (real_pmt > 0)
    pmt = real_pmt.where(has_real_pmt, owed / (term_days / period_days).replace(0, np.nan))
    nper = pd.Series(np.nan, index=df.index, dtype="float64")
    nper.loc[has_real_pmt] = owed[has_real_pmt] / real_pmt[has_real_pmt]
    nper.loc[~has_real_pmt] = term_days[~has_real_pmt] / period_days[~has_real_pmt]

    valid = (
        nper.notna() & (nper > 0) & pmt.notna() & (pmt > 0) & principal.notna() & (principal > 0)
    )

    # Solved one loan at a time: numpy_financial.rate() vectorizes its Newton solve
    # with a single shared convergence check (np.all(diff < tol)) across the whole
    # input array, so if even one loan in a batch fails to converge, it returns NaN
    # for every loan in that batch - not just the offending one. Looping isolates
    # each loan's convergence from the others.
    rate_period = pd.Series(np.nan, index=df.index, dtype="float64")
    for idx in df.index[valid]:
        # float(...) forces a native Python float regardless of the Series'
        # backing dtype - numpy_financial's Newton solve needs that, not a
        # pandas/pyarrow-backed scalar (see _num() above).
        rate_period.loc[idx] = npf.rate(
            nper=float(nper.loc[idx]),
            pmt=float(pmt.loc[idx]),
            pv=-float(principal.loc[idx]),
            fv=0,
        )

    rates = pd.DataFrame(
        {
            "Principal Value": principal,
            "APR": rate_period * periods_per_year,
            "EAR": (1 + rate_period) ** periods_per_year - 1,
        }
    )
    # Diagnostics: distinguish "inputs were unusable before we even tried to
    # solve" from "inputs looked fine but the solver didn't converge" - these
    # point to very different root causes.
    rates.attrs["n_valid_inputs"] = int(valid.sum())
    rates.attrs["n_converged"] = int(rate_period.notna().sum())
    return rates


def principal_weighted_average_rates(df: pd.DataFrame) -> dict:
    """Principal-weighted average APR/EAR across the given loans (per-loan rates
    averaged, not solved once on aggregated totals - avoids the bias a nonlinear
    solve like npf.rate introduces when applied to already-summed inputs.

    Also reports coverage, so a blank result is diagnosable without re-deriving
    it by hand: how many loans solved, and which owed/pmt basis was used.
    """
    rates = compute_loan_rates(df)
    weights = rates["Principal Value"].where(rates["APR"].notna())
    total_weight = weights.sum()
    n_total = len(df)
    n_solved = int(rates["APR"].notna().sum())
    n_real_pmt = int(
        (_num(df["Payment per Period"]) > 0).sum() if "Payment per Period" in df.columns else 0
    )
    if not total_weight:
        apr, ear = float("nan"), float("nan")
    else:
        apr = (rates["APR"] * weights).sum() / total_weight
        ear = (rates["EAR"] * weights).sum() / total_weight
    return {
        "APR": apr,
        "EAR": ear,
        "n_total": n_total,
        "n_solved": n_solved,
        "n_real_pmt": n_real_pmt,
        "n_valid_inputs": rates.attrs.get("n_valid_inputs"),
        "n_converged": rates.attrs.get("n_converged"),
    }
