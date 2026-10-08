"""Renders the sign-in screen (Cognito via Streamlit's native OIDC support
when the container is running on the ECS Express deployment, real Google
OAuth when a Google client is configured, otherwise a temporary
self-declared-email fallback) and reports the active auth mode for the rest
of the app to branch on."""

import os

import streamlit as st
from google_oauth import (
    GOOGLE_LOGIN_SCOPE,
    _exchange_google_code,
    _fetch_google_userinfo,
    _get_google_client_id,
    _get_google_client_secret,
    _google_auth_url,
)
from session_cookie import (
    _consume_oauth_state,
    _issue_oauth_state,
    _restore_session_from_cookie,
    _set_session_cookie_and_reload,
)


def _render_dummy_login_gate() -> str:
    """Fallback identity when no Google OAuth client is configured. This is
    NOT a security boundary: the "user" here is whatever the visitor types
    in, not verified by anyone - only used so per-user saved progress can be
    built and tested before Google sign-in (or other real SSO) is set up."""
    st.subheader("Sign in")
    st.caption(
        "Temporary sign-in while Google sign-in is being set up - this only identifies "
        "you so your uploaded file, mapping, and Ask AI chat can be saved and picked back "
        "up later. It is not a password and does not verify who you are."
    )
    email = st.text_input(
        "Your Lendable email", key="login_email_input", placeholder="you@lendable.io"
    )
    if st.button("Continue", key="login_submit"):
        if email.strip():
            clean_email = email.strip().lower()
            st.session_state["current_user"] = clean_email
            st.session_state["auth_mode"] = "dummy"
            _set_session_cookie_and_reload({"email": clean_email, "auth_mode": "dummy"})
        else:
            st.warning("Enter your email to continue.")
    st.stop()
    return ""


def _render_google_login_gate() -> str:
    """Real "Sign in with Google" - Google verifies the email, unlike the
    dummy text-box fallback. The same OAuth grant also carries Drive scopes
    (drive.readonly for "My Google Drive" file picking, drive.file for
    saving/sharing a profile snapshot in the user's own Drive - see
    scripts/drive_store.py), so signing in doubles as connecting Drive; nothing
    else has to separately "Connect Google Drive" afterwards.

    A share link (?share=<id>) opened while signed out would otherwise lose
    that query param across the Google redirect round-trip (the redirect URI
    registered in Google Cloud is fixed and comes back with only ?code=&
    state=) - so it's folded into the opaque `state` value and restored onto
    st.query_params right before the post-login rerun."""
    code = st.query_params.get("code")
    state = st.query_params.get("state")
    if code and state:
        decoded = _consume_oauth_state(state)
        if decoded is not None:
            try:
                with st.spinner("Signing in with Google..."):
                    token = _exchange_google_code(code)
                    userinfo = _fetch_google_userinfo(token["access_token"])
            except Exception as e:
                st.query_params.clear()
                st.error(f"Google sign-in failed: {e}")
                st.stop()
                return ""
            email = (userinfo.get("email") or "").strip().lower()
            if not email:
                st.query_params.clear()
                st.error("Google didn't return an email address for this account.")
                st.stop()
                return ""
            st.session_state["current_user"] = email
            st.session_state["auth_mode"] = "google"
            st.session_state["gdrive_token"] = token
            share_token = decoded.get("share")
            base_url = (st.context.url or "").split("?")[0] or "/"
            redirect_url = f"{base_url}?share={share_token}" if share_token else base_url
            _set_session_cookie_and_reload(
                {
                    "email": email,
                    "auth_mode": "google",
                    "access_token": token["access_token"],
                    "expires_at": token["expires_at"],
                },
                redirect_url=redirect_url,
            )
            return ""
        st.query_params.clear()

    st.subheader("Sign in")
    st.caption(
        "Sign in with your Lendable Google account. This also lets the app save your "
        "progress to your own Google Drive, and load analyses shared with you from there."
    )
    state_param = _issue_oauth_state(st.query_params.get("share"))
    auth_url = _google_auth_url(state_param, scope=GOOGLE_LOGIN_SCOPE)
    # st.link_button (opens a new tab, target="_blank") - not a same-tab/
    # target="_top" anchor: Streamlit Community Cloud renders the app inside
    # a sandboxed wrapping frame that doesn't permit top-level navigation
    # from embedded content, so a raw <a target="_top"> link was silently
    # inert there (worked locally, where nothing sandboxes it). A new tab
    # is a proven-safe escape hatch from any such sandbox on every host.
    st.link_button("Sign in with Google", auth_url)
    st.stop()
    return ""


def _render_cognito_login_gate() -> str:
    """Streamlit's native OIDC login (st.login/st.user), backed by AWS
    Cognito. Only reached when COGNITO_CLIENT_ID is set - i.e. the ECS
    Express deployment, where docker-entrypoint.sh has rendered the [auth]
    block those credentials feed into - so local dev and any Streamlit
    Cloud / Google-sign-in deployment are unaffected.

    Fails closed: st.user only gains is_logged_in once [auth] is actually
    configured; without it, reading the attribute raises AttributeError. A
    bare getattr(st.user, "is_logged_in", False) would fail OPEN instead -
    serving an internet-facing app with no gate at all - so this checks
    hasattr first and refuses to serve rather than let that exception fall
    through."""
    if not hasattr(st.user, "is_logged_in"):
        st.error("Authentication is misconfigured - refusing to serve.")
        st.stop()
        return ""
    if not st.user.is_logged_in:
        st.subheader("Sign in")
        st.caption("Sign in with your Lendable account to continue.")
        st.button("Log in", on_click=st.login, type="primary")
        st.stop()
        return ""
    email = (st.user.email or "").strip().lower()
    st.session_state["current_user"] = email
    st.session_state["auth_mode"] = "cognito"
    return email


def _render_login_gate() -> str:
    if os.environ.get("COGNITO_CLIENT_ID"):
        return _render_cognito_login_gate()
    _restore_session_from_cookie()
    if st.session_state.get("current_user"):
        return st.session_state["current_user"]
    if _get_google_client_id() and _get_google_client_secret():
        return _render_google_login_gate()
    return _render_dummy_login_gate()


def _auth_mode() -> str:
    return st.session_state.get("auth_mode", "dummy")
