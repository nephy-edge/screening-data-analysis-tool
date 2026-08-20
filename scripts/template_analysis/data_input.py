import pandas as pd
import numpy as np

_DATE_COLS = ["Disbursement Date", "Expected Completion Date", "Begin Date"]
_TOTAL_DUE_ALIASES = ["Total Due", "Total Dues Calculated", "POS"]


def _coerce_dates(df: pd.DataFrame) -> pd.DataFrame:
    for col in _DATE_COLS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", dayfirst=True)
    return df


def _compute_begin_date(df: pd.DataFrame) -> pd.Series:
    if "Begin Date" in df.columns:
        return df["Begin Date"]
    return df["Disbursement Date"] + pd.DateOffset(months=1)


def _compute_total_due(
    df: pd.DataFrame, extraction_date
) -> pd.Series | None:
    for alias in _TOTAL_DUE_ALIASES:
        if alias in df.columns:
            if alias != "Total Due":
                df.rename(columns={alias: "Total Due"}, inplace=True)
            return None
    needed = {"Payment per Period", "Payment Frequency"}
    if not needed.issubset(df.columns):
        return None
    begin = _compute_begin_date(df)
    freq = df["Payment Frequency"].str.lower()
    pp = pd.to_numeric(df["Payment per Period"], errors="coerce").fillna(0)
    fee = (
        pd.to_numeric(df["Expected Fee"], errors="coerce").fillna(0)
        if "Expected Fee" in df.columns
        else 0
    )
    end = df["Expected Completion Date"]
    tenor_months = (end - begin).dt.days / 30
    elapsed_days = (extraction_date - begin).dt.days.clip(lower=0)
    elapsed = np.where(
        freq == "weekly", elapsed_days / 7,
        np.where(freq == "biweekly", elapsed_days / 14,
        np.where(freq == "monthly", (extraction_date.year - begin.dt.year) * 12
                 + (extraction_date.month - begin.dt.month),
        0))
    )
    elapsed = np.maximum(np.floor(elapsed) - 1, 0)
    adj_tenor = np.where(
        freq == "weekly", tenor_months * 30 / 7,
        np.where(freq == "biweekly", tenor_months * 30 / 14,
        tenor_months)
    )
    capped = np.minimum(elapsed, adj_tenor)
    return capped * pp + fee


def _normalize_columns(df):
    rename_map = {
        "Payment per Period*": "Payment per Period",
        "Principal Repayment": "Payment Frequency",
    }
    for old, new in rename_map.items():
        if old in df.columns and new not in df.columns:
            df.rename(columns={old: new}, inplace=True)
    return df


def process_data_input(df: pd.DataFrame, extraction_date, days_after_term=90):
    result = _normalize_columns(_coerce_dates(df.copy()))

    if "Expected Fee" not in result.columns:
        result["Expected Fee"] = 0

    result["Cohort"] = pd.to_datetime(
        result["Disbursement Date"].dt.to_period("M").dt.start_time
    )

    result["Term (days)"] = (
        result["Expected Completion Date"] - result["Disbursement Date"]
    ).dt.days

    computed = _compute_total_due(result, extraction_date)
    if computed is not None:
        result["Total Due"] = computed

    cutoff = extraction_date - pd.Timedelta(days=days_after_term)
    result["Reached T+3?"] = result["Expected Completion Date"].notna() & (
        result["Expected Completion Date"] <= cutoff
    )

    return result
