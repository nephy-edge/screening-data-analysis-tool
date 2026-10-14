"""A deal saved before share links existed (or by the Streamlit app, same S3
layout) must still be shareable: resume it, hit Share, and another user can
open the link. Uses the dict-backed fake S3 from test_s3_store."""

import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, "scripts")
from test_s3_store import _use_fake_s3  # noqa: E402

import web_api.main as m  # noqa: E402

CSV = b"loan_id,amount\n1,100\n2,200\n"


@pytest.fixture
def client():
    return TestClient(m.app, base_url="https://testserver")


def test_resumed_saved_deal_can_be_shared_and_opened_by_someone_else(client, monkeypatch):
    _use_fake_s3(monkeypatch)
    m._PROFILES_CACHE.clear()
    # An old-style saved deal: written straight to S3, no ingest_info/view_state.
    m.s3_store.save_profile(
        "alice@lendable.io",
        "old-deal",
        upload_bytes=CSV,
        upload_name="tape.csv",
        mapping={"loan_id": "loan_id"},
        currency=None,
        gi_overrides=None,
        label="Old deal",
    )

    assert client.post("/api/auth/dummy", json={"email": "alice@lendable.io"}).status_code == 200
    resumed = client.get("/api/profiles/old-deal")
    assert resumed.status_code == 200
    ingest_id = resumed.json()["ingestId"]

    shared = client.post(
        "/api/share", json={"ingestId": ingest_id, "mapping": {"loan_id": "loan_id"}}
    )
    assert shared.status_code == 200, shared.text
    token = shared.json()["token"]

    client.cookies.clear()
    assert client.post("/api/auth/dummy", json={"email": "bob@lendable.io"}).status_code == 200
    opened = client.get(f"/api/share/{token}")
    assert opened.status_code == 200, opened.text
    assert opened.json()["mapping"] == {"loan_id": "loan_id"}
    assert m.s3_store.load_share(token)["upload_bytes"] == CSV
