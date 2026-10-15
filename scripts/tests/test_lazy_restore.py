"""Reopening a saved run renders its stored result without downloading the
data; the data is fetched only when something first reads the ingest."""

import pytest
from fastapi.testclient import TestClient

import web_api.main as m


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _CountingS3:
    """Dict-backed S3 that records which keys were downloaded."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.gets: list[str] = []

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.encode()

    def get_object(self, Bucket, Key):
        self.gets.append(Key)
        if Key not in self.objects:
            raise Exception("NoSuchKey")
        return {"Body": _FakeBody(self.objects[Key])}


@pytest.fixture
def s3(monkeypatch):
    fake = _CountingS3()
    monkeypatch.setattr(m.s3_store, "_client", lambda: fake)
    monkeypatch.setattr(m.s3_store, "_bucket", lambda: "bucket")
    return fake


@pytest.fixture
def client():
    c = TestClient(m.app, base_url="https://testserver")
    assert c.post("/api/auth/dummy", json={"email": "alice@lendable.io"}).status_code == 200
    yield c
    # Unloaded restores count towards the ingest cap other tests measure.
    for ingest_id in list(m._PENDING_INGESTS):
        m._drop_ingest(ingest_id)


def _data_gets(s3):
    return [k for k in s3.gets if k.endswith(".bin")]


def _save(client, csv: bytes) -> dict:
    up = client.post("/api/ingest", files={"file": ("tape.csv", csv, "text/csv")}).json()
    r = client.post(
        "/api/profiles/save",
        json={
            "sessionId": "sess1",
            "label": "Deal A",
            "ingestId": up["ingestId"],
            "analysisResult": {"summary": {"rows": 2}},
        },
    )
    assert r.status_code == 200
    return up


def test_reopening_a_saved_run_does_not_download_the_data(client, s3):
    up = _save(client, b"a,b\n1,2\n3,4\n")

    restored = client.get("/api/profiles/sess1").json()

    assert _data_gets(s3) == []
    assert restored["analysisResult"] == {"summary": {"rows": 2}}
    for key in ("columns", "rowCount", "preview", "sheets", "sheet"):
        assert restored[key] == up[key]
    assert restored["ingestId"] != up["ingestId"]

    # The first read downloads it once; later reads use the parsed frame.
    df = m._get_ingest(restored["ingestId"])
    assert df is not None and len(df) == 2
    m._get_ingest(restored["ingestId"])
    assert len(_data_gets(s3)) == 1


def test_saving_a_restored_run_again_keeps_its_data(client, s3):
    _save(client, b"a,b\n1,2\n3,4\n")
    restored = client.get("/api/profiles/sess1").json()

    r = client.post(
        "/api/profiles/save",
        json={"sessionId": "sess1", "label": "Deal A", "ingestId": restored["ingestId"]},
    )

    assert r.status_code == 200
    data_key = next(k for k in s3.objects if k.endswith("sess1.bin"))
    assert s3.objects[data_key] == b"a,b\n1,2\n3,4\n"
    assert client.get("/api/profiles/sess1").json()["rowCount"] == 2


def test_runs_saved_before_ingest_info_still_load_their_data(client, s3, monkeypatch):
    _save(client, b"a,b\n1,2\n3,4\n")
    meta_key = next(k for k in s3.objects if k.endswith("sess1.json"))
    meta = m.s3_store._get_json(meta_key)
    meta.pop("ingest_info")
    m.s3_store._put_json(meta_key, meta)

    restored = client.get("/api/profiles/sess1").json()

    assert restored["rowCount"] == 2
    assert len(_data_gets(s3)) == 1


def test_a_missing_data_object_fails_on_first_read_not_on_open(client, s3):
    _save(client, b"a,b\n1,2\n3,4\n")
    for k in [k for k in s3.objects if k.endswith(".bin")]:
        del s3.objects[k]

    restored = client.get("/api/profiles/sess1")
    assert restored.status_code == 200

    with pytest.raises(m.HTTPException) as exc:
        m._get_ingest(restored.json()["ingestId"])
    assert exc.value.status_code == 502


def test_ask_ai_works_on_a_reopened_saved_run(client, s3, monkeypatch):
    import time

    tape = (
        b"contract_id,asset_id,start_date,expected_end_date,status,downpayment,"
        b"monthly_expected_payment,total_paid,cost_of_asset\n"
        b"c0,a0,2024-01-01,2027-01-01,Open,0,100,500,3000\n"
        b"c1,a1,2024-02-01,2027-02-01,Open,0,100,400,3000\n"
    )
    _save(client, tape)
    restored = client.get("/api/profiles/sess1").json()
    monkeypatch.setattr(m, "_extract_whatif", lambda q, h, f: {})
    monkeypatch.setattr(m, "_ask_data_chatbot", lambda q, ctx, h: f"saw {len(ctx)} chars")

    cols = [
        "contract_id",
        "asset_id",
        "start_date",
        "expected_end_date",
        "status",
        "downpayment",
        "monthly_expected_payment",
        "total_paid",
        "cost_of_asset",
    ]
    job = client.post(
        "/api/ai/chat/rental/start",
        json={
            "ingest": {"ingestId": restored["ingestId"], "mapping": {c: c for c in cols}},
            "generalInputs": {"extractionDate": "2025-01-01"},
            "question": "q",
        },
    ).json()
    for _ in range(100):
        res = client.get(f"/api/ai/chat/status/{job['jobId']}").json()
        if res["status"] != "running":
            break
        time.sleep(0.05)
    assert res["status"] == "done", res
    assert res["answer"].startswith("saw ")
    assert len(_data_gets(s3)) == 1  # the saved data was fetched on first read


_RENTAL_TAPE = (
    b"cid,asset,start,end,st,dp,mrr,paid,cost\n"
    b"c0,a0,2024-01-01,2027-01-01,Open,0,100,500,3000\n"
    b"c1,a1,2024-02-01,2027-02-01,Open,0,100,400,3000\n"
)
_RENTAL_MAPPING = {
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
_DERIVED = [{"name": "Cost x2", "formula": "cost * 2"}]


def _grid(client, restored):
    return client.post(
        "/api/data-grid/rental",
        json={
            "ingest": {
                "ingestId": restored["ingestId"],
                "mapping": restored["mapping"],
                "derivedColumns": restored["derivedColumns"],
            },
            "generalInputs": {"extractionDate": "2025-01-01"},
        },
    )


def test_saved_and_shared_runs_keep_ai_derived_columns_for_the_data_grid(client, s3):
    up = client.post("/api/ingest", files={"file": ("t.csv", _RENTAL_TAPE, "text/csv")}).json()
    common = {
        "ingestId": up["ingestId"],
        "mapping": _RENTAL_MAPPING,
        "derivedColumns": _DERIVED,
    }
    assert (
        client.post(
            "/api/profiles/save", json={"sessionId": "sess2", "label": "R", **common}
        ).status_code
        == 200
    )
    token = client.post("/api/share", json=common).json()["token"]

    for restored in (
        client.get("/api/profiles/sess2").json(),
        client.get(f"/api/share/{token}").json(),
    ):
        assert restored["derivedColumns"] == _DERIVED
        res = _grid(client, restored)
        assert res.status_code == 200, res.text
        grid = res.json()
        assert grid["derived"] == {"cost_of_asset": "cost * 2"}
        assert grid["rows"][0]["cost_of_asset"] == 6000
        assert "term_days" in grid["calculated"]
