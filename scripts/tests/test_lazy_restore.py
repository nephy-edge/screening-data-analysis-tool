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
