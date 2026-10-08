"""The Performance tab: one row per figure, one column per month, plus the
term and loss rate the LTV Calculator and Unit Economics tabs use.

    net collection ratio = (principal + interest collected) / last month's outstanding
    estimated term       = 1 / net collection ratio
    estimated loss rate  = (cumulative write-off + ECL) / cumulative principal issued

ECL is the performing-book ECL from the point-in-time loss-rate build (each
month's roll rates observable by that month) multiplied by LGD."""

import numpy as np
import pandas as pd

from .transition_matrices import month_labels

# Rows are (key, label, kind, group); kind: money | pct | months | count.
# Group "breakdown" rows can be shown as value or as % of Total Balance.
_COARSE_BUCKETS = [
    ("Active (New + Current)", ["principal_outstanding-active"]),
    ("PAR 1-60", ["principal_outstanding-par30-60"]),
    ("PAR 61-90", ["principal_outstanding-par60-90"]),
]
_FULL_BUCKETS = [
    ("Current", ["BAL_CURRENT"]),
    ("1-30 days", ["BAL_PAR_1_30"]),
    ("31-60 days", ["BAL_PAR_31_60"]),
    ("61-90 days", ["BAL_PAR_61_90"]),
    ("91-120 days", ["BAL_PAR_91_120"]),
    ("121-180+ days", ["BAL_PAR_121_150", "BAL_PAR_151_180", "BAL_PAR_180_PLUS"]),
    ("Defaulted stock", ["BAL_DEFAULT"]),
]
LTV_STRESS_PERCENTILE = 95


def _div(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where((den != 0) & ~np.isnan(den), num / den, np.nan)


def performance(monthly: pd.DataFrame, pit: pd.DataFrame, lgd: float, full: bool) -> dict:
    col = lambda c: monthly[c].to_numpy(dtype=float)  # noqa: E731
    total = col("principal_outstanding")
    customers = col("number_of_customers")
    issued = col("principal_issued")
    written_off = col("principal_written-off")
    prev_total = np.concatenate([[np.nan], total[:-1]])
    ncr = _div(col("principal_collected") + col("interest_collected"), prev_total)
    term = _div(np.ones_like(ncr), ncr)
    ecl = pit["ECL on performing book (before LGD)"].to_numpy(dtype=float) * lgd
    cum_wo = np.cumsum(written_off)
    loss_rate = _div(cum_wo + ecl, np.cumsum(issued))

    rows: list[tuple[str, str, str, str, np.ndarray]] = [
        (
            "new_balance",
            "New Balance",
            "money",
            "balance",
            col("BAL_NEW") if full else np.full(len(total), np.nan),
        ),
        ("total_balance", "Total Balance", "money", "balance", total),
    ]
    buckets = _FULL_BUCKETS if full else _COARSE_BUCKETS
    for label, cols in buckets:
        rows.append(
            (
                f"bucket:{label}",
                label,
                "money",
                "breakdown",
                monthly[cols].sum(axis=1).to_numpy(dtype=float),
            )
        )
    if not full:
        rest = total - sum(r[4] for r in rows if r[3] == "breakdown")
        rows.append(
            (
                "bucket:91+ and defaulted stock",
                "91+ and defaulted stock",
                "money",
                "breakdown",
                rest,
            )
        )
    rows += [
        ("ncr", "Net Collection Ratio", "pct", "metric", ncr),
        ("term", "Estimated Avg Term (m)", "months", "metric", term),
        ("ecl", "ECL", "money", "metric", ecl),
        ("loss_rate", "Estimated Loss Rate", "pct", "metric", loss_rate),
        (
            "avg_credit_line",
            "Avg Credit Line",
            "money",
            "metric",
            _div(col("total_credit_line"), customers),
        ),
        ("interest_paid", "Total Interest Paid", "money", "metric", col("interest_collected")),
        ("principal_paid", "Total Principal Paid", "money", "metric", col("principal_collected")),
        ("principal_issued", "Total Principal Issued", "money", "metric", issued),
        ("cum_write_off", "Total Write Off", "money", "metric", cum_wo),
        ("write_off", "Write Off in Period", "money", "metric", written_off),
        ("customers", "Number of Customers", "count", "metric", customers),
        (
            "issued_per_customer",
            "Avg Principal Issued per Customer",
            "money",
            "metric",
            _div(issued, customers),
        ),
    ]
    months = month_labels(monthly)
    table = [
        {
            "key": k,
            "Metric": label,
            "kind": kind,
            "group": group,
            **{m: None if np.isnan(v) else v for m, v in zip(months, values.tolist())},
        }
        for k, label, kind, group, values in rows
    ]

    in_sample = pit["In sample"].to_numpy(dtype=bool)
    sample = loss_rate[in_sample & ~np.isnan(loss_rate)]
    terms = term[~np.isnan(term)]
    return {
        "table": table,
        "avgTerm": float(terms.mean()) if terms.size else None,
        "termMonths": int(terms.size),
        "lossRateNow": None if np.isnan(loss_rate[-1]) else float(loss_rate[-1]),
        "lossRate95": float(np.percentile(sample, LTV_STRESS_PERCENTILE)) if sample.size else None,
        "lossRateMonths": int(sample.size),
    }
