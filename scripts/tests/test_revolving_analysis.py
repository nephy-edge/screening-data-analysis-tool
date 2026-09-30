"""Revolving Lending (RCL) engine: the template's Outputs ratios, the roll-rate
/ ECL build (full buckets and the Outputs-columns approximation), and a
round-trip against rcl_rollrate_ecl_3.xlsx's own cached values when that
reference workbook is available locally."""

import io
import os
import sys

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from revolving_analysis.engine import RevolvingInputs, run
from revolving_analysis.monthly_input import REQUIRED_COLUMNS

sys.path.insert(0, "scripts")
import web_api.main as m  # noqa: E402

REFERENCE = os.environ.get(
    "RCL_REFERENCE_WORKBOOK",
    r"C:\Users\NephyNdwiga\Downloads\projects\Investments\revolving\rcl_rollrate_ecl_3.xlsx",
)


def _monthly(**cols):
    """Monthly rows with every required Outputs field (zero unless given);
    principal_outstanding defaults to active + PAR 1-90."""
    n = len(next(iter(cols.values())))
    base = {c: [0.0] * n for c in REQUIRED_COLUMNS}
    base["report_month"] = pd.date_range("2025-01-01", periods=n, freq="MS")
    base.update(cols)
    df = pd.DataFrame(base)
    if "principal_outstanding" not in cols:
        df["principal_outstanding"] = df[
            [
                "principal_outstanding-active",
                "principal_outstanding-par30-60",
                "principal_outstanding-par60-90",
            ]
        ].sum(axis=1)
    return df


def _ratios(result):
    return {r["row"]: r["value"] for r in result["ratios"]}


def _reference_frame(with_buckets=True):
    """The workbook's Monthly Report, mapped onto the Outputs fields."""
    src = pd.read_excel(REFERENCE, sheet_name="Monthly Report", nrows=36)
    df = pd.DataFrame(
        {
            "report_month": src["MONTH_MT"],
            "number_of_customers": src["N_BORROWERS"],
            "total_credit_line": src["CREDIT_LIMIT_TOTAL"],
            "interest_collected": src["REVENUE_COLLECTED"],
            "principal_collected": src["PRINCIPAL_COLLECTED"],
            "principal_issued": src["PRINCIPAL_ISSUED"],
            "principal_outstanding": src["PRINCIPAL_OUTSTANDING"],
            "principal_outstanding-active": src["BAL_NEW"] + src["BAL_CURRENT"],
            "principal_outstanding-par30-60": src["BAL_PAR_1_30"] + src["BAL_PAR_31_60"],
            "principal_outstanding-par60-90": src["BAL_PAR_61_90"],
            "principal_written-off": src["PRINCIPAL_WRITTEN_OFF"],
        }
    )
    if with_buckets:
        extra = [c for c in src.columns if c.startswith("BAL_")] + [
            "DUP_LOAN_MONTHS",
            "MONTHS_FILLED_BY_CARRYFWD",
            "UNEXPLAINED",
        ]
        df = pd.concat([df, src[extra]], axis=1)
    return df


def test_outputs_ratio_block_reproduces_the_template_sample():
    """The template's two sample rows (Outputs A2:K3). Row 3 has no customer
    count or credit line; every other field matches row 2, so it gets row 2's."""
    df = _monthly(
        number_of_customers=[100.0, 100.0],
        total_credit_line=[30e6, 30e6],
        interest_collected=[100_000.0, 100_000.0],
        principal_collected=[10e6, 10e6],
        principal_issued=[1e6, 1e6],
        principal_outstanding=[20e6, 20e6],
        **{
            "principal_outstanding-active": [18e6, 18e6],
            "principal_outstanding-par30-60": [1e6, 1e6],
            "principal_outstanding-par60-90": [0.5e6, 0.5e6],
            "principal_written-off": [200_000.0, 200_000.0],
        },
    )
    r = _ratios(run(df, RevolvingInputs(ratio_window_months=2)))
    assert r[9] == pytest.approx(2.197802198)  # Expected Tenor (m)
    assert r[10] == pytest.approx(0.005)  # Interest Rate (m)
    assert r[11] == pytest.approx(0.01098901099)  # Interest Rate (overall)
    assert r[15] == pytest.approx(0.9)  # Active
    assert r[16] == pytest.approx(0.05)  # PAR30
    assert r[17] == pytest.approx(0.02777777778)  # PAR60 = J / H
    assert r[18] == pytest.approx(0.025)  # PAR90 = J / G
    assert r[19] == pytest.approx(0.01)  # Write offs
    assert r[24] == pytest.approx(0.6666666667)  # % of credit line utilised


def _full_by_hand():
    df = _monthly(
        **{
            "principal_outstanding-active": [200.0, 180.0, 150.0],
            "principal_outstanding-par30-60": [30.0, 30.0, 20.0],
            "principal_outstanding-par60-90": [10.0, 5.0, 8.0],
            "principal_written-off": [0.0, 5.0, 4.0],
        },
        principal_outstanding=[250.0, 223.0, 182.0],
    )
    buckets = {
        "BAL_NEW": [100.0, 0.0, 0.0],
        "BAL_CURRENT": [100.0, 180.0, 150.0],
        "BAL_PAR_1_30": [20.0, 20.0, 10.0],
        "BAL_PAR_31_60": [10.0, 10.0, 10.0],
        "BAL_PAR_61_90": [10.0, 5.0, 8.0],
        "BAL_PAR_91_120": [10.0, 8.0, 4.0],
        "BAL_DEFAULT": [0.0, 0.0, 0.0],
    }
    return df.assign(**buckets)


def test_full_roll_rates_chain_and_ecl_by_hand():
    res = run(_full_by_hand(), RevolvingInputs(roll_rate_window="ALL", min_outstanding_in_sample=0))
    assert res["eclSummary"]["method"] == "full"
    rates = res["rollRates"]
    # Current + New -> PAR 1-30: 20 / (100 + 100), then 10 / 180; ALL averages both.
    assert rates.loc["Current + New → PAR 1-30", "2025-01"] == pytest.approx(0.1)
    assert rates.loc["Current + New → PAR 1-30", "ALL"] == pytest.approx((0.1 + 10 / 180) / 2)
    # PAR 91-120 -> Written off uses next month's write-off flow: 5 / 10, 4 / 8.
    assert rates.loc["PAR 91-120 → Written off", "SELECTED"] == pytest.approx(0.5)
    assert np.isnan(rates.loc["PAR 91-120 → Written off", "2025-03"])  # no next month
    lf = res["lossFactors"]["Loss factor"]
    links = rates["SELECTED"].to_numpy()
    assert lf["PAR 1-30"] == pytest.approx(links[1] * links[2] * links[3] * links[4])
    ecl = res["ecl"].set_index("Bucket")["ECL"]
    assert ecl["Current"] == pytest.approx(150 * lf["Current + New"])
    assert res["eclSummary"]["eclValid"]


def test_coarse_model_runs_on_the_outputs_columns_alone():
    df = _monthly(
        **{
            "principal_outstanding-active": [200.0, 180.0, 150.0, 140.0],
            "principal_outstanding-par30-60": [30.0, 40.0, 20.0, 10.0],
            "principal_outstanding-par60-90": [10.0, 12.0, 8.0, 6.0],
            "principal_written-off": [0.0, 1.0, 5.0, 6.0],
        },
        principal_outstanding=[250.0, 240.0, 190.0, 170.0],
    )
    res = run(df, RevolvingInputs(roll_rate_window="ALL", min_outstanding_in_sample=0))
    assert res["eclSummary"]["method"] == "coarse"
    rates = res["rollRates"]
    # Active -> PAR 1-60: next month's 1-60 over this month's active.
    assert rates.loc["Active → PAR 1-60", "ALL"] == pytest.approx(
        np.mean([40 / 200, 20 / 180, 10 / 150])
    )
    # PAR 61-90 -> write-off pairs month t with t+2: 5 / 10 and 6 / 12.
    wo = rates.loc["PAR 61-90 → Written off (2 months on)"]
    assert wo["2025-01"] == pytest.approx(0.5) and wo["2025-02"] == pytest.approx(0.5)
    assert np.isnan(wo["2025-03"])
    ecl = res["ecl"].set_index("Bucket")
    # The remainder (G - H - I - J = 14) is carried at loss factor 1.
    assert ecl.loc["91+ and defaulted stock (G − H − I − J)", "ECL"] == pytest.approx(14.0)
    assert any("Approximate roll rates" in w for w in res["warnings"])


def test_ecl_is_withheld_when_buckets_do_not_tie():
    df = _full_by_hand()
    df.loc[2, "principal_outstanding"] = 999.0
    res = run(df, RevolvingInputs())
    assert res["checks"].loc["Buckets tie to total outstanding", "2025-03"] == "CHECK"
    assert res["eclSummary"]["totalEcl"] is None
    assert _ratios(res)[20] is None
    assert any("CHECK" in w for w in res["warnings"])


def test_as_of_month_uses_no_later_data():
    df = _monthly(
        **{
            "principal_outstanding-active": [100.0, 90.0, 80.0, 70.0],
            "principal_outstanding-par30-60": [5.0, 10.0, 20.0, 40.0],
        },
        principal_issued=[10.0] * 4,
    )
    cut = run(df, RevolvingInputs(as_of_month="2025-03", min_outstanding_in_sample=0))
    alone = run(df.iloc[:3], RevolvingInputs(min_outstanding_in_sample=0))
    pd.testing.assert_frame_equal(cut["rollRates"], alone["rollRates"])
    assert cut["eclSummary"] == alone["eclSummary"]
    assert cut["availableMonths"][-1] == "2025-04"


def test_repeated_month_is_refused():
    df = _monthly(**{"principal_outstanding-active": [1.0, 1.0]})
    df["report_month"] = pd.Timestamp("2025-01-15")
    with pytest.raises(ValueError, match="repeated: 2025-01"):
        run(df, RevolvingInputs())


needs_reference = pytest.mark.skipif(
    not os.path.exists(REFERENCE), reason="reference workbook not available"
)


@needs_reference
def test_round_trip_against_the_reference_workbook():
    res = run(_reference_frame(), RevolvingInputs())
    assert res["eclSummary"]["method"] == "full"
    wb = load_workbook(REFERENCE, data_only=True)
    ecl_ws, loss_ws = wb["Roll Rate ECL"], wb["Loss Rate LTV"]

    assert (res["checks"].loc["Buckets tie to total outstanding"] == "OK").all()
    rates = res["rollRates"]
    for i, excel_row in enumerate(range(29, 34)):
        for j, month in enumerate(res["availableMonths"][:-1]):  # C..AK
            want = ecl_ws.cell(row=excel_row, column=3 + j).value
            got = rates.iloc[i][month]
            assert np.isnan(got) if want in (None, "") else got == pytest.approx(want)
        for w, col in zip(["T3", "T6", "T12", "ALL", "SELECTED"], "NOPQR", strict=True):
            assert rates.iloc[i][w] == pytest.approx(ecl_ws[f"A{col}{excel_row}"].value)

    lf = res["lossFactors"]["Loss factor"]
    for excel_row, bucket in zip(range(39, 44), lf.index, strict=True):
        assert lf[bucket] == pytest.approx(ecl_ws[f"C{excel_row}"].value)
    ecl = res["ecl"]["ECL"].to_numpy()
    assert ecl[:6] == pytest.approx([ecl_ws[f"E{r}"].value for r in range(48, 54)])
    assert ecl[6] == pytest.approx(ecl_ws["E55"].value)
    s = res["eclSummary"]
    assert s["performingEcl"] == pytest.approx(ecl_ws["E54"].value)
    assert s["totalEcl"] == pytest.approx(ecl_ws["E57"].value)
    assert s["coveragePerforming"] == pytest.approx(ecl_ws["E58"].value)
    assert s["coverageTotal"] == pytest.approx(ecl_ws["E59"].value)

    dist = res["distribution"]
    for col, basis in zip("BCD", ["Naive", "Blended", "Lagged"], strict=True):
        for excel_row, stat in _STAT_ROWS.items():
            want = loss_ws[f"{col}{excel_row}"].value
            assert dist.loc[stat, basis] == pytest.approx(want), (basis, stat)

    r = _ratios(res)
    assert r[12] == pytest.approx(loss_ws["C49"].value)  # blended mean
    assert r[13] == pytest.approx(loss_ws["C57"].value)  # blended stress percentile


@needs_reference
def test_reference_without_buckets_falls_back_to_the_approximation():
    """Same data, Outputs columns only: the 3-link chain. Reads lower than the
    full model (1,663.1M) because the 1-60 bucket hides the 1-30 -> 31-60 roll."""
    res = run(_reference_frame(with_buckets=False), RevolvingInputs())
    s = res["eclSummary"]
    assert s["method"] == "coarse"
    assert s["totalEcl"] == pytest.approx(1_432_530_714, rel=1e-6)
    # Everything that doesn't depend on roll rates is unchanged.
    full = _ratios(run(_reference_frame(), RevolvingInputs()))
    coarse = _ratios(res)
    for row in (9, 10, 11, 15, 16, 17, 18, 19, 22, 23, 24):
        assert coarse[row] == pytest.approx(full[row])


# 'Loss Rate LTV' rows 48-57 (row 52, the coefficient of variation, isn't carried).
_STAT_ROWS = {
    48: "Months in sample",
    49: "Mean",
    50: "Median",
    51: "Standard deviation",
    53: "Minimum",
    54: "Maximum",
    55: "25th percentile",
    56: "75th percentile",
    57: "Stress percentile",
}


def _client():
    client = TestClient(m.app, base_url="https://testserver")
    assert client.post("/api/auth/dummy", json={"email": "a@lendable.io"}).status_code == 200
    return client


def _rows():
    df = _monthly(
        number_of_customers=[10.0, 12.0, 13.0],
        total_credit_line=[1000.0, 1100.0, 1200.0],
        interest_collected=[5.0, 6.0, 7.0],
        principal_collected=[50.0, 60.0, 70.0],
        principal_issued=[100.0, 80.0, 90.0],
        **{
            "principal_outstanding-active": [100.0, 170.0, 240.0],
            "principal_outstanding-par30-60": [0.0, 5.0, 8.0],
            "principal_written-off": [0.0, 0.0, 1.0],
        },
    )
    df["report_month"] = df["report_month"].dt.strftime("%Y-%m-%d")
    return df.to_dict("records")


def test_analyse_endpoint_returns_both_tabs():
    res = _client().post("/api/analyse/revolving", json={"rows": _rows()})
    assert res.status_code == 200, res.text
    body = res.json()
    assert list(body["facts"][0]) == REQUIRED_COLUMNS
    assert [r["row"] for r in body["ratios"]][:3] == [9, 10, 11]
    assert body["rollRates"][0]["Transition"] == "Active → PAR 1-60"
    assert body["eclSummary"]["asOfMonth"] == "2025-03"
    assert body["eclSummary"]["method"] == "coarse"


def test_trailing_blank_rows_are_ignored():
    """The Monthly Report tab ends with summary rows that are blank in every
    mapped field; they used to fail the run as months with no report_month."""
    rows = _rows() + [{k: None for k in _rows()[0]}, {k: "" for k in _rows()[0]}]
    res = _client().post("/api/analyse/revolving", json={"rows": rows})
    assert res.status_code == 200, res.text
    assert res.json()["summary"]["rows"] == 3


def test_a_row_with_data_but_no_month_is_still_refused():
    rows = _rows()
    rows[1]["report_month"] = None
    res = _client().post("/api/analyse/revolving", json={"rows": rows})
    assert res.status_code == 422 and "report_month" in res.json()["detail"]


def test_missing_field_is_a_422_naming_it():
    rows = [{k: v for k, v in r.items() if k != "principal_written-off"} for r in _rows()]
    res = _client().post("/api/analyse/revolving", json={"rows": rows})
    assert res.status_code == 422 and "principal_written-off" in res.json()["detail"]


def test_export_writes_outputs_with_live_formulas():
    res = _client().post("/api/export/revolving", json={"rows": _rows()})
    assert res.status_code == 200, res.text
    wb = load_workbook(io.BytesIO(res.content))
    assert wb.sheetnames == [
        "Outputs",
        "Transition Matrices",
        "Standard Metrics",
        "Backtest & Stress",
        "Facility",
        "Inputs",
    ]
    ws = wb["Outputs"]
    assert ws["K1"].value == "principal_written-off"
    # 3 fact rows -> as-of row 4, "UE / LTV" header at row 9, B9 (tenor) at row 10.
    assert ws["A9"].value == "UE / LTV"
    assert ws["B10"].value == '=IFERROR(1/((E4+D4-F4)/AVERAGE(G3:G4)),"")'
    assert ws["B16"].value == '=IFERROR(H4/G4,"")'


def test_saved_revolving_mapping_restores_as_revolving():
    mapping = {"report_month": "m", "principal_outstanding-active": "a"}
    assert m._infer_model_key(mapping) == "revolving"
    assert m._infer_model_key({"Loan ID": "id"}) == "lending"


# --- Base-template additions: roll-forward, standard metrics, UE, early
# warnings, backtest, stress, LGD from recoveries, facility / covenants.

SCENARIOS = [
    {"name": "Base", "rollRateMultiplier": 1.0, "lgdMultiplier": 1.0},
    {"name": "Stress", "rollRateMultiplier": 1.25, "lgdMultiplier": 1.1},
    {"name": "Severe", "rollRateMultiplier": 1.5, "lgdMultiplier": 1.25},
]


@needs_reference
def test_roll_forward_reproduces_the_source_unexplained_column():
    checks = run(_reference_frame(), RevolvingInputs())["checks"]
    agree = checks.loc["Roll-forward agrees with source UNEXPLAINED"].tolist()
    assert agree[0] == "" and set(agree[1:]) == {"OK"}


@needs_reference
def test_backtest_matches_the_out_of_sample_check():
    """Same figures as the hand backtest: forecast write-offs vs actual."""
    full = run(_reference_frame(), RevolvingInputs())["backtestSummary"]
    assert (full["window"], full["months"]) == ("t+1..t+5", 19)
    assert full["pooledRatio"] == pytest.approx(0.913, abs=5e-4)
    coarse = run(_reference_frame(with_buckets=False), RevolvingInputs())["backtestSummary"]
    assert (coarse["window"], coarse["months"]) == ("t+2..t+4", 20)
    assert coarse["pooledRatio"] == pytest.approx(0.904, abs=5e-4)


@needs_reference
def test_standard_metrics_on_the_reference_book():
    res = run(_reference_frame(), RevolvingInputs())
    std = {m["key"]: m["value"] for m in res["standardMetrics"]}
    assert std["par91_total"] == pytest.approx(0.1763, abs=5e-5)  # template "PAR90" is 2.97%
    assert std["principal_tenor"] == pytest.approx(3.507, abs=5e-4)
    assert std["defaulted_share"] == pytest.approx(0.1489, abs=5e-5)


@needs_reference
def test_base_scenario_reproduces_the_unstressed_ecl_and_stress_is_worse():
    res = run(_reference_frame(), RevolvingInputs(scenarios=SCENARIOS))
    base, stress, severe = res["stress"]
    assert base["totalEcl"] == pytest.approx(res["eclSummary"]["totalEcl"])
    assert base["totalEcl"] < stress["totalEcl"] < severe["totalEcl"]
    assert base["advanceRate"] > stress["advanceRate"] > severe["advanceRate"]


def test_stressed_links_are_capped_at_100_percent():
    from revolving_analysis.backtest_stress import stressed_links

    assert stressed_links([0.5, 0.9, 1.02], 1.5, cap=1.0) == [0.75, 1.0, 1.02]


def _small_book():
    return _monthly(
        interest_collected=[5.0, 6.0, 8.0, 9.0, 9.0],
        principal_collected=[20.0, 25.0, 30.0, 30.0, 35.0],
        principal_issued=[100.0, 30.0, 30.0, 30.0, 30.0],
        total_credit_line=[400.0] * 5,
        **{
            "principal_outstanding-active": [90.0, 90.0, 85.0, 80.0, 70.0],
            "principal_outstanding-par30-60": [5.0, 5.0, 8.0, 10.0, 15.0],
            "principal_outstanding-par60-90": [0.0, 1.0, 2.0, 3.0, 5.0],
            "principal_written-off": [0.0, 0.0, 1.0, 2.0, 4.0],
        },
        principal_outstanding=[100.0, 100.0, 100.0, 100.0, 100.0],
    )


def test_standard_metrics_and_unit_economics_by_hand():
    res = run(
        _small_book(),
        RevolvingInputs(cost_of_funds_pct=0.12, opex_pct=0.06, min_outstanding_in_sample=0),
    )
    std = {m["key"]: m["value"] for m in res["standardMetrics"]}
    assert std["par91_total"] == pytest.approx((100 - 70 - 15 - 5) / 100)
    assert std["par61_total"] == pytest.approx((100 - 70 - 15) / 100)
    assert std["payment_rate"] == pytest.approx(35 / 100)
    assert std["principal_tenor"] == pytest.approx(100 / 35)
    assert std["defaulted_share"] is None  # needs the bucket balances
    ue = {r["item"]: r["monthly"] for r in res["unitEconomics"]}
    # Window 2: (9 + 9) / (100 + 100); write-offs 4 / 100; costs 18% / 12.
    assert ue["Net yield"] == pytest.approx(0.09 - 0.04 - 0.015)


def test_early_warning_flags_a_deteriorating_metric():
    res = run(
        _small_book(),
        RevolvingInputs(
            early_warning_lookback_months=3,
            early_warning_threshold=0.2,
            min_outstanding_in_sample=0,
        ),
    )
    flags = {e["key"]: e for e in res["earlyWarnings"]}
    # PAR 91+ by month is 5%, 4%, 5%, 7%, 10%: now 10% vs mean(4%, 5%, 7%).
    assert flags["par91_total"]["flag"]
    assert flags["par91_total"]["prior"] == pytest.approx((0.04 + 0.05 + 0.07) / 3)


def test_covenants_pass_breach_and_headroom():
    cov = [
        {"metric": "par91_total", "operator": "<=", "limit": 0.08},
        {"metric": "payment_rate", "operator": ">=", "limit": 0.30},
        {"metric": "defaulted_share", "operator": "<=", "limit": 0.1},
    ]
    res = run(_small_book(), RevolvingInputs(covenants=cov, min_outstanding_in_sample=0))
    par, pay, dft = res["covenants"]
    assert (par["status"], par["headroom"]) == ("BREACH", pytest.approx(0.08 - 0.10))
    assert (pay["status"], pay["headroom"]) == ("PASS", pytest.approx(0.35 - 0.30))
    assert dft["status"] == "N/A"  # needs the bucket balances


def test_lgd_from_recoveries():
    df = _small_book().assign(recoveries=[0.0, 0.0, 0.0, 1.0, 1.75])
    res = run(df, RevolvingInputs(lgd_basis="Recoveries", min_outstanding_in_sample=0))
    assert res["lgdUsed"] == pytest.approx(1 - 2.75 / 7)
    with pytest.raises(ValueError, match="recoveries column"):
        run(_small_book(), RevolvingInputs(lgd_basis="Recoveries"))


def test_api_rejects_an_unknown_covenant_metric():
    body = {
        "rows": _rows(),
        "generalInputs": {
            "covenants": [{"metric": "no_such_metric", "operator": "<=", "limit": 1}]
        },
    }
    res = _client().post("/api/analyse/revolving", json=body)
    assert res.status_code == 422 and "no_such_metric" in res.json()["detail"]


def test_defaults_endpoint_carries_scenarios_and_covenants():
    d = _client().get("/api/revolving/defaults").json()
    assert [s["name"] for s in d["scenarios"]] == ["Base", "Stress", "Severe"]
    assert d["covenants"][0]["metric"] == "par91_total"
    assert d["costOfFundsPct"] == pytest.approx(0.13)


def test_engine_defaults_come_from_config_toml():
    """No tunable is hard-coded in the engine: every RevolvingInputs default,
    the averaging windows and the approximation's lag are config.toml's."""
    from dataclasses import fields

    from revolving_analysis.settings import DEFAULTS, WINDOW_LENGTHS
    from revolving_analysis.transition_matrices import COARSE_SPEC

    defaults = RevolvingInputs()
    for f in fields(RevolvingInputs):
        # scenarios/covenants are converted to the engine's key names; checked below.
        if f.name in DEFAULTS and f.name not in ("scenarios", "covenants"):
            assert getattr(defaults, f.name) == DEFAULTS[f.name], f.name
    assert WINDOW_LENGTHS == {k: (v or None) for k, v in DEFAULTS["roll_rate_windows"].items()}
    assert COARSE_SPEC.edges[-1][4] == DEFAULTS["approx_writeoff_lag_months"]
    assert [s["name"] for s in defaults.scenarios] == [s["name"] for s in DEFAULTS["scenarios"]]


def test_options_endpoint_serves_windows_and_covenant_metrics():
    opts = _client().get("/api/revolving/options").json()
    assert opts["rollRateWindows"] == ["T3", "T6", "T12", "ALL"]
    assert {"key": "par91_total", "label": "PAR 91+ incl. defaulted stock"} in opts[
        "covenantMetrics"
    ]


def test_api_rejects_a_window_not_in_config():
    res = _client().post(
        "/api/analyse/revolving", json={"rows": _rows(), "generalInputs": {"rollRateWindow": "T24"}}
    )
    assert res.status_code == 422 and "T3, T6, T12, ALL" in res.json()["detail"]
