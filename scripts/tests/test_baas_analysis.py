"""BaaS engine: golden tie-out to BaaS_Standard_UE.xlsx (Excel-recalculated
values, valuation date 30 Sep 2026) for all 8 seed markets, validation, and
a round-trip against the workbook itself when it is available locally."""

import datetime
import os

import pytest
from baas_analysis.engine import BaasMarket, _eomonth, run_market, validate, xirr
from baas_analysis.seed_markets import SEED_MARKETS
from openpyxl import load_workbook

VALUATION = datetime.date(2026, 9, 30)
REFERENCE = os.environ.get(
    "BAAS_REFERENCE_WORKBOOK",
    r"C:\Users\NephyNdwiga\Downloads\projects\Investments\BaaS\BaaS_Standard_UE.xlsx",
)

MARKETS = {m.market: m for m in SEED_MARKETS}

# BaaS_Standard_UE.xlsx UE sheet, recalculated by Excel (analysis/standard_recalc.json):
# total revenue/day, total opex/day, CM1/day, gross IRR, net IRR, financing, net UE, payments.
GOLDEN = {
    "Amp-RW": (2.3960275862068965, -1.2042762835249041, 1.1917513026819924, 1.0840100646018984,
               0.37434133887290943, 0.18975, 0.1845913388729094, 3909.1245977011517),
    "Amp-KE": (2.548076923076923, -1.280957264957265, 1.267119658119658, 1.194852125644684,
               0.4111074864864349, 0.18975, 0.22135748648643488, 4007.434615384613),
    "Arc-NBO": (2.951387, -1.0273060682666666, 1.9240809317333334, 0.756453549861908,
                0.14567490220069887, 0.1675, -0.021825097799301113, 1275.4718459999992),
    "Arc-KE72": (3.025, -1.1586316666666667, 1.8663683333333332, 0.8384925007820132,
                 0.23928800225257876, 0.1675, 0.07178800225257878, 2254.251000000001),
    "Arc-GH": (2.725, -1.0757566666666667, 1.6492433333333334, 0.7310503363609315,
               0.16783888936042785, 0.1675, 0.0003388893604278631, 1930.450999999999),
    "Arc-JB": (3.4, -0.80874, 2.59126, 0.9949236035346984,
               0.5628364264965056, 0.1675, 0.3953364264965056, 2680.7896000000014),
    "Arc-UG": (3.15, -1.0573649999999999, 2.092635, 0.6780168473720553,
               0.2198781549930572, 0.1675, 0.05237815499305723, 2165.7510000000016),
    "Arc-TZ": (3.0875, -0.5674566666666666, 2.5200433333333336, 0.7999961495399475,
               0.5146411240100861, 0.1675, 0.3471411240100861, 2262.5010000000016),
}  # fmt: skip
KEYS = ("total_revenue", "total_opex", "cm1", "gross_irr", "net_irr", "financing_cost", "net_ue",
        "total_payments")  # fmt: skip


@pytest.mark.parametrize("name", list(GOLDEN))
def test_matches_excel_workbook(name):
    out = run_market(MARKETS[name], VALUATION)
    for key, expected in zip(KEYS, GOLDEN[name], strict=True):
        # Excel's XIRR stops at ~1e-8; the per-day arithmetic is exact.
        assert out[key] == pytest.approx(expected, abs=1e-7), key


def test_cash_flow_grid_shape():
    out = run_market(MARKETS["Amp-RW"], VALUATION)
    cf = out["cashflows"]
    assert len(cf) == 121
    assert cf[0]["date"] == datetime.date(2026, 9, 30)
    assert cf[5]["date"] == datetime.date(2027, 2, 28)
    assert cf[0]["net"] == pytest.approx(-out["total_capex"])
    assert cf[70]["revenue"] > 0 and cf[71]["revenue"] == 0  # lifetime is 70 months


def test_eomonth_matches_excel():
    assert _eomonth(datetime.date(2026, 1, 31), 1) == datetime.date(2026, 2, 28)
    assert _eomonth(datetime.date(2027, 12, 15), 2) == datetime.date(2028, 2, 29)


def test_xirr_known_value_and_no_sign_change():
    dates = [datetime.date(2026, 1, 1), datetime.date(2027, 1, 1)]
    assert xirr([-100.0, 110.0], dates) == pytest.approx(0.10)
    assert xirr([100.0, 110.0], dates) is None


def test_break_even_uses_capex_times_net_ue():
    out = run_market(MARKETS["Amp-RW"], VALUATION)
    be = out["break_even"]
    per_bike = out["total_capex"] * out["net_ue"]
    assert be["net_ue_per_bike_usd"] == pytest.approx(per_bike)
    assert be["vehicles_to_break_even"] == pytest.approx(4522 / per_bike)
    assert be["batteries_to_break_even"] == pytest.approx(4522 / per_bike * 1.4)
    assert be["vehicle_shortfall"] == pytest.approx(4522 / per_bike - 4461)


def test_break_even_not_available_without_fixed_opex():
    # Arc has no fixed-opex figures - break-even is n/a, not a number.
    assert run_market(MARKETS["Arc-KE72"], VALUATION)["break_even"] is None


@pytest.mark.parametrize(
    "change, message",
    [
        ({"lifetime_months": 121}, "Lifetime must be 1-120"),
        ({"fx_rate": 0}, "FX rate must be above 0"),
        ({"fx_rate": 3700}, "Currency is USD"),
        ({"commission_pct": 150}, "commission_pct must be between 0 and 100"),
        ({"batteries_per_swap": 0}, "batteries_per_swap must be above 0"),
    ],
)
def test_validation_rejects_bad_inputs(change, message):
    bad = BaasMarket(**{**MARKETS["Arc-GH"].__dict__, **change})
    assert any(message in issue for issue in validate(bad))
    with pytest.raises(ValueError, match=message):
        run_market(bad, VALUATION)


def test_seed_markets_are_valid():
    assert all(validate(m) == [] for m in MARKETS.values())


# Inputs-sheet label -> engine field, for the round-trip below.
WORKBOOK_ROWS = {
    "Currency": "currency",
    "FX rate (LCY per USD)": "fx_rate",
    "Revenue per swap (LCY)": "price_per_swap",
    "User sign-up fee": "signup_fee",
    "Sign-up renewal tenor (months)": "signup_renewal_months",
    "Battery pack cost (USD)": "pack_cost_usd",
    "Battery pack capacity": "pack_capacity_kwh",
    "Maximum lifetime (months)": "lifetime_months",
    "Salvage value": "salvage_pct",
    "Batteries per swap": "batteries_per_swap",
    "Batteries in bike (per swap unit)": "batteries_in_bike",
    "Batteries per bike (total incl. spares)": "batteries_per_bike",
    "Station capex (USD per bike deployed)": "station_capex_usd",
    "Station lifetime": "station_life_years",
    "Station installation (USD/bike)": "station_install_usd",
    "Lifetime of installation": "install_life_years",
    "Station infrastructure (USD/bike)": "station_infra_usd",
    "Lifetime of infrastructure": "infra_life_years",
    "Station capex fully upfront? (1=Arc, 0=Amper allocated)": "station_capex_upfront",
    "# of swaps per day per user": "swaps_per_day",
    "Revenue days per month (monthly CF)": "revenue_days_per_month",
    "Opex days per month (monthly CF)": "opex_days_per_month",
    "Electricity expense (LCY per kWh)": "electricity_lcy_per_kwh",
    "Station rent (USD per bike per month)": "station_rent_usd_month",
    "Commission": "commission_pct",
    "Repair & maintenance": "repair_maintenance_pct",
    "Insurance": "insurance_pct",
    "Battery IT (cloud/SIM)": "battery_it_usd_month",
    "Other opex (incl. staff)": "other_opex_pct",
    "Facility grace period": "facility_grace_months",
    "Facility tenor": "facility_tenor_months",
    "Upfront fee": "upfront_fee_pct",
    "Interest rate": "interest_rate_pct",
    "WHT": "wht_pct",
    "Annual hedging cost": "hedging_pct",
}


@pytest.mark.skipif(not os.path.exists(REFERENCE), reason="BaaS reference workbook not available")
def test_round_trip_against_reference_workbook():
    """Read every market's inputs straight off the workbook's Inputs sheet and
    compare the engine with the workbook's own cached UE outputs."""
    wb = load_workbook(REFERENCE, data_only=True)
    inputs, ue = wb["Inputs"], wb["UE"]
    rows = {inputs.cell(r, 2).value: r for r in range(1, inputs.max_row + 1)}
    ue_rows = {ue.cell(r, 2).value: r for r in range(1, 60)}
    valuation = inputs["D4"].value.date()
    for i in range(8):
        value_col, ue_col = 4 + 2 * i, 4 + i
        kwargs = {
            f: inputs.cell(rows[label], value_col).value for label, f in WORKBOOK_ROWS.items()
        }
        kwargs["station_capex_upfront"] = kwargs["station_capex_upfront"] == 1
        market = BaasMarket(market=ue.cell(3, ue_col).value, **kwargs)
        out = run_market(market, valuation)
        for label, key in [
            ("Total revenue per day", "total_revenue"),
            ("Total operating direct costs", "total_opex"),
            ("Gross IRR (p.a.)", "gross_irr"),
            ("Net IRR before financing (p.a.)", "net_irr"),
            ("Net UE after financing (p.a.)", "net_ue"),
        ]:
            cached = ue.cell(ue_rows[label], ue_col).value
            assert out[key] == pytest.approx(cached, abs=1e-7), (market.market, key)
