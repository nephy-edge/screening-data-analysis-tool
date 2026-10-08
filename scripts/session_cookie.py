"""The "stay signed in" browser cookie and the OAuth `state` param's
single-use CSRF tracking. Both exist because Streamlit's own session_state
cannot survive what this app needs it to: session_state is tied to one
WebSocket connection (gone the moment a tab closes or the server restarts),
and Google's redirect back to this app is a full page load that tears that
connection down and starts a brand-new session with empty state."""

import base64
import json
import secrets

import streamlit as st
import streamlit.components.v1 as components
from google_oauth import _fetch_google_userinfo


def _encode_oauth_state(csrf: str, share_token: str | None) -> str:
    payload = json.dumps({"csrf": csrf, "share": share_token or ""})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_oauth_state(state: str) -> dict:
    try:
        return json.loads(base64.urlsafe_b64decode(state.encode()).decode())
    except Exception:
        return {}


# CSRF tracking for the OAuth "state" param CANNOT live in st.session_state:
# Google's redirect back to this app is a full page load, which tears down
# the WebSocket connection and starts a brand-new Streamlit session with
# empty session_state - so anything stashed in session_state right before
# navigating to Google is already gone by the time the callback arrives, no
# matter whether the link opened in the same tab or a new one. It also can't
# be a plain module-level variable: Streamlit re-executes this whole script's
# top-level code on every single rerun (that's how it works), so a bare
# `= set()` here would be reset to empty on every rerun too, not just once
# per process. @st.cache_resource is the actual "persists for the life of
# this server process, shared across every session" mechanism (same pattern
# _ca_bundle_path already uses above) - a real, single-use, unguessable
# CSRF/replay check with nothing exposed to the client except the
# accompanying `share` payload riding along inside `state`.
@st.cache_resource
def _pending_oauth_states() -> set:
    return set()


def _issue_oauth_state(share_token: str | None) -> str:
    csrf = secrets.token_urlsafe(16)
    _pending_oauth_states().add(csrf)
    return _encode_oauth_state(csrf, share_token)


def _consume_oauth_state(state: str) -> dict | None:
    """Validate and single-use-consume an OAuth `state` value we issued.
    Returns the decoded payload (e.g. {"csrf", "share"}) if valid, else None."""
    decoded = _decode_oauth_state(state)
    csrf = decoded.get("csrf")
    pending = _pending_oauth_states()
    if not csrf or csrf not in pending:
        return None
    pending.discard(csrf)
    return decoded


# "Stay signed in" cookie: st.session_state only lives for as long as this
# browser tab's connection does - closing the tab, or the server restarting
# (which a code deploy does), logs everyone out immediately otherwise. This
# is a plain, JS-readable/writable browser cookie (Streamlit has no Python
# API to set one, only st.context.cookies to read one), short-lived by
# design (4h). For Google auth mode it holds only the short-lived access
# token - deliberately NOT the refresh_token, since a JS-readable cookie can
# be exfiltrated by any script on the origin and a stolen refresh token is a
# long-lived credential, while a stolen access token expires on its own. The
# token is re-validated against Google on restore, so the cookie is only
# good while the access token itself is still fresh.
_SESSION_COOKIE_NAME = "sc_analysis_session"
_SESSION_COOKIE_MAX_AGE_SECONDS = 4 * 3600


def _set_session_cookie_and_reload(payload: dict, redirect_url: str | None = None) -> None:
    """Write the "stay signed in" cookie via a tiny injected script, then
    navigate - not st.rerun(), so the cookie (a synchronous document.cookie
    write) is guaranteed to land before anything else happens, rather than
    racing a Python-triggered rerun. window.top, not window/window.parent,
    because this script runs inside components.html's own iframe - .top
    reaches the real browser tab regardless of whatever else this app is
    nested inside (see the OAuth relay postmortem).

    `redirect_url`, when given, navigates there instead of just reloading
    the current URL - needed right after the Google OAuth callback, whose
    current URL still carries the now-consumed ?code=&state= and must be
    replaced, not reloaded (which would just fail the same one-time state
    check again)."""
    cookie_value = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    cookie_str = (
        f"{_SESSION_COOKIE_NAME}={cookie_value}; path=/; "
        f"max-age={_SESSION_COOKIE_MAX_AGE_SECONDS}; SameSite=Lax; Secure"
    )
    nav_js = (
        f"window.top.location.href = {json.dumps(redirect_url)};"
        if redirect_url
        else "window.top.location.reload();"
    )
    components.html(
        f"""<script>
document.cookie = {json.dumps(cookie_str)};
setTimeout(function() {{ {nav_js} }}, 50);
</script>""",
        height=0,
    )
    st.info("Signed in - continuing...")
    st.stop()


def _clear_session_cookie_and_reload() -> None:
    components.html(
        f"""<script>
document.cookie = "{_SESSION_COOKIE_NAME}=; path=/; max-age=0";
setTimeout(function() {{ window.top.location.reload(); }}, 50);
</script>""",
        height=0,
    )
    st.stop()


def _restore_session_from_cookie() -> None:
    """Best-effort: if a valid "stay signed in" cookie is present, restore
    current_user/auth_mode (and gdrive_token, for Google mode) into
    st.session_state so the login screen never renders. Silently does
    nothing on any missing/invalid/expired/revoked cookie - falls through
    to a normal sign-in, same as if there were no cookie at all."""
    if st.session_state.get("current_user"):
        return
    try:
        raw = st.context.cookies.get(_SESSION_COOKIE_NAME)
        if not raw:
            return
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        email = payload["email"]
        if payload.get("auth_mode") == "google":
            token = {
                "access_token": payload["access_token"],
                "expires_at": payload["expires_at"],
            }
            # Actually validate the token with Google before trusting it: the
            # userinfo call 401s on an expired/revoked/forged token, which falls
            # through to a normal sign-in below. The cookie deliberately no longer
            # carries a refresh_token - a stolen cookie could otherwise be
            # re-refreshed into a fresh long-lived credential - so an expired
            # access token simply means re-signing in.
            _fetch_google_userinfo(token["access_token"])
            st.session_state["gdrive_token"] = token
            st.session_state["auth_mode"] = "google"
        else:
            st.session_state["auth_mode"] = "dummy"
        st.session_state["current_user"] = email
    except Exception:
        pass
