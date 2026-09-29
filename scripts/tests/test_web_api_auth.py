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
    monkeypatch.setattr(m.s3_store, "load_profile", lambda ident, sid: calls.append(ident) or None)
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
    monkeypatch.setattr(m, "_INGEST_TTL_SECONDS", 3600)
    m.INGESTS.clear()
    m.INGEST_RAW.clear()
    m._INGEST_LAST_USED.clear()
    m._INGEST_USE_SEQ.clear()

    stale = _register(0)["ingestId"]
    # Backdate it rather than using a zero TTL: "now - last_used > 0" is false
    # within a single clock tick (~15ms on Windows), which made this flaky.
    m._INGEST_LAST_USED[stale] -= 7200
    _register(1)  # registering anything runs eviction

    assert stale not in m.INGESTS
    assert stale not in m.INGEST_RAW


# --- route-level session enforcement ---------------------------------------
#
# The ingest/analyse/export/AI routes had no auth at all: anyone who could
# reach the URL could upload a tape, run the engine and spend the AI budget.
# A middleware guards them centrally so a route added later is protected
# without anyone remembering to decorate it - these pin that, and pin the
# public allow-list so a careless prefix change can't lock out sign-in.


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/ingest"),
        ("post", "/api/ingest/merge"),
        ("post", "/api/analyse/lending"),
        ("post", "/api/analyse/rental"),
        ("post", "/api/export/lending"),
        ("post", "/api/ai/suggest-mapping"),
        ("post", "/api/ai/chat/lending"),
        ("post", "/api/ltv-calculator"),
    ],
)
def test_data_routes_reject_anonymous_callers(client, method, path):
    r = getattr(client, method)(path, json={})
    assert r.status_code == 401, f"{path} answered {r.status_code} without a session"


@pytest.mark.parametrize("path", ["/api/health", "/api/auth/config"])
def test_public_routes_stay_reachable_without_a_session(client, path):
    assert client.get(path).status_code == 200


def test_sign_in_routes_are_reachable_anonymously(client):
    """Guarding /api/auth/* would make signing in impossible."""
    # /api/auth/me is public but answers 401 without a cookie - that is the
    # endpoint reporting "not signed in", not the middleware refusing it.
    assert client.get("/api/auth/me").status_code == 401
    # ...and signing in anonymously has to work, or nobody can ever get a
    # cookie. Checked second: it sets one on this client.
    assert client.post("/api/auth/dummy", json={"email": "a@lendable.io"}).status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_anon_escape_hatch_is_opt_in(client, monkeypatch):
    """SC_ALLOW_ANON_API exists for local dev and must default to off."""
    assert client.post("/api/ltv-calculator", json={}).status_code == 401
    monkeypatch.setenv("SC_ALLOW_ANON_API", "1")
    # Now the middleware lets it through to the handler, which rejects the
    # empty body on its own terms (422) rather than on auth.
    assert client.post("/api/ltv-calculator", json={}).status_code != 401


def test_oversized_uploads_are_refused(client, monkeypatch):
    """The parse is fully in memory and a DataFrame is several times its
    source file, so one large upload can OOM the task however few are kept."""
    monkeypatch.setattr(m, "_MAX_UPLOAD_BYTES", 100)
    _sign_in_as(client, "alice@lendable.io")
    r = client.post(
        "/api/ingest",
        files={"file": ("big.csv", b"x" * 500, "text/csv")},
    )
    assert r.status_code == 413
    assert "too large" in r.json()["detail"].lower()


def test_spa_fallback_cannot_escape_dist(client, monkeypatch, tmp_path):
    """ "\\" is a path separator on Windows, so ..%5C must not reach files
    outside the built dist folder (the route needs no session)."""
    if not hasattr(m, "spa_fallback"):
        pytest.skip("web/dist not built, SPA route not registered")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>index</html>")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("TOP-SECRET")
    monkeypatch.setattr(m, "_WEB_DIST_DIR", str(dist))
    for path in ("/..%5Csecret.txt", "/..%2Fsecret.txt", "/assets%5C..%5C..%5Csecret.txt"):
        assert "TOP-SECRET" not in client.get(path).text
    assert client.get("/favicon.svg").text == "<svg/>"


@pytest.mark.parametrize("cashflows", [[-100] + [1] * 10, [-100] + [-5] * 10])
def test_irr_heavy_loss_returns_none_not_crash(cashflows):
    """A badly performing book overflowed the Newton step and 500'd the export."""
    assert m._irr(cashflows) is None


def test_irr_still_solves_a_normal_book():
    assert m._irr([-100, 110]) == pytest.approx(0.1)


def test_rental_blank_extraction_date_falls_back_to_latest_start():
    """The web API passes extraction_date=None when the field is blank."""
    from rental_analysis.general_inputs import GeneralInputs

    df = pd.DataFrame({"start_date": pd.to_datetime(["2025-01-01", "2025-06-30"])})
    assert GeneralInputs(df, extraction_date=None).extraction_date == pd.Timestamp("2025-06-30")


def test_loading_a_share_requires_a_session(client, monkeypatch):
    """Each share load registers an ingest; anonymous loads could otherwise
    evict every signed-in user's working upload."""
    monkeypatch.setattr(m.s3_store, "load_share", lambda token: pytest.fail("reached storage"))
    assert client.get("/api/share/sometoken").status_code == 401


def test_size_cap_covers_every_byte_source(monkeypatch):
    """Drive/link imports go through _register_ingest, not /api/ingest, so
    the cap has to live there."""
    monkeypatch.setattr(m, "_MAX_UPLOAD_BYTES", 100)
    with pytest.raises(m.HTTPException) as exc:
        m._register_ingest(b"x" * 500, "google_file.csv")
    assert exc.value.status_code == 413


def test_restoring_a_saved_run_skips_the_size_cap(monkeypatch):
    """A saved warehouse/merge frame is serialized to CSV at save time and
    can be far past the cap it never had to pass; restoring it must work."""
    monkeypatch.setattr(m, "_MAX_UPLOAD_BYTES", 10)
    payload = m._restore_payload(
        {"upload_bytes": b"a,b\n1,2\n3,4\n", "upload_name": "saved.csv", "mapping": {}}
    )
    assert payload["rowCount"] == 2


def test_oauth_states_are_capped_and_expire(client, monkeypatch):
    monkeypatch.setattr(m, "_get_google_client_id", lambda: "id")
    monkeypatch.setattr(m, "_get_google_client_secret", lambda: "secret")
    monkeypatch.setattr(m, "_OAUTH_STATE_MAX", 3)
    m._PENDING_GOOGLE_OAUTH_STATES.clear()
    for _ in range(10):
        assert client.get("/api/auth/google/login-url").status_code == 200
    assert len(m._PENDING_GOOGLE_OAUTH_STATES) == 3

    m._PENDING_GOOGLE_OAUTH_STATES["stale"] = 0.0  # issued at the epoch
    r = client.post("/api/auth/google/callback", json={"code": "c", "state": "stale"})
    assert r.status_code == 400
    assert "stale" not in m._PENDING_GOOGLE_OAUTH_STATES


def test_engine_result_is_reused_until_settings_or_upload_change():
    """analyse -> chat -> export on one upload should run the engine once."""
    ingest_id = m._register_ingest(None, "tape.csv", df=pd.DataFrame({"a": [1, 2]}))["ingestId"]
    runs = []

    def run():
        runs.append(1)
        return (None, None, {"n": len(runs)})

    def body(**gi):
        return m.LendingChatBody(
            ingest={"ingestId": ingest_id, "mapping": {"Loan ID": "a"}},
            generalInputs=gi,
            question="ignored by the key",
        )

    first = m._cached_pipeline("lending", body(), run)
    # Different question/history (chat-only fields) -> same result, no rerun.
    again = m._cached_pipeline("lending", body().model_copy(update={"question": "other"}), run)
    assert again is first and len(runs) == 1

    m._cached_pipeline("lending", body(daysAfterTerm=30), run)  # settings changed
    assert len(runs) == 2

    # Same id re-registered (the sheet picker does this) -> never serve the old sheet.
    m._register_ingest(None, "tape.csv", df=pd.DataFrame({"a": [3]}), ingest_id=ingest_id)
    m._cached_pipeline("lending", body(daysAfterTerm=30), run)
    assert len(runs) == 3

    m._drop_ingest(ingest_id)
    assert ingest_id not in m._ENGINE_CACHE
