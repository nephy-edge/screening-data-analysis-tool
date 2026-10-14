"""Cognito OIDC sign-in for the deployed React app (ECS Express).

Express Mode owns the load balancer and exposes no listener-rule API, so there
is no ALB `authenticate-cognito` action in front of this service: the app runs
the authorization-code flow itself. Ported from verifications_automation's
api/cognito.py, which runs this same flow against the same user pool.

Everything is injected by Terraform (lendableio/terraform,
ecs-express/app_screening_tool.tf + modules/ecs-express-app):

    COGNITO_DOMAIN         Managed Login base URL       env
    COGNITO_ISSUER         https://cognito-idp.<region>.amazonaws.com/<pool>
    COGNITO_CLIENT_ID      Secrets Manager, key client_id
    COGNITO_CLIENT_SECRET  Secrets Manager, key client_secret
    APP_SESSION_SECRET     Secrets Manager, 64-char random
    APP_BASE_URL           the service's own public URL (second apply)

With COGNITO_CLIENT_ID unset - a local run, or the test suite - nothing here
is active and the local Google / temporary sign-in paths apply instead.

Read from the environment on every call (not at import) so the tests can
switch Cognito on and off per test.
"""

from __future__ import annotations

import os
from functools import lru_cache
from urllib.parse import quote

from authlib.integrations.starlette_client import OAuth

# Cognito already has this exact path registered as the app client's callback
# (it is the path Streamlit hard-codes). Keeping it means the cutover needs no
# Cognito change, and rolling back to the Streamlit image keeps working.
CALLBACK_PATH = "/oauth2callback"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip().rstrip("/")


def is_configured() -> bool:
    """True when the app must enforce Cognito sign-in. Keyed on the client id
    alone so the switch is unambiguous; configuration_error() is what makes a
    half-configured deployment fail closed rather than fall through to the
    unverified local sign-in."""
    return bool(_env("COGNITO_CLIENT_ID"))


def configuration_error() -> str:
    """Why Cognito sign-in cannot work, or "" when it can (or is off)."""
    if not is_configured():
        return ""
    missing = [
        name
        for name in (
            "COGNITO_CLIENT_SECRET",
            "COGNITO_ISSUER",
            "COGNITO_DOMAIN",
            "APP_SESSION_SECRET",
            "APP_BASE_URL",
        )
        if not _env(name)
    ]
    if missing:
        return f"Cognito is configured but {', '.join(missing)} is not set"
    return ""


@lru_cache(maxsize=1)
def client():
    """The Authlib Cognito client, registered on first use. Authlib verifies
    the ID token's signature against the pool's JWKS and checks
    iss/aud/exp/nonce inside authorize_access_token."""
    oauth = OAuth()
    oauth.register(
        name="cognito",
        server_metadata_url=f"{_env('COGNITO_ISSUER')}/.well-known/openid-configuration",
        client_id=_env("COGNITO_CLIENT_ID"),
        client_secret=_env("COGNITO_CLIENT_SECRET"),
        # The scopes the app client grants; asking for more is invalid_scope.
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth.cognito


def redirect_uri() -> str:
    """Cognito matches this against the registered callback character for
    character, so it is built from APP_BASE_URL (required when Cognito is on)
    rather than from request headers, which behind the load balancer would
    give the container's internal origin."""
    return f"{_env('APP_BASE_URL')}{CALLBACK_PATH}"


def logout_url() -> str:
    """Cognito's hosted logout, returning to the app root - "/" is the logout
    URL Terraform registers on the app client (cognito_logout_path)."""
    return (
        f"{_env('COGNITO_DOMAIN')}/logout?client_id={quote(_env('COGNITO_CLIENT_ID'))}"
        f"&logout_uri={quote(_env('APP_BASE_URL') + '/', safe='')}"
    )


def safe_next(value: str | None) -> str:
    """Where to land after sign-in: a same-origin relative path only (so a
    share link survives the round trip), never an absolute or
    protocol-relative URL - otherwise /api/auth/login?next=//evil.example is
    an open redirect wearing this app's login page."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value
