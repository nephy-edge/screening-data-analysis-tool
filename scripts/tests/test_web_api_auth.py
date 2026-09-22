"""The React API's per-user storage must key off the VERIFIED identity in the
signed session cookie, never an identity the caller supplies.

s3_store enforces access control in application code (nothing reads outside
the caller's own users/<safe_id>/ prefix), so if the client gets to name the
prefix, that enforcement is worth nothing - anyone could list and load anyone
else's saved loan tape by changing an email in a query string. These tests
pin that the parameter is gone and the cookie is what counts.

Also pins that the in-memory ingest registries stay bounded: nothing else
removes from them, so an unbounded registry means a long-running task holds
every loan tape it has ever parsed until it is OOM-killed.
"""

import sys
import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, "scripts")
import web_api.main as m  # noqa: E402


@pytest.fixture
def client():
    # https, because the session cookie is secure=True and a plain-http test
    # client would silently drop it (as a browser would).
    return TestClient(m.app, base_url="https://testserver")


@pytest.fixture
def seen(monkeypatch):
    """Record the identity s3_store is actually called with."""
    calls = []
    monkeypatch.setattr(m.s3_store, "list_profiles", lambda ident: calls.append(ident) or [])
    monkeypatch.setattr(
        m.s3_store, "load_profile", lambda ident, sid: calls.append(ident) or None
    )
    m._PROFILES_CACHE.clear()
    return calls


def _sign_in_as(client, email):
    """Issue a real signed session cookie via the dummy sign-in endpoint."""
    r = client.post("/api/auth/dummy", json={"email": email})
    assert r.status_code == 200
    return r


def test_listing_profiles_without_a_cookie_is_rejected(client, seen):
    r = client.get("/api/profiles")
    assert r.status_code == 401
    # Nothing reached storage.
    assert seen == []


def test_identity_query_param_cannot_override_the_cookie(client, seen):
    _sign_in_as(client, "alice@lendable.io")
    # The old hole: ask for someone else's profiles by naming them.
    r = client.get("/api/profiles?identity=bob@lendable.io")
    assert r.status_code == 200
    # Storage saw alice - the query string was ignored, not honoured.
    assert seen == ["alice@lendable.io"]
    assert "bob@lendable.io" not in seen


def test_loading_one_profile_also_ignores_a_supplied_identity(client, seen):
    _sign_in_as(client, "alice@lendable.io")
    r = client.get("/api/profiles/some-session?identity=bob@lendable.io")
    assert r.status_code == 404  # load_profile stub returns None
    assert seen == ["alice@lendable.io"]


def test_loading_a_profile_without_a_cookie_is_rejected(client, seen):
    r = client.get("/api/profiles/some-session")
    assert r.status_code == 401
    assert seen == []


def test_a_forged_cookie_is_rejected(client, seen):
    client.cookies.set(m._REACT_SESSION_COOKIE, "not-a-valid-signature")
    r = client.get("/api/profiles")
    assert r.status_code == 401
    assert seen == []


def test_saving_and_sharing_require_a_cookie(client):
    body = {"sessionId": "s1", "label": "x", "ingestId": "i1", "mapping": {}}
    assert client.post("/api/profiles/save", json=body).status_code == 401
    assert client.post("/api/share", json={"ingestId": "i1", "mapping": {}}).status_code == 401


# --- ingest registry bounds -------------------------------------------------


def _register(n):
    return m._register_ingest(b"", f"f{n}.csv", None, df=pd.DataFrame({"a": [n]}))


def test_ingest_registry_stays_under_its_cap(monkeypatch):
    monkeypatch.setattr(m, "_INGEST_MAX", 3)
    m.INGESTS.clear()
    m.INGEST_RAW.clear()
    m._INGEST_LAST_USED.clear()
    m._INGEST_USE_SEQ.clear()

    ids = [_register(i)["ingestId"] for i in range(6)]

    assert len(m.INGESTS) == 3
    # Both registries are evicted together, so raw bytes never outlive the
    # DataFrame they belong to (or the reverse).
    assert set(m.INGESTS) == set(m.INGEST_RAW) == set(m._INGEST_LAST_USED) == set(m._INGEST_USE_SEQ)
    # Oldest went first, newest survive.
    assert ids[-1] in m.INGESTS
    assert ids[0] not in m.INGESTS


def test_an_ingest_in_active_use_is_not_evicted_by_newer_uploads(monkeypatch):
    """A user analysing a deal touches their ingest as they go (every
    /api/analyse, export and save reads it), so it must stay live even while
    other uploads push through. LRU only protects what is actually being
    used - an ingest left untouched longer than every other is genuinely the
    right one to drop, which is what the TTL and cap are for."""
    monkeypatch.setattr(m, "_INGEST_MAX", 3)
    m.INGESTS.clear()
    m.INGEST_RAW.clear()
    m._INGEST_LAST_USED.clear()
    m._INGEST_USE_SEQ.clear()

    keep = _register(0)["ingestId"]
    for i in range(1, 8):
        _register(i)
        # ...meanwhile the user keeps working on their own deal.
        assert m._get_ingest(keep) is not None, "active ingest evicted mid-session"

    assert keep in m.INGESTS
    assert len(m.INGESTS) == 3


def test_expired_ingests_are_dropped(monkeypatch):
    monkeypatch.setattr(m, "_INGEST_TTL_SECONDS", 0)
    m.INGESTS.clear()
    m.INGEST_RAW.clear()
    m._INGEST_LAST_USED.clear()
    m._INGEST_USE_SEQ.clear()

    stale = _register(0)["ingestId"]
    _register(1)  # registering anything runs eviction

    assert stale not in m.INGESTS
    assert stale not in m.INGEST_RAW
