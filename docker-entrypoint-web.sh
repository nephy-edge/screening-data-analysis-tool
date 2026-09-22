#!/bin/sh
# Startup guard for the React + FastAPI web app (Dockerfile.web).
#
# Unlike the Streamlit image, the FastAPI app reads its config straight from
# environment variables (web_api/main.py uses os.environ and load_dotenv()), so
# there is no secrets.toml to render - but we fail loudly and immediately if
# the pieces that make login actually work are missing, rather than starting a
# server whose Google sign-in is quietly broken. This mirrors the guard in the
# Streamlit docker-entrypoint.sh.
set -eu

if [ "${SC_APP_WEB:-1}" = "1" ]; then
    # Google OAuth client (client id + secret). Without both,
    # /api/auth/config returns googleEnabled=false and the frontend shows the
    # unverified "temporary sign-in" fallback instead of Google login.
    : "${GOOGLE_CLIENT_ID:?injected from Secrets Manager}"
    : "${GOOGLE_CLIENT_SECRET:?injected from Secrets Manager}"

    # Absolute origin the SPA + API are served from. Must equal a redirect URI
    # registered on the Google Cloud OAuth client for GOOGLE_CLIENT_ID (the URL
    # path is /auth/google/callback, a browser route the SPA handles).
    : "${APP_BASE_URL:?set to the deployed origin, e.g. https://host.on.aws}"

    # Session-cookie signing secret - a wrong/rotating value just logs everyone
    # out on restart, but we still want it explicit & stable across restarts.
    : "${SC_REACT_SESSION_SECRET:?injected from Secrets Manager}"

    # Derive the Google OAuth redirect URI from the deployed origin so it cannot
    # drift from APP_BASE_URL. web_api/main.py only falls back to localhost when
    # this env var is unset, which would break deployed login.
    export GOOGLE_REACT_REDIRECT_URI="${GOOGLE_REACT_REDIRECT_URI:-${APP_BASE_URL%/}/auth/google/callback}"

    echo "entrypoint: web app with redirect_uri=${GOOGLE_REACT_REDIRECT_URI}" >&2
else
    echo "entrypoint: SC_APP_WEB unset/0 - not validating web-auth secrets" >&2
fi

exec "$@"
