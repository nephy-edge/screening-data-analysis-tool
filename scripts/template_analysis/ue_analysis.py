import pandas as pd


class UeAnalysis:
    def __init__(self, df: pd.DataFrame):
        self.df = df

    def avg_expected_term(self):
        product = (self.df["Term (days)"] * self.df["Principal Value"]).sum()
        total_principal = self.df["Principal Value"].sum()
        if total_principal == 0:
            return float("nan")
        return product / total_principal

    def avg_loss(self):
        matured = self.df[self.df["Reached T+3?"] == True]
        if matured.empty:
            return float("nan")
        owed = (
            matured["Principal Value"].sum()
            + matured["Expected Interest"].sum()
            + matured["Expected Fee"].sum()
        )
        paid = matured["Total Paid"].sum()
        if owed == 0:
            return float("nan")
        return (owed - paid) / owed

    def loss_rate_proxy(self):
        if "Total Due" not in self.df.columns:
            return float("nan")
        total_due = self.df["Total Due"].sum()
        if total_due == 0:
            return float("nan")
        return 1 - self.df["Total Paid"].sum() / total_due

    def avg_principal(self):
        return self.df["Principal Value"].mean()

    def avg_fee_pct(self):
        total_principal = self.df["Principal Value"].sum()
        if total_principal == 0:
            return float("nan")
        return self.df["Expected Fee"].sum() / total_principal

    def avg_interest_pct(self):
        total_principal = self.df["Principal Value"].sum()
        if total_principal == 0:
            return float("nan")
        return self.df["Expected Interest"].sum() / total_principal

    def sense_check_margin(self):
        int_pct = self.avg_interest_pct()
        fee_pct = self.avg_fee_pct()
        loss = self.avg_loss()
        rev_pct = int_pct + fee_pct
        if pd.isna(loss) or pd.isna(rev_pct):
            return float("nan")
        return rev_pct - (loss * (1 + rev_pct))

    def as_dict(self):
        return {
            "Average Expected Term": self.avg_expected_term(),
            "Average Loss": self.avg_loss(),
            "Loss Rate Proxy (1-PvD)": self.loss_rate_proxy(),
            "Average Principal Amount": self.avg_principal(),
            "Average Fee %": self.avg_fee_pct(),
            "Average Interest %": self.avg_interest_pct(),
            "Sense-check Margin": self.sense_check_margin(),
        }
