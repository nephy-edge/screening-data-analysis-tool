"""Data Checks > Data Input shows the engine's per-row table: mapped inputs,
AI-derived inputs (with their formula) and the columns the engine computes."""

import sys

from fastapi.testclient import TestClient

sys.path.insert(0, "scripts")
import web_api.main as m  # noqa: E402

TAPE = (
    b"cid,asset,start,end,st,dp,mrr,paid,cost\n"
    b"c0,a0,2024-01-01,2027-01-01,Open,0,100,500,3000\n"
    b"c1,a1,2024-02-01,2027-02-01,Open,0,100,400,3000\n"
)
MAPPING = {
    "contract_id": "cid",
    "asset_id": "asset",
    "start_date": "start",
    "expected_end_date": "end",
    "status": "st",
    "downpayment": "dp",
    "monthly_expected_payment": "mrr",
    "total_paid": "paid",
    "cost_of_asset": "Cost x2",
}


def test_rental_grid_marks_inputs_derived_and_calculated_columns():
    client = TestClient(m.app, base_url="https://testserver")
    client.post("/api/auth/dummy", json={"email": "a@lendable.io"})
    ingest_id = client.post("/api/ingest", files={"file": ("t.csv", TAPE, "text/csv")}).json()[
        "ingestId"
    ]
    body = {
        "ingest": {
            "ingestId": ingest_id,
            "mapping": MAPPING,
            "derivedColumns": [{"name": "Cost x2", "formula": "cost * 2"}],
        },
        "generalInputs": {"extractionDate": "2025-01-01"},
    }
    res = client.post("/api/data-grid/rental", json=body)
    assert res.status_code == 200, res.text
    grid = res.json()
    assert grid["rowCount"] == 2
    assert grid["derived"] == {"cost_of_asset": "cost * 2"}
    assert {"term_days", "status_mapping", "contract_cohort"} <= set(grid["calculated"])
    assert "contract_id" not in grid["calculated"] and "cost_of_asset" not in grid["calculated"]
    assert grid["rows"][0]["cost_of_asset"] == 6000
    assert grid["rows"][0]["term_days"] == 1096
    assert client.post("/api/data-grid/nope", json=body).status_code == 404
