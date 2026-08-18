"""Unit Economics Analysis sheet."""

import numpy as np
import pandas as pd


class UeAnalysis:
    def __init__(self, df, asset_view, curve, gi):
        self.df = df
        self.av = asset_view
        self.curve = curve
        self.gi = gi

    def avg_original_asset_value(self):
        return self.df[self.df["first_asset_lease"]]["cost_of_asset"].mean()

    def downpayment_pct(self):
        return self.df["downpayment"].sum() / self.df["cost_of_asset"].sum()

    def lease_tenor_m(self):
        df = self.df
        return (df["term_days"] * df["cost_of_asset"]).sum() / df["cost_of_asset"].sum() / 30.5

    def monthly_avg_contractual_payment(self):
        df = self.df
        return (df["monthly_expected_payment"] * df["cost_of_asset"]).sum() / df["cost_of_asset"].sum()

    def avg_total_gbv_leased(self):
        return self.monthly_avg_contractual_payment() * self.lease_tenor_m()

    def margin(self):
        return self.avg_total_gbv_leased() / self.avg_original_asset_value() - 1

    def pvd(self):
        return self.df["total_paid"].sum() / self.df["amount_expected_to_date"].sum()

    def utilisation_rate(self):
        return (self.av["open_count"] == 1).sum() / len(self.av)

    def pct_collected_historical(self):
        df = self.df
        matured = df[df["original_asset_reached_term"]]
        denom = df[df["first_asset_lease"] & df["reached_t"]]["cost_of_asset"].sum()
        if not denom:
            return np.nan
        return (matured["total_paid"].sum() + matured["recovery_amount"].fillna(0).sum()) / denom

    def pct_collected_estimate(self):
        return (self.margin() + 1) * self.pvd() * self.utilisation_rate()

    def monthly_observed_repayment(self):
        curve = self.curve
        fixed = curve[(curve["mob"] >= 1) & (curve["mob"] <= 19) & curve["include_in_calc"]]
        weight = fixed["cost_of_assets"]
        if not weight.sum():
            return np.nan
        return (fixed["pct_avg_collection"] * weight).sum() / weight.sum()

    def pct_collected_estimate_ii(self):
        return self.monthly_observed_repayment() * self.lease_tenor_m()

    def historical_pct_collected_on_principal(self):
        candidates = [v for v in (
            self.pct_collected_historical(), self.pct_collected_estimate_ii(), self.pct_collected_estimate(),
        ) if pd.notna(v)]
        return min(candidates) if candidates else np.nan

    def expected_monthly_collection(self):
        return self.monthly_observed_repayment() * self.avg_original_asset_value()

    def monthly_avg_contractual_payment_pct(self):
        return self.df["monthly_expected_payment"].sum() / self.df["cost_of_asset"].sum()

    def monthly_avg_actual_payment_pct(self):
        return self.monthly_avg_contractual_payment_pct() * self.pvd()

    def as_dict(self):
        return {
            "avg_original_asset_value": self.avg_original_asset_value(),
            "downpayment_pct": self.downpayment_pct(),
            "lease_tenor_m": self.lease_tenor_m(),
            "monthly_avg_contractual_payment": self.monthly_avg_contractual_payment(),
            "avg_total_gbv_leased": self.avg_total_gbv_leased(),
            "margin": self.margin(),
            "pvd": self.pvd(),
            "utilisation_rate": self.utilisation_rate(),
            "pct_collected_historical": self.pct_collected_historical(),
            "pct_collected_estimate": self.pct_collected_estimate(),
            "monthly_observed_repayment": self.monthly_observed_repayment(),
            "pct_collected_estimate_ii": self.pct_collected_estimate_ii(),
            "historical_pct_collected_on_principal": self.historical_pct_collected_on_principal(),
            "expected_monthly_collection": self.expected_monthly_collection(),
            "monthly_avg_contractual_payment_pct": self.monthly_avg_contractual_payment_pct(),
            "monthly_avg_actual_payment_pct": self.monthly_avg_actual_payment_pct(),
        }
