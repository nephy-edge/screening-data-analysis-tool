"""The monthly input the revolving engine runs on: one row per report month.
The required fields are the Revolving Lending template's Outputs columns
(A1:K1); the per-bucket balances of rcl_rollrate_ecl_3.xlsx's 'Monthly
Report' tab are optional and, when all mapped, enable the full roll-rate
model (see transition_matrices.FULL_SPEC)."""

import numpy as np
import pandas as pd

MONTH = "report_month"

# Outputs A1:K1 - header names and order are the template's contract.
REQUIRED_COLUMNS = [
    MONTH,
    "number_of_customers",
    "total_credit_line",
    "interest_collected",
    "principal_collected",
    "principal_issued",
    "principal_outstanding",
    "principal_outstanding-active",
    "principal_outstanding-par30-60",
    "principal_outstanding-par60-90",
    "principal_written-off",
]

# Every one of these must be mapped for the full roll-rate model.
CORE_BUCKET_COLUMNS = [
    "BAL_NEW",
    "BAL_CURRENT",
    "BAL_PAR_1_30",
    "BAL_PAR_31_60",
    "BAL_PAR_61_90",
    "BAL_PAR_91_120",
    "BAL_DEFAULT",
]
# Buckets past the chain's end (121+ DPD), zero-filled when not supplied.
ZERO_FILLED_COLUMNS = ["BAL_PAR_121_150", "BAL_PAR_151_180", "BAL_PAR_180_PLUS"]
# All bucket balances, in the fixed order of 'Roll Rate ECL' rows 13-22.
BUCKET_COLUMNS = CORE_BUCKET_COLUMNS[:6] + ZERO_FILLED_COLUMNS + ["BAL_DEFAULT"]
DATA_QUALITY_COLUMNS = [
    "DUP_LOAN_MONTHS",
    "SOURCE_ROWS",
    "MONTHS_FILLED_BY_CARRYFWD",
    "UNEXPLAINED",
]
OPTIONAL_COLUMNS = CORE_BUCKET_COLUMNS + ZERO_FILLED_COLUMNS + DATA_QUALITY_COLUMNS


def has_full_buckets(df: pd.DataFrame) -> bool:
    return all(c in df.columns for c in CORE_BUCKET_COLUMNS)


def prepare_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalise the monthly rows: months to month-start, every
    other column numeric, sorted ascending. Raises ValueError on a missing
    required column, an unparseable month or a repeated month - roll rates
    pair each month with the next, so one row per month is required."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required revolving fields: {', '.join(missing)}")
    out = df[[c for c in REQUIRED_COLUMNS + OPTIONAL_COLUMNS if c in df.columns]].copy()
    # Rows blank in every mapped field (e.g. a sheet's trailing summary rows,
    # whose values sit in unmapped columns) aren't months.
    out = out.replace(r"^\s*$", np.nan, regex=True).dropna(how="all")
    months = pd.to_datetime(out[MONTH], errors="coerce")
    if months.isna().any():
        raise ValueError(f"{int(months.isna().sum())} row(s) have no readable {MONTH}.")
    out[MONTH] = months.dt.to_period("M").dt.to_timestamp()
    repeated = out[MONTH][out[MONTH].duplicated()].dt.strftime("%Y-%m").unique().tolist()
    if repeated:
        raise ValueError(f"Each month must appear once; repeated: {', '.join(repeated)}")
    for c in out.columns:
        if c != MONTH:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    if has_full_buckets(out):
        for c in ZERO_FILLED_COLUMNS:
            out[c] = out[c].fillna(0.0) if c in out.columns else 0.0
    return out.sort_values(MONTH).reset_index(drop=True)


def missing_months(monthly: pd.DataFrame) -> list[str]:
    """Calendar months absent between the first and last row - a gap makes a
    roll rate pair two months that are not consecutive."""
    full = pd.date_range(monthly[MONTH].min(), monthly[MONTH].max(), freq="MS")
    return [m.strftime("%Y-%m") for m in full.difference(monthly[MONTH])]
