"""Point-in-time portfolio loss rate - rcl_rollrate_ecl_3.xlsx's 'Loss Rate
LTV' tab. Each month uses only the roll rates observable by that month:

    loss rate = (ECL on performing book + cumulative write-offs) x LGD / principal issued

with three denominators: every dollar issued to date (NAIVE, a lower bound),
issued as at `lag` months earlier (LAGGED), and a weighted BLEND of the two."""

import numpy as np
import pandas as pd

from .transition_matrices import Spec, loss_factor_chain, month_labels, selected_links

BASES = ["Naive", "Blended", "Lagged"]


def point_in_time_loss_rates(
    monthly: pd.DataFrame,
    spec: Spec,
    rates: np.ndarray,
    window_length: int | None,
    lgd: float,
    lag_months: int,
    blend_weight: float,
    min_outstanding: float,
) -> pd.DataFrame:
    """One row per month: the loss-rate build ('Loss Rate LTV' rows 32-44).
    A month whose chain has any unobservable link has no rate (NaN) - the
    sheet's IFERROR, which only ever affects the ramp-up months the
    minimum-outstanding filter already drops. The absorbed bucket (defaulted
    stock) is left out: it is already in the cumulative write-offs."""
    n = len(monthly)
    ecl = np.full(n, np.nan)
    for j in range(n):
        factors = loss_factor_chain(selected_links(rates, spec, j, window_length), spec)
        ecl[j] = sum(monthly[col].iloc[j] * factors[chain] for col, chain in spec.ecl_buckets)

    cum_wo = monthly["principal_written-off"].cumsum().to_numpy(dtype=float)
    cum_issued = monthly["principal_issued"].cumsum().to_numpy(dtype=float)
    lagged_issued = np.full(n, np.nan)
    if lag_months < n:
        lagged_issued[lag_months:] = cum_issued[: n - lag_months]
    total_loss = ecl + cum_wo
    with np.errstate(divide="ignore", invalid="ignore"):
        naive = np.where(cum_issued != 0, total_loss * lgd / cum_issued, np.nan)
        lagged = np.where(lagged_issued != 0, total_loss * lgd / lagged_issued, np.nan)
    blended = naive * (1 - blend_weight) + lagged * blend_weight
    outstanding = monthly["principal_outstanding"].to_numpy(dtype=float)
    out = pd.DataFrame(
        {
            "Month": month_labels(monthly),
            "ECL on performing book (before LGD)": ecl,
            "Cumulative principal written off": cum_wo,
            "Total loss (before LGD)": total_loss,
            "Cumulative principal issued": cum_issued,
            "Cumulative principal issued, lagged": lagged_issued,
            "Principal outstanding": outstanding,
            "Naive": naive,
            "Lagged": lagged,
            "Blended": blended,
        }
    )
    out["In sample"] = outstanding >= min_outstanding
    return out


def loss_rate_distribution(pit: pd.DataFrame, stress_percentile: float) -> pd.DataFrame:
    """'Loss Rate LTV' rows 48-57 over the in-sample months: Excel's
    PERCENTILE is numpy's default linear interpolation, STDEV is ddof=1."""
    stats = {}
    for basis in BASES:
        vals = pit.loc[pit["In sample"], basis].dropna().to_numpy(dtype=float)
        has = vals.size > 0
        stats[basis] = {
            "Months in sample": float(vals.size),
            "Mean": vals.mean() if has else np.nan,
            "Median": float(np.median(vals)) if has else np.nan,
            "Standard deviation": vals.std(ddof=1) if vals.size > 1 else np.nan,
            "Minimum": vals.min() if has else np.nan,
            "Maximum": vals.max() if has else np.nan,
            "25th percentile": float(np.percentile(vals, 25)) if has else np.nan,
            "75th percentile": float(np.percentile(vals, 75)) if has else np.nan,
            "Stress percentile": float(np.percentile(vals, stress_percentile * 100))
            if has
            else np.nan,
        }
    out = pd.DataFrame(stats)
    out.index.name = "Statistic"
    return out
