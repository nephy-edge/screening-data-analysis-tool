"""Revolving Lending (RCL) engine entry point: monthly rows + inputs ->
everything the Outputs, Transition Matrices, Backtest & Stress and Facility
tabs show."""

from dataclasses import dataclass, field

import pandas as pd

from .backtest_stress import backtest, stress_scenarios
from .facility import advance_rate, covenant_tests
from .loss_rate import BASES, loss_rate_distribution, point_in_time_loss_rates
from .metrics import early_warnings, metrics_at, metrics_over_time, standard_metrics, unit_economics
from .monthly_input import (
    MONTH,
    RECOVERIES,
    REQUIRED_COLUMNS,
    has_full_buckets,
    missing_months,
    prepare_monthly,
)
from .outputs import fact_block, ratio_block
from .performance import performance
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
    writeoff_window_months: int = DEFAULTS["writeoff_window_months"]
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
    wo_window = inputs.writeoff_window_months
    over_time = metrics_over_time(monthly, window, full, wo_window)
    values = metrics_at(monthly, len(monthly) - 1, window, full, wo_window)
    perf = performance(monthly, pit, lgd, full)
    ue = unit_economics(values, inputs.cost_of_funds_pct, inputs.opex_pct, perf["avgTerm"])
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
    bt_table = _with_gap_and_tenor(bt_table, over_time)
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
        "performance": perf,
        "ltvInterest": values["rate_m"] * perf["avgTerm"]
        if values.get("rate_m") is not None and perf["avgTerm"] is not None
        else None,
        "tenorInputs": _tenor_inputs(monthly, window, values),
        "ageingMix": _ageing_mix(monthly) if full else [],
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
        "qualityChecks": quality_checks(monthly, tm["checks"], inputs),
        **tm,
    }


_AGEING = [
    ("Current (incl. new)", ["BAL_NEW", "BAL_CURRENT"]),
    ("1-30 days", ["BAL_PAR_1_30"]),
    ("31-60 days", ["BAL_PAR_31_60"]),
    ("61-90 days", ["BAL_PAR_61_90"]),
    ("91-120 days", ["BAL_PAR_91_120"]),
    (
        "121-180+ days (not yet defaulted)",
        ["BAL_PAR_121_150", "BAL_PAR_151_180", "BAL_PAR_180_PLUS"],
    ),
    ("Defaulted stock", ["BAL_DEFAULT"]),
]


def _ageing_mix(monthly: pd.DataFrame) -> list[dict]:
    """Standard ageing split of principal outstanding at the as-of month;
    shares sum to 100% when the buckets tie to the total."""
    now = monthly.iloc[-1]
    total = float(now["principal_outstanding"])
    return [
        {
            "bucket": label,
            "balance": float(now[cols].sum()),
            "share": float(now[cols].sum()) / total if total else None,
        }
        for label, cols in _AGEING
    ]


def _tenor_inputs(monthly: pd.DataFrame, window: int, values: dict) -> dict:
    """The inputs behind the principal tenor used in unit economics."""
    win = monthly["principal_outstanding"].iloc[-window:]
    return {
        "principalCollected": float(monthly["principal_collected"].iloc[-1]),
        "averageOutstanding": float(win.mean()),
        "windowMonths": int(len(win)),
        "paymentRate": values.get("payment_rate"),
        "principalTenor": values.get("principal_tenor"),
    }


def _with_gap_and_tenor(table: pd.DataFrame, over_time: pd.DataFrame) -> pd.DataFrame:
    """Backtest table plus the gap (actual vs forecast less 1) and the
    principal tenor at each as-of month, to relate the two."""
    out = table.copy()
    at = out.columns.get_loc("Actual vs forecast") + 1
    out.insert(at, "Gap", out["Actual vs forecast"] - 1)
    tenor = over_time.loc["principal_tenor"] if "principal_tenor" in over_time.index else None
    out.insert(
        at + 1,
        "Principal tenor (m)",
        out["As-of"].map(tenor).astype(float) if tenor is not None else float("nan"),
    )
    return out


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


def quality_checks(monthly, checks, inputs) -> list[dict]:
    """The Data Checks tab: one entry per problem found, each with the months
    behind it, in the Lending/Rental check shape (level, check_id, message,
    detail). Built from the same per-month rows as the Transition Matrices
    data-quality table, so the two never disagree."""
    out = []
    tie_row = next((r for r in checks.index if r.startswith(("Buckets tie", "Active + PAR"))), None)
    if tie_row:
        bad = [m for m, v in checks.loc[tie_row].items() if v == "CHECK"]
        if bad:
            out.append(
                {
                    "level": "warning",
                    "check_id": "balance_tie",
                    "message": (
                        f"{len(bad)} month(s) fail '{tie_row}' - "
                        "ECL for those months is unreliable."
                    ),
                    "detail": [{"Month": m} for m in bad],
                }
            )
    unexplained = checks.loc["Roll-forward: unexplained movement"]
    pct = checks.loc["Unexplained as % of outstanding"]
    over = [m for m, v in pct.items() if pd.notna(v) and abs(v) > inputs.unexplained_warning_pct]
    if over:
        out.append(
            {
                "level": "warning",
                "check_id": "unexplained_movement",
                "message": (
                    f"{len(over)} month(s) where the opening balance, plus new drawings, less "
                    f"collections and write-offs, misses the closing balance by more than "
                    f"{inputs.unexplained_warning_pct:.0%} of outstanding."
                ),
                "detail": [
                    {
                        "Month": m,
                        "Unexplained": round(float(unexplained[m]), 2),
                        "% of outstanding": f"{pct[m]:.2%}",
                    }
                    for m in over
                ],
            }
        )
    gaps = missing_months(monthly)
    if gaps:
        out.append(
            {
                "level": "warning",
                "check_id": "missing_months",
                "message": (
                    f"{len(gaps)} calendar month(s) missing - roll rates across a gap "
                    "pair non-consecutive months."
                ),
                "detail": [{"Month": m} for m in gaps],
            }
        )
    months = monthly[MONTH].dt.strftime("%Y-%m")
    negative = [
        {"Month": months[i], "Field": col, "Value": float(monthly.at[i, col])}
        for i in monthly.index
        for col in REQUIRED_COLUMNS
        if col != MONTH and pd.notna(monthly.at[i, col]) and monthly.at[i, col] < 0
    ]
    if negative:
        out.append(
            {
                "level": "warning",
                "check_id": "negative_values",
                "message": (
                    f"{len(negative)} negative value(s) in fields that should never be negative."
                ),
                "detail": negative,
            }
        )
    for row, check_id in (
        ("Duplicate loan-months in source", "source_duplicates"),
        ("Loan-months filled by carry-forward", "source_carry_forward"),
    ):
        if row in checks.index:
            hits = [
                {"Month": m, "Count": float(v)}
                for m, v in checks.loc[row].items()
                if pd.notna(v) and v
            ]
            if hits:
                out.append(
                    {
                        "level": "info",
                        "check_id": check_id,
                        "message": f"Source reports {row.lower()} in {len(hits)} month(s).",
                        "detail": hits,
                    }
                )
    return out


def _num(v):
    return None if pd.isna(v) else float(v)
