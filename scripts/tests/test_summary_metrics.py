"""summary_metrics.py holds the Summary tab figures that app.py's Streamlit
tab and the React app's Summary screen both read. This pins the arithmetic
(Outstanding GBV's maturity/status filter, PvD, the principal-weighted term
that must exclude rows missing either input, and the APR proxy built on it)
against a small hand-checkable tape, so a change to either caller can't
silently move a number an analyst reads off the page.
"""

import pandas as pd
from summary_metrics import lending_summary_metrics


class _Gi:
    extraction_date = pd.Timestamp("2025-12-31")


def _tape() -> pd.DataFrame:
    # 2 matured loans (Reached T+3? True) + 1 still outstanding.
    return pd.DataFrame(
        {
            "Loan ID": ["a", "b", "c"],
            "Disbursement Date": pd.to_datetime(["2025-01-01", "2025-02-01", "2025-06-01"]),
            "Principal Value": [1000.0, 2000.0, 500.0],
            "Expected Interest": [100.0, 200.0, 50.0],
            "Expected Fee": [10.0, 20.0, 5.0],
            "Total Paid": [1000.0, 2000.0, 300.0],
            "Total Due": [1110.0, 2220.0, 555.0],
            "Term (days)": [90.0, 180.0, 90.0],
            "Reached T+3?": [True, True, False],
        }
    )


_UE = {
    "Average Loss": 0.05,
    "Average Interest %": 0.10,
    "Average Fee %": 0.01,
    "Average Principal Amount": 1166.67,
}
_LTV = {"95th Percentile Losses": 0.09, "Average Total Revenue %": 0.11}


def test_outstanding_gbv_covers_only_unmatured_loans():
    m = lending_summary_metrics(_tape(), _Gi(), _UE, _LTV)
    # Loan "c" only: 500 + 50 + 5 - 300.
    assert m["outstandingGbv"] == 255.0


def test_outstanding_gbv_narrows_to_active_status_values_when_given():
    df = _tape()
    df["Loan Status"] = ["closed", "closed", "dormant"]
    # "dormant" isn't in the picked active values, so nothing qualifies.
    m = lending_summary_metrics(df, _Gi(), _UE, _LTV, active_status_values=["active"])
    assert m["outstandingGbv"] is None
    # With it picked, loan "c" counts again.
    m2 = lending_summary_metrics(df, _Gi(), _UE, _LTV, active_status_values=["dormant"])
    assert m2["outstandingGbv"] == 255.0


def test_principal_outstanding_is_pro_rata_estimate_for_unmatured_loans():
    m = lending_summary_metrics(_tape(), _Gi(), _UE, _LTV)
    # Loan "c": 500 - 300 x 500 / 555.
    assert abs(m["principalOutstanding"] - (500.0 - 300.0 * 500.0 / 555.0)) < 1e-9
    df = _tape()
    df["Loan Status"] = ["closed", "closed", "dormant"]
    m2 = lending_summary_metrics(df, _Gi(), _UE, _LTV, active_status_values=["active"])
    assert m2["principalOutstanding"] is None


def test_pvd_uses_matured_loans_only():
    m = lending_summary_metrics(_tape(), _Gi(), _UE, _LTV)
    # (1000 + 2000) / (1110 + 2220), loan "c" excluded as unmatured.
    assert m["pvd"] == (3000.0 / 3330.0)


def test_weighted_avg_term_excludes_rows_missing_either_input():
    df = _tape()
    df.loc[1, "Term (days)"] = None  # drops loan "b" from both parts of the ratio
    m = lending_summary_metrics(df, _Gi(), _UE, _LTV)
    # Only loan "a" survives among matured loans: its own term.
    assert m["weightedAvgTermMatured"] == 90.0
    # ...and the APR proxy is built on that same figure.
    assert m["aprProxy"] == (_UE["Average Interest %"] / (90.0 / 30.4375)) * 12


def test_weighted_avg_term_is_principal_weighted():
    m = lending_summary_metrics(_tape(), _Gi(), _UE, _LTV)
    # (90*1000 + 180*2000) / 3000, matured loans only.
    assert m["weightedAvgTermMatured"] == (90 * 1000 + 180 * 2000) / 3000


def test_snapshot_totals_cover_the_whole_tape_not_just_matured():
    m = lending_summary_metrics(_tape(), _Gi(), _UE, _LTV)
    assert m["loans"] == 3
    assert m["principalDisbursed"] == 3500.0
    assert m["totalCollected"] == 3300.0
    assert m["dateRangeStart"] == "2025-01-01"
    assert m["dateRangeEnd"] == "2025-12-31"
