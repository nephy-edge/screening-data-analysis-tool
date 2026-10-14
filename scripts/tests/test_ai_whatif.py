"""Ask AI what-ifs: the AI only names which input to change; the app re-runs
the engine with it, so every what-if figure comes from code."""

import sys

import pandas as pd
import pytest

sys.path.insert(0, "scripts")
import ai_client  # noqa: E402

import web_api.main as m  # noqa: E402


def _rental_body(question):
    tape = pd.DataFrame(
        {
            "contract_id": ["c0", "c1"],
            "asset_id": ["a0", "a1"],
            "start_date": ["2024-01-01", "2024-02-01"],
            "expected_end_date": ["2027-01-01", "2027-02-01"],
            "status": ["Open", "Open"],
            "downpayment": [0.0, 0.0],
            "monthly_expected_payment": [100.0, 100.0],
            "total_paid": [500.0, 400.0],
            "cost_of_asset": [3000.0, 3000.0],
        }
    )
    return m.RentalChatBody(
        rows=tape.to_dict("records"),
        generalInputs=m.RentalGeneralInputsBody(extractionDate="2025-01-01"),
        question=question,
    )


def test_whatif_fields_are_the_numeric_inputs_with_current_values():
    fields = m._whatif_fields(_rental_body("q"), "rental")
    assert fields["generalInputs.fundingCostPct"] == 0.13
    assert fields["generalInputs.fxDevalPct"] is None
    assert "generalInputs.extractionDate" not in fields
    assert "generalInputs.statusMap" not in fields


def test_lending_offers_ue_model_costs_even_when_untouched():
    body = m.LendingChatBody(question="q")
    fields = m._whatif_fields(body, "lending")
    assert fields["ueModelInputs.financingCostPct"] == pytest.approx(0.13)
    assert "ltvCalculator.hedgeRate" in fields


def test_apply_whatif_overrides_a_copy_and_rejects_out_of_range():
    body = m.RevolvingChatBody(question="q")
    new, applied, rejected = m._apply_whatif(
        body, "revolving", {"generalInputs.costOfFundsPct": 0.1, "generalInputs.lgd": "x"}
    )
    assert new.generalInputs.costOfFundsPct == 0.1
    assert body.generalInputs.costOfFundsPct != 0.1 or body is not new
    assert applied == {"generalInputs.costOfFundsPct": 0.1}
    assert rejected == ["generalInputs.lgd"]
    _, applied, rejected = m._apply_whatif(body, "revolving", {"generalInputs.advanceHaircut": 5})
    assert applied == {} and rejected == ["generalInputs.advanceHaircut"]


def test_extract_whatif_keeps_only_known_numeric_fields(monkeypatch):
    monkeypatch.setattr(ai_client, "_get_deepinfra_api_key", lambda: "k")
    monkeypatch.setattr(
        ai_client,
        "_chat_json",
        lambda *a, **k: {
            "overrides": {"generalInputs.fundingCostPct": 0.1, "made.up": 1, "x": "y"}
        },
    )
    out = ai_client._extract_whatif(
        "what if funding were 10%?", [], {"generalInputs.fundingCostPct": 0.13}
    )
    assert out == {"generalInputs.fundingCostPct": 0.1}


def test_rental_chat_recomputes_the_whatif_in_code(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        m, "_extract_whatif", lambda q, h, f: {"generalInputs.fundingCostPct": 0.05}
    )
    monkeypatch.setattr(
        m, "_ask_data_chatbot", lambda q, ctx, h: seen.setdefault("ctx", ctx) and "ok"
    )
    body = _rental_body("What if funding cost were 5%?")
    base = m._compute_rental(*m._resolve_rental_df(body)[:1], body.generalInputs)["ue"].net_margin()
    res = m.ai_chat_rental(body)
    assert res["whatif"] == {
        "changes": [{"input": "General Inputs: Funding Cost %", "from": "13%", "to": "5%"}],
        "rejected": [],
    }
    ctx = seen["ctx"]
    assert "## What-if scenario" in ctx
    assert "generalInputs.fundingCostPct: 0.13 -> 0.05" in ctx
    alt = m._compute_rental(
        *m._resolve_rental_df(body)[:1],
        body.generalInputs.model_copy(update={"fundingCostPct": 0.05}),
    )["ue"].net_margin()
    assert alt > base
    assert f"{alt:.1%}" in ctx.split("## What-if scenario", 1)[1]


def test_plain_question_has_no_whatif_banner(monkeypatch):
    monkeypatch.setattr(m, "_extract_whatif", lambda q, h, f: {})
    monkeypatch.setattr(m, "_ask_data_chatbot", lambda q, ctx, h: "ok")
    assert m.ai_chat_rental(_rental_body("What is the PvD?"))["whatif"] is None
