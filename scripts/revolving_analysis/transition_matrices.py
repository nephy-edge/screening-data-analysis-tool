"""Roll-rate transition model and ECL - rcl_rollrate_ecl_3.xlsx's 'Roll Rate
ECL' tab. Aggregate bucket-to-bucket method: each rate is a later month's
balance in the later bucket over this month's balance in the earlier one.

Two bucket definitions share the method:
- FULL_SPEC: the workbook's own buckets (needs every CORE_BUCKET_COLUMN).
- COARSE_SPEC: an approximation from the Outputs columns alone - active,
  PAR 1-60, PAR 61-90, then write-off two months on (no 91-120 bucket). The
  1-60 bucket hides the 1-30 -> 31-60 roll, so it reads lower than FULL_SPEC."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .monthly_input import BUCKET_COLUMNS, DATA_QUALITY_COLUMNS, MONTH

WRITTEN_OFF = "principal_written-off"
OUTSTANDING = "principal_outstanding"
REMAINDER = "_remainder_91_plus"  # coarse: G - H - I - J (91+ and defaulted stock)


@dataclass(frozen=True)
class Spec:
    method: str
    # (row label, column) for the balances table.
    balance_rows: list
    # (transition, denominator label, from-columns at t, numerator column at t+lag, lag)
    edges: list
    # (column, chain it starts) for the ECL rows; the chain is an edge's denominator label.
    ecl_buckets: list
    # (row label, column) held at loss factor 1.
    absorbed: tuple


FULL_SPEC = Spec(
    method="full",
    balance_rows=[
        ("New (origination month)", "BAL_NEW"),
        ("Current", "BAL_CURRENT"),
        ("PAR 1-30", "BAL_PAR_1_30"),
        ("PAR 31-60", "BAL_PAR_31_60"),
        ("PAR 61-90", "BAL_PAR_61_90"),
        ("PAR 91-120", "BAL_PAR_91_120"),
        ("PAR 121-150", "BAL_PAR_121_150"),
        ("PAR 151-180", "BAL_PAR_151_180"),
        ("PAR 180+", "BAL_PAR_180_PLUS"),
        ("Defaulted / written-off stock", "BAL_DEFAULT"),
    ],
    # Exactly 'Roll Rate ECL' rows 29-33; the row-34 memo is marked "not used".
    edges=[
        (
            "Current + New → PAR 1-30",
            "Current + New",
            ["BAL_CURRENT", "BAL_NEW"],
            "BAL_PAR_1_30",
            1,
        ),
        ("PAR 1-30 → PAR 31-60", "PAR 1-30", ["BAL_PAR_1_30"], "BAL_PAR_31_60", 1),
        ("PAR 31-60 → PAR 61-90", "PAR 31-60", ["BAL_PAR_31_60"], "BAL_PAR_61_90", 1),
        ("PAR 61-90 → PAR 91-120", "PAR 61-90", ["BAL_PAR_61_90"], "BAL_PAR_91_120", 1),
        ("PAR 91-120 → Written off", "PAR 91-120", ["BAL_PAR_91_120"], WRITTEN_OFF, 1),
    ],
    # The chain terminates at PAR 91-120 (B8): ECL rows 48-53.
    ecl_buckets=[
        ("BAL_NEW", "Current + New"),
        ("BAL_CURRENT", "Current + New"),
        ("BAL_PAR_1_30", "PAR 1-30"),
        ("BAL_PAR_31_60", "PAR 31-60"),
        ("BAL_PAR_61_90", "PAR 61-90"),
        ("BAL_PAR_91_120", "PAR 91-120"),
    ],
    absorbed=("Defaulted / written-off stock", "BAL_DEFAULT"),
)

COARSE_SPEC = Spec(
    method="coarse",
    balance_rows=[
        ("Active (New + Current)", "principal_outstanding-active"),
        ("PAR 1-60", "principal_outstanding-par30-60"),
        ("PAR 61-90", "principal_outstanding-par60-90"),
        ("91+ and defaulted stock (G − H − I − J)", REMAINDER),
    ],
    edges=[
        (
            "Active → PAR 1-60",
            "Active",
            ["principal_outstanding-active"],
            "principal_outstanding-par30-60",
            1,
        ),
        (
            "PAR 1-60 → PAR 61-90",
            "PAR 1-60",
            ["principal_outstanding-par30-60"],
            "principal_outstanding-par60-90",
            1,
        ),
        (
            "PAR 61-90 → Written off (2 months on)",
            "PAR 61-90",
            ["principal_outstanding-par60-90"],
            WRITTEN_OFF,
            2,
        ),
    ],
    ecl_buckets=[
        ("principal_outstanding-active", "Active"),
        ("principal_outstanding-par30-60", "PAR 1-60"),
        ("principal_outstanding-par60-90", "PAR 61-90"),
    ],
    absorbed=("91+ and defaulted stock (G − H − I − J)", REMAINDER),
)

# Averaging windows ('Roll Rate ECL' AN28:AQ28); None = every observable rate.
WINDOW_LENGTHS: dict[str, int | None] = {"T3": 3, "T6": 6, "T12": 12, "ALL": None}


def with_remainder(monthly: pd.DataFrame) -> pd.DataFrame:
    """Add the coarse spec's derived 91+ / defaulted column."""
    out = monthly.copy()
    out[REMAINDER] = (
        out[OUTSTANDING]
        - out["principal_outstanding-active"]
        - out["principal_outstanding-par30-60"]
        - out["principal_outstanding-par60-90"]
    )
    return out


def month_labels(monthly: pd.DataFrame) -> list[str]:
    return monthly[MONTH].dt.strftime("%Y-%m").tolist()


def roll_rates(monthly: pd.DataFrame, spec: Spec) -> np.ndarray:
    """(edges x months) rates; rate t pairs month t with month t+lag, so the
    last `lag` months have none. A zero denominator gives NaN, the sheet's ""."""
    n = len(monthly)
    out = np.full((len(spec.edges), n), np.nan)
    for i, (_, _, frm, to, lag) in enumerate(spec.edges):
        denom = monthly[frm].sum(axis=1).to_numpy(dtype=float)
        num = monthly[to].to_numpy(dtype=float)
        for t in range(n - lag):
            if denom[t] != 0:
                out[i, t] = num[t + lag] / denom[t]
    return out


def trailing_average(rates: np.ndarray, end: int, length: int | None, lag: int = 1) -> float:
    """Mean of the rates observable at month index `end` - those whose later
    month is at or before `end`, i.e. rate indices up to end - lag - over the
    last `length`. With lag 1 this is 'Loss Rate LTV' row 16's OFFSET and, at
    the last month, 'Roll Rate ECL' T3..ALL. Blanks are skipped as AVERAGE
    skips ""."""
    stop = max(0, end - lag + 1)
    lo = 0 if length is None else max(0, stop - length)
    vals = rates[lo:stop]
    vals = vals[~np.isnan(vals)]
    return float(vals.mean()) if vals.size else np.nan


def selected_links(rates: np.ndarray, spec: Spec, end: int, length: int | None) -> list[float]:
    return [trailing_average(r, end, length, e[4]) for r, e in zip(rates, spec.edges, strict=True)]


def loss_factor_chain(links: list[float], spec: Spec) -> dict[str, float]:
    """Cumulative loss factor per starting bucket ('Roll Rate ECL' rows 39-43):
    the selected links multiplied from that bucket down to write-off."""
    out, lf = {}, 1.0
    for link, edge in zip(reversed(links), reversed(spec.edges), strict=True):
        lf = link * lf
        out[edge[1]] = lf
    return out


def balance_checks(monthly: pd.DataFrame, spec: Spec, tolerance: float) -> pd.DataFrame:
    """Per-month data-quality rows ('Roll Rate ECL' rows 64-68). Full: buckets
    must sum to principal outstanding. Coarse: H + I + J must not exceed it."""
    total = monthly[OUTSTANDING]
    if spec.method == "full":
        gap = (monthly[BUCKET_COLUMNS].sum(axis=1) - total).abs()
        rows = {"Buckets tie to total outstanding": np.where(gap < tolerance, "OK", "CHECK")}
    else:
        rows = {
            "Active + PAR 1-90 within total outstanding": np.where(
                monthly[REMAINDER] > -tolerance, "OK", "CHECK"
            )
        }
    labels = {
        "DUP_LOAN_MONTHS": "Duplicate loan-months in source",
        "MONTHS_FILLED_BY_CARRYFWD": "Loan-months filled by carry-forward",
        "UNEXPLAINED": "Unexplained balance movement",
    }
    for col in DATA_QUALITY_COLUMNS:
        if col in labels and col in monthly.columns:
            rows[labels[col]] = monthly[col].to_numpy()
    if "UNEXPLAINED" in monthly.columns:
        rows["Unexplained as % of outstanding"] = (
            monthly["UNEXPLAINED"] / total.replace(0, np.nan)
        ).to_numpy()
    out = pd.DataFrame(rows, index=month_labels(monthly)).T
    out.index.name = "Check"
    return out


def build_transition_matrices(
    monthly: pd.DataFrame, spec: Spec, window: str, lgd: float, tolerance: float
) -> dict:
    """Everything on the Transition Matrices tab, struck at the last month of
    `monthly` (callers truncate to the as-of month first)."""
    if window not in WINDOW_LENGTHS:
        raise ValueError(f"Roll-rate window must be one of {', '.join(WINDOW_LENGTHS)}")
    labels = month_labels(monthly)
    as_of = len(monthly) - 1

    rows = spec.balance_rows + [
        ("Total principal outstanding", OUTSTANDING),
        ("Principal written off (flow in month)", WRITTEN_OFF),
    ]
    balances = pd.DataFrame(
        [monthly[col].to_numpy() for _, col in rows],
        index=pd.Index([label for label, _ in rows], name="Bucket"),
        columns=labels,
    )

    rates = roll_rates(monthly, spec)
    windows = {w: selected_links(rates, spec, as_of, n) for w, n in WINDOW_LENGTHS.items()}
    rate_table = pd.DataFrame(rates, columns=labels)
    rate_table.insert(0, "Denominator", [e[1] for e in spec.edges])
    for w in WINDOW_LENGTHS:
        rate_table[w] = windows[w]
    rate_table["SELECTED"] = windows[window]
    rate_table.index = pd.Index([e[0] for e in spec.edges], name="Transition")

    factors = loss_factor_chain(windows[window], spec)
    factor_table = pd.DataFrame(
        {"Loss factor": list(factors.values())},
        index=pd.Index(list(factors), name="Starting bucket"),
    )

    checks = balance_checks(monthly, spec, tolerance)
    ecl_valid = checks.iloc[0, -1] == "OK"

    last = monthly.iloc[as_of]
    label_of = {col: label for label, col in spec.balance_rows}
    ecl_rows = [(label_of[col], last[col], factors[chain]) for col, chain in spec.ecl_buckets]
    absorbed_label, absorbed_col = spec.absorbed
    ecl_rows.append((absorbed_label, last[absorbed_col], 1.0))
    ecl = pd.DataFrame(ecl_rows, columns=["Bucket", "Balance at as-of", "Loss factor"])
    ecl["LGD"] = lgd
    ecl["ECL"] = ecl["Balance at as-of"] * ecl["Loss factor"] * lgd
    performing_balance = float(ecl["Balance at as-of"].iloc[:-1].sum())
    performing_ecl = float(ecl["ECL"].iloc[:-1].sum())
    total_balance = performing_balance + float(last[absorbed_col])
    total_ecl = performing_ecl + float(ecl["ECL"].iloc[-1])
    summary = {
        "method": spec.method,
        "asOfMonth": labels[as_of],
        "window": window,
        "lgd": lgd,
        "eclValid": bool(ecl_valid),
        "performingBalance": performing_balance,
        "performingEcl": performing_ecl if ecl_valid else None,
        "totalBalance": total_balance,
        "totalEcl": total_ecl if ecl_valid else None,
        "coveragePerforming": _div(performing_ecl, performing_balance) if ecl_valid else None,
        "coverageTotal": _div(total_ecl, total_balance) if ecl_valid else None,
    }
    if not ecl_valid:
        # Balances and loss factors stay visible; the ECL itself is withheld.
        ecl["ECL"] = np.nan
    return {
        "balances": balances,
        "rates": rates,
        "rollRates": rate_table,
        "lossFactors": factor_table,
        "ecl": ecl,
        "eclSummary": summary,
        "checks": checks,
    }


def _div(num: float, den: float) -> float | None:
    return num / den if den else None
