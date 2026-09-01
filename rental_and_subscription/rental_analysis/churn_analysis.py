"""Churn Analysis sheet: 95th-percentile / average monthly churn, the
1.7x-stressed churn rate, MRR survival multipliers, and the expected
residual-portfolio decay curve."""

import numpy as np
import pandas as pd


class ChurnAnalysis:
    def __init__(self, cohorts, gi):
        self.cohorts = cohorts
        self.gi = gi
        self.stress_multiplier = gi.get("churn_stress_multiplier", 1.7)
        self._eligible = cohorts[cohorts["active_leases_in_month"] > gi["min_loans_per_cohort"]]

    def pctile_95(self):
        return self._eligible["churn_rate"].quantile(0.95) if len(self._eligible) else 0.0

    def avg_churn(self):
        return self._eligible["churn_rate"].mean() if len(self._eligible) else 0.0

    def stress_churn(self):
        # A genuine 95th-percentile churn near 100% (a small eligible cohort
        # that fully defaulted in one month) would push the stressed rate
        # above 1.0, which makes (1-c) negative and the survival multipliers
        # below oscillate in sign - churn can't exceed 100% of a month's pool.
        return min(self.stress_multiplier * self.pctile_95(), 1.0)

    def _multiplier(self, months):
        c = self.stress_churn()
        if c <= 0:
            return months + 1
        return (1 - (1 - c) ** (months + 1)) / c

    def multiplier_1y(self):
        return self._multiplier(12)

    def multiplier_2y(self):
        return self._multiplier(24)

    def multiplier_3y(self):
        return self._multiplier(36)

    def multiplier_total(self):
        c = self.stress_churn()
        return (1 / c) if c > 0 else np.nan

    def residual_curve(self, horizon=60):
        c = self.stress_churn()
        residual = [1.0]
        for _ in range(1, horizon):
            residual.append(residual[-1] * (1 - c))
        return pd.DataFrame({"month": range(horizon), "residual_value": residual})

    def as_dict(self):
        return {
            "pctile_95": self.pctile_95(),
            "avg_churn": self.avg_churn(),
            "stress_churn": self.stress_churn(),
            "multiplier_1y": self.multiplier_1y(),
            "multiplier_2y": self.multiplier_2y(),
            "multiplier_3y": self.multiplier_3y(),
            "multiplier_total": self.multiplier_total(),
            "residual_curve": self.residual_curve(),
        }
