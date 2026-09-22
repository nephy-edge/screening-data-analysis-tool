"""Asset View sheet: per-asset rollup of the Data Input tape."""

import numpy as np
import pandas as pd


def count_missing_asset_id_rows(df):
    """Rows with a blank/null asset_id - pandas' groupby("asset_id") below
    drops these by default, silently excluding them from every per-asset
    rollup (and everything derived from it, e.g. utilisation_rate). Callers
    should surface this count rather than let the rows vanish silently."""
    return int(df["asset_id"].isna().sum())


def build_asset_view(df, gi):
    extraction_date = gi["extraction_date"]
    g = df.groupby("asset_id")
    av = pd.DataFrame({
        "start_first_contract": g["start_date"].min(),
        "number_of_contracts": g.size(),
        "total_expected": g["amount_expected_to_date"].sum(),
        "total_paid": g["total_paid"].sum(),
        "asset_cost": g["cost_of_asset"].min(),
        "open_count": g.apply(lambda x: (x["status_mapping"] == gi["open_label"]).sum()),
        "closed_count": g.apply(lambda x: (x["status_mapping"] == gi["closed_label"]).sum()),
        "paidoff_count": g.apply(lambda x: (x["status_mapping"] == gi["paidoff_label"]).sum()),
        "max_closed_date": g["closed_date"].max(),
    })
    av["mob"] = ((extraction_date - av["start_first_contract"]).dt.days / 30).round()
    av["paid_over_cost"] = av["total_paid"] / av["asset_cost"].replace(0, np.nan)
    av["pvd"] = av["total_paid"] / av["total_expected"].replace(0, np.nan)
    av.loc[av["closed_count"] == 0, "max_closed_date"] = pd.NaT

    open_starts = df[df["status_mapping"] == gi["open_label"]].groupby("asset_id")["start_date"].max()
    av["next_contract_date"] = pd.Series(pd.NaT, index=av.index, dtype="datetime64[ns]")
    has_closed = av["closed_count"] > 0
    no_open = av["open_count"] == 0
    av.loc[has_closed & no_open, "next_contract_date"] = extraction_date
    idx = av.index[has_closed & ~no_open]
    av.loc[idx, "next_contract_date"] = open_starts.reindex(idx)
    av["time_to_redeploy"] = (av["next_contract_date"] - av["max_closed_date"]).dt.days

    # Per-asset value + delinquency fields for the LTV / covenant analysis.
    # initial_asset_value is constant per asset (cost on the first lease); the
    # current Lendable value is the most recent (most depreciated) estimate, i.e.
    # the min across the asset's contract rows (extraction-date value for open
    # contracts, value-at-close for closed ones).
    if "initial_asset_value" in df.columns:
        av["initial_asset_value"] = g["initial_asset_value"].min()
    else:
        av["initial_asset_value"] = av["asset_cost"]
    if "lendable_asset_value" in df.columns:
        av["lendable_asset_value"] = g["lendable_asset_value"].min()
    else:
        av["lendable_asset_value"] = np.nan

    # Asset-level DSLP = days since the most recent payment across the asset's
    # contracts (falls back to the most recent closed_date for closed contracts).
    if "last_payment_date" in df.columns:
        av["last_payment_date"] = g["last_payment_date"].max()
        av["dslp_days"] = (extraction_date - av["last_payment_date"]).dt.days
    else:
        av["last_payment_date"] = pd.NaT
        av["dslp_days"] = np.nan

    # Guide: "an active asset is defined as an asset with positive current value
    # that has not been sold to third parties or end customers." Screening proxy:
    # positive Lendable current value AND currently on an open contract (paid-off /
    # sold and fully depreciated assets fall out).
    av["is_active_asset"] = (av["lendable_asset_value"] > 0) & (av["open_count"] >= 1)
    return av.reset_index()
