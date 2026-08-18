"""Asset View sheet: per-asset rollup of the Data Input tape."""

import numpy as np
import pandas as pd


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
    return av.reset_index()
