"""Receivables-collateral LTV Calculator: a faithful port of the "Receivables"
sheet's per-deal waterfall (FX stress build-up -> credit stress build-up ->
LTGBV -> LTV / Advance on Principal) from "Copy of LTV Calculator v3.xlsx".

Lookup-table constants below were extracted directly from that workbook's
"Inputs" sheet. Two data-quality bugs found in the source workbook are
corrected here rather than reproduced:
  - Inputs!M38:M50 (the "Alt Lender Country" dropdown mirror) is broken in
    the source file (a duplicated "Saudi Arabia" entry, a #REF! error, and
    several blank/zero entries) - COUNTRIES below uses the clean list from
    Inputs!Y6:Z39 instead.
  - Most Receivables columns look up the sector-minimum floor via
    Inputs!$G$6:$H$10, which excludes the "Insurance" row (11) and would
    #N/A if selected - SECTOR_MIN_OBSERVED/_SELF_REPORTED below include it.
"""

SEGMENTATIONS = [
    "Asset Lending", "PayGo Solar", "Consumer Credit",
    "MSME Credit", "Payment Platforms", "Insurance",
]

# Sector-minimum stress loss floor, by segmentation. Insurance has no
# self-reported figure in the source workbook (Inputs!K11 is blank) - the
# calculator falls back to the Observed floor for that combination and
# surfaces a note explaining why, rather than raising or silently guessing.
SECTOR_MIN_OBSERVED = {
    "Asset Lending": 0.20, "PayGo Solar": 0.30, "Consumer Credit": 0.25,
    "MSME Credit": 0.15, "Payment Platforms": 0.10, "Insurance": 0.10,
}
SECTOR_MIN_SELF_REPORTED = {
    "Asset Lending": 0.30, "PayGo Solar": 0.40, "Consumer Credit": 0.30,
    "MSME Credit": 0.20, "Payment Platforms": 0.10,
}

DATA_INPUT_SOURCES = ["Observed", "Self-reported"]
FX_RISK_RATINGS = ["Green", "Orange", "Red"]
HEDGE_TYPES = ["Roll", "Term"]

# Tenor buckets (months) - the Receivables "Weighted Avg. Receivable Term"
# dropdown only allows these values; every lookup table below is keyed to
# this same tenor axis.
TENORS = [1, 2, 3, 6, 9, 12, 18, 24, 30, 36]

try:
    # config.py lives at the repo root, alongside app.py - reachable when
    # this module is imported as part of the running app (repo root is on
    # sys.path), but not under the test suite (pythonpath = scripts only).
    # Falling back to the same hardcoded defaults keeps this module usable
    # standalone either way.
    import config as _app_config
    FX_STRESS_FACTOR = _app_config.CREDIT_STRESS["fx_stress_factor"]
    CREDIT_STRESS_FACTOR = _app_config.CREDIT_STRESS["credit_stress_factor"]
except Exception:
    FX_STRESS_FACTOR = 1.2
    CREDIT_STRESS_FACTOR = 1.7

# 99th-percentile historical FX devaluation by country x tenor (decimal,
# e.g. 0.2714 = 27.14%), extracted from Inputs!Z6:AJ39.
COUNTRIES = [
    "Ghana", "Kenya", "Nigeria", "South Africa", "Indonesia", "Philippines",
    "Vietnam", "Uganda", "Zambia", "Senegal", "Myanmar", "Singapore",
    "Mexico", "India", "Egypt", "Thailand", "Malaysia", "Pakistan",
    "Bangladesh", "Cambodia", "Colombia", "Peru", "Brazil", "Euro",
    "Mozambique", "Tanzania", "Benin", "Togo", "Rwanda", "Uzbekistan",
    "Mongolia", "United Arab Emirates", "United Kingdom", "Saudi Arabia",
]

FX_DEVAL_TABLE = {
    "Ghana": [0.2713947991, 0.3578073615, 0.4235420174, 0.8018981127, 1.096278389, 1.245926291, 1.34150045, 1.344000274, 1.529170718, 1.678722425],
    "Kenya": [0.04112983152, 0.06588395329, 0.09320943844, 0.1474724745, 0.2085225559, 0.2787373372, 0.3772998389, 0.4026554777, 0.4670953728, 0.4526267706],
    "Nigeria": [0.656374515, 0.7366257122, 0.8485119269, 0.9799323379, 2.182629118, 2.28641375, 2.774916824, 2.861275372, 3.02655059, 3.144768569],
    "South Africa": [0.1125899561, 0.2029421256, 0.2586198821, 0.2838889607, 0.3427013068, 0.3739745866, 0.3717541308, 0.5059389551, 0.4380342499, 0.3895618428],
    "Indonesia": [0.0551014197, 0.1272727273, 0.1367268261, 0.1187354818, 0.1394233087, 0.1252139177, 0.1365267188, 0.1680348922, 0.1680746385, 0.1857113833],
    "Philippines": [0.04436294144, 0.0589713809, 0.07372699735, 0.118255951, 0.146653193, 0.1565494505, 0.2189038587, 0.2108211616, 0.1753968766, 0.2128415055],
    "Vietnam": [0.03089589494, 0.04590202073, 0.05507507896, 0.06871307071, 0.08577357742, 0.09118874973, 0.08792764267, 0.1020789803, 0.1206922314, 0.1189707308],
    "Uganda": [0.07115413971, 0.1096101906, 0.1647412168, 0.2375381113, 0.232745572, 0.1991772445, 0.2325584608, 0.2496513546, 0.2439800504, 0.2574611165],
    "Zambia": [0.2213691762, 0.3947855366, 0.5476510067, 0.6291579455, 0.8236196319, 0.7052631579, 0.8545647887, 0.9170039956, 1.15302358, 1.335042371],
    "Senegal": [0.0532988086, 0.06657243673, 0.08335189137, 0.1179951543, 0.1516264659, 0.1828644958, 0.2374252884, 0.1956981074, 0.1344348791, 0.131393736],
    "Myanmar": [0.1317297297, 0.1728571429, 0.1728571429, 0.2515243902, 0.3095748277, 0.3654912989, 0.594350568, 0.6230232558, 0.6242048875, 0.6259032729],
    "Singapore": [0.03106331763, 0.05013229241, 0.06125018626, 0.06364748675, 0.0684524253, 0.06426720803, 0.07358993754, 0.0849449963, 0.0523245497, 0.03923244352],
    "Mexico": [0.1122276401, 0.2569329972, 0.280093241, 0.2389729228, 0.2570890701, 0.2654264609, 0.3871480146, 0.4046234201, 0.2847853065, 0.3136164492],
    "India": [0.04181985033, 0.06268755006, 0.07087287458, 0.108027794, 0.1388685551, 0.1286260284, 0.1322742712, 0.1501011963, 0.1799626603, 0.1800477644],
    "Egypt": [0.5464234109, 0.967450945, 1.06606905, 1.072376941, 1.128426316, 1.359969054, 1.536535622, 1.608391608, 2.10527542, 2.232450767],
    "Thailand": [0.04690757997, 0.06787288154, 0.07849385108, 0.118979885, 0.1318330126, 0.1293551855, 0.2594724887, 0.2051366806, 0.1838198455, 0.2429112544],
    "Malaysia": [0.06593974832, 0.1111614449, 0.1444594595, 0.1926529024, 0.2044842939, 0.1678655784, 0.2334460438, 0.235851675, 0.1982324882, 0.1725005409],
    "Pakistan": [0.1130225328, 0.204438268, 0.2536100588, 0.2971801882, 0.3890385463, 0.5619000331, 0.8087851628, 0.8586444815, 0.8891013125, 0.8251962018],
    "Bangladesh": [0.09675516224, 0.1126710212, 0.1209439528, 0.2201669796, 0.2490118551, 0.2653380531, 0.2993499292, 0.3827763971, 0.411062771, 0.4352856824],
    "Cambodia": [0.01797214192, 0.02084960071, 0.02478826668, 0.02648382707, 0.02277976402, 0.02006275425, 0.02434983469, 0.02247605402, 0.02851693172, 0.02582577176],
    "Colombia": [0.1375410298, 0.1835379708, 0.2328647961, 0.2689492994, 0.3248061737, 0.3597595357, 0.4037824609, 0.4295672505, 0.3858651415, 0.4576068864],
    "Peru": [0.04294413556, 0.06052767714, 0.08146571353, 0.1230022629, 0.1403373993, 0.1508721571, 0.2233941133, 0.2210054869, 0.2428760255, 0.2407243807],
    "Brazil": [0.1364711463, 0.226412486, 0.2924628325, 0.3299664476, 0.4547467366, 0.4800762293, 0.5055357492, 0.5669228607, 0.7109324277, 0.7742465331],
    "Euro": [0.05308572931, 0.06584260734, 0.08109849577, 0.1186179577, 0.1517586361, 0.1828326483, 0.2374113081, 0.1954119183, 0.1341580783, 0.1309325388],
    "Mozambique": [0.1120696441, 0.1966761636, 0.2795901146, 0.527128906, 0.6514505119, 0.8262692419, 1.311561028, 1.115648044, 0.8224682552, 0.820125],
    "Tanzania": [0.07557398373, 0.1202706577, 0.1511150291, 0.1822033494, 0.1989016479, 0.2066324279, 0.2325008295, 0.259796529, 0.2400159628, 0.2671979347],
    "Benin": [0.0532988086, 0.06657243673, 0.08335189137, 0.1179951543, 0.1516264659, 0.1828644958, 0.2374252884, 0.1956981074, 0.1344348791, 0.131393736],
    "Togo": [0.0532988086, 0.06657243673, 0.08335189137, 0.1179951543, 0.1516264659, 0.1828644958, 0.2374252884, 0.1956981074, 0.1344348791, 0.131393736],
    "Rwanda": [0.06831131466, 0.08134479559, 0.08349527006, 0.1087190663, 0.1558307662, 0.1910805821, 0.2852068736, 0.3175590094, 0.3757331103, 0.3966785139],
    "Uzbekistan": [0.08323699422, 0.9523670874, 1.019102, 1.207417582, 1.488717156, 1.658307001, 1.90357069, 2.095377538, 2.250635642, 2.35785124],
    "Mongolia": [0.06205889385, 0.1002945932, 0.1398409326, 0.2420159681, 0.2436486541, 0.2452922041, 0.2815584416, 0.2783499036, 0.3218449062, 0.3307759529],
    "United Arab Emirates": [0.00021785899, 0.00024317102, 0.00024512928, 0.00027233857, 0.00029958059, 0.00029957716, 0.00030420218, 0.00020055683, 0.00019062169, 0.00021785306],
    "United Kingdom": [0.08201689263, 0.1055711527, 0.1093112904, 0.1748649101, 0.1912578314, 0.2342270111, 0.2588906928, 0.2402344179, 0.187947349, 0.1999298599],
    "Saudi Arabia": [0.00152003694, 0.00177394411, 0.00199547569, 0.00229247748, 0.00229296162, 0.00242686082, 0.00245326791, 0.00245284015, 0.00237339662, 0.00261319396],
}

# FX basis-risk slippage % by country (decimal), Inputs!AL6:AL50.
FX_SLIPPAGE = {
    "Ghana": 0.02, "Kenya": 0.02, "Nigeria": 0.10, "South Africa": 0.01,
    "Indonesia": 0.02, "Philippines": 0.02, "Vietnam": 0.05, "Uganda": 0.03,
    "Zambia": 0.05, "Senegal": 0.03, "Myanmar": 0.03, "Singapore": 0.00,
    "Mexico": 0.01, "India": 0.01, "Egypt": 0.02, "Thailand": 0.02,
    "Malaysia": 0.02, "Pakistan": 0.02, "Bangladesh": 0.02, "Cambodia": 0.02,
    "Colombia": 0.02, "Peru": 0.02, "Brazil": 0.02, "Euro": 0.00,
    "Mozambique": 0.00, "Tanzania": 0.00, "Benin": 0.03, "Togo": 0.03,
    "Rwanda": 0.02, "Uzbekistan": 0.02, "Mongolia": 0.02,
    "United Arab Emirates": 0.02, "United Kingdom": 0.02, "Saudi Arabia": 0.00,
}

# Roll-risk adjustment %, by FX Risk Rating bucket x tenor, Inputs!AR9:BA11
# (low end of range) and AR16:BA18 (high end of range).
ROLL_RISK_LOW = {
    "Green": [0.000, 0.000, 0.000, 0.000, 0.005, 0.005, 0.010, 0.010, 0.010, 0.010],
    "Orange": [0.000, 0.005, 0.005, 0.005, 0.010, 0.010, 0.015, 0.015, 0.015, 0.015],
    "Red": [0.020, 0.020, 0.025, 0.025, 0.035, 0.035, 0.050, 0.050, 0.050, 0.050],
}
ROLL_RISK_HIGH = {
    "Green": [0.000, 0.000, 0.005, 0.005, 0.010, 0.010, 0.020, 0.020, 0.020, 0.020],
    "Orange": [0.005, 0.010, 0.010, 0.010, 0.015, 0.015, 0.025, 0.025, 0.025, 0.025],
    "Red": [0.020, 0.020, 0.025, 0.025, 0.035, 0.035, 0.050, 0.050, 0.050, 0.050],
}


def nearest_tenor_bucket(months):
    """Snap a continuous month value onto the nearest allowed tenor bucket -
    mirrors the Receivables "Weighted Avg. Receivable Term" dropdown, which
    only accepts TENORS values."""
    if months is None:
        return TENORS[0]
    return min(TENORS, key=lambda t: abs(t - months))


def compute_ltv(
    gross_interest, term_months, loss_rate, country, macro_fx_risk,
    segmentation, hedge_rate, rolled_hedge, data_source,
):
    """Run the Receivables sheet's per-deal waterfall (rows 17-43) and return
    every intermediate figure plus the final LTGBV/LTV outputs."""
    tenor = nearest_tenor_bucket(term_months)
    idx = TENORS.index(tenor)
    six_month_idx = TENORS.index(6)

    notes = []

    # --- FX Stress Build Up ---
    historical_fx_deval = FX_DEVAL_TABLE[country][idx]
    utilized_fx_deval = (
        historical_fx_deval if tenor > 6 else FX_DEVAL_TABLE[country][six_month_idx]
    )
    stress_fx_factor = FX_STRESS_FACTOR
    stress_fx_deval = max(historical_fx_deval, utilized_fx_deval) * stress_fx_factor
    base_fx_deval = stress_fx_deval if hedge_rate == 0 else min(stress_fx_deval, hedge_rate)
    fx_slippage = FX_SLIPPAGE[country]
    if rolled_hedge == "Term":
        roll_risk_high = roll_risk_low = 0.0
    else:
        roll_risk_high = ROLL_RISK_HIGH[macro_fx_risk][idx]
        roll_risk_low = ROLL_RISK_LOW[macro_fx_risk][idx]
    selected_fx_deval_high = base_fx_deval + fx_slippage + roll_risk_high
    selected_fx_deval_low = base_fx_deval + fx_slippage + roll_risk_low

    # --- Credit Stress Build Up ---
    credit_stress_factor = CREDIT_STRESS_FACTOR
    stress_loss_rate = loss_rate * credit_stress_factor
    if data_source == "Observed":
        minimum_stress_loss = SECTOR_MIN_OBSERVED[segmentation]
    else:
        if segmentation not in SECTOR_MIN_SELF_REPORTED:
            notes.append(
                f"No self-reported sector-minimum floor is defined for '{segmentation}' "
                "in the source workbook - using the Observed floor instead."
            )
            minimum_stress_loss = SECTOR_MIN_OBSERVED[segmentation]
        else:
            minimum_stress_loss = SECTOR_MIN_SELF_REPORTED[segmentation]
    selected_stress_loss = max(stress_loss_rate, minimum_stress_loss)

    # --- LTGBV ---
    ltgbv_no_fx = 1 - selected_stress_loss
    ltgbv_high = ltgbv_no_fx / (1 + selected_fx_deval_high)
    ltgbv_low = ltgbv_no_fx / (1 + selected_fx_deval_low)

    # --- Advance on Principal (LTV) ---
    ltv_no_fx = ltgbv_no_fx * (1 + gross_interest)
    ltv_high = ltgbv_high * (1 + gross_interest)
    ltv_low = ltgbv_low * (1 + gross_interest)

    return {
        "tenor_bucket": tenor,
        "historical_fx_deval": historical_fx_deval,
        "utilized_fx_deval": utilized_fx_deval,
        "stress_fx_factor": stress_fx_factor,
        "stress_fx_deval": stress_fx_deval,
        "base_fx_deval": base_fx_deval,
        "fx_slippage": fx_slippage,
        "roll_risk_high": roll_risk_high,
        "roll_risk_low": roll_risk_low,
        "selected_fx_deval_high": selected_fx_deval_high,
        "selected_fx_deval_low": selected_fx_deval_low,
        "credit_stress_factor": credit_stress_factor,
        "stress_loss_rate": stress_loss_rate,
        "minimum_stress_loss": minimum_stress_loss,
        "selected_stress_loss": selected_stress_loss,
        "ltgbv_no_fx": ltgbv_no_fx,
        "ltgbv_high": ltgbv_high,
        "ltgbv_low": ltgbv_low,
        "ltv_no_fx": ltv_no_fx,
        "ltv_high": ltv_high,
        "ltv_low": ltv_low,
        "notes": notes,
    }
