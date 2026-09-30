"""Standard-definition metrics for the revolving book, evaluated at any month.

One catalogue feeds the Outputs tab's standard-metrics block, the ratios over
time, the early-warning flags and the covenant tests, so each metric has a
single definition. Notation as in the template: B customers, C credit line,
D interest collected, E principal collected, F issued, G outstanding (incl.
defaulted stock), H active, I PAR 1-60, J PAR 61-90, K written off."""

from dataclasses import dataclass

import pandas as pd

from .monthly_input import MONTH

ACTIVE, PAR_1_60, PAR_61_90 = (
    "principal_outstanding-active",
    "principal_outstanding-par30-60",
    "principal_outstanding-par60-90",
)
G, K = "principal_outstanding", "principal_written-off"
PERFORMING_91_PLUS = ["BAL_PAR_91_120", "BAL_PAR_121_150", "BAL_PAR_151_180", "BAL_PAR_180_PLUS"]
PERFORMING_31_PLUS = ["BAL_PAR_31_60", "BAL_PAR_61_90"] + PERFORMING_91_PLUS


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    formula: str
    kind: str  # "pct" | "months" | "money"
    higher_is_worse: bool | None  # None = no direction (not early-warned)
    needs_buckets: bool = False


CATALOGUE = [
    # Template ratios (Outputs B9:B24), over time.
    Metric(
        "tenor", "Expected Tenor (m) - template", "1 / ((E + D − F) ÷ average G)", "months", None
    ),
    Metric("rate_m", "Interest Rate (m)", "Σ D ÷ Σ G over the window", "pct", False),
    Metric("active", "Active - template", "H ÷ G", "pct", False),
    Metric("par30_t", "PAR30 - template (1-60 DPD)", "I ÷ G", "pct", True),
    Metric("par60_t", "PAR60 - template (61-90 ÷ active)", "J ÷ H", "pct", True),
    Metric("par90_t", "PAR90 - template (61-90 DPD)", "J ÷ G", "pct", True),
    Metric("writeoff_m", "Write-off rate (m)", "K ÷ average G", "pct", True),
    Metric("utilisation", "% of credit line utilised", "G ÷ C", "pct", None),
    # Standard definitions from the Outputs columns.
    Metric("par61_total", "PAR 61+ incl. defaulted stock", "(G − H − I) ÷ G", "pct", True),
    Metric("par91_total", "PAR 91+ incl. defaulted stock", "(G − H − I − J) ÷ G", "pct", True),
    Metric("payment_rate", "Principal payment rate (m)", "E ÷ average G", "pct", False),
    Metric(
        "principal_tenor",
        "Tenor from principal turnover (m)",
        "1 ÷ principal payment rate",
        "months",
        None,
    ),
    Metric(
        "writeoff_annual", "Write-off rate (annualised)", "Write-off rate (m) × 12", "pct", True
    ),
    # Standard definitions that need the bucket balances.
    Metric("defaulted_share", "Defaulted stock share", "BAL_DEFAULT ÷ G", "pct", True, True),
    Metric(
        "par31_total",
        "PAR 31+ incl. defaulted stock",
        "(31-60 … 180+ + defaulted) ÷ G",
        "pct",
        True,
        True,
    ),
    Metric(
        "par31_perf",
        "PAR 31+ of performing book",
        "(31-60 … 180+) ÷ (G − defaulted)",
        "pct",
        True,
        True,
    ),
    Metric(
        "par91_perf",
        "PAR 91+ of performing book",
        "(91-120 … 180+) ÷ (G − defaulted)",
        "pct",
        True,
        True,
    ),
    Metric(
        "active_perf", "Active share of performing book", "H ÷ (G − defaulted)", "pct", False, True
    ),
    Metric(
        "yield_perf_m",
        "Interest yield on performing book (m)",
        "Σ D ÷ Σ (G − defaulted) over the window",
        "pct",
        False,
        True,
    ),
    Metric(
        "utilisation_perf",
        "Credit line utilised by performing book",
        "(G − defaulted) ÷ C",
        "pct",
        None,
        True,
    ),
]
BY_KEY = {m.key: m for m in CATALOGUE}


def _div(num, den):
    if num is None or den is None or pd.isna(num) or pd.isna(den) or den == 0:
        return None
    return float(num) / float(den)


def metrics_at(monthly: pd.DataFrame, idx: int, window: int, full_buckets: bool) -> dict:
    """Every catalogue metric at month index `idx`, using months up to it only.
    Windowed averages cover the `window` months ending at `idx`."""
    win = monthly.iloc[max(0, idx - window + 1) : idx + 1]
    now = monthly.iloc[idx]
    avg_g = win[G].mean()
    g, h, i, j = now[G], now[ACTIVE], now[PAR_1_60], now[PAR_61_90]
    turnover = _div(
        now["principal_collected"] + now["interest_collected"] - now["principal_issued"], avg_g
    )
    payment = _div(now["principal_collected"], avg_g)
    writeoff = _div(now[K], avg_g)
    out = {
        "tenor": 1 / turnover if turnover else None,
        "rate_m": _div(win["interest_collected"].sum(), win[G].sum()),
        "active": _div(h, g),
        "par30_t": _div(i, g),
        "par60_t": _div(j, h),
        "par90_t": _div(j, g),
        "writeoff_m": writeoff,
        "utilisation": _div(g, now["total_credit_line"]),
        "par61_total": _div(g - h - i, g),
        "par91_total": _div(g - h - i - j, g),
        "payment_rate": payment,
        "principal_tenor": 1 / payment if payment else None,
        "writeoff_annual": writeoff * 12 if writeoff is not None else None,
    }
    if full_buckets:
        default = now["BAL_DEFAULT"]
        perf = g - default
        perf_win = win[G] - win["BAL_DEFAULT"]
        out.update(
            {
                "defaulted_share": _div(default, g),
                "par31_total": _div(now[PERFORMING_31_PLUS].sum() + default, g),
                "par31_perf": _div(now[PERFORMING_31_PLUS].sum(), perf),
                "par91_perf": _div(now[PERFORMING_91_PLUS].sum(), perf),
                "active_perf": _div(h, perf),
                "yield_perf_m": _div(win["interest_collected"].sum(), perf_win.sum()),
                "utilisation_perf": _div(perf, now["total_credit_line"]),
            }
        )
    else:
        out.update({m.key: None for m in CATALOGUE if m.needs_buckets})
    return out


def metrics_over_time(monthly: pd.DataFrame, window: int, full_buckets: bool) -> pd.DataFrame:
    """One row per month (months as columns), every catalogue metric."""
    labels = monthly[MONTH].dt.strftime("%Y-%m").tolist()
    rows = [metrics_at(monthly, t, window, full_buckets) for t in range(len(monthly))]
    return pd.DataFrame(rows, index=labels).T


def standard_metrics(values: dict) -> list[dict]:
    """The Outputs tab's standard-definition block at the as-of month."""
    template_keys = {"tenor", "active", "par30_t", "par60_t", "par90_t", "utilisation"}
    return [
        {
            "key": m.key,
            "label": m.label,
            "value": values.get(m.key),
            "kind": m.kind,
            "formula": m.formula,
            "note": "Map the bucket balances to calculate"
            if m.needs_buckets and values.get(m.key) is None
            else "",
        }
        for m in CATALOGUE
        if m.key not in template_keys
    ]


def unit_economics(values: dict, cost_of_funds: float, opex: float) -> list[dict]:
    """Net yield per dollar outstanding: interest yield − write-offs − costs.
    Costs are annual rates on outstanding; everything is shown monthly and
    annualised, plus over the principal-turnover tenor."""
    rate, wo, tenor = values.get("rate_m"), values.get("writeoff_m"), values.get("principal_tenor")
    cost_m = (cost_of_funds + opex) / 12
    net = rate - wo - cost_m if rate is not None and wo is not None else None
    rows = [
        ("Interest yield", rate),
        ("Write-offs", -wo if wo is not None else None),
        ("Cost of funds", -cost_of_funds / 12),
        ("Operating costs", -opex / 12),
        ("Net yield", net),
    ]
    return [
        {
            "item": item,
            "monthly": v,
            "annualised": v * 12 if v is not None else None,
            "overTenor": v * tenor if v is not None and tenor is not None else None,
        }
        for item, v in rows
    ]


def early_warnings(over_time: pd.DataFrame, lookback: int, threshold: float) -> list[dict]:
    """Metrics at the as-of month that are worse than their average over the
    previous `lookback` months by more than `threshold` (relative)."""
    out = []
    if over_time.shape[1] <= lookback:
        return out
    for m in CATALOGUE:
        if m.higher_is_worse is None or m.key not in over_time.index:
            continue
        series = pd.to_numeric(over_time.loc[m.key], errors="coerce")
        now, prior = series.iloc[-1], series.iloc[-lookback - 1 : -1].mean()
        if pd.isna(now) or pd.isna(prior) or prior == 0:
            continue
        change = (now - prior) / abs(prior)
        worse = change > 0 if m.higher_is_worse else change < 0
        out.append(
            {
                "key": m.key,
                "label": m.label,
                "kind": m.kind,
                "now": float(now),
                "prior": float(prior),
                "change": float(change),
                "flag": bool(worse and abs(change) > threshold),
            }
        )
    return out
