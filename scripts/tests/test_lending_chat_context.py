"""Ask AI (Lending) must see every figure the React screens compute, not just
the Cohorts table: before this, a question about "Advance on GBV (No FX)" got
"that isn't in the summary" even though the LTV tab showed it. It must also
quote the tabs at the choices on screen, and never carry loan rows."""

import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, "scripts")
import web_api.main as m  # noqa: E402


def _rows():
    # Three matured cohorts of 12 loans (above the 10-loan threshold), one
    # duplicated Loan ID so the loan-level duplicate check fires.
    rows = []
    for month in ("2024-01", "2024-02", "2024-03"):
        for i in range(12):
            rows.append(
                {
                    "Loan ID": f"LN{month}-{i}",
                    "Disbursement Date": f"{month}-05",
                    "Expected Completion Date": f"{month}-05".replace("2024", "2025"),
                    "Principal Value": 1000.0,
                    "Expected Interest": 200.0,
                    "Expected Fee": 50.0,
                    "Total Paid": 1100.0 if i % 3 else 400.0,
                    "Total Due": 1250.0,
                }
            )
    rows[1]["Loan ID"] = rows[0]["Loan ID"]
    return rows


@pytest.fixture
def context(monkeypatch):
    """Run /api/ai/chat/lending with the model stubbed, returning the context."""
    seen = {}

    def fake_chat(question, context, history):
        seen["context"] = context
        return "ok"

    monkeypatch.setattr(m, "_ask_data_chatbot", fake_chat)
    client = TestClient(m.app, base_url="https://testserver")
    assert client.post("/api/auth/dummy", json={"email": "a@lendable.io"}).status_code == 200

    def run(**extra):
        body = {
            "rows": _rows(),
            "generalInputs": {"extractionDate": "2026-08-31"},
            "question": "why?",
            **extra,
        }
        r = client.post("/api/ai/chat/lending", json=body)
        assert r.status_code == 200, r.text
        return seen["context"]

    return run


def test_context_carries_every_tab(context):
    text = context()
    for section in (
        "## Summary tab",
        "## Unit Economics tab - Overview",
        "## LTV Analysis tab - LTV Inputs",
        "## LTV Analysis tab - LTV Calculator",
        "## Unit Economics tab - UE Model sub-tab",
        "## Cohorts tab - matured loans only",
        "## Cohorts tab - all loans",
        "## Data Checks tab - checks flagged",
        "## How each figure is calculated",
    ):
        assert section in text
    for figure in (
        "Outstanding GBV",
        "APR Proxy",
        "Advance on GBV - No FX",
        "Advance on Principal - FX High",
        "Net IRR",
        "Sense-check Margin",
    ):
        assert figure in text


def test_ltv_calculator_uses_the_on_screen_dropdowns(context):
    default = context()
    chosen = context(
        ltvCalculator={
            "dataSource": "Observed",
            "country": "Philippines",
            "macroFxRisk": "Green",
            "segmentation": "Consumer Credit",
            "hedgeRate": 0.05,
            "rolledHedge": "Roll",
        }
    )
    assert "Alt Lender Country Ghana" in default
    assert "Alt Lender Country Philippines; Macro FX Risk Green; Segmentation Consumer Credit" in (
        chosen
    )
    fx_high = [ln for ln in chosen.splitlines() if ln.startswith("- Advance on GBV - FX High")]
    assert fx_high and fx_high != [
        ln for ln in default.splitlines() if ln.startswith("- Advance on GBV - FX High")
    ]


def test_ue_model_uses_the_on_screen_assumptions(context):
    default = context()
    edited = context(
        ueModelInputs={
            "upfrontCostPct": 0.01,
            "ongoingCostPct": 0.02,
            "financingCostPct": 0.20,
            "facilityFeePct": 0.02,
            "hedgeCostPct": 0.03,
            "lossChoice": "Input",
            "lossInputPct": 10,
        }
    )
    assert "at the default assumptions" in default
    assert "- Loss Rate used (Actual)" in default
    assert "at the assumptions currently entered on screen" in edited
    assert "- Loss Rate used (Input): 0.1" in edited
    assert "- Cost of Finance (% p.a.): 0.25" in edited


def test_no_loan_rows_in_the_context(context):
    text = context()
    # The duplicate-ID check's message goes in; its loan-level detail does not.
    assert "Duplicate" in text or "duplicate" in text
    assert "LN2024" not in text
