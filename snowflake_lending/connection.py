"""Builds a Snowpark session for the Lending Snowflake pipeline.

Reads its own env vars (see README.md) rather than Kilo's kilo.json - this
integration stays independent of the AI-agent MCP wiring even though both
ultimately authenticate with a Snowflake Programmatic Access Token (PAT)
against the same account.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from snowflake.snowpark import Session

load_dotenv()

# This network intercepts/re-signs outbound TLS with Cisco Umbrella's own CA
# (confirmed by inspecting the cert actually presented for the Snowflake
# host: issuer "Cisco Umbrella Secondary SubCA cpt1-SG"). Windows trusts
# Umbrella's root already; Python's own CA bundle doesn't, so the connector
# fails with "certificate verify failed" during the TLS handshake itself
# (before OCSP/revocation is even reached). _corporate_ca_bundle.pem is
# certifi's public bundle + the Umbrella root exported from the Windows
# certificate store (Cert:\LocalMachine\Root, thumbprint
# C5091132E9ADF8AD3E33932AE60A5C8FA939E824) - regenerate it if this stops
# working (e.g. a different machine, or Umbrella rotates its root).
_CORPORATE_CA_BUNDLE = Path(__file__).resolve().parent / "_corporate_ca_bundle.pem"
if _CORPORATE_CA_BUNDLE.is_file() and not os.environ.get("REQUESTS_CA_BUNDLE"):
    os.environ["REQUESTS_CA_BUNDLE"] = str(_CORPORATE_CA_BUNDLE)

_REQUIRED_ENV = [
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_PAT",
    "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_DATABASE",
    "SNOWFLAKE_SCHEMA",
]


def build_session() -> Session:
    missing = [k for k in _REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing required env var(s): {', '.join(missing)} - see "
            "snowflake_lending/README.md for what to set in .env"
        )

    connection_params = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        # PAT auth: newer snowflake-connector-python versions accept a PAT via
        # this authenticator; if your installed version predates it, the
        # documented fallback is passing the same token as `password` instead
        # with the default authenticator - see README.md's Auth section.
        "authenticator": "PROGRAMMATIC_ACCESS_TOKEN",
        "token": os.environ["SNOWFLAKE_PAT"],
        "warehouse": os.environ["SNOWFLAKE_WAREHOUSE"],
        "database": os.environ["SNOWFLAKE_DATABASE"],
        "schema": os.environ["SNOWFLAKE_SCHEMA"],
        # Corporate networks that TLS-inspect (or otherwise block outbound
        # OCSP responder calls) break the connector's certificate-revocation
        # check with "certificate verify failed", even though the server
        # certificate chain itself is fine - this only skips that revocation
        # check, not certificate verification as a whole. Confirmed needed
        # here via curl showing CRYPT_E_NO_REVOCATION_CHECK against this
        # exact host. Defaults to False (secure) - set SNOWFLAKE_DISABLE_OCSP=true
        # only on a network that hits the issue above.
        "disable_ocsp_checks": os.environ.get("SNOWFLAKE_DISABLE_OCSP", "false").lower()
        == "true",
    }
    role = os.environ.get("SNOWFLAKE_ROLE")
    if role:
        connection_params["role"] = role

    return Session.builder.configs(connection_params).create()
