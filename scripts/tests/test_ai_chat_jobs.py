"""Ask AI runs as a background job the browser polls, so no single request
lasts long enough for a proxy or load balancer in front of the app to time
it out (the bare 502/503s seen on the deployed app)."""

import sys
import time

import pandas as pd
from fastapi.testclient import TestClient

sys.path.insert(0, "scripts")
import web_api.main as m  # noqa: E402


def _client(email="a@lendable.io"):
    client = TestClient(m.app, base_url="https://testserver")
    client.post("/api/auth/dummy", json={"email": email})
    return client


def _rental_rows():
    return pd.DataFrame(
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
    ).to_dict("records")


def _poll(client, job_id):
    for _ in range(100):
        res = client.get(f"/api/ai/chat/status/{job_id}")
        assert res.status_code == 200, res.text
        if res.json()["status"] != "running":
            return res.json()
        time.sleep(0.05)
    raise AssertionError("job never finished")


def _body():
    return {
        "rows": _rental_rows(),
        "generalInputs": {"extractionDate": "2025-01-01"},
        "question": "q",
    }


def test_chat_job_returns_the_same_answer_as_the_direct_call(monkeypatch):
    monkeypatch.setattr(m, "_extract_whatif", lambda q, h, f: {})
    monkeypatch.setattr(m, "_ask_data_chatbot", lambda q, ctx, h: "the answer")
    client = _client()
    res = client.post("/api/ai/chat/rental/start", json=_body())
    assert res.status_code == 200, res.text
    done = _poll(client, res.json()["jobId"])
    assert done == {"status": "done", "answer": "the answer", "whatif": None}


def test_chat_job_error_keeps_its_status_code_and_text(monkeypatch):
    def boom(q, ctx, h):
        raise RuntimeError("No DEEPINFRA_API_KEY found.")

    monkeypatch.setattr(m, "_extract_whatif", lambda q, h, f: {})
    monkeypatch.setattr(m, "_ask_data_chatbot", boom)
    client = _client()
    job_id = client.post("/api/ai/chat/rental/start", json=_body()).json()["jobId"]
    assert _poll(client, job_id) == {
        "status": "error",
        "code": 503,
        "detail": "No DEEPINFRA_API_KEY found.",
    }


def test_another_user_cannot_read_the_job(monkeypatch):
    monkeypatch.setattr(m, "_extract_whatif", lambda q, h, f: {})
    monkeypatch.setattr(m, "_ask_data_chatbot", lambda q, ctx, h: "secret answer")
    job_id = _client().post("/api/ai/chat/rental/start", json=_body()).json()["jobId"]
    other = _client("b@lendable.io")
    assert other.get(f"/api/ai/chat/status/{job_id}").status_code == 404
    assert other.get("/api/ai/chat/status/not-a-job").status_code == 404
