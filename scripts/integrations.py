"""Thin clients for the external services app.py talks to that aren't the
AI model itself: live FX rates, Slack feedback, and the Snowflake warehouse
data source (see scripts/warehouse.py - still named "redshift" throughout for
backward compatibility with the React client's route names). Each secret
lookup falls back from Streamlit secrets to an environment variable so the
same code works locally (.env) and on Streamlit Cloud (secrets.toml)."""

import os
import re
from datetime import datetime as _dt

import pandas as pd
import requests
import streamlit as st
import warehouse
import warehouse_jobs
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


def _redshift_available() -> bool:
    """Whether the warehouse data source can be offered at all. Previously a
    check for a configured proxy URL + API key; now a check that Snowflake
    credentials are present, since this app talks to Snowflake directly."""
    return warehouse.is_configured()


@st.cache_data(ttl=300, show_spinner=False)
def _redshift_list_borrowers() -> list:
    """List available borrower slugs. Each borrower is its own schema in
    LOAN_TAPE_DATABASE holding a LOANS table (e.g. MAESTRO_SRC.LEASY.LOANS),
    so the list is derived from INFORMATION_SCHEMA rather than stored
    anywhere - it reflects current warehouse metadata and changes only when
    a new deal onboards.

    Cached for 5min because this data-source branch re-runs on every
    Streamlit rerun while selected, so without it every keystroke or click
    would re-query the warehouse for a list that is effectively static. The
    old proxy held a second, server-side cache for the same reason; going
    direct means this one is the only one left."""
    payload = _redshift_query(
        "SELECT schema_name "
        f"FROM {warehouse.LOAN_TAPE_DATABASE}.INFORMATION_SCHEMA.SCHEMATA s "
        "WHERE EXISTS (SELECT 1 FROM "
        f"{warehouse.LOAN_TAPE_DATABASE}.INFORMATION_SCHEMA.TABLES t "
        "WHERE t.table_schema = s.schema_name "
        f"AND t.table_name = '{warehouse.LOAN_TAPE_TABLE}') "
        "ORDER BY schema_name",
        500,
    )
    return sorted({row[0].lower() for row in payload["rows"]})


@st.cache_data(ttl=300, show_spinner=False)
def _redshift_fetch_loan_tape(borrower: str, limit: int) -> pd.DataFrame:
    """Fetch one borrower's loan tape. Cached client-side so re-running the
    script for an unrelated widget change elsewhere doesn't re-run a
    potentially slow warehouse query for the same borrower/limit.

    `borrower` is checked against the real borrower list rather than only
    pattern-matched, so a typo fails with a useful message instead of a bare
    SQL error - and so this can never be pointed at an arbitrary schema.
    Snowflake identifiers are case-insensitive unquoted, so a lowercase slug
    like `leasy` resolves to MAESTRO_SRC.LEASY.LOANS."""
    if not re.fullmatch(r"[a-z0-9_]+", borrower):
        raise ValueError("Invalid borrower slug")
    known = set(_redshift_list_borrowers())
    if borrower not in known:
        raise ValueError(f"Unknown borrower '{borrower}'. Available: {', '.join(sorted(known))}")
    payload = _redshift_query(
        f"SELECT * FROM {warehouse.LOAN_TAPE_DATABASE}.{borrower}.{warehouse.LOAN_TAPE_TABLE}",
        limit,
    )
    return pd.DataFrame(payload["rows"], columns=payload["columns"])


def _redshift_query(sql: str, limit: int) -> dict:
    """Run one query against Snowflake and return {"columns", "rows", ...} -
    shared by every function below that needs its own SQL, so validation and
    the statement-API plumbing live in one place (scripts/warehouse.py).

    Callers are responsible for their own SQL string safety (see
    _redshift_table_sql's docstring); warehouse.validate_query's read-only
    and schema-allowlist checks are the real enforcement boundary."""
    return warehouse.query(sql, limit)


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
    database: str,
    schema: str,
    table: str,
    limit: int,
    where: str | None = None,
) -> str:
    """Start _redshift_table_sql's query as a background job and return its
    job id, for polling real partition-by-partition progress via
    _redshift_job_status instead of blocking on one call for however long a
    big, many-partition fetch takes. See scripts/warehouse_jobs.py for why
    the job runs in its own process rather than a thread."""
    return warehouse_jobs.start(_redshift_table_sql(database, schema, table, where), limit)


def _redshift_job_status(job_id: str) -> dict:
    """Poll one job started by _redshift_start_table_job - returns
    {status, partitionsDone, partitionsTotal, ...}, plus columns/rows once
    it's done."""
    return warehouse_jobs.status(job_id)


@st.cache_data(ttl=300, show_spinner=False)
def _redshift_list_schemas(database: str = "MAESTRO_SRC") -> list[str]:
    """List every schema in `database` via INFORMATION_SCHEMA.SCHEMATA - the
    dropdown source for "Other table"'s schema picker (unlike
    _redshift_list_borrowers, which only lists schemas that own a LOANS
    table). INFORMATION_SCHEMA itself is excluded since it's metadata, not
    a data schema a user would ever want to pick."""
    payload = _redshift_query(
        f"SELECT schema_name FROM {database}.INFORMATION_SCHEMA.SCHEMATA ORDER BY schema_name",
        500,
    )
    return [row[0] for row in payload["rows"] if row[0] != "INFORMATION_SCHEMA"]


@st.cache_data(ttl=300, show_spinner=False)
def _redshift_list_tables(schema: str, database: str = "MAESTRO_SRC") -> list[str]:
    """List every table in `database.schema` via INFORMATION_SCHEMA.TABLES -
    the dropdown source for "Other table"'s table picker, once a schema is
    chosen. `schema` is validated as a plain identifier by the caller before
    reaching here (see redshift_tables in web_api/main.py)."""
    payload = _redshift_query(
        f"SELECT table_name FROM {database}.INFORMATION_SCHEMA.TABLES "
        f"WHERE table_schema = '{schema}' ORDER BY table_name",
        500,
    )
    return [row[0] for row in payload["rows"]]


@st.cache_data(ttl=300, show_spinner=False)
def _redshift_list_columns(schema: str, table: str, database: str = "MAESTRO_SRC") -> list[str]:
    """List every column in `database.schema.table` via INFORMATION_SCHEMA.
    COLUMNS - grounds the natural-language filter suggestion (_suggest_
    redshift_filter) in real column names instead of letting the model
    guess/hallucinate them. Not data_type: these are raw staging tables
    where every column - dates, amounts, everything - is declared TEXT
    (confirmed live), so type info would just be noise, not a useful
    signal for what kind of comparison makes sense."""
    payload = _redshift_query(
        f"SELECT column_name FROM {database}.INFORMATION_SCHEMA.COLUMNS "
        f"WHERE table_schema = '{schema}' AND table_name = '{table}' ORDER BY ordinal_position",
        500,
    )
    return [row[0] for row in payload["rows"]]
