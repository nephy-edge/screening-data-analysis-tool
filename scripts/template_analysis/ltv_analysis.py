import pandas as pd


class LtvAnalysis:
    def __init__(self, df: pd.DataFrame, filtered_cohorts: pd.DataFrame):
        self.df = df
        self.filtered_cohorts = filtered_cohorts

    def percentile_95_losses(self):
        series = self.filtered_cohorts["Loss Rate"].dropna()
        return series.quantile(0.95) if not series.empty else float("nan")

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
