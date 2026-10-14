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
        "tenor",
        "Expected Tenor (m) - template",
        "One divided by collections plus interest, less new drawings, over average outstanding",
        "months",
        None,
    ),
    Metric(
        "rate_m",
        "Interest Rate (m)",
        "Interest collected over the window divided by principal outstanding over the window",
        "pct",
        False,
    ),
    Metric(
        "active",
        "Active - template",
        "Active balance divided by principal outstanding",
        "pct",
        False,
    ),
    Metric(
        "par30_t",
        "PAR30 - template (1-60 DPD)",
        "Balance 1-60 days past due divided by principal outstanding",
        "pct",
        True,
    ),
    Metric(
        "par60_t",
        "PAR60 - template (61-90 as a share of active)",
        "Balance 61-90 days past due divided by the active balance",
        "pct",
        True,
    ),
    Metric(
        "par90_t",
        "PAR90 - template (61-90 DPD)",
        "Balance 61-90 days past due divided by principal outstanding",
        "pct",
        True,
    ),
    Metric(
        "writeoff_m",
        "Write-off rate (m)",
        "Principal written off in the month divided by average outstanding",
        "pct",
        True,
    ),
    Metric(
        "utilisation",
        "% of credit line utilised",
        "Principal outstanding divided by the total of approved credit lines",
        "pct",
        None,
    ),
    # Standard definitions from the Outputs columns.
    Metric(
        "par61_total",
        "PAR 61+ incl. defaulted stock",
        "Balance 61 or more days past due, plus defaulted stock, divided by principal outstanding",
        "pct",
        True,
    ),
    Metric(
        "par91_total",
        "PAR 91+ incl. defaulted stock",
        "Balance 91 or more days past due, plus defaulted stock, divided by principal outstanding",
        "pct",
        True,
    ),
    Metric(
        "payment_rate",
        "Principal payment rate (m)",
        "Principal collected in the month divided by average outstanding",
        "pct",
        False,
    ),
    Metric(
        "principal_tenor",
        "Tenor from principal turnover (m)",
        "One divided by the principal payment rate",
        "months",
        None,
    ),
    Metric(
        "writeoff_annual",
        "Write-off rate (annualised)",
        "The monthly write-off rate multiplied by 12",
        "pct",
        True,
    ),
    # Standard definitions that need the bucket balances.
    Metric(
        "defaulted_share",
        "Defaulted stock share",
        "Defaulted balance divided by principal outstanding",
        "pct",
        True,
        True,
    ),
    Metric(
        "par31_total",
        "PAR 31+ incl. defaulted stock",
        "Balance 31 or more days past due, plus defaulted stock, divided by principal outstanding",
        "pct",
        True,
        True,
    ),
    Metric(
        "par31_perf",
        "PAR 31+ of performing book",
        "Balance 31 or more days past due divided by the performing book (less defaulted)",
        "pct",
        True,
        True,
    ),
    Metric(
        "par91_perf",
        "PAR 91+ of performing book",
        "Balance 91 or more days past due divided by the performing book (less defaulted)",
        "pct",
        True,
        True,
    ),
    Metric(
        "active_perf",
        "Active share of performing book",
        "Active balance divided by the performing book",
        "pct",
        False,
        True,
    ),
    Metric(
        "yield_perf_m",
        "Interest yield on performing book (m)",
        "Interest collected over the window divided by the performing book over the window",
        "pct",
        False,
        True,
    ),
    Metric(
        "utilisation_perf",
        "Credit line utilised by performing book",
        "Performing book divided by the total of approved credit lines",
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


def metrics_at(
    monthly: pd.DataFrame, idx: int, window: int, full_buckets: bool, writeoff_window: int = 1
) -> dict:
    """Every catalogue metric at month index `idx`, using months up to it only.
    Windowed averages cover the `window` months ending at `idx`. With
    `writeoff_window` > 1 the write-off rate is total written off over that
    many months divided by total outstanding over them (a smoothed rate)."""
    win = monthly.iloc[max(0, idx - window + 1) : idx + 1]
    now = monthly.iloc[idx]
    avg_g = win[G].mean()
    g, h, i, j = now[G], now[ACTIVE], now[PAR_1_60], now[PAR_61_90]
    turnover = _div(
        now["principal_collected"] + now["interest_collected"] - now["principal_issued"], avg_g
    )
    payment = _div(now["principal_collected"], avg_g)
    writeoff = _div(now[K], avg_g)
    if writeoff_window > 1:
        wo_win = monthly.iloc[max(0, idx - writeoff_window + 1) : idx + 1]
        writeoff = _div(wo_win[K].sum(), wo_win[G].sum())
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


def metrics_over_time(
    monthly: pd.DataFrame, window: int, full_buckets: bool, writeoff_window: int = 1
) -> pd.DataFrame:
    """One row per month (months as columns), every catalogue metric."""
    labels = monthly[MONTH].dt.strftime("%Y-%m").tolist()
    rows = [
        metrics_at(monthly, t, window, full_buckets, writeoff_window) for t in range(len(monthly))
    ]
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


def unit_economics(
    cost_of_funds: float, opex: float, tenor: float | None, rate: float | None, wo: float | None
) -> list[dict]:
    """Net yield per dollar outstanding: interest yield − write-offs − costs.
    `rate` and `wo` are the trailing monthly interest and write-off rates
    (performance.py, same window); costs are annual rates on outstanding;
    everything is shown monthly and annualised, plus over `tenor` (the
    average of 1 / net collection ratio)."""
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
