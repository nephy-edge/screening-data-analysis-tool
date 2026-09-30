"""Revolving Lending (RCL) engine entry point: monthly rows + inputs ->
everything the Outputs, Transition Matrices, Backtest & Stress and Facility
tabs show."""

from dataclasses import dataclass, field

import pandas as pd

from .backtest_stress import backtest, stress_scenarios
from .facility import advance_rate, covenant_tests
from .loss_rate import BASES, loss_rate_distribution, point_in_time_loss_rates
from .metrics import early_warnings, metrics_at, metrics_over_time, standard_metrics, unit_economics
from .monthly_input import MONTH, RECOVERIES, has_full_buckets, missing_months, prepare_monthly
from .outputs import fact_block, ratio_block
from .settings import DEFAULTS, WINDOW_LENGTHS, default_covenants, default_scenarios
from .transition_matrices import COARSE_SPEC, FULL_SPEC, build_transition_matrices, with_remainder


@dataclass
class RevolvingInputs:
    """The single set of tunables. Every default is config.toml
    [revolving_defaults] (see settings.py); callers override per run."""

    as_of_month: str | None = None  # "YYYY-MM"; None = last month in the data
    lgd: float = DEFAULTS["lgd"]
    roll_rate_window: str = DEFAULTS["roll_rate_window"]
    ratio_window_months: int = DEFAULTS["ratio_window_months"]
    min_outstanding_in_sample: float = DEFAULTS["min_outstanding_in_sample"]
    stress_percentile: float = DEFAULTS["stress_percentile"]
    denominator_lag_months: int = DEFAULTS["denominator_lag_months"]
    lagged_blend_weight: float = DEFAULTS["lagged_blend_weight"]
    loss_rate_basis: str = DEFAULTS["loss_rate_basis"]
    balance_tolerance: float = DEFAULTS["balance_tolerance"]
    unexplained_warning_pct: float = DEFAULTS["unexplained_warning_pct"]
    lgd_basis: str = DEFAULTS["lgd_basis"]  # "Input" or "Recoveries"
    cost_of_funds_pct: float = DEFAULTS["cost_of_funds_pct"]  # annual, on outstanding
    opex_pct: float = DEFAULTS["opex_pct"]  # annual, on outstanding
    advance_haircut: float = DEFAULTS["advance_haircut"]
    backtest_min_history_months: int = DEFAULTS["backtest_min_history_months"]
    early_warning_lookback_months: int = DEFAULTS["early_warning_lookback_months"]
    early_warning_threshold: float = DEFAULTS["early_warning_threshold"]
    stress_roll_rate_cap: float = DEFAULTS["stress_roll_rate_cap"]
    # [{name, rollRateMultiplier, lgdMultiplier}]
    scenarios: list = field(default_factory=default_scenarios)
    # [{metric, operator ("<=" / ">="), limit}]
    covenants: list = field(default_factory=default_covenants)


def derived_lgd(monthly: pd.DataFrame) -> float:
    """LGD = 1 - recoveries / principal written off, over all months to date."""
    written_off = monthly["principal_written-off"].sum()
    if RECOVERIES not in monthly.columns:
        raise ValueError("LGD basis 'Recoveries' needs the recoveries column to be mapped.")
    if not written_off:
        raise ValueError("LGD basis 'Recoveries' needs some principal written off to date.")
    return min(1.0, max(0.0, 1 - monthly[RECOVERIES].fillna(0).sum() / written_off))


def run(df: pd.DataFrame, inputs: RevolvingInputs) -> dict:
    if inputs.loss_rate_basis not in BASES:
        raise ValueError(f"Loss-rate basis must be one of {', '.join(BASES)}")
    if inputs.lgd_basis not in ("Input", "Recoveries"):
        raise ValueError("LGD basis must be 'Input' or 'Recoveries'.")
    monthly = prepare_monthly(df)
    available_months = monthly[MONTH].dt.strftime("%Y-%m").tolist()
    if inputs.as_of_month:
        as_of = pd.Timestamp(f"{inputs.as_of_month}-01")
        monthly = monthly[monthly[MONTH] <= as_of].reset_index(drop=True)
        if monthly.empty or monthly[MONTH].iloc[-1] != as_of:
            raise ValueError(f"As-of month {inputs.as_of_month} is not in the data.")
    # Everything below sees only months up to the as-of date - no look-ahead.

    lgd = derived_lgd(monthly) if inputs.lgd_basis == "Recoveries" else inputs.lgd

    # Full roll-rate model when every core bucket balance is mapped, else the
    # 3-link approximation from the Outputs columns.
    full = has_full_buckets(monthly)
    if full:
        spec, model_frame = FULL_SPEC, monthly
    else:
        spec, model_frame = COARSE_SPEC, with_remainder(monthly)
    tm = build_transition_matrices(
        model_frame, spec, inputs.roll_rate_window, lgd, inputs.balance_tolerance
    )
    pit = point_in_time_loss_rates(
        model_frame,
        spec,
        tm["rates"],
        WINDOW_LENGTHS[inputs.roll_rate_window],
        lgd,
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

    # Standard metrics, over time and at the as-of month.
    window = inputs.ratio_window_months
    over_time = metrics_over_time(monthly, window, full)
    values = metrics_at(monthly, len(monthly) - 1, window, full)
    ue = unit_economics(values, inputs.cost_of_funds_pct, inputs.opex_pct)
    values.update(
        {
            "coverage_perf": tm["eclSummary"]["coveragePerforming"],
            "coverage_total": tm["eclSummary"]["coverageTotal"],
            "net_yield_annual": ue[-1]["annualised"],
        }
    )
    bt_table, bt_summary = backtest(
        model_frame,
        spec,
        inputs.roll_rate_window,
        inputs.balance_tolerance,
        inputs.backtest_min_history_months,
    )
    stress = stress_scenarios(
        model_frame,
        spec,
        tm,
        pit.iloc[-1],
        lgd,
        inputs.lagged_blend_weight,
        inputs.loss_rate_basis,
        inputs.scenarios,
        inputs.advance_haircut,
        inputs.stress_roll_rate_cap,
    )
    return {
        "availableMonths": available_months,
        "lgdUsed": lgd,
        "lgdBasis": inputs.lgd_basis,
        "standardMetrics": standard_metrics(values),
        "overTime": over_time,
        "unitEconomics": ue,
        "earlyWarnings": early_warnings(
            over_time, inputs.early_warning_lookback_months, inputs.early_warning_threshold
        ),
        "backtest": bt_table,
        "backtestSummary": bt_summary,
        "stress": stress,
        "advance": advance_rate(_num(basis["Stress percentile"]), inputs.advance_haircut),
        "covenants": covenant_tests(values, inputs.covenants),
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
