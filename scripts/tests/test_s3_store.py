"""s3_store.py has no live AWS in CI, so this exercises it against a
dict-backed fake S3 client - covering the parts that are actual logic
(hash-dedup, per-user prefix isolation), not boto3 itself."""

import s3_store


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.encode()

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise Exception("NoSuchKey")
        return {"Body": _FakeBody(self.objects[Key])}

    def list_objects_v2(self, Bucket, Prefix):
        return {"Contents": [{"Key": k} for k in self.objects if k.startswith(Prefix)]}


def _use_fake_s3(monkeypatch) -> _FakeS3:
    fake = _FakeS3()
    monkeypatch.setattr(s3_store, "_client", lambda: fake)
    return fake


def test_save_list_load_round_trip(monkeypatch):
    _use_fake_s3(monkeypatch)
    s3_store.save_profile(
        "Analyst@Lendable.io", "sess1",
        upload_bytes=b"loan tape v1", upload_name="tape.xlsx",
        mapping={"a": "b"}, currency="GBP", gi_overrides=None, label="Deal A",
    )
    profiles = s3_store.list_profiles("analyst@lendable.io")
    assert [p["session_id"] for p in profiles] == ["sess1"]
    assert profiles[0]["label"] == "Deal A"

    loaded = s3_store.load_profile("analyst@lendable.io", "sess1")
    assert loaded["upload_bytes"] == b"loan tape v1"
    assert loaded["mapping"] == {"a": "b"}


def test_unchanged_upload_bytes_are_not_reuploaded(monkeypatch):
    fake = _use_fake_s3(monkeypatch)
    kwargs = dict(
        upload_bytes=b"loan tape v1", upload_name="tape.xlsx",
        mapping={"a": "b"}, currency="GBP", gi_overrides=None, label="Deal A",
    )
    s3_store.save_profile("analyst@lendable.io", "sess1", **kwargs)
    data_key = "ai-tools/users/analyst_lendable.io/sessions/sess1.bin"
    before = fake.objects[data_key]

    s3_store.save_profile(
        "analyst@lendable.io", "sess1", chat_history=[{"q": "hi"}], **kwargs
    )
    assert fake.objects[data_key] == before
    assert s3_store.load_profile("analyst@lendable.io", "sess1")["chat_history"] == [{"q": "hi"}]


def test_users_do_not_see_each_others_sessions(monkeypatch):
    _use_fake_s3(monkeypatch)
    s3_store.save_profile(
        "analyst@lendable.io", "sess1",
        upload_bytes=b"x", upload_name="tape.xlsx",
        mapping=None, currency="GBP", gi_overrides=None,
    )
    assert s3_store.list_profiles("someone.else@lendable.io") == []


def test_share_round_trip_and_unknown_token(monkeypatch):
    _use_fake_s3(monkeypatch)
    token = s3_store.create_share(
        "analyst@lendable.io",
        upload_bytes=b"shared tape", upload_name="shared.xlsx",
        mapping=None, currency="USD", gi_overrides=None,
    )
    share = s3_store.load_share(token)
    assert share["upload_bytes"] == b"shared tape"
    assert share["shared_by"] == "analyst@lendable.io"
    assert s3_store.load_share("not-a-real-token") is None
