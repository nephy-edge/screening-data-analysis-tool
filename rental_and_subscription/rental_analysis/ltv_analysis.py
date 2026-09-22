"""LTV Analysis sheet: asset recoverability + MRR cash-flow multiplier.

Extends the workbook's "LTV Inputs" sheet with the Guide's asset-value LTV
waterfall (max 95% vs asset value; recoverability + subscription revenue + FX)
and the DSLP30 / Receivables coverage covenant inputs.
"""

import numpy as np
import pandas as pd


class LtvAnalysis:
    def __init__(self, df, churn, gi, av=None):
        self.df = df
        self.churn = churn
        self.gi = gi
        self.av = av

    def avg_useful_life_m(self):
        df = self.df
        total_cost = df["cost_of_asset"].sum()
        if not total_cost:
            return np.nan
        return (df["term_days"] * df["cost_of_asset"]).sum() / total_cost / 30.5

    def n_defaulted_gt_3m(self):
        return (self.df["defaulted_gt_nmo"] == True).sum()  # noqa: E712

    def n_defaulted_last_12m(self):
        """Contracts defaulted/cancelled in the last 12 months, at least 3 months
        prior to the testing date (Guide Recovery Rate denominator)."""
        defaulted = self.df[self.df["defaulted_gt_nmo"] == True]  # noqa: E712
        if defaulted.empty:
            return 0
        cutoff = self.gi["extraction_date"] - pd.Timedelta(days=365)
        return int((defaulted["closed_date"] >= cutoff).sum())

    def pct_recovered(self):
        n_defaulted = self.n_defaulted_gt_3m()
        if not n_defaulted:
            return np.nan
        n_recovered = ((self.df["defaulted_gt_nmo"] == True) & self.df["redeployed"]).sum()  # noqa: E712
        return n_recovered / n_defaulted

    def pct_recovered_last_12m(self):
        """Guide Recovery Rate: contracts repossessed/insured (proxy: redeployed)
        among all defaulted >= 3 months ago, over contracts defaulted/cancelled in
        the last 12 months (>= 3 months prior to the testing date). Capped at 1.0:
        the numerator and denominator are different populations (all >= 3m
        recoveries vs last-12m defaults), which can otherwise exceed 100%."""
        n = self.n_defaulted_last_12m()
        if not n:
            return np.nan
        n_recovered = ((self.df["defaulted_gt_nmo"] == True) & self.df["redeployed"]).sum()  # noqa: E712
        return min(n_recovered / n, 1.0)

    def loss_non_recoverability(self):
        pr = self.pct_recovered()
        return 1 - pr if pd.notna(pr) else np.nan

    def recoverability_pctile_95(self):
        """Guide: 95th %ile of historical asset value recovered against all assets
        that defaulted (per-asset recovery_amount / lendable value at default).
        If recovery values are unavailable (common at screening), falls back to the
        repossession / insurance rate assuming 100% recovery - the Guide's stated
        fallback."""
        if "recovery_amount" not in self.df.columns:
            return self.pct_recovered()
        defaulted = self.df[self.df["defaulted_gt_nmo"] == True]  # noqa: E712
        if defaulted.empty:
            return np.nan
        value = defaulted["lendable_asset_value"]
        rec = defaulted["recovery_amount"].fillna(0)
        if (rec > 0).sum() == 0:
            return self.pct_recovered()
        # Each ratio capped at 1.0 (recovery can't exceed the asset value
        # economically); fully-depreciated defaulted assets (value 0) count as 0%
        # recovered rather than being dropped, so they pull the percentile down.
        with np.errstate(divide="ignore", invalid="ignore"):
            ratios = (rec / value).clip(upper=1.0).where(value > 0, 0.0)
        ratios = pd.to_numeric(ratios, errors="coerce").dropna()
        if len(ratios) == 0:
            return self.pct_recovered()
        return float(ratios.quantile(0.95))

    def mrr_multiplier(self):
        return self.churn.multiplier_3y()

    def mrr_over_avg_cost(self):
        total_cost = self.df["cost_of_asset"].sum()
        if not total_cost:
            return np.nan
        return self.df["monthly_expected_payment"].sum() / total_cost

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
        """MRR on all open contracts (unfiltered - workbook definition)."""
        return self._open_df()["monthly_expected_payment"].sum()

    def mrr_dslp30(self):
        """MRR on open contracts that are active & performing (days since last
        payment <= 30) - the Guide's collateral eligibility. n/a when no
        payment-date data is available."""
        open_df = self._open_df()
        if "days_since_last_payment" not in open_df.columns:
            return np.nan
        dslp = open_df["days_since_last_payment"]
        if not dslp.notna().any():
            return np.nan
        return open_df[dslp <= 30]["monthly_expected_payment"].sum()

    def subscription_margin(self):
        """Historical average subscription margin (MRR / avg cost of asset),
        Guide's expected-subscription-revenue building block. Uses the
        DSLP30-eligible MRR when available, else the unfiltered MRR."""
        total_cost = self.df["cost_of_asset"].sum()
        if not total_cost:
            return np.nan
        mrr = self.mrr_dslp30()
        if pd.isna(mrr):
            mrr = self.mrr()
        return mrr / total_cost

    def subscription_support(self):
        """Expected subscription revenue as a fraction of asset cost:
        subscription margin x min(stressed churn multiple, 6 months) - Guide caps
        the multiple at 6. Clamped to full cost coverage."""
        m = self.subscription_margin()
        if pd.isna(m):
            return np.nan
        mult = self.mrr_multiplier()
        if pd.isna(mult):
            mult = 6.0
        return min(m * min(mult, 6.0), 1.0)

    def fx_deval(self):
        """FX risk: Guide's 95th %ile FX devaluation (up to 24 months) with a max
        50% discount applied by default. None input -> no FX component (0.0)."""
        pct = self.gi.get("fx_deval_pct")
        if pct is None or pd.isna(pct):
            return 0.0
        if self.gi.get("fx_discount_50pct", True):
            return pct * 0.5
        return pct

    def ltv_no_fx(self):
        """Asset-value LTV before FX: recoverable fraction of the asset value plus
        expected subscription revenue support, floored/ceiled to [0, 1]."""
        rec = self.recoverability_pctile_95()
        if pd.isna(rec):
            return np.nan
        sub = self.subscription_support()
        if pd.isna(sub):
            sub = 0.0
        return min(rec + sub, 1.0)

    def ltv_asset_value(self):
        """Guide: LTV max set at 95% against asset value, driven by recoverability,
        expected subscription revenue and FX risk."""
        ltv_nfx = self.ltv_no_fx()
        if pd.isna(ltv_nfx):
            return np.nan
        return min(ltv_nfx / (1 + self.fx_deval()), 0.95)

    def asset_dslp30(self):
        """Guide Asset DSLP30: initial asset value of active Secured Assets that
        received no payment in the last 30 days / initial asset value of all active
        Secured Assets. n/a when no payment-date data is available. Active assets
        without a known payment date are treated as delinquent - conservative for
        the covenant (a partial-data figure must not flatter the borrower)."""
        if self.av is None:
            return np.nan
        active = self.av[self.av["is_active_asset"]]
        if active.empty:
            return np.nan
        denom = active["initial_asset_value"].sum()
        if not denom:
            return np.nan
        if not active["dslp_days"].notna().any():
            return np.nan
        num = active[(active["dslp_days"] > 30) | active["dslp_days"].isna()][
            "initial_asset_value"
        ].sum()
        return num / denom

    def receivables_value_180d(self):
        """Guide Receivables Coverage numerator: aggregate value of Secured
        Receivables = daily price x 180 days (monthly_expected_payment / 30 x 180
        = monthly payment x 6), over open performing (<= DSLP30) contracts."""
        open_df = self._open_df()
        if open_df.empty:
            return 0.0
        has_dslp = (
            "days_since_last_payment" in open_df.columns
            and open_df["days_since_last_payment"].notna().any()
        )
        if has_dslp:
            open_df = open_df[open_df["days_since_last_payment"] <= 30]
        return open_df["monthly_expected_payment"].sum() * 6

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
            "n_defaulted_last_12m": self.n_defaulted_last_12m(),
            "pct_recovered": self.pct_recovered(),
            "pct_recovered_last_12m": self.pct_recovered_last_12m(),
            "loss_non_recoverability": self.loss_non_recoverability(),
            "recoverability_pctile_95": self.recoverability_pctile_95(),
            "mrr_multiplier": self.mrr_multiplier(),
            "mrr_over_avg_cost": self.mrr_over_avg_cost(),
            "pct_value_recovered_company": self.pct_value_recovered_company(),
            "pct_recovered_of_current_value_company": self.pct_recovered_of_current_value_company(),
            "pct_value_recovered_lendable": self.pct_value_recovered_lendable(),
            "pct_recovered_of_current_value_lendable": (
                self.pct_recovered_of_current_value_lendable()
            ),
            "mrr": self.mrr(),
            "mrr_dslp30": self.mrr_dslp30(),
            "subscription_margin": self.subscription_margin(),
            "subscription_support": self.subscription_support(),
            "fx_deval_used": self.fx_deval(),
            "ltv_no_fx": self.ltv_no_fx(),
            "ltv_asset_value": self.ltv_asset_value(),
            "asset_dslp30": self.asset_dslp30(),
            "receivables_value_180d": self.receivables_value_180d(),
            "avg_monthly_churn": self.avg_monthly_churn(),
            "avg_collection_rate": self.avg_collection_rate(),
            "pctile_95_churn": self.churn.pctile_95(),
            "stressed_churn": self.churn.stress_churn(),
            "multiplier_1y": self.churn.multiplier_1y(),
            "multiplier_2y": self.churn.multiplier_2y(),
            "multiplier_3y": self.churn.multiplier_3y(),
            "multiplier_total": self.churn.multiplier_total(),
        }
