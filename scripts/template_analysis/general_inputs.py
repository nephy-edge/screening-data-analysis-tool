import pandas as pd


class GeneralInputs:
    def __init__(self, df: pd.DataFrame, **overrides):
        self.extraction_date = overrides.get("extraction_date", df["Disbursement Date"].max())
        self.days_after_term = overrides.get("days_after_term", 90)
        self.min_loans_per_cohort = overrides.get("min_loans_per_cohort", 10)

    def as_dict(self):
        return {
            "Date of extraction": self.extraction_date,
            "Days after term": self.days_after_term,
            "Minimum loans per cohort": self.min_loans_per_cohort,
        }
