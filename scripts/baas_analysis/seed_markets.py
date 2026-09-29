"""The 8 markets BaaS_Standard_UE.xlsx was seeded with (Ampersand Rwanda /
Kenya, Arc's six markets): the example rows of the upload template and the
fixture the tests tie out against the workbook."""

from .engine import BaasMarket

_AMP = dict(
    pack_cost_usd=653,
    pack_capacity_kwh=6.5,
    batteries_per_swap=1,
    batteries_in_bike=1,
    batteries_per_bike=1.4,
    station_capex_usd=250.0,
    station_life_years=7,
    repair_maintenance_pct=5,
    insurance_pct=2,
    other_opex_pct=10,
    facility_grace_months=12,
    facility_tenor_months=36,
    upfront_fee_pct=1.5,
    interest_rate_pct=11.5,
    wht_pct=15,
    hedging_pct=5.0,
)
_ARC = dict(
    currency="USD",
    fx_rate=1.0,
    pack_capacity_kwh=4.42,
    lifetime_months=46.1538,
    batteries_per_swap=2,
    batteries_in_bike=2,
    batteries_per_bike=3.5,
    station_capex_usd=110.649,
    station_life_years=7,
    station_capex_upfront=True,
    swaps_per_day=1.25,
    revenue_days_per_month=26,
    station_rent_usd_month=2.7692,
    facility_grace_months=12,
    facility_tenor_months=60,
    upfront_fee_pct=1.5,
    interest_rate_pct=10.0,
    wht_pct=15,
    hedging_pct=4.75,
)

SEED_MARKETS = [
    BaasMarket(
        market="Amp-RW", currency="RWF", fx_rate=1450.0, price_per_swap=2068.0,
        lifetime_months=70.0, swaps_per_day=1.68, electricity_lcy_per_kwh=110.0,
        station_rent_usd_month=0.493, fixed_opex_usd=4522, active_vehicles=4461,
        batteries_on_road=6149, **_AMP,
    ),
    BaasMarket(
        market="Amp-KE", currency="KES", fx_rate=130.0, price_per_swap=265.0,
        lifetime_months=67.2, swaps_per_day=1.25, electricity_lcy_per_kwh=13.44,
        station_rent_usd_month=1.754, fixed_opex_usd=1339, active_vehicles=1500,
        batteries_on_road=2100, **_AMP,
    ),
    BaasMarket(
        **{**_ARC, "pack_capacity_kwh": 2.88, "lifetime_months": 33.2308,
           "batteries_per_bike": 4.0, "station_capex_usd": 96.8182, "swaps_per_day": 1.73611},
        market="Arc-NBO", price_per_swap=1.7, pack_cost_usd=580, electricity_lcy_per_kwh=0.187,
    ),
    BaasMarket(
        **_ARC, market="Arc-KE72", price_per_swap=2.42, pack_cost_usd=716,
        electricity_lcy_per_kwh=0.193,
    ),
    BaasMarket(
        **_ARC, market="Arc-GH", price_per_swap=2.18, pack_cost_usd=696,
        electricity_lcy_per_kwh=0.178,
    ),
    BaasMarket(
        **{**_ARC, "station_capex_usd": 69.6104, "station_rent_usd_month": 3.0462},
        market="Arc-JB", price_per_swap=2.72, pack_cost_usd=752, electricity_lcy_per_kwh=0.128,
    ),
    BaasMarket(
        **{**_ARC, "station_rent_usd_month": 3.0462},
        market="Arc-UG", price_per_swap=2.52, pack_cost_usd=852, electricity_lcy_per_kwh=0.173,
    ),
    BaasMarket(
        **_ARC, market="Arc-TZ", price_per_swap=2.47, pack_cost_usd=754,
        electricity_lcy_per_kwh=0.086,
    ),
]  # fmt: skip
