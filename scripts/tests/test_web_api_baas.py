"""BaaS endpoints: the template round-trips through /api/analyse/baas to the
workbook's numbers, bad rows are reported per market, export and defaults."""

import io
import sys

import pandas as pd
from fastapi.testclient import TestClient
from openpyxl import load_workbook

sys.path.insert(0, "scripts")
import web_api.main as m  # noqa: E402

VALUATION = {"valuationDate": "2026-09-30"}


def _client():
    client = TestClient(m.app, base_url="https://testserver")
    assert client.post("/api/auth/dummy", json={"email": "a@lendable.io"}).status_code == 200
    return client


def _template_rows(client):
    res = client.get("/api/baas/template")
    assert res.status_code == 200, res.text
    df = pd.read_csv(io.StringIO(res.text))
    return df.astype(object).where(df.notna(), None).to_dict("records")


def test_template_round_trips_to_the_workbook_numbers():
    client = _client()
    rows = _template_rows(client)
    assert len(rows) == 8
    res = client.post("/api/analyse/baas", json={"rows": rows, "generalInputs": VALUATION})
    assert res.status_code == 200, res.text
    body = res.json()
    by_name = {mk["market"]: mk for mk in body["markets"]}
    assert all(mk["issues"] == [] for mk in body["markets"])
    # BaaS_Standard_UE.xlsx, Excel-recalculated (see test_baas_analysis GOLDEN).
    assert abs(by_name["Amp-RW"]["metrics"]["net_ue"] - 0.1845913388729094) < 1e-7
    assert abs(by_name["Arc-GH"]["metrics"]["gross_irr"] - 0.7310503363609315) < 1e-7
    assert by_name["Arc-GH"]["breakEven"] is None  # no fixed opex for Arc
    assert by_name["Amp-RW"]["breakEven"]["vehicles_to_break_even"] > 0
    assert len(body["cashflows"]) == 121
    assert body["cashflows"][0]["Date"] == "2026-09-30"
    assert set(body["cashflows"][0]) == {"Month", "Date", *by_name}


def test_bad_market_is_reported_and_the_rest_still_run():
    client = _client()
    rows = _template_rows(client)
    rows[0]["lifetime_months"] = 200
    rows[1]["fx_rate"] = "abc"
    res = client.post("/api/analyse/baas", json={"rows": rows, "generalInputs": VALUATION})
    assert res.status_code == 200, res.text
    markets = res.json()["markets"]
    assert "Lifetime must be 1-120" in markets[0]["issues"][0]
    assert "fx_rate is not a number" in markets[1]["issues"][0]
    assert markets[0]["metrics"] == {} and markets[2]["metrics"]["net_irr"] is not None


def test_missing_required_field_is_a_422_naming_it():
    client = _client()
    rows = [{k: v for k, v in r.items() if k != "pack_cost_usd"} for r in _template_rows(client)]
    res = client.post("/api/analyse/baas", json={"rows": rows})
    assert res.status_code == 422 and "pack_cost_usd" in res.json()["detail"]


def test_export_has_results_cash_flows_and_inputs():
    client = _client()
    body = {"rows": _template_rows(client), "generalInputs": VALUATION}
    res = client.post("/api/export/baas", json=body)
    assert res.status_code == 200, res.text
    wb = load_workbook(io.BytesIO(res.content))
    assert wb.sheetnames == ["Results", "Cash flows", "Inputs"]
    assert wb["Results"]["A3"].value == "market" and wb["Results"]["A4"].value == "Amp-RW"


def test_defaults_are_the_end_of_this_month():
    res = _client().get("/api/baas/defaults")
    assert res.status_code == 200
    assert res.json()["valuationDate"] == m._end_of_this_month().isoformat()


def test_saved_baas_mapping_restores_as_baas():
    assert m._infer_model_key({"market": "Market", "pack_cost_usd": "Pack"}) == "baas"
