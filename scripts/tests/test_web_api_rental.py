"""The React rental path (/api/analyse/rental) must match the Streamlit one on
two things it used to skip: the client-status -> Lendable-status mapping, and
mixed-format date parsing. Without the first, a tape whose statuses aren't
spelled exactly "Open"/"Closed"/... ran with every status NaN and reported
MRR 0 / loss 0% as if real (GoCab's lowercase "open"/"closed" did this)."""

import sys

import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, "scripts")
import web_api.main as m  # noqa: E402


def _tape(statuses):
    n = len(statuses)
    return pd.DataFrame(
        {
            "contract_id": [f"c{i}" for i in range(n)],
            "asset_id": [f"a{i}" for i in range(n)],
            "start_date": pd.to_datetime(["2024-01-01"] * n),
            "expected_end_date": pd.to_datetime(["2026-01-01"] * n),
            "closed_date": pd.to_datetime([None] * n),
            "status": statuses,
            "downpayment": [0.0] * n,
            "monthly_expected_payment": [100.0] * n,
            "total_paid": [500.0] * n,
            "cost_of_asset": [1000.0] * n,
        }
    )


def test_unmapped_statuses_are_refused_not_silently_zeroed():
    with pytest.raises(m.HTTPException) as exc:
        m._compute_rental(_tape(["open", "disabled"]), m.RentalGeneralInputsBody())
    assert exc.value.status_code == 422
    assert "disabled" in exc.value.detail and "open" in exc.value.detail


def test_status_map_from_the_request_reaches_the_engine():
    body = m.RentalGeneralInputsBody(
        extractionDate="2025-01-01", statusMap={"open": "Open", "1": "Open"}
    )
    c = m._compute_rental(_tape(["open", 1]), body)
    assert c["ltv"].mrr() == 200.0  # both rows counted as Open


def test_mixed_date_formats_all_parse():
    rows = _tape(["Open", "Open"]).astype({"start_date": object})
    rows["start_date"] = ["11/28/2024", "2024-08-11 00:00:00"]
    rows = rows.drop(columns=["expected_end_date", "closed_date"])
    df, _ = m._resolve_rental_df(m.RentalAnalyseBody(rows=rows.to_dict("records")))
    assert df["start_date"].tolist() == [pd.Timestamp("2024-11-28"), pd.Timestamp("2024-08-11")]


def test_blank_dates_serialize_as_null():
    """A NaT in a DQ-check detail row 500'd /api/analyse/rental (GoCab's blank
    last_payment_date) - .where(notna, None) can't put None in a datetime column."""
    df = pd.DataFrame({"d": pd.to_datetime(["2024-01-01", None])})
    assert m._frame_to_records(df) == [{"d": "2024-01-01T00:00:00"}, {"d": None}]


def test_status_values_lists_every_status_with_a_case_blind_suggestion():
    ingest_id = m._register_ingest(
        b"", "t.csv", df=pd.DataFrame({"St": ["open", "closed", "open", None, "disabled"]})
    )["ingestId"]
    client = TestClient(m.app, base_url="https://testserver")
    assert client.post("/api/auth/dummy", json={"email": "a@lendable.io"}).status_code == 200
    r = client.post(
        "/api/ingest/status-values",
        json={"ingest": {"ingestId": ingest_id, "mapping": {"status": "St"}}},
    )
    assert r.status_code == 200
    assert r.json() == {
        "values": [
            {"value": "open", "count": 2, "suggested": "Open"},
            {"value": "closed", "count": 1, "suggested": "Closed"},
            {"value": "disabled", "count": 1, "suggested": ""},
        ],
        "truncated": False,
    }


def test_status_values_requires_a_session():
    client = TestClient(m.app, base_url="https://testserver")
    assert client.post("/api/ingest/status-values", json={}).status_code == 401


def test_summary_data_up_to_is_the_last_contract_start_not_the_extraction_date():
    tape = _tape(["Open", "Open"])
    tape["start_date"] = pd.to_datetime(["2024-01-01", "2024-03-15"])
    c = m._compute_rental(tape, m.RentalGeneralInputsBody(extractionDate="2025-01-01"))
    s = m.rental_summary_metrics(
        c["processed"], c["av"], c["gi"], c["ue"].as_dict(), c["ltv"].as_dict(), c["ts"]
    )
    assert s["dataUpTo"] == "2024-03-15"
    assert s["dateRangeEnd"] == "2025-01-01"


def test_ue_by_cohort_matches_the_portfolio_formulas_per_cohort():
    from rental_analysis.ue_analysis import UeAnalysis, ue_by_cohort

    tape = _tape(["Open"] * 3)
    tape["start_date"] = pd.to_datetime(["2024-01-05", "2024-01-20", "2024-02-10"])
    tape["cost_of_asset"] = [1000.0, 3000.0, 2000.0]
    c = m._compute_rental(tape, m.RentalGeneralInputsBody(extractionDate="2025-01-01"))
    df = c["processed"]
    rows = ue_by_cohort(df, c["gi_calc"]).set_index("cohort")
    jan = df[df["contract_cohort"] == pd.Timestamp("2024-01-01")]
    ue_jan = UeAnalysis(jan, None, None, c["gi_calc"])
    assert list(rows.index) == [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01")]
    assert rows.loc["2024-01-01", "margin"] == pytest.approx(ue_jan.margin())
    assert rows.loc["2024-01-01", "lease_tenor_m"] == pytest.approx(ue_jan.lease_tenor_m())
