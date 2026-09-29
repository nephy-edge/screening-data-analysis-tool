"""BaaS engine: one market's assumptions -> per-swap-unit (bike) daily unit
economics, a 120-month cash flow, gross / net IRR and break-even.

A formula-for-formula port of the UE sheet of BaaS_Standard_UE.xlsx
(Investments/BaaS/tools/build_standard_model.py), so results tie to that
workbook. Percent inputs are whole numbers (5 = 5%), as on its Inputs sheet.
"""

import calendar
import datetime
from dataclasses import MISSING, dataclass, fields

GRID_MONTHS = 120  # the workbook's cash-flow grid is months 0..120
PCT_FIELDS = (
    "salvage_pct",
    "commission_pct",
    "repair_maintenance_pct",
    "insurance_pct",
    "other_opex_pct",
    "wht_pct",
)


@dataclass
class BaasMarket:
    market: str
    currency: str
    fx_rate: float  # LCY per USD; 1 when the market is priced in USD
    price_per_swap: float  # LCY
    pack_cost_usd: float  # one swap pack
    pack_capacity_kwh: float
    lifetime_months: float  # months of revenue generation
    batteries_per_swap: float
    batteries_in_bike: float
    batteries_per_bike: float  # incl. spares
    station_capex_usd: float  # per bike deployed
    station_life_years: float
    swaps_per_day: float
    electricity_lcy_per_kwh: float
    station_rent_usd_month: float  # per bike
    interest_rate_pct: float
    facility_tenor_months: float
    facility_grace_months: float = 0.0
    upfront_fee_pct: float = 0.0
    wht_pct: float = 0.0
    hedging_pct: float = 0.0
    signup_fee: float = 0.0  # LCY
    signup_renewal_months: float = 1000.0  # 1000 = never renewed
    salvage_pct: float = 0.0  # % of pack cost
    station_install_usd: float = 0.0
    install_life_years: float = 1.0
    station_infra_usd: float = 0.0
    infra_life_years: float = 1.0
    station_capex_upfront: bool = False  # True: full station capex in month 0
    revenue_days_per_month: float = 30.0
    opex_days_per_month: float = 30.0
    commission_pct: float = 0.0  # % of usage revenue
    repair_maintenance_pct: float = 0.0  # % of usage revenue
    insurance_pct: float = 0.0  # % of pack cost p.a.
    battery_it_usd_month: float = 0.0
    other_opex_pct: float = 0.0  # % of usage revenue
    fixed_opex_usd: float = 0.0  # annual company-level fixed cost; 0 = not provided
    active_vehicles: float = 0.0
    batteries_on_road: float = 0.0


REQUIRED_FIELDS = [f.name for f in fields(BaasMarket) if f.default is MISSING]


def validate(m: BaasMarket) -> list[str]:
    """The workbook's Checks-sheet integrity rules, plus the divisors the
    engine needs non-zero. An empty list means the market can be run."""
    issues = []
    if not 1 <= m.lifetime_months <= GRID_MONTHS:
        issues.append(f"Lifetime must be 1-{GRID_MONTHS} months (got {m.lifetime_months:g}).")
    if m.fx_rate <= 0:
        issues.append("FX rate must be above 0.")
    elif m.currency.strip().upper() == "USD" and m.fx_rate != 1:
        issues.append(f"Currency is USD, so the FX rate must be 1 (got {m.fx_rate:g}).")
    for name in PCT_FIELDS:
        v = getattr(m, name)
        if not 0 <= v <= 100:
            issues.append(f"{name} must be between 0 and 100 (got {v:g}).")
    for name in ("batteries_per_swap", "batteries_per_bike", "station_life_years"):
        if getattr(m, name) <= 0:
            issues.append(f"{name} must be above 0.")
    if m.facility_grace_months + m.facility_tenor_months <= 0:
        issues.append("Facility grace + tenor must be above 0 months.")
    return issues


def _eomonth(d: datetime.date, months: int) -> datetime.date:
    """Excel EOMONTH."""
    y, mo = divmod(d.month - 1 + months, 12)
    y += d.year
    return datetime.date(y, mo + 1, calendar.monthrange(y, mo + 1)[1])


def xirr(cashflows: list[float], dates: list[datetime.date]) -> float | None:
    """Excel XIRR: the rate r with sum(cf / (1 + r) ** (days / 365)) = 0,
    Newton's method from Excel's default guess of 0.1, with a bisection
    fallback. None where Excel would error (no sign change, no root)."""
    if not (any(c > 0 for c in cashflows) and any(c < 0 for c in cashflows)):
        return None
    t = [(d - dates[0]).days / 365.0 for d in dates]

    def npv(r):
        return sum(c / (1 + r) ** ti for c, ti in zip(cashflows, t, strict=True))

    r = 0.1
    for _ in range(100):
        d_npv = sum(-ti * c / (1 + r) ** (ti + 1) for c, ti in zip(cashflows, t, strict=True))
        if d_npv == 0:
            break
        step = npv(r) / d_npv
        r -= step
        if r <= -1:
            break
        if abs(step) < 1e-12:
            return r
    lo, hi = -0.9999, 1000.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def run_market(m: BaasMarket, valuation_date: datetime.date) -> dict:
    """UE sheet column for one market. Raises ValueError if validate() fails."""
    issues = validate(m)
    if issues:
        raise ValueError(f"{m.market}: " + " ".join(issues))
    fx, life = m.fx_rate, m.lifetime_months

    # Capex per swap-unit (month 0)
    battery_capex = m.pack_cost_usd * m.batteries_per_bike / m.batteries_per_swap
    if m.station_capex_upfront:
        station = m.station_capex_usd
    else:
        station = m.station_capex_usd * life / (m.station_life_years * 12)
    install = (
        0.0
        if m.station_install_usd == 0
        else m.station_install_usd * life / (m.install_life_years * 12)
    )
    infra = (
        0.0 if m.station_infra_usd == 0 else m.station_infra_usd * life / (m.infra_life_years * 12)
    )
    total_capex = battery_capex + station + install + infra

    # Revenue per day. NB: like the workbook, salvage and insurance divide the
    # USD pack cost by FX.
    pct_in_vehicle = m.batteries_in_bike / m.batteries_per_bike
    usage = m.swaps_per_day * m.price_per_swap / fx
    signup = (
        (life // m.signup_renewal_months + 1) * m.signup_fee / fx / (life * 30) * pct_in_vehicle
    )
    salvage = m.salvage_pct / 100 * m.pack_cost_usd / fx / (life * 30)
    total_revenue = usage + signup + salvage

    # Operating direct costs per day
    opex = {
        "electricity": -m.electricity_lcy_per_kwh * m.pack_capacity_kwh * m.swaps_per_day / fx,
        "station_rent": -m.station_rent_usd_month / 30,
        "commission": -m.commission_pct / 100 * usage,
        "repair_maintenance": -m.repair_maintenance_pct / 100 * usage,
        "insurance": -m.insurance_pct / 100 * m.pack_cost_usd / fx / 360,
        "battery_it": -m.battery_it_usd_month / fx / 30,
        "other_opex": -m.other_opex_pct / 100 * usage,
    }
    total_opex = sum(opex.values())
    cm1 = total_revenue + total_opex

    # Monthly cash flows, month 0 = end of the valuation month
    cashflows = []
    for month in range(GRID_MONTHS + 1):
        if month == 0:
            revenue, cost = -total_capex, 0.0
        elif month == life:
            revenue = usage * m.revenue_days_per_month + m.pack_cost_usd / fx * m.salvage_pct / 100
        elif month <= life:
            revenue = usage * m.revenue_days_per_month
            if month % m.signup_renewal_months == 0:
                revenue += m.signup_fee / fx * pct_in_vehicle
        else:
            revenue = 0.0
        if month:
            cost = total_opex * m.opex_days_per_month if month <= life else 0.0
        cashflows.append(
            {
                "month": month,
                "date": _eomonth(valuation_date, month),
                "revenue": revenue,
                "opex": cost,
                "net": revenue + cost,
            }
        )
    dates = [c["date"] for c in cashflows]
    gross_irr = xirr([c["revenue"] for c in cashflows], dates)
    net_irr = xirr([c["net"] for c in cashflows], dates)

    interest = m.interest_rate_pct / 100 * (1 + m.wht_pct / 100)
    facility_fee = (
        m.upfront_fee_pct / 100 / ((m.facility_grace_months + m.facility_tenor_months) / 2) * 12
    )
    hedging = m.hedging_pct / 100
    financing = interest + facility_fee + hedging
    net_ue = None if net_irr is None else net_irr - financing

    return {
        "market": m.market,
        "battery_capex": battery_capex,
        "station_capex": station,
        "station_installation": install,
        "station_infrastructure": infra,
        "total_capex": total_capex,
        "usage_revenue": usage,
        "signup_revenue": signup,
        "salvage_revenue": salvage,
        "total_revenue": total_revenue,
        **opex,
        "total_opex": total_opex,
        "cm1": cm1,
        "cm1_pct": cm1 / total_revenue if total_revenue else None,
        "gross_irr": gross_irr,
        "net_irr": net_irr,
        "interest": interest,
        "facility_fee": facility_fee,
        "hedging": hedging,
        "financing_cost": financing,
        "net_ue": net_ue,
        "total_payments": sum(c["revenue"] for c in cashflows),
        "break_even": _break_even(m, total_capex, net_ue),
        "cashflows": cashflows,
    }


def _break_even(m: BaasMarket, total_capex: float, net_ue: float | None) -> dict | None:
    """Fleet needed for the net UE to cover the company's fixed opex. Uses
    the source workbooks' definition, net UE per bike = total capex x net
    UE %. None when fixed opex is not provided or net UE is not positive."""
    if m.fixed_opex_usd <= 0 or net_ue is None or net_ue <= 0:
        return None
    per_bike = total_capex * net_ue
    vehicles = m.fixed_opex_usd / per_bike
    batteries = vehicles * m.batteries_per_bike / m.batteries_per_swap
    return {
        "net_ue_per_bike_usd": per_bike,
        "vehicles_to_break_even": vehicles,
        "batteries_to_break_even": batteries,
        "vehicle_shortfall": vehicles - m.active_vehicles,
        "battery_shortfall": batteries - m.batteries_on_road,
    }
