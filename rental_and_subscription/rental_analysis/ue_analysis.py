"""Unit Economics Analysis sheet."""

import numpy as np
import pandas as pd


class UeAnalysis:
    def __init__(self, df, asset_view, curve, gi, churn=None):
        self.df = df
        self.av = asset_view
        self.curve = curve
        self.gi = gi
        self.churn = churn
        # Guide: "Monthly Avg Churn ... Input for implied % Collected on Principal".
        self.avg_churn = churn.avg_churn() if churn is not None else np.nan

    def avg_original_asset_value(self):
        return self.df[self.df["first_asset_lease"]]["cost_of_asset"].mean()

    def downpayment_pct(self):
        total_cost = self.df["cost_of_asset"].sum()
        if not total_cost:
            return np.nan
        return self.df["downpayment"].sum() / total_cost

    def lease_tenor_m(self):
        df = self.df
        total_cost = df["cost_of_asset"].sum()
        if not total_cost:
            return np.nan
        return (df["term_days"] * df["cost_of_asset"]).sum() / total_cost / 30.5

    def monthly_avg_contractual_payment(self):
        df = self.df
        total_cost = df["cost_of_asset"].sum()
        if not total_cost:
            return np.nan
        return (df["monthly_expected_payment"] * df["cost_of_asset"]).sum() / total_cost

    def avg_total_gbv_leased(self):
        return self.monthly_avg_contractual_payment() * self.lease_tenor_m()

    def margin(self):
        avg_cost = self.avg_original_asset_value()
        if not avg_cost or pd.isna(avg_cost):
            return np.nan
        return self.avg_total_gbv_leased() / avg_cost - 1

    def pvd(self):
        total_expected = self.df["amount_expected_to_date"].sum()
        if not total_expected:
            return np.nan
        return self.df["total_paid"].sum() / total_expected

    def utilisation_rate(self):
        return (self.av["open_count"] == 1).sum() / len(self.av)

    def pct_collected_historical(self):
        df = self.df
        matured = df[df["reached_t"]]
        denom = df[df["first_asset_lease"] & df["original_asset_reached_term"]]["cost_of_asset"].sum()
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

    def pct_collected_estimate_iii(self):
        """Implied % collected from the monthly observed repayment run off at the
        average monthly churn over the useful life - the Guide's "Monthly Avg
        Churn is an input for implied % Collected on Principal"."""
        c = self.avg_churn
        tenor = self.lease_tenor_m()
        monthly = self.monthly_observed_repayment()
        if pd.isna(c) or pd.isna(tenor) or pd.isna(monthly) or tenor <= 0:
            return np.nan
        n = max(int(round(tenor)), 1)
        survival_sum = sum((1 - c) ** t for t in range(n))
        return monthly * survival_sum

    def historical_pct_collected_on_principal(self):
        candidates = [v for v in (
            self.pct_collected_historical(), self.pct_collected_estimate_ii(),
            self.pct_collected_estimate(), self.pct_collected_estimate_iii(),
        ) if pd.notna(v)]
        return min(candidates) if candidates else np.nan

    def expected_monthly_collection(self):
        return self.monthly_observed_repayment() * self.avg_original_asset_value()

    def monthly_avg_contractual_payment_pct(self):
        total_cost = self.df["cost_of_asset"].sum()
        if not total_cost:
            return np.nan
        return self.df["monthly_expected_payment"].sum() / total_cost

    def monthly_avg_actual_payment_pct(self):
        return self.monthly_avg_contractual_payment_pct() * self.pvd()

    def funding_cost_pct(self):
        return self.gi.get("funding_cost_pct", 0.0)

    def direct_cost_pct(self):
        return self.gi.get("direct_cost_pct", 0.0)

    def cost_of_funding_over_life_pct(self):
        """Lendable's cost of funding + direct costs, grossed up over the useful
        life: annual % of asset cost x tenor(m)/12."""
        tenor = self.lease_tenor_m()
        if pd.isna(tenor):
            return np.nan
        return (self.funding_cost_pct() + self.direct_cost_pct()) * tenor / 12

    def net_margin(self):
        """Guide: UE = cash margin after taking into account Lendable's cost of
        funding and any direct costs."""
        m = self.margin()
        c = self.cost_of_funding_over_life_pct()
        if pd.isna(m) or pd.isna(c):
            return np.nan
        return m - c

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
            "pct_collected_estimate_iii": self.pct_collected_estimate_iii(),
            "historical_pct_collected_on_principal": self.historical_pct_collected_on_principal(),
            "expected_monthly_collection": self.expected_monthly_collection(),
            "monthly_avg_contractual_payment_pct": self.monthly_avg_contractual_payment_pct(),
            "monthly_avg_actual_payment_pct": self.monthly_avg_actual_payment_pct(),
            "funding_cost_pct": self.funding_cost_pct(),
            "direct_cost_pct": self.direct_cost_pct(),
            "cost_of_funding_over_life_pct": self.cost_of_funding_over_life_pct(),
            "net_margin": self.net_margin(),
        }
