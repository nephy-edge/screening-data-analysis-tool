#!/bin/sh
# Render Streamlit's [auth] block before starting the server.
#
# Streamlit reads authentication config only from secrets.toml via st.secrets --
# it will not pick [auth] up from environment variables. ECS injects the values
# as env vars and secrets, so the file has to be built here at start-up. It is
# never committed; .streamlit/secrets.toml is gitignored.
#
# With COGNITO_CLIENT_ID unset (a local `docker run`, say) no file is written
# and the app starts unauthenticated, matching the gate in app.py.
set -eu

if [ -n "${COGNITO_CLIENT_ID:-}" ]; then
    # Fail loudly and immediately rather than starting a server whose login is
    # quietly broken. APP_BASE_URL in particular is easy to forget: it is set
    # by a second Terraform apply, because Express Mode only assigns the
    # service's public URL after the service exists.
    : "${APP_BASE_URL:?set by the second terraform apply - see RUNBOOK section 3}"
    : "${APP_SESSION_SECRET:?injected from Secrets Manager}"
    : "${COGNITO_CLIENT_SECRET:?injected from Secrets Manager}"
    : "${COGNITO_ISSUER:?injected as an environment variable}"

    mkdir -p /app/.streamlit
    # Owner-read-only: the file holds the OIDC client secret and the cookie
    # signing key.
    umask 077
    cat > /app/.streamlit/secrets.toml <<EOF
[auth]
redirect_uri = "${APP_BASE_URL}/oauth2callback"
cookie_secret = "${APP_SESSION_SECRET}"
client_id = "${COGNITO_CLIENT_ID}"
client_secret = "${COGNITO_CLIENT_SECRET}"
server_metadata_url = "${COGNITO_ISSUER}/.well-known/openid-configuration"
EOF
    echo "entrypoint: wrote .streamlit/secrets.toml for ${APP_BASE_URL}/oauth2callback" >&2
else
    echo "entrypoint: COGNITO_CLIENT_ID unset - starting WITHOUT authentication" >&2
fi

exec "$@"
