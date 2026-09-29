import pandas as pd


class LtvAnalysis:
    def __init__(self, df: pd.DataFrame, filtered_cohorts: pd.DataFrame):
        self.df = df
        self.filtered_cohorts = filtered_cohorts

    def percentile_95_losses(self, cohort_from=None, cohort_to=None):
        """95th percentile of the (already min-loans-filtered) cohort loss
        rates, optionally limited to cohorts in [cohort_from, cohort_to]
        (inclusive by month, either end open when None) - the LTV
        Calculator's cohort range, same convention as UeAnalysis.avg_interest_pct."""
        return _p95(self.filtered_cohorts, cohort_from, cohort_to)

    def all_loans_cohort_losses(self, min_loans):
        """Each cohort's loss over EVERY loan in it, finished or not -
        1 - Total Paid / (Principal + Expected Interest + Expected Fee), for
        cohorts with at least `min_loans` loans. This is the Salmon sheet's
        "GBV Loss" basis, offered as an LTV Calculator option; the template's
        own Loss Rate (build_cohorts) stays matured-only."""
        g = self.df.groupby("Cohort")
        owed = g["Principal Value"].sum() + g["Expected Interest"].sum() + g["Expected Fee"].sum()
        loss = (1 - g["Total Paid"].sum() / owed).where(owed != 0)
        loss = loss.where(g.size() >= min_loans).dropna()
        return pd.DataFrame({"Cohort": loss.index, "Loss Rate": loss.values})

    def percentile_95_losses_all_loans(self, min_loans, cohort_from=None, cohort_to=None):
        """percentile_95_losses on the all-loans basis above."""
        return _p95(self.all_loans_cohort_losses(min_loans), cohort_from, cohort_to)

    def avg_total_revenue_pct(self):
        total_interest = self.df["Expected Interest"].sum()
        total_fee = self.df["Expected Fee"].sum()
        total_principal = self.df["Principal Value"].sum()
        if total_principal == 0:
            return float("nan")
        return (total_interest + total_fee) / total_principal

    def avg_term(self):
        product = (self.df["Term (days)"] * self.df["Principal Value"]).sum()
        total_principal = self.df["Principal Value"].sum()
        if total_principal == 0:
            return float("nan")
        return product / total_principal

    def as_dict(self):
        return {
            "95th Percentile Losses": self.percentile_95_losses(),
            "Average Total Revenue %": self.avg_total_revenue_pct(),
            "Average Term": self.avg_term(),
        }


def _p95(cohorts: pd.DataFrame, cohort_from=None, cohort_to=None):
    """95th percentile of cohorts["Loss Rate"], optionally limited to cohorts
    in [cohort_from, cohort_to] (inclusive by month, either end open)."""
    if cohort_from is not None:
        cohorts = cohorts[cohorts["Cohort"] >= pd.Timestamp(cohort_from)]
    if cohort_to is not None:
        cohorts = cohorts[cohorts["Cohort"] <= pd.Timestamp(cohort_to)]
    series = cohorts["Loss Rate"].dropna()
    return series.quantile(0.95) if not series.empty else float("nan")
