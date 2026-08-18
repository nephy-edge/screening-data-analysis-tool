DEFAULT_STATUS_MAP = {
    "Closed": "Closed", "Open": "Open", "Repaid": "Paid-off",
    "Active": "Open", "Write-off": "Closed",
}


class GeneralInputs:
    """Mirrors the workbook's General Inputs sheet. Defaults match the
    Template workbook; pass overrides for a specific deal (e.g. Astranova
    used days_after_term=0, months_since_default=3, min_loans_per_cohort=1,
    useful_life_years=5)."""

    def __init__(self, df, **overrides):
        self.extraction_date = overrides.get("extraction_date", df["start_date"].max())
        self.days_after_term = overrides.get("days_after_term", 0)
        self.months_since_default = overrides.get("months_since_default", 3)
        self.min_loans_per_cohort = overrides.get("min_loans_per_cohort", 20)
        self.useful_life_years = overrides.get("useful_life_years", 3.0)
        self.status_map = overrides.get("status_map", DEFAULT_STATUS_MAP)
        self.open_label = overrides.get("open_label", "Open")
        self.closed_label = overrides.get("closed_label", "Closed")
        self.paidoff_label = overrides.get("paidoff_label", "Paid-off")

    def as_dict(self):
        return {
            "Date of extraction": self.extraction_date,
            "Days after term": self.days_after_term,
            "Months since default": self.months_since_default,
            "Minimum loans per cohort": self.min_loans_per_cohort,
            "Useful life of asset (years)": self.useful_life_years,
            "Status used for active": self.open_label,
            "Status used for canceled": self.closed_label,
            "Status used for paid-off": self.paidoff_label,
        }

    def as_calc_dict(self):
        """Shape expected by the compute_* functions in this package."""
        return {
            "extraction_date": self.extraction_date,
            "days_after_term": self.days_after_term,
            "months_since_default": self.months_since_default,
            "min_loans_per_cohort": self.min_loans_per_cohort,
            "useful_life_years": self.useful_life_years,
            "status_map": self.status_map,
            "open_label": self.open_label,
            "closed_label": self.closed_label,
            "paidoff_label": self.paidoff_label,
        }
