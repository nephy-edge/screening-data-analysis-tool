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
        ("Expected Tenor (m)", tenor, "UE / LTV", "1 / ((E + D − F) at as-of ÷ average G)"),
        ("Interest Rate (m)", rate_m, "UE", "Σ D ÷ Σ G over the window"),
        (
            "Interest Rate (overall)",
            rate_m * tenor if rate_m is not None and tenor is not None else None,
            "LTV Product Interest Rate",
            "Interest Rate (m) × Expected Tenor (m)",
        ),
        ("Loss Rate", loss_rate, "UE ", "Point-in-time roll-rate loss rate, mean"),
        (
            "95th %ile",
            stress_loss_rate,
            "LTV",
            "Point-in-time roll-rate loss rate, stress percentile",
        ),
        ("Active", _div(h, g), "today & overtime", "H ÷ G"),
        ("PAR30", _div(now["principal_outstanding-par30-60"], g), "today & overtime", "I ÷ G"),
        ("PAR60", _div(j, h), "today & overtime", "J ÷ H"),
        ("PAR90", _div(j, g), "today & overtime", "J ÷ G"),
        ("Write offs", _div(now["principal_written-off"], avg_out), None, "K ÷ average G"),
        ("ECL", total_ecl, None, "Total ECL at as-of (Transition Matrices)"),
        ("Loss Rate", _div(total_ecl, g), None, "ECL ÷ G"),
        ("Outstanding", float(g), None, "G at as-of"),
        ("number of customers", _num(now["number_of_customers"]), None, "B at as-of"),
        (
            "% of credit line utilized",
            _div(g, now["total_credit_line"]),
            "today & overtime",
            "G ÷ C",
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
