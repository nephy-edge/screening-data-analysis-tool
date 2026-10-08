DEFAULT_STATUS_MAP = {
    "Closed": "Closed",
    "Open": "Open",
    "Repaid": "Paid-off",
    "Active": "Open",
    "Write-off": "Closed",
}


class GeneralInputs:
    """Mirrors the workbook's General Inputs sheet. Defaults match the
    Template workbook; pass overrides for a specific deal (e.g. Astranova
    used days_after_term=0, months_since_default=3, min_loans_per_cohort=1,
    useful_life_years=5)."""

    def __init__(self, df, **overrides):
        self.extraction_date = overrides.get("extraction_date") or df["start_date"].max()
        self.days_after_term = overrides.get("days_after_term", 0)
        self.months_since_default = overrides.get("months_since_default", 3)
        self.min_loans_per_cohort = overrides.get("min_loans_per_cohort", 20)
        self.useful_life_years = overrides.get("useful_life_years", 3.0)
        self.churn_stress_multiplier = overrides.get("churn_stress_multiplier", 1.7)
        self.status_map = overrides.get("status_map", DEFAULT_STATUS_MAP)
        self.open_label = overrides.get("open_label", "Open")
        self.closed_label = overrides.get("closed_label", "Closed")
        self.paidoff_label = overrides.get("paidoff_label", "Paid-off")
        # UE cost assumptions (Guide: UE = cash margin after Lendable's cost of
        # funding and direct costs). Percentages are per annum, applied to the
        # asset cost over the useful life.
        self.funding_cost_pct = overrides.get("funding_cost_pct", 0.13)
        self.direct_cost_pct = overrides.get("direct_cost_pct", 0.02)
        # LTV (asset-value path): FX risk per the Guide - 95th %ile FX devaluation
        # up to 24 months, with a max 50% discount applied (fx_discount_50pct).
        # fx_deval_pct is entered per deal (e.g. from the LTV Calculator); None
        # skips the FX component entirely (a note is surfaced).
        self.fx_deval_pct = overrides.get("fx_deval_pct")
        self.fx_discount_50pct = overrides.get("fx_discount_50pct", True)
        # Receivables Coverage Ratio covenant: total debt service due to the Lender
        # under the facility over the following 180 days (facility-level input, not
        # derivable from the borrower tape). None leaves the ratio n/a.
        self.debt_service_180d = overrides.get("debt_service_180d")

    def as_dict(self):
        return {
            "Date of extraction": self.extraction_date,
            "Days after term": self.days_after_term,
            "Months since default": self.months_since_default,
            "Minimum loans per cohort": self.min_loans_per_cohort,
            "Useful life of asset (years)": self.useful_life_years,
            "Churn stress multiplier": self.churn_stress_multiplier,
            "Status used for active": self.open_label,
            "Status used for canceled": self.closed_label,
            "Status used for paid-off": self.paidoff_label,
            "Funding cost (% p.a.)": self.funding_cost_pct,
            "Direct cost (% p.a.)": self.direct_cost_pct,
            "FX devaluation (95th %ile, %)": self.fx_deval_pct,
            "FX max 50% discount": self.fx_discount_50pct,
            "Debt service due over 180d": self.debt_service_180d,
        }

    def as_calc_dict(self):
        """Shape expected by the compute_* functions in this package."""
        return {
            "extraction_date": self.extraction_date,
            "days_after_term": self.days_after_term,
            "months_since_default": self.months_since_default,
            "min_loans_per_cohort": self.min_loans_per_cohort,
            "useful_life_years": self.useful_life_years,
            "churn_stress_multiplier": self.churn_stress_multiplier,
            "status_map": self.status_map,
            "open_label": self.open_label,
            "closed_label": self.closed_label,
            "paidoff_label": self.paidoff_label,
            "funding_cost_pct": self.funding_cost_pct,
            "direct_cost_pct": self.direct_cost_pct,
            "fx_deval_pct": self.fx_deval_pct,
            "fx_discount_50pct": self.fx_discount_50pct,
            "debt_service_180d": self.debt_service_180d,
        }
