"""Cognito sign-in for the deployed React app (web_api/cognito.py).

Pins the properties the ECS Express deployment depends on: under Cognito the
ONLY way to a session is a verified ID token (the temporary sign-in that
mints a cookie for any typed email is refused), the callback issues the same
signed cookie the data routes already trust, a share link survives the
round trip without becoming an open redirect, and Google is reduced to
connecting the signed-in user's OWN Drive.

Authlib's network half (metadata, token exchange, JWKS) is replaced by a fake
client: what is under test is what this app does with its result.
"""

import sys

import pytest
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

sys.path.insert(0, "scripts")
import web_api.main as m  # noqa: E402

BASE = "https://le-test.ecs.us-west-2.on.aws"
COGNITO_ENV = {
    "COGNITO_CLIENT_ID": "client-id",
    "COGNITO_CLIENT_SECRET": "client-secret",
    "COGNITO_ISSUER": "https://cognito-idp.us-west-2.amazonaws.com/pool",
    "COGNITO_DOMAIN": "https://pool.auth.us-west-2.amazoncognito.com",
    "APP_SESSION_SECRET": "x" * 64,
    "APP_BASE_URL": BASE,
}


class FakeCognito:
    def __init__(self):
        self.claims = {"email": "Analyst@Lendable.io", "name": "An Analyst"}
        self.fail = False
        self.redirect_uris = []

    async def authorize_redirect(self, request, redirect_uri):
        self.redirect_uris.append(redirect_uri)
        return RedirectResponse("https://pool.auth.example/oauth2/authorize", status_code=302)

    async def authorize_access_token(self, request):
        if self.fail:
            raise RuntimeError("mismatching_state")
        return {"userinfo": self.claims}


@pytest.fixture
def fake(monkeypatch):
    for k, v in COGNITO_ENV.items():
        monkeypatch.setenv(k, v)
    f = FakeCognito()
    monkeypatch.setattr(m.cognito, "client", lambda: f)
    return f


@pytest.fixture
def client():
    return TestClient(m.app, base_url="https://testserver", follow_redirects=False)


def _sign_in(client, next_path="/"):
    assert client.get("/api/auth/login", params={"next": next_path}).status_code == 302
    return client.get("/oauth2callback", params={"code": "c", "state": "s"})


def test_temporary_sign_in_is_refused_under_cognito(client, fake):
    r = client.post("/api/auth/dummy", json={"email": "anyone@lendable.io"})
    assert r.status_code == 404
    assert m._REACT_SESSION_COOKIE not in client.cookies
    assert client.get("/api/profiles").status_code == 401


def test_login_route_is_off_without_cognito(client):
    assert client.get("/api/auth/login").status_code == 404
    assert client.get("/oauth2callback").status_code == 404


def test_login_hands_cognito_the_registered_callback(client, fake):
    r = client.get("/api/auth/login")
    assert r.status_code == 302
    assert fake.redirect_uris == [f"{BASE}/oauth2callback"]


def test_callback_issues_the_session_the_data_routes_trust(client, fake, monkeypatch):
    seen = []
    monkeypatch.setattr(m.s3_store, "list_profiles", lambda ident: seen.append(ident) or [])
    m._PROFILES_CACHE.clear()

    r = _sign_in(client, "/?share=tok123")
    assert r.status_code == 303
    assert r.headers["location"] == "/?share=tok123"
    assert client.get("/api/auth/me").json()["email"] == "analyst@lendable.io"
    assert client.get("/api/profiles").status_code == 200
    assert seen == ["analyst@lendable.io"]


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example", "/\\evil.example"])
def test_next_cannot_redirect_off_site(client, fake, target):
    r = _sign_in(client, target)
    assert r.headers["location"] == "/"


def test_a_failed_token_exchange_issues_no_session(client, fake):
    fake.fail = True
    r = _sign_in(client)
    assert r.status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_an_account_without_email_is_refused(client, fake):
    fake.claims = {"name": "no email"}
    assert _sign_in(client).status_code == 403
    assert client.get("/api/auth/me").status_code == 401


def test_logout_also_ends_the_cognito_session(client, fake):
    _sign_in(client)
    body = client.post("/api/auth/logout").json()
    assert body["logoutUrl"].startswith(
        f"{COGNITO_ENV['COGNITO_DOMAIN']}/logout?client_id=client-id"
    )
    assert "logout_uri=https%3A%2F%2Fle-test.ecs.us-west-2.on.aws%2F" in body["logoutUrl"]
    assert client.get("/api/auth/me").status_code == 401


def test_auth_config_reports_cognito(client, fake):
    assert client.get("/api/auth/config").json()["cognitoEnabled"] is True


def test_half_configured_cognito_is_an_error(monkeypatch):
    for k, v in COGNITO_ENV.items():
        monkeypatch.setenv(k, v)
    assert m.cognito.configuration_error() == ""
    monkeypatch.delenv("APP_BASE_URL")
    monkeypatch.delenv("COGNITO_CLIENT_SECRET")
    err = m.cognito.configuration_error()
    assert "APP_BASE_URL" in err and "COGNITO_CLIENT_SECRET" in err


@pytest.fixture
def google(monkeypatch):
    """A Google OAuth client whose exchange returns `google.email`."""
    import google_oauth

    state = {"email": "analyst@lendable.io"}
    monkeypatch.setattr(m, "_get_google_client_id", lambda: "gid")
    monkeypatch.setattr(m, "_get_google_client_secret", lambda: "gsecret")
    monkeypatch.setattr(google_oauth, "_get_google_client_id", lambda: "gid")
    monkeypatch.setattr(
        m, "_exchange_google_code", lambda code, redirect_uri: {"access_token": "at"}
    )
    monkeypatch.setattr(m, "_fetch_google_userinfo", lambda at: {"email": state["email"]})
    monkeypatch.setattr(m, "_save_google_tokens", lambda: None)
    m._PENDING_GOOGLE_OAUTH_STATES.clear()
    return state


def _connect_drive(client):
    assert client.get("/api/auth/google/login-url").status_code == 200
    state = next(iter(m._PENDING_GOOGLE_OAUTH_STATES))
    return client.post("/api/auth/google/callback", json={"code": "c", "state": state})


def test_drive_connect_needs_a_cognito_session(client, fake, google):
    assert client.get("/api/auth/google/login-url").status_code == 401
    r = client.post("/api/auth/google/callback", json={"code": "c", "state": "s"})
    assert r.status_code == 401


def test_drive_connect_attaches_to_the_existing_session(client, fake, google):
    _sign_in(client)
    cookie = client.cookies[m._REACT_SESSION_COOKIE]
    r = _connect_drive(client)
    assert r.status_code == 200
    assert r.json() == {"email": "analyst@lendable.io", "name": "An Analyst", "gdrive": True}
    # Same identity cookie, now with Drive connected - not a new Google login.
    assert client.cookies[m._REACT_SESSION_COOKIE] == cookie
    assert client.get("/api/auth/me").json()["gdrive"] is True


def test_drive_connect_refuses_a_different_google_account(client, fake, google):
    _sign_in(client)
    google["email"] = "someone.else@gmail.com"
    r = _connect_drive(client)
    assert r.status_code == 403
    assert client.get("/api/auth/me").json()["gdrive"] is False
