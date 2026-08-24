"""LTV / LTGBV calculator replicating the "Receivables" column-D formulas from
the LTV Calculator v3 workbook.

Formula chain (per column D of the Receivables sheet):
    D18/D19  Historical FX Devaluation      = lookup(country, term)   [99th %tile]
    D20      Stress FX Factor               = 1.2  (Inputs!$U$6)
    D21      Stress FX Devaluation          = MAX(D18:D19) * D20
    D22      Base FX Deval                  = IF(hedge=0, D21, MIN(D21, hedge))
    D23      FX Slippage Stress             = lookup(country)
    D24/D25  Roll Risk (high/low)           = lookup(rating, term)    [0 if "Term"]
    D26/D27  Selected FX Deval (high/low)   = D22 + D23 + D24/D25
    D30      Credit Stress Factor           = 1.7
    D31      Stress Loss Rate               = loss_rate * D30
    D32      Minimum Stress Loss            = lookup(segmentation)    [observed vs self-reported]
    D33      Selected Stress Loss           = MAX(D31, D32)
    D36      LTGBV No FX                    = 1 - D33
    D37/D38  LTGBV With FX (high/low)       = D36 / (1 + D26/D27)
    D41      Advance on Principal, no FX    = D36 * (1 + gross_interest)
    D42/D43  LTV With FX (high/low)         = D37/D38 * (1 + gross_interest)
"""

from __future__ import annotations

import math

from dataclasses import dataclass
from typing import Optional

from .ltv_calculator_data import (
    FX_SLIPPAGE,
    HISTORICAL_FX_DEVALUATION,
    MIN_STRESS_LOSS_OBSERVED,
    MIN_STRESS_LOSS_SELF_REPORTED,
    ROLL_RISK_HIGH,
    ROLL_RISK_LOW,
)

STRESS_FX_FACTOR = 1.2            # D20 / Inputs!$U$6
CREDIT_STRESS_FACTOR = 1.7        # D30

FX_TERMS = sorted({int(t) for _, terms in HISTORICAL_FX_DEVALUATION.items() for t in terms})
FX_RISK_RATINGS = list(ROLL_RISK_HIGH)
SEGMENTS = list(MIN_STRESS_LOSS_SELF_REPORTED)
COUNTRIES = sorted(HISTORICAL_FX_DEVALUATION)


@dataclass
class LtvInputs:
    """Green inputs are computed from the loan tape; yellow are manual (both optional)."""

    green_gross_interest: Optional[float] = None        # D10
    green_receivable_term_months: Optional[float] = None  # D11
    green_loss_rate: Optional[float] = None             # D12
    # Yellow / manual inputs (optional):
    currency: Optional[str] = None                      # D6
    fx_risk_rating: Optional[str] = None                # D7
    segmentation: Optional[str] = None                  # D9
    hedge_rate: Optional[float] = None                  # D13
    rolled_hedge: str = "Term"                          # D14 (Term | Roll)
    data_input_source: str = "Observed"                 # D5  (Observed | Self-reported)


def _to_float(value: Optional[float]) -> float:
    if value is None:
        return 0.0
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return value


def _nearest_term(months: Optional[float]) -> int:
    if not months or months <= 0:
        return 0
    return min(FX_TERMS, key=lambda t: abs(t - months))


def compute_ltv(inp: LtvInputs) -> dict:
    """Returns the column-D-style build-up for a single facility."""
    gross_interest = _to_float(inp.green_gross_interest)
    loss_rate = _to_float(inp.green_loss_rate)
    hedge_rate = _to_float(inp.hedge_rate)
    term = _nearest_term(inp.green_receivable_term_months)

    country = inp.currency
    rating = inp.fx_risk_rating

    # ---- FX Stress Build Up ----
    hist = HISTORICAL_FX_DEVALUATION.get(country, {})
    hist_fx_deval = float(hist.get(term, 0.0)) if country else 0.0          # D18
    util_hist_fx_deval = float(hist.get(term, 0.0)) if country else 0.0     # D19
    stress_fx_deval = max(hist_fx_deval, util_hist_fx_deval) * STRESS_FX_FACTOR  # D21
    base_fx_deval = stress_fx_deval if hedge_rate == 0 else min(stress_fx_deval, hedge_rate)  # D22
    fx_slippage = float(FX_SLIPPAGE.get(country, 0.0)) if country else 0.0  # D23
    if inp.rolled_hedge == "Roll" and rating in ROLL_RISK_HIGH:
        roll_high = float(ROLL_RISK_HIGH[rating].get(term, 0.0))            # D24
        roll_low = float(ROLL_RISK_LOW[rating].get(term, 0.0))              # D25
    else:
        roll_high = roll_low = 0.0
    selected_fx_high = base_fx_deval + fx_slippage + roll_high             # D26
    selected_fx_low = base_fx_deval + fx_slippage + roll_low               # D27

    # ---- Credit Stress Build Up ----
    stress_loss_rate = loss_rate * CREDIT_STRESS_FACTOR                     # D31
    min_tbl = (MIN_STRESS_LOSS_SELF_REPORTED
               if inp.data_input_source == "Self-reported"
               else MIN_STRESS_LOSS_OBSERVED)
    min_stress_loss = float(min_tbl.get(inp.segmentation, 0.0)) if inp.segmentation else 0.0  # D32
    selected_stress_loss = max(stress_loss_rate, min_stress_loss)          # D33

    # ---- LTGBV ----
    no_fx_ltgbv = 1.0 - selected_stress_loss                               # D36
    ltgbv_fx_high = no_fx_ltgbv / (1.0 + selected_fx_high)                 # D37
    ltgbv_fx_low = no_fx_ltgbv / (1.0 + selected_fx_low)                   # D38

    # ---- Advance on Principal / LTV ----
    no_fx_advance = no_fx_ltgbv * (1.0 + gross_interest)                   # D41
    ltv_fx_high = ltgbv_fx_high * (1.0 + gross_interest)                   # D42
    ltv_fx_low = ltgbv_fx_low * (1.0 + gross_interest)                     # D43

    return {
        "selected_term_months": term,
        "hist_fx_deval": hist_fx_deval,
        "util_hist_fx_deval": util_hist_fx_deval,
        "stress_fx_factor": STRESS_FX_FACTOR,
        "stress_fx_deval": stress_fx_deval,
        "base_fx_deval": base_fx_deval,
        "fx_slippage": fx_slippage,
        "roll_risk_high": roll_high,
        "roll_risk_low": roll_low,
        "selected_fx_high": selected_fx_high,
        "selected_fx_low": selected_fx_low,
        "credit_stress_factor": CREDIT_STRESS_FACTOR,
        "stress_loss_rate": stress_loss_rate,
        "min_stress_loss": min_stress_loss,
        "selected_stress_loss": selected_stress_loss,
        "no_fx_ltgbv": no_fx_ltgbv,
        "ltgbv_fx_high": ltgbv_fx_high,
        "ltgbv_fx_low": ltgbv_fx_low,
        "no_fx_advance_ltv": no_fx_advance,
        "ltv_fx_high": ltv_fx_high,
        "ltv_fx_low": ltv_fx_low,
    }
