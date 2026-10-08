"""The Revolving Lending template's Outputs tab: the monthly fact block
(A:K) and the UE / LTV ratio block (A8:C24), recomputed at the as-of month."""

import pandas as pd

from .monthly_input import REQUIRED_COLUMNS

FACT_COLUMNS = REQUIRED_COLUMNS


def fact_block(monthly: pd.DataFrame) -> pd.DataFrame:
    """Outputs A:K - the mapped monthly columns, month as a date string."""
    out = monthly[FACT_COLUMNS].copy()
    out["report_month"] = out["report_month"].dt.strftime("%Y-%m-%d")
    return out.reset_index(drop=True)


def _div(num, den):
    if num is None or den is None or pd.isna(num) or pd.isna(den) or den == 0:
        return None
    return float(num) / float(den)


def ratio_block(
    facts: pd.DataFrame,
    window_months: int,
    loss_rate: float | None,
    stress_loss_rate: float | None,
    total_ecl: float | None,
) -> list[dict]:
    """Outputs B9:B24 at the last row of `facts` (the as-of month). The
    template's formulas over rows 2:3 are generalised to a window of
    `window_months` ending at the as-of month; its 2 rows are window 2.
    Stock ratios use the as-of month and keep the template's own
    denominators: PAR60 is J / H (vs active) while PAR90 is J / G (vs total)."""
    if window_months < 1:
        raise ValueError("Ratio window must be at least 1 month.")
    win = facts.iloc[-window_months:]
    now = facts.iloc[-1]
    avg_out = win["principal_outstanding"].mean()
    # B9 = 1/((SUM(E3+D3)-F3)/AVERAGE(G2:G3)): as-of month's net flow over average outstanding.
    turnover = _div(
        now["principal_collected"] + now["interest_collected"] - now["principal_issued"], avg_out
    )
    tenor = 1 / turnover if turnover else None
    rate_m = _div(win["interest_collected"].sum(), win["principal_outstanding"].sum())
    g, h = now["principal_outstanding"], now["principal_outstanding-active"]
    j = now["principal_outstanding-par60-90"]
    rows = [
        (
            "Expected Tenor (m)",
            tenor,
            "UE / LTV",
            "One divided by the as-of month's collections plus interest, less new drawings, "
            "as a share of average outstanding over the window",
        ),
        (
            "Interest Rate (m)",
            rate_m,
            "UE",
            "Interest collected over the window divided by principal outstanding over the window",
        ),
        (
            "Interest Rate (overall)",
            rate_m * tenor if rate_m is not None and tenor is not None else None,
            "LTV Product Interest Rate",
            "The monthly interest rate multiplied by the expected tenor",
        ),
        (
            "Loss Rate",
            loss_rate,
            "UE ",
            "Average of the monthly point-in-time loss rates in the sample",
        ),
        (
            "95th %ile",
            stress_loss_rate,
            "LTV",
            "The monthly point-in-time loss rate at the stress percentile",
        ),
        (
            "Active",
            _div(h, g),
            "today & overtime",
            "Active balance divided by principal outstanding",
        ),
        (
            "PAR30",
            _div(now["principal_outstanding-par30-60"], g),
            "today & overtime",
            "Balance 1-60 days past due divided by principal outstanding",
        ),
        (
            "PAR60",
            _div(j, h),
            "today & overtime",
            "Balance 61-90 days past due divided by the active balance",
        ),
        (
            "PAR90",
            _div(j, g),
            "today & overtime",
            "Balance 61-90 days past due divided by principal outstanding",
        ),
        (
            "Write offs",
            _div(now["principal_written-off"], avg_out),
            None,
            "Principal written off in the month divided by average outstanding over the window",
        ),
        (
            "ECL",
            total_ecl,
            None,
            "Total expected credit loss at the as-of month (Transition Matrices)",
        ),
        ("Loss Rate", _div(total_ecl, g), None, "Total ECL divided by principal outstanding"),
        ("Outstanding", float(g), None, "Principal outstanding at the as-of month"),
        (
            "number of customers",
            _num(now["number_of_customers"]),
            None,
            "Number of customers at the as-of month",
        ),
        (
            "% of credit line utilized",
            _div(g, now["total_credit_line"]),
            "today & overtime",
            "Principal outstanding divided by the total of approved credit lines",
        ),
    ]
    return [
        {"row": row, "label": label, "value": value, "annotation": note, "formula": formula}
        for row, (label, value, note, formula) in zip(RATIO_ROWS, rows, strict=True)
    ]


# Template rows of the ratio block (A14 is blank in the template).
RATIO_ROWS = [9, 10, 11, 12, 13, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24]


def _num(v):
    return None if pd.isna(v) else float(v)
