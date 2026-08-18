import pandas as pd


def filter_cohorts(cohorts: pd.DataFrame, min_loans: int):
    return cohorts[cohorts["Matured Count"] >= min_loans].copy()
