"""High-level repayment curve (Unit Economics Analysis sheet, rows 27+):
% of asset cost collected by month-on-books, per MOB bucket."""

import numpy as np
import pandas as pd


def build_repayment_curve(av, gi):
    max_mob = int(gi["useful_life_years"] * 12)
    rows = []
    # Inclusive of max_mob itself - ts_covenants.py looks up fixed MOB points
    # (6/12/24) that must be reachable even for short-life assets where
    # useful_life_years * 12 == 24, not just for larger assets where it's
    # comfortably past those checkpoints.
    for mob in range(0, max_mob + 1):
        bucket = av[av["mob"] == mob]
        count = len(bucket)
        total_paid = bucket["total_paid"].sum()
        cost = bucket["asset_cost"].sum()
        pct_paid = total_paid / cost if cost > 0 else np.nan
        pct_avg_collection = pct_paid / mob if mob > 0 else np.nan
        include = count > gi["min_loans_per_cohort"]
        rows.append({
            "mob": mob, "number_of_contracts": count, "total_paid": total_paid,
            "cost_of_assets": cost, "pct_paid_over_cost": pct_paid,
            "pct_avg_collection": pct_avg_collection, "include_in_calc": include,
        })
    return pd.DataFrame(rows)
