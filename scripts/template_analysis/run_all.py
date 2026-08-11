import pandas as pd
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from template_analysis.general_inputs import GeneralInputs
from template_analysis.data_questionnaire import QUESTIONS
from template_analysis.data_input import process_data_input
from template_analysis.cohorts import build_cohorts
from template_analysis.cohorts_for_x_or_more_loans import filter_cohorts
from template_analysis.ltv_analysis import LtvAnalysis
from template_analysis.ue_analysis import UeAnalysis
from template_analysis.general_analysis import describe as general_analysis


def run(loan_csv_path):
    raw = pd.read_csv(loan_csv_path, parse_dates=[
        "Disbursement Date", "Expected Completion Date"
    ])

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
    df = process_data_input(raw, gi.extraction_date, gi.days_after_term)
    print(f"  Rows processed: {len(df)}")
    print(f"  Columns: {list(df.columns)}")
    print(f"  Reached T+3: {df['Reached T+3?'].sum()} / {len(df)}")
    print(f"  Unique cohorts: {df['Cohort'].nunique()}")
    print()

    print("=" * 60)
    print("COHORTS")
    print("=" * 60)
    cohorts = build_cohorts(df)
    print(f"  Total cohorts: {len(cohorts)}")
    print(f"  Avg loss rate (matured): {cohorts['Loss Rate'].mean():.4%}")
    print()

    print("=" * 60)
    print("COHORTS FOR X OR MORE LOANS")
    print("=" * 60)
    filtered = filter_cohorts(cohorts, gi.min_loans_per_cohort)
    print(f"  Cohorts with >= {gi.min_loans_per_cohort} loans: {len(filtered)}")
    print()

    print("=" * 60)
    print("LTV ANALYSIS")
    print("=" * 60)
    ltv = LtvAnalysis(df, filtered)
    for k, v in ltv.as_dict().items():
        print(f"  {k}: {v}")
    print()

    print("=" * 60)
    print("UNIT ECONOMICS ANALYSIS")
    print("=" * 60)
    ue = UeAnalysis(df)
    for k, v in ue.as_dict().items():
        print(f"  {k}: {v}")
    print()

    return {
        "general_inputs": gi.as_dict(),
        "cohorts": cohorts,
        "filtered_cohorts": filtered,
        "ltv": ltv.as_dict(),
        "ue": ue.as_dict(),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m template_analysis.run_all <loan_data.csv>")
        sys.exit(1)
    run(sys.argv[1])
