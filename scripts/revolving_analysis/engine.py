"""Revolving Lending (RCL) engine entry point: monthly rows + inputs ->
everything the Outputs and Transition Matrices tabs show."""

from dataclasses import dataclass

import pandas as pd

from .loss_rate import BASES, loss_rate_distribution, point_in_time_loss_rates
from .monthly_input import MONTH, has_full_buckets, missing_months, prepare_monthly
from .outputs import fact_block, ratio_block
from .transition_matrices import (
    COARSE_SPEC,
    FULL_SPEC,
    WINDOW_LENGTHS,
    build_transition_matrices,
    with_remainder,
)


@dataclass
class RevolvingInputs:
    """The single set of tunables (defaults: config.toml [revolving_defaults])."""

    as_of_month: str | None = None  # "YYYY-MM"; None = last month in the data
    lgd: float = 1.0
    roll_rate_window: str = "T12"
    ratio_window_months: int = 2
    min_outstanding_in_sample: float = 1_000_000_000.0
    stress_percentile: float = 0.95
    denominator_lag_months: int = 5
    lagged_blend_weight: float = 0.5
    loss_rate_basis: str = "Blended"
    balance_tolerance: float = 1.0
    unexplained_warning_pct: float = 0.05


def run(df: pd.DataFrame, inputs: RevolvingInputs) -> dict:
    if inputs.loss_rate_basis not in BASES:
        raise ValueError(f"Loss-rate basis must be one of {', '.join(BASES)}")
    monthly = prepare_monthly(df)
    available_months = monthly[MONTH].dt.strftime("%Y-%m").tolist()
    if inputs.as_of_month:
        as_of = pd.Timestamp(f"{inputs.as_of_month}-01")
        monthly = monthly[monthly[MONTH] <= as_of].reset_index(drop=True)
        if monthly.empty or monthly[MONTH].iloc[-1] != as_of:
            raise ValueError(f"As-of month {inputs.as_of_month} is not in the data.")
    # Everything below sees only months up to the as-of date - no look-ahead.

    # Full roll-rate model when every core bucket balance is mapped, else the
    # 3-link approximation from the Outputs columns.
    if has_full_buckets(monthly):
        spec, model_frame = FULL_SPEC, monthly
    else:
        spec, model_frame = COARSE_SPEC, with_remainder(monthly)
    tm = build_transition_matrices(
        model_frame, spec, inputs.roll_rate_window, inputs.lgd, inputs.balance_tolerance
    )
    pit = point_in_time_loss_rates(
        model_frame,
        spec,
        tm["rates"],
        WINDOW_LENGTHS[inputs.roll_rate_window],
        inputs.lgd,
        inputs.denominator_lag_months,
        inputs.lagged_blend_weight,
        inputs.min_outstanding_in_sample,
    )
    dist = loss_rate_distribution(pit, inputs.stress_percentile)
    basis = dist[inputs.loss_rate_basis]
    facts = fact_block(monthly)
    ratios = ratio_block(
        facts,
        inputs.ratio_window_months,
        _num(basis["Mean"]),
        _num(basis["Stress percentile"]),
        tm["eclSummary"]["totalEcl"],
    )
    return {
        "availableMonths": available_months,
        "monthly": monthly,
        "facts": facts,
        "ratios": ratios,
        "pit": pit,
        "distribution": dist,
        "warnings": _warnings(monthly, tm, pit, inputs),
        **tm,
    }


def _warnings(monthly, tm, pit, inputs) -> list[str]:
    out = []
    summary = tm["eclSummary"]
    if summary["method"] == "coarse":
        out.append(
            "Approximate roll rates: only the Outputs columns are mapped, so the chain is "
            "Active → PAR 1-60 → PAR 61-90 → write-off. The 1-60 bucket hides the 1-30 → 31-60 "
            "roll, which understates ECL; map the bucket balances (BAL_NEW … BAL_DEFAULT) for "
            "the full model."
        )
    if not summary["eclValid"]:
        out.append(
            f"Balances don't tie to principal_outstanding for {summary['asOfMonth']} "
            "(CHECK) - the ECL is withheld until they do."
        )
    gaps = missing_months(monthly)
    if gaps:
        out.append(
            "Months missing from the data (roll rates across a gap pair non-consecutive "
            f"months): {', '.join(gaps)}"
        )
    tail_cols = ["BAL_PAR_121_150", "BAL_PAR_151_180", "BAL_PAR_180_PLUS"]
    tail = monthly[tail_cols].iloc[-1].sum() if summary["method"] == "full" else 0
    if tail:
        out.append(
            f"{tail:,.0f} of balance sits in PAR 121+ at the as-of month; the chain ends at "
            "PAR 91-120, so it is outside the ECL."
        )
    checks = tm["checks"]
    if "Unexplained as % of outstanding" in checks.index:
        pct = checks.loc["Unexplained as % of outstanding"].iloc[-1]
        if pd.notna(pct) and abs(pct) > inputs.unexplained_warning_pct:
            out.append(
                f"Unexplained balance movement is {pct:.1%} of outstanding at the as-of month "
                f"(above {inputs.unexplained_warning_pct:.0%}) - read the checks before "
                "relying on the ECL."
            )
    if not pit["In sample"].any():
        out.append(
            "No month reaches the minimum outstanding for the loss-rate sample, so Loss Rate "
            "and 95th %ile are blank - lower it in General Inputs."
        )
    return out


def _num(v):
    return None if pd.isna(v) else float(v)
