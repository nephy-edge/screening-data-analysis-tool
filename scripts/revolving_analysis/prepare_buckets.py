"""Turn a long-format ageing-buckets sheet into the Revolving Lending monthly input.

Source layout (one row per product x Year x Month x ageing bucket), e.g. the
Punto Pago "Buckets" sheet:

    product type | Year | Month | aging summary | Sum of main balance | ...

Output: one row per month with the 11 required template fields plus the
optional BAL_* bucket columns. Fields the source does not carry (customers,
credit lines, interest, collections, drawings, BAL_NEW) are set to 0, and
principal_written-off is a proxy: the month's 121-150 day balance. See
docs/deals/punto_pago_transformation.md for the limitations.

With --flows, the loan-level quarterly snapshot sheet ("por cliente") fills
customers, credit lines, drawings, collections and interest instead:
quarter-on-quarter changes in cumulative drawn / repaid, spread evenly over
the quarter's three months. Months outside the snapshot range keep 0 flows.

Usage:
    python -m scripts.revolving_analysis.prepare_buckets SOURCE.xlsx BCL data/punto_pago_bcl.csv
    python -m scripts.revolving_analysis.prepare_buckets SOURCE.xlsx BCL out.csv --flows
"""

from __future__ import annotations

import argparse
import re

import pandas as pd

# Provider bucket (lower bound of days past due) -> app bucket column.
_GROUPS = {
    "BAL_CURRENT": [0],
    "BAL_PAR_1_30": [1, 16],
    "BAL_PAR_31_60": [31, 46],
    "BAL_PAR_61_90": [61, 76],
    "BAL_PAR_91_120": [91],
    "BAL_DEFAULT": [121, 151, 180],
}
_ZERO = [
    "number_of_customers",
    "total_credit_line",
    "interest_collected",
    "principal_collected",
    "principal_issued",
    "BAL_NEW",
]


def _lower_bound(label: str) -> int:
    """'1 - 15 Days' -> 1, "'+180 Days" -> 180, '0 Days' -> 0."""
    return int(re.search(r"\d+", str(label)).group())


def prepare(source: str, product: str, sheet: str = "Buckets") -> pd.DataFrame:
    raw = pd.read_excel(source, sheet_name=sheet, usecols="A:G")
    raw = raw[raw["product type"] == product]
    if raw.empty:
        raise ValueError(f"No rows for product {product!r} in sheet {sheet!r}")
    month = pd.to_datetime(
        raw["Year"].astype(int).astype(str) + " " + raw["Month"].astype(str), format="%Y %B"
    )
    wide = (
        raw.assign(
            report_month=month,
            dpd=raw["aging summary"].map(_lower_bound),
            bal=-raw["Sum of main balance"],
        )
        .pivot_table(
            index="report_month", columns="dpd", values="bal", aggfunc="sum", fill_value=0.0
        )
        .sort_index()
    )
    out = pd.DataFrame(index=wide.index)
    for col in _ZERO:
        out[col] = 0.0
    for col, bounds in _GROUPS.items():
        out[col] = wide.reindex(columns=bounds, fill_value=0.0).sum(axis=1)
    out["principal_outstanding"] = out[list(_GROUPS)].sum(axis=1)
    out["principal_outstanding-active"] = out["BAL_CURRENT"]
    out["principal_outstanding-par30-60"] = out["BAL_PAR_1_30"] + out["BAL_PAR_31_60"]
    out["principal_outstanding-par60-90"] = out["BAL_PAR_61_90"]
    out["principal_written-off"] = wide.get(121, 0.0)
    return out.reset_index()


# Statuses that are real accounts (not applications) for customers / limits.
_ACCOUNT_STATUSES = {"ISSUED", "OVERDUE", "CLOSED"}


def add_flows(
    out: pd.DataFrame, source: str, product: str, sheet: str = "por cliente"
) -> pd.DataFrame:
    """Fill the zeroed flow fields from quarterly loan-level snapshots.

    Nothing is written off in the source, so principal repaid in a quarter is
    the change in drawn less the change in outstanding; the rest of the change
    in repaid is interest and fees."""
    raw = pd.read_excel(source, sheet_name=sheet)
    raw = raw[raw["product type"] == product]
    q = raw.groupby("cutoff date").agg(
        drawn=("used total", "sum"), repaid=("paid total", "sum"), main=("main balance", "sum")
    )
    q = -q[["drawn", "main"]].join(q["repaid"])  # drawn and balances are stored negative
    q["repaid"] = -q["repaid"]
    acc = raw[raw["loan status"].isin(_ACCOUNT_STATUSES)].groupby("cutoff date")
    q["customers"] = acc["client id"].nunique()
    q["limit"] = acc["loan limit"].sum()
    d = q.diff()
    flows = (
        pd.DataFrame(
            {
                "principal_issued": d["drawn"],
                "principal_collected": d["drawn"] - d["main"],
                "interest_collected": d["repaid"] - (d["drawn"] - d["main"]),
            }
        ).dropna()
        / 3
    )
    out = out.copy()
    quarter_end = out["report_month"].dt.to_period("Q").dt.end_time.dt.normalize()
    for col in flows:
        out[col] = quarter_end.map(flows[col]).fillna(0.0)
    snap = q[["customers", "limit"]].reindex(quarter_end).ffill().bfill().to_numpy()
    out["number_of_customers"], out["total_credit_line"] = snap[:, 0], snap[:, 1]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source")
    ap.add_argument("product")
    ap.add_argument("output")
    ap.add_argument("--sheet", default="Buckets")
    ap.add_argument("--flows", action="store_true", help="fill flows from the snapshot sheet")
    ap.add_argument("--flows-sheet", default="por cliente")
    a = ap.parse_args()
    out = prepare(a.source, a.product, a.sheet)
    if a.flows:
        out = add_flows(out, a.source, a.product, a.flows_sheet)
    out.to_csv(a.output, index=False, date_format="%Y-%m-%d")


if __name__ == "__main__":
    main()
