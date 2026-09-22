"""Thin clients for the external services app.py talks to that aren't the
AI model itself: live FX rates, Slack feedback, and the Redshift Query API
data source. Each secret lookup falls back from Streamlit secrets to an
environment variable so the same code works locally (.env) and on Streamlit
Cloud (secrets.toml)."""

import os
from datetime import datetime as _dt

import pandas as pd
import requests
import streamlit as st
from ca_bundle import _ca_bundle_path

import config as app_config

_FX_API_URL = app_config.FX["api_url"]


@st.cache_data(ttl=app_config.FX["cache_ttl_seconds"])
def _fetch_usd_fx_rates() -> dict:
    """Live USD exchange rates from a free, keyless, open API (open.er-api.com).
    Returns {currency_code: units of that currency per 1 USD}; cached for an
    hour so repeated reruns/uploads don't hammer the endpoint."""
    resp = requests.get(
        _FX_API_URL, timeout=app_config.FX["request_timeout_seconds"], verify=_ca_bundle_path()
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("result") != "success":
        raise ValueError(f"FX API returned {data.get('result', 'an error')}")
    return data["rates"]


def _get_slack_webhook_url():
    try:
        return st.secrets["SLACK_WEBHOOK_URL"]
    except Exception:
        return os.environ.get("SLACK_WEBHOOK_URL")


def _send_slack_feedback(message: str, model: str, user: str = "") -> tuple[bool, str]:
    """POST a feedback submission to the configured Slack Incoming Webhook."""
    webhook = _get_slack_webhook_url()
    if not webhook:
        return False, "SLACK_WEBHOOK_URL not configured."
    who = f" — {user.strip()}" if user.strip() else ""
    payload = {
        "text": (
            f"*Feedback* ({model}){who}\n{message.strip()}\n"
            f"_Submitted {_dt.now().strftime('%Y-%m-%d %H:%M')}_"
        )
    }
    try:
        resp = requests.post(
            webhook,
            json=payload,
            timeout=15,
            verify=_ca_bundle_path(),
        )
        resp.raise_for_status()
        return True, ""
    except Exception as e:
        return False, str(e)


def _get_redshift_api_url():
    try:
        return st.secrets["REDSHIFT_API_URL"]
    except Exception:
        return os.environ.get("REDSHIFT_API_URL")


def _get_redshift_api_key():
    try:
        return st.secrets["REDSHIFT_API_KEY"]
    except Exception:
        return os.environ.get("REDSHIFT_API_KEY")


@st.cache_data(ttl=300, show_spinner=False)
def _redshift_list_borrowers(api_url: str, api_key: str) -> list:
    """List available borrower slugs from the Redshift Query API's /borrowers
    endpoint (see Verifications/redshift-api). Cached client-side for 5min on
    top of that API's own 10min server-side cache - this data-source branch
    re-runs on every script rerun while selected, so without caching here
    every keystroke/click would re-hit the API even though the borrower list
    only changes when a new deal onboards."""
    resp = requests.get(
        f"{api_url.rstrip('/')}/borrowers",
        headers={"X-API-Key": api_key},
        timeout=30,
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    return resp.json().get("borrowers", [])


@st.cache_data(ttl=300, show_spinner=False)
def _redshift_fetch_loan_tape(
    api_url: str, api_key: str, borrower: str, limit: int
) -> pd.DataFrame:
    """Fetch one borrower's loan tape from the Redshift Query API's
    /loan-tape endpoint. Cached client-side so re-running the script for an
    unrelated widget change elsewhere doesn't re-run a potentially slow
    warehouse query against the same borrower/limit."""
    resp = requests.get(
        f"{api_url.rstrip('/')}/loan-tape",
        params={"borrower": borrower, "limit": limit},
        headers={"X-API-Key": api_key},
        # A bit above that API's own QUERY_TIMEOUT_SECONDS (120s default) so
        # the server's own timeout error comes back to us, rather than this
        # client cutting the connection first and masking it as a generic
        # read timeout.
        timeout=150,
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    payload = resp.json()
    return pd.DataFrame(payload["rows"], columns=payload["columns"])


def _redshift_query(api_url: str, api_key: str, sql: str, limit: int) -> dict:
    """POST one query to the Query API's /query endpoint and return its raw
    JSON payload ({"columns", "rows", ...}) - shared by every function below
    that runs its own SQL against the warehouse, so the request plumbing
    (timeout, TLS bundle, error surfacing) lives in one place. Callers are
    responsible for their own SQL string safety (see _redshift_table_sql's
    docstring); the API's read-only + schema-allowlist checks are the real
    enforcement boundary regardless."""
    resp = requests.post(
        f"{api_url.rstrip('/')}/query",
        json={"sql": sql, "limit": limit},
        headers={"X-API-Key": api_key},
        timeout=150,
        verify=_ca_bundle_path(),
    )
    if not resp.ok:
        # The API returns a clear {"detail": "..."} on a 4xx (e.g. "'limit'
        # must be >= 1") - resp.raise_for_status() alone discards that body
        # and raises a bare "400 Client Error", which reached the user as an
        # unhelpful "Couldn't fetch ... from the warehouse: 400 Client Error:
        # Bad Request" with no indication of why.
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = None
        raise requests.HTTPError(detail or f"{resp.status_code} {resp.reason}", response=resp)
    return resp.json()


def _redshift_table_sql(database: str, schema: str, table: str, where: str | None = None) -> str:
    """Build the SELECT for any table under the Query API's schema allowlist
    (MAESTRO_SRC*, MAESTRO_*_ANA, MAESTRO_*_MRT), for tables outside the
    fixed <database>.<borrower>.LOANS convention _redshift_fetch_loan_tape
    covers - e.g. MAESTRO_SRC.SALMON.salmon_credit_lines, where "SALMON"
    isn't a loan-tape borrower (no LOANS table, so it never appears in
    /borrowers) but the schema still holds other tables worth pulling in
    directly. database/schema/table are validated as plain identifiers by
    the caller before reaching here; the API's own read-only + schema-
    allowlist checks (redshift-api/app/main.py's _validate_read_only/
    _validate_schema_allowlist) are the real enforcement boundary, not this
    string formatting - `where` is passed through as raw SQL the same way,
    on the same reasoning: this tool's user is the same person who'd
    receive the fetched rows anyway, and already has equal-or-greater
    direct query access to this warehouse (see the Snowsight screenshot
    that started this feature), so there's no privilege boundary being
    crossed by not sanitizing filter text beyond what the API itself
    already enforces.

    `where` matters most when the caller passes a `limit` below the table's
    real row count - without it, a partial fetch silently means "whatever
    rows Snowflake happened to scan first", not a meaningful slice; a filter
    (a date range, a status, an active-only condition) makes that cutoff
    land on rows the caller actually chose instead of an arbitrary scan
    order. The warehouse API itself has no row cap - see db.py's partition
    handling in the sibling redshift-api project.

    One query, not split into concurrent LIMIT/OFFSET chunks - tried that
    (see git history) and measured it corrupting the result: LIMIT/OFFSET
    without ORDER BY has no guarantee that separate queries scan a table in
    the same order, and on salmon_installment_loans it didn't - a 2M-row
    chunked fetch came back with 1.1M duplicate LOANIDs, more than half the
    "rows" repeats, while being no faster than one query (both ~62s, since
    redshift-api already parallelizes the Snowflake-partition fetches
    *within* one query - see its db.py). Forcing a stable order (ORDER BY)
    would fix the duplication but requires sorting the full table before
    paging through it, which is slower than just running one query."""
    sql = f"SELECT * FROM {database}.{schema}.{table}"
    if where and where.strip():
        sql += f" WHERE {where.strip()}"
    return sql


def _redshift_start_table_job(
    api_url: str,
    api_key: str,
    database: str,
    schema: str,
    table: str,
    limit: int,
    where: str | None = None,
) -> str:
    """Start _redshift_table_sql's query as a background job on the
    warehouse API and return its job id, for polling real partition-by-
    partition progress via _redshift_job_status instead of blocking on one
    request for however long a big, many-partition fetch takes."""
    resp = requests.post(
        f"{api_url.rstrip('/')}/query/jobs",
        json={"sql": _redshift_table_sql(database, schema, table, where), "limit": limit},
        headers={"X-API-Key": api_key},
        timeout=30,
        verify=_ca_bundle_path(),
    )
    if not resp.ok:
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = None
        raise requests.HTTPError(detail or f"{resp.status_code} {resp.reason}", response=resp)
    return resp.json()["jobId"]


def _redshift_job_status(api_url: str, api_key: str, job_id: str) -> dict:
    """Poll one job started by _redshift_start_table_job - see redshift-api's
    /query/jobs/{id} for the {status, partitionsDone, partitionsTotal, ...}
    shape."""
    resp = requests.get(
        f"{api_url.rstrip('/')}/query/jobs/{job_id}",
        headers={"X-API-Key": api_key},
        timeout=30,
        verify=_ca_bundle_path(),
    )
    if not resp.ok:
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = None
        raise requests.HTTPError(detail or f"{resp.status_code} {resp.reason}", response=resp)
    return resp.json()


@st.cache_data(ttl=300, show_spinner=False)
def _redshift_list_schemas(api_url: str, api_key: str, database: str = "MAESTRO_SRC") -> list[str]:
    """List every schema in `database` via INFORMATION_SCHEMA.SCHEMATA - the
    dropdown source for "Other table"'s schema picker (unlike /borrowers,
    which only lists schemas that happen to own a LOANS table). INFORMATION_
    SCHEMA itself is excluded since it's metadata, not a data schema a user
    would ever want to pick."""
    payload = _redshift_query(
        api_url,
        api_key,
        f"SELECT schema_name FROM {database}.INFORMATION_SCHEMA.SCHEMATA ORDER BY schema_name",
        500,
    )
    return [row[0] for row in payload["rows"] if row[0] != "INFORMATION_SCHEMA"]


@st.cache_data(ttl=300, show_spinner=False)
def _redshift_list_tables(
    api_url: str, api_key: str, schema: str, database: str = "MAESTRO_SRC"
) -> list[str]:
    """List every table in `database.schema` via INFORMATION_SCHEMA.TABLES -
    the dropdown source for "Other table"'s table picker, once a schema is
    chosen. `schema` is validated as a plain identifier by the caller before
    reaching here (see redshift_tables in web_api/main.py)."""
    payload = _redshift_query(
        api_url,
        api_key,
        f"SELECT table_name FROM {database}.INFORMATION_SCHEMA.TABLES "
        f"WHERE table_schema = '{schema}' ORDER BY table_name",
        500,
    )
    return [row[0] for row in payload["rows"]]


@st.cache_data(ttl=300, show_spinner=False)
def _redshift_list_columns(
    api_url: str, api_key: str, schema: str, table: str, database: str = "MAESTRO_SRC"
) -> list[str]:
    """List every column in `database.schema.table` via INFORMATION_SCHEMA.
    COLUMNS - grounds the natural-language filter suggestion (_suggest_
    redshift_filter) in real column names instead of letting the model
    guess/hallucinate them. Not data_type: these are raw staging tables
    where every column - dates, amounts, everything - is declared TEXT
    (confirmed live), so type info would just be noise, not a useful
    signal for what kind of comparison makes sense."""
    payload = _redshift_query(
        api_url,
        api_key,
        f"SELECT column_name FROM {database}.INFORMATION_SCHEMA.COLUMNS "
        f"WHERE table_schema = '{schema}' AND table_name = '{table}' ORDER BY ordinal_position",
        500,
    )
    return [row[0] for row in payload["rows"]]
