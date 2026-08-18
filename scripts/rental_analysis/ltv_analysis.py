"""LTV Analysis sheet: asset recoverability + MRR cash-flow multiplier."""

import numpy as np
import pandas as pd


class LtvAnalysis:
    def __init__(self, df, churn, gi):
        self.df = df
        self.churn = churn
        self.gi = gi

    def avg_useful_life_m(self):
        df = self.df
        return (df["term_days"] * df["cost_of_asset"]).sum() / df["cost_of_asset"].sum() / 30.5

    def n_defaulted_gt_3m(self):
        return (self.df["defaulted_gt_nmo"] == True).sum()  # noqa: E712

    def pct_recovered(self):
        n_defaulted = self.n_defaulted_gt_3m()
        if not n_defaulted:
            return np.nan
        n_recovered = ((self.df["defaulted_gt_nmo"] == True) & self.df["redeployed"]).sum()  # noqa: E712
        return n_recovered / n_defaulted

    def loss_non_recoverability(self):
        pr = self.pct_recovered()
        return 1 - pr if pd.notna(pr) else np.nan

    def mrr_multiplier(self):
        return self.churn.multiplier_3y()

    def mrr_over_avg_cost(self):
        return self.df["monthly_expected_payment"].sum() / self.df["cost_of_asset"].sum()

    def pct_value_recovered_company(self):
        redeployed = self.df[self.df["redeployed"]]
        denom = redeployed["current_asset_value"].sum()
        return redeployed["recovery_amount"].sum() / denom if denom else np.nan

    def pct_recovered_of_current_value_company(self):
        return self.pct_value_recovered_company() * self.pct_recovered()

    def pct_value_recovered_lendable(self):
        redeployed = self.df[self.df["redeployed"]]
        denom = redeployed["lendable_asset_value"].sum()
        return redeployed["recovery_amount"].sum() / denom if denom else np.nan

    def pct_recovered_of_current_value_lendable(self):
        return self.pct_value_recovered_lendable() * self.pct_recovered()

    def _open_df(self):
        return self.df[self.df["status_mapping"] == self.gi["open_label"]]

    def mrr(self):
        return self._open_df()["monthly_expected_payment"].sum()

    def avg_monthly_churn(self):
        return self.churn.avg_churn()

    def avg_collection_rate(self):
        open_df = self._open_df()
        denom = open_df["amount_expected_to_date"].sum()
        return open_df["total_paid"].sum() / denom if denom else np.nan

    def as_dict(self):
        return {
            "avg_useful_life_m": self.avg_useful_life_m(),
            "n_defaulted_gt_3m": self.n_defaulted_gt_3m(),
            "pct_recovered": self.pct_recovered(),
            "loss_non_recoverability": self.loss_non_recoverability(),
            "mrr_multiplier": self.mrr_multiplier(),
            "mrr_over_avg_cost": self.mrr_over_avg_cost(),
            "pct_value_recovered_company": self.pct_value_recovered_company(),
            "pct_recovered_of_current_value_company": self.pct_recovered_of_current_value_company(),
            "pct_value_recovered_lendable": self.pct_value_recovered_lendable(),
            "pct_recovered_of_current_value_lendable": self.pct_recovered_of_current_value_lendable(),
            "mrr": self.mrr(),
            "avg_monthly_churn": self.avg_monthly_churn(),
            "avg_collection_rate": self.avg_collection_rate(),
            "pctile_95_churn": self.churn.pctile_95(),
            "stressed_churn": self.churn.stress_churn(),
            "multiplier_1y": self.churn.multiplier_1y(),
            "multiplier_2y": self.churn.multiplier_2y(),
            "multiplier_3y": self.churn.multiplier_3y(),
            "multiplier_total": self.churn.multiplier_total(),
        }
