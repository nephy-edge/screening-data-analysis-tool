#!/bin/sh
# Startup guard for the React + FastAPI web app (Dockerfile.web).
#
# The FastAPI app reads its config straight from environment variables
# (web_api/main.py, web_api/cognito.py), so there is no secrets.toml to render
# - but we fail loudly and immediately if the pieces that make login actually
# work are missing, rather than starting a server whose sign-in is quietly
# broken or absent. This mirrors the guard in the Streamlit docker-entrypoint.sh.
set -eu

if [ -n "${COGNITO_CLIENT_ID:-}" ]; then
    # All injected by the ECS Express module (lendableio/terraform,
    # ecs-express/modules/ecs-express-app). APP_BASE_URL is set by the second
    # Terraform apply, because Express Mode only assigns the service's public
    # URL after the service exists; Cognito's redirect_uri is built from it.
    : "${APP_BASE_URL:?set by the second terraform apply - see the runbook}"
    : "${APP_SESSION_SECRET:?injected from Secrets Manager}"
    : "${COGNITO_CLIENT_SECRET:?injected from Secrets Manager}"
    : "${COGNITO_ISSUER:?injected as an environment variable}"
    : "${COGNITO_DOMAIN:?injected as an environment variable}"

    # Google is optional here: it only powers "Connect Google Drive". When it
    # is configured, derive its redirect URI from the deployed origin so it
    # cannot drift from APP_BASE_URL (web_api/main.py otherwise falls back to
    # localhost). The Google OAuth client must list this exact URI.
    export GOOGLE_REACT_REDIRECT_URI="${GOOGLE_REACT_REDIRECT_URI:-${APP_BASE_URL%/}/auth/google/callback}"

    echo "entrypoint: cognito sign-in via ${APP_BASE_URL%/}/oauth2callback" >&2
elif [ "${SC_ALLOW_NO_AUTH:-0}" = "1" ]; then
    # Explicit, deliberate opt-out - a local `docker run` or the CI health
    # check, where there is no Cognito pool to point at.
    echo "entrypoint: SC_ALLOW_NO_AUTH=1 - starting WITHOUT Cognito" >&2
else
    # Fail closed. Without Cognito the app offers the unverified "temporary
    # sign-in" (any typed email gets a session), which on a public URL opens
    # every saved client loan tape to anyone with the link.
    echo "entrypoint: refusing to start - COGNITO_CLIENT_ID is unset, so anyone" >&2
    echo "  could sign in as anyone via the temporary sign-in. Set the Cognito" >&2
    echo "  variables, or SC_ALLOW_NO_AUTH=1 (never on a deployed service)." >&2
    exit 1
fi

exec "$@"
