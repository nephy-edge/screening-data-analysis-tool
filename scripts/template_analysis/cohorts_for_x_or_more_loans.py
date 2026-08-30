import pandas as pd


def filter_cohorts(cohorts: pd.DataFrame, min_loans: int):
    # cohorts["Loan Count"] is already matured-loan-only (build_cohorts mirrors
    # the Excel pivot's Reached T+3? page filter), so this matches the Excel
    # template's Cohorts!B:B >= 'General Inputs'!C5 threshold directly.
    return cohorts[cohorts["Loan Count"] >= min_loans].copy()
