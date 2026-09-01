import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rental_analysis.general_inputs import GeneralInputs
from rental_analysis.data_questionnaire import QUESTIONS
from rental_analysis.data_input import process_data_input, apply_fallbacks
from rental_analysis.asset_view import build_asset_view
from rental_analysis.repayment_curve import build_repayment_curve
from rental_analysis.lease_cohorts import build_cohorts
from rental_analysis.cohorts_for_x_or_more_loans import filter_cohorts
from rental_analysis.churn_analysis import ChurnAnalysis
from rental_analysis.ltv_analysis import LtvAnalysis
from rental_analysis.ue_analysis import UeAnalysis
from rental_analysis.ts_covenants import build_ts_covenants
from rental_analysis.general_analysis import describe as general_analysis

DATE_COLS = ["start_date", "expected_end_date", "closed_date", "asset_recovery_date", "recovery_date"]


def run(contract_csv_path):
    raw = pd.read_csv(contract_csv_path)
    for col in DATE_COLS:
        if col in raw.columns:
            raw[col] = pd.to_datetime(raw[col], errors="coerce")

    print("=" * 60)
    print("GENERAL INPUTS")
    print("=" * 60)
    gi = GeneralInputs(raw)
    for k, v in gi.as_dict().items():
        print(f"  {k}: {v}")
    print()

    print("=" * 60)
    print("DATA INPUT (computed columns)")
    print("=" * 60)
    fallback_df, notes = apply_fallbacks(raw, gi.useful_life_years, gi.extraction_date)
    for note in notes:
        print(f"  [fallback] {note}")
    df = process_data_input(fallback_df, gi.as_calc_dict())
    print(f"  Rows processed: {len(df)}")
    print(f"  Columns: {list(df.columns)}")
    print()

    print("=" * 60)
    print("ASSET VIEW")
    print("=" * 60)
    av = build_asset_view(df, gi.as_calc_dict())
    print(f"  Unique assets: {len(av)}")
    print()

    curve = build_repayment_curve(av, gi.as_calc_dict())
    cohorts = build_cohorts(df, gi.as_calc_dict())
    filtered = filter_cohorts(cohorts, gi.min_loans_per_cohort)

    print("=" * 60)
    print("LEASE COHORTS")
    print("=" * 60)
    print(f"  Total cohorts: {len(cohorts)}")
    print(f"  Cohorts with >= {gi.min_loans_per_cohort} new leases: {len(filtered)}")
    print()

    churn = ChurnAnalysis(cohorts, gi.as_calc_dict())
    print("=" * 60)
    print("CHURN ANALYSIS")
    print("=" * 60)
    for k, v in churn.as_dict().items():
        if k != "residual_curve":
            print(f"  {k}: {v}")
    print()

    ue = UeAnalysis(df, av, curve, gi.as_calc_dict())
    print("=" * 60)
    print("UNIT ECONOMICS ANALYSIS")
    print("=" * 60)
    for k, v in ue.as_dict().items():
        print(f"  {k}: {v}")
    print()

    ltv = LtvAnalysis(df, churn, gi.as_calc_dict())
    print("=" * 60)
    print("LTV ANALYSIS")
    print("=" * 60)
    for k, v in ltv.as_dict().items():
        print(f"  {k}: {v}")
    print()

    ts = build_ts_covenants(ue.as_dict(), ltv.as_dict(), curve)
    print("=" * 60)
    print("TS COVENANTS")
    print("=" * 60)
    for k, v in ts.items():
        print(f"  {k}: {v}")
    print()

    return {
        "general_inputs": gi.as_dict(),
        "data_input": df,
        "asset_view": av,
        "repayment_curve": curve,
        "lease_cohorts": cohorts,
        "filtered_cohorts": filtered,
        "churn": churn.as_dict(),
        "ue": ue.as_dict(),
        "ltv": ltv.as_dict(),
        "ts_covenants": ts,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m rental_analysis.run_all <contract_data.csv>")
        sys.exit(1)
    run(sys.argv[1])
