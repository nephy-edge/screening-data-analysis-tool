"""Data Input sheet: raw contract data + Lendable-derived columns.

See docs/README_Data_Dictionary.md for the input schema and the "if missing"
fallback rules implemented in apply_fallbacks().
"""

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = [
    "contract_id", "asset_id", "start_date", "status", "downpayment",
    "monthly_expected_payment", "total_paid", "cost_of_asset",
]
OPTIONAL_COLUMNS = [
    "expected_end_date", "closed_date", "asset_recovery_date",
    "total_contract_value", "amount_expected_to_date", "current_asset_value",
    "recovery_date", "recovery_amount",
]


def validate_columns(df):
    return [c for c in REQUIRED_COLUMNS if c not in df.columns]


def apply_fallbacks(df, useful_life_years, extraction_date):
    notes = []
    df = df.copy()
    for col in OPTIONAL_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan

    missing_end = df["expected_end_date"].isna()
    if missing_end.any():
        df.loc[missing_end, "expected_end_date"] = df.loc[missing_end, "start_date"] + pd.to_timedelta(
            useful_life_years * 365, unit="D"
        )
        notes.append(f"{missing_end.sum()} row(s): expected_end_date inferred as start_date + useful life")

    missing_tcv = df["total_contract_value"].isna()
    if missing_tcv.any():
        df.loc[missing_tcv, "total_contract_value"] = (
            df.loc[missing_tcv, "monthly_expected_payment"] * useful_life_years * 12
        )
        notes.append(f"{missing_tcv.sum()} row(s): total_contract_value inferred as monthly payment x useful life")

    missing_aetd = df["amount_expected_to_date"].isna()
    if missing_aetd.any():
        end_of_activity = df.loc[missing_aetd, "closed_date"].fillna(extraction_date)
        end_of_activity = end_of_activity.clip(upper=extraction_date)
        months_elapsed = (end_of_activity - df.loc[missing_aetd, "start_date"]).dt.days / 30
        df.loc[missing_aetd, "amount_expected_to_date"] = (
            df.loc[missing_aetd, "monthly_expected_payment"] * months_elapsed.clip(lower=0)
        )
        notes.append(f"{missing_aetd.sum()} row(s): amount_expected_to_date inferred from elapsed months")

    return df, notes


def process_data_input(df, gi):
    """gi: dict with extraction_date, days_after_term, months_since_default,
    useful_life_years, status_map, open_label, closed_label, paidoff_label."""
    df = df.copy()
    extraction_date = gi["extraction_date"]

    df["status_mapping"] = df["status"].map(gi["status_map"])

    first_start = df.groupby("asset_id")["start_date"].transform("min")
    df["first_asset_lease"] = df["start_date"] == first_start

    df["term_days"] = (df["expected_end_date"] - df["start_date"]).dt.days

    initial_value = df[df["first_asset_lease"]].groupby("asset_id")["cost_of_asset"].sum()
    df["initial_asset_value"] = df["asset_id"].map(initial_value).fillna(0)

    asset_life_days = df[df["first_asset_lease"]].groupby("asset_id")["term_days"].sum()
    df["asset_useful_life_d"] = df["asset_id"].map(asset_life_days)

    end_of_life = df["closed_date"].fillna(extraction_date)
    df["days_past_initial_sale"] = (end_of_life - first_start).dt.days

    life = df["asset_useful_life_d"].replace(0, np.nan)
    df["lendable_asset_value"] = (
        df["initial_asset_value"] * (1 - df["days_past_initial_sale"] / life)
    ).clip(lower=0).fillna(0)

    if df["current_asset_value"].isna().any():
        missing_cav = df["current_asset_value"].isna()
        df.loc[missing_cav, "current_asset_value"] = df.loc[missing_cav, "lendable_asset_value"]
    if df["recovery_amount"].isna().any():
        missing_rec = df["recovery_amount"].isna() & df["recovery_date"].notna()
        df.loc[missing_rec, "recovery_amount"] = df.loc[missing_rec, "lendable_asset_value"] * 0.85

    df["reached_t"] = (df["expected_end_date"] + pd.to_timedelta(gi["days_after_term"], unit="D")) <= extraction_date
    df["original_asset_reached_term"] = df.groupby("asset_id")["reached_t"].transform("any")

    df["defaulted_gt_nmo"] = np.where(
        df["closed_date"].notna(),
        (df["closed_date"] + pd.to_timedelta(gi["months_since_default"] * 30, unit="D")) <= extraction_date,
        np.nan,
    )

    df["rank_by_asset"] = df.groupby("asset_id")["start_date"].rank(method="min")
    max_rank = df.groupby("asset_id")["rank_by_asset"].transform("max")
    df["redeployed"] = ~((df["status_mapping"] == gi["closed_label"]) & (df["rank_by_asset"] == max_rank))

    df["contract_cohort"] = df["start_date"].dt.to_period("M").dt.to_timestamp()

    next_rank_start = df.set_index(["asset_id", "rank_by_asset"])["start_date"]
    lookup_key = list(zip(df["asset_id"], df["rank_by_asset"] + 1))
    next_start = pd.Series([next_rank_start.get(k, pd.NaT) for k in lookup_key], index=df.index)
    is_closed = df["status_mapping"] == gi["closed_label"]
    df["days_to_recovery"] = np.nan
    not_redeployed_end = is_closed & ~df["redeployed"]
    df.loc[not_redeployed_end, "days_to_recovery"] = (
        extraction_date - df.loc[not_redeployed_end, "closed_date"]
    ).dt.days
    redeployed_end = is_closed & df["redeployed"]
    df.loc[redeployed_end, "days_to_recovery"] = (
        next_start[redeployed_end] - df.loc[redeployed_end, "closed_date"]
    ).dt.days

    is_open = df["status_mapping"] == gi["open_label"]
    extraction_month = pd.Timestamp(extraction_date.year, extraction_date.month, 1)
    df["last_active_date"] = pd.NaT
    df.loc[~is_open & df["closed_date"].isna(), "last_active_date"] = extraction_month
    has_close = ~is_open & df["closed_date"].notna()
    df.loc[has_close, "last_active_date"] = df.loc[has_close, "closed_date"].dt.to_period("M").dt.to_timestamp()

    return df
