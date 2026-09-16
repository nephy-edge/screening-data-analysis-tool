"""Google OAuth mechanics: the client-credentials lookup, the three token
endpoints (authorize/token/userinfo), and the Drive access-token helpers.
Shared by the sign-in flow (login_gates.py) and the "My Google Drive" file
picker in app.py, which is why the Drive-token helpers live here rather than
with the sign-in-only code in session_cookie.py/login_gates.py."""

import os
import time
from urllib.parse import quote

import requests
import streamlit as st
from ca_bundle import _ca_bundle_path

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
# "My Google Drive" (browsing a file to analyze) only ever needs read access;
# sign-in additionally needs an identity (openid/email/profile) and write
# access scoped to files this app itself creates (drive.file, NOT full drive
# access) so it can save/share a profile snapshot in the user's own Drive.
GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_LOGIN_SCOPE = (
    "openid email profile "
    "https://www.googleapis.com/auth/drive.readonly "
    "https://www.googleapis.com/auth/drive.file"
)
# OAuth callback lands back on the app itself (the redirect URI registered in the
# Google Cloud console) with ?code=...&state=... in the query string. For local
# runs that's http://localhost:8501; override via GOOGLE_REDIRECT_URI for cloud.


def _get_google_client_id():
    try:
        return st.secrets.get("GOOGLE_CLIENT_ID")
    except Exception:
        return os.environ.get("GOOGLE_CLIENT_ID")


def _get_google_client_secret():
    try:
        return st.secrets.get("GOOGLE_CLIENT_SECRET")
    except Exception:
        return os.environ.get("GOOGLE_CLIENT_SECRET")


def _google_redirect_uri() -> str:
    uri = None
    try:
        uri = st.secrets.get("GOOGLE_REDIRECT_URI")
    except Exception:
        pass
    uri = uri or os.environ.get("GOOGLE_REDIRECT_URI")
    return uri or "http://localhost:8501"


def _google_auth_url(state: str, scope: str = GOOGLE_DRIVE_SCOPE) -> str:
    return (
        f"{GOOGLE_AUTH_URL}?client_id={quote(_get_google_client_id())}"
        f"&redirect_uri={quote(_google_redirect_uri())}"
        f"&response_type=code&scope={quote(scope)}"
        f"&access_type=offline&prompt=consent&state={quote(state)}"
    )


def _exchange_google_code(code: str) -> dict:
    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": _get_google_client_id(),
            "client_secret": _get_google_client_secret(),
            "redirect_uri": _google_redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=30,
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    token = resp.json()
    token["expires_at"] = time.time() + int(token.get("expires_in", 3600))
    return token


def _refresh_google_token(refresh_token: str) -> dict:
    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": _get_google_client_id(),
            "client_secret": _get_google_client_secret(),
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    token = resp.json()
    token["expires_at"] = time.time() + int(token.get("expires_in", 3600))
    return token


def _fetch_google_userinfo(access_token: str) -> dict:
    resp = requests.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    return resp.json()


def _gdrive_token() -> dict | None:
    # Deliberately session-only, not cached to a local file: this app can now
    # be signed into by multiple different real people (see
    # _render_google_login_gate), and a shared on-disk token cache would leak
    # whoever signed in most recently to the next visitor's fresh session.
    return st.session_state.get("gdrive_token")


def _gdrive_access_token(token: dict) -> str:
    if time.time() >= token.get("expires_at", 0) - 60 and token.get("refresh_token"):
        refreshed = _refresh_google_token(token["refresh_token"])
        refreshed["refresh_token"] = token["refresh_token"]
        st.session_state["gdrive_token"] = refreshed
        return refreshed["access_token"]
    return token["access_token"]
