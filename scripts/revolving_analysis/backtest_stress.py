"""Backtest and stress scenarios for the roll-rate ECL.

Backtest: at each past month t the chain is struck on months up to t only;
its performing-book loss (before LGD) forecasts the write-offs of the book
already on the balance sheet, which arrive between `last edge's lag` and
`sum of lags` months later (full chain: t+1..t+5; approximation: t+2..t+4).
Money issued after t cannot reach write-off inside that window, so the
comparison is clean.

Stress: the selected roll rates and the LGD are scaled per scenario, and the
ECL, coverage and as-of loss rate recomputed."""

import numpy as np
import pandas as pd

from .transition_matrices import Spec, build_transition_matrices, loss_factor_chain, month_labels


def forecast_window(spec: Spec) -> tuple[int, int]:
    return spec.edges[-1][4], sum(e[4] for e in spec.edges)


def backtest(
    frame: pd.DataFrame, spec: Spec, window: str, tolerance: float, min_history: int
) -> tuple[pd.DataFrame, dict]:
    lo, hi = forecast_window(spec)
    labels = month_labels(frame)
    wo = frame["principal_written-off"].to_numpy(dtype=float)
    actual_col = f"Actual write-offs (t+{lo}..t+{hi})"
    rows = []
    for t in range(min_history, len(frame)):
        tm = build_transition_matrices(frame.iloc[: t + 1], spec, window, 1.0, tolerance)
        ecl, s = tm["ecl"], tm["eclSummary"]
        forecast = (
            float((ecl["Balance at as-of"] * ecl["Loss factor"]).iloc[:-1].sum())
            if s["eclValid"]
            else np.nan
        )
        complete = t + hi < len(frame)
        actual = float(wo[t + lo : t + hi + 1].sum()) if complete else np.nan
        rows.append(
            {
                "As-of": labels[t],
                "Forecast write-offs": forecast,
                actual_col: actual,
                "Actual ÷ forecast": actual / forecast if complete and forecast else np.nan,
                "Coverage (performing)": s["coveragePerforming"],
                "Outcome observed": complete,
            }
        )
    table = pd.DataFrame(
        rows,
        columns=[
            "As-of",
            "Forecast write-offs",
            actual_col,
            "Actual ÷ forecast",
            "Coverage (performing)",
            "Outcome observed",
        ],
    )
    done = table[table["Outcome observed"] & table["Actual ÷ forecast"].notna()]
    ratios = done["Actual ÷ forecast"]
    summary = {
        "window": f"t+{lo}..t+{hi}",
        "months": int(len(ratios)),
        "pooledRatio": _div(done[actual_col].sum(), done["Forecast write-offs"].sum()),
        "medianRatio": float(ratios.median()) if len(ratios) else None,
        "minRatio": float(ratios.min()) if len(ratios) else None,
        "maxRatio": float(ratios.max()) if len(ratios) else None,
        "meanAbsError": float((ratios - 1).abs().mean()) if len(ratios) else None,
    }
    return table, summary


def stressed_links(links, multiplier: float, cap: float) -> list[float]:
    """Scale each roll rate; a stressed rate is capped at `cap` (config:
    stress_roll_rate_cap) unless the observed rate is already above it
    (aggregate rates can exceed 1)."""
    out = []
    for link in links:
        if pd.isna(link):
            out.append(np.nan)
        elif link > cap:
            out.append(link)
        else:
            out.append(min(link * multiplier, cap))
    return out


def stress_scenarios(
    frame: pd.DataFrame,
    spec: Spec,
    tm: dict,
    pit_last: pd.Series,
    lgd: float,
    blend_weight: float,
    basis: str,
    scenarios: list[dict],
    haircut: float,
    cap: float,
) -> list[dict]:
    """ECL, coverage and as-of loss rate per scenario. With every multiplier
    at 1 this reproduces the unstressed Transition Matrices figures."""
    if not tm["eclSummary"]["eclValid"]:
        return [{"name": s["name"], "valid": False} for s in scenarios]
    last = frame.iloc[-1]
    base_links = tm["rollRates"]["SELECTED"].tolist()
    absorbed = float(last[spec.absorbed[1]])
    cum_wo = pit_last["Cumulative principal written off"]
    cum_issued = pit_last["Cumulative principal issued"]
    lagged_issued = pit_last["Cumulative principal issued, lagged"]
    out = []
    for s in scenarios:
        factors = loss_factor_chain(stressed_links(base_links, s["rollRateMultiplier"], cap), spec)
        s_lgd = min(1.0, lgd * s["lgdMultiplier"])
        performing = sum(float(last[col]) * factors[chain] for col, chain in spec.ecl_buckets)
        perf_balance = sum(float(last[col]) for col, _ in spec.ecl_buckets)
        total_loss = performing + cum_wo
        naive = _div(total_loss * s_lgd, cum_issued)
        lagged = _div(total_loss * s_lgd, lagged_issued)
        blended = (
            naive * (1 - blend_weight) + lagged * blend_weight
            if naive is not None and lagged is not None
            else None
        )
        loss = {"Naive": naive, "Lagged": lagged, "Blended": blended}[basis]
        ecl_perf = performing * s_lgd
        ecl_total = ecl_perf + absorbed * s_lgd
        out.append(
            {
                "name": s["name"],
                "valid": True,
                "rollRateMultiplier": s["rollRateMultiplier"],
                "lgdMultiplier": s["lgdMultiplier"],
                "lgd": s_lgd,
                "lossFactorCurrent": factors[spec.edges[0][1]],
                "performingEcl": ecl_perf,
                "totalEcl": ecl_total,
                "coveragePerforming": _div(ecl_perf, perf_balance),
                "coverageTotal": _div(ecl_total, perf_balance + absorbed),
                "lossRate": loss,
                "advanceRate": max(0.0, 1 - loss - haircut) if loss is not None else None,
            }
        )
    return out


def _div(num, den):
    if num is None or den is None or pd.isna(num) or pd.isna(den) or den == 0:
        return None
    return float(num) / float(den)
