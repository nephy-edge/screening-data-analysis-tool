"""Direct Snowflake SQL Statement API client for the warehouse data source.

Replaces the local `redshift-api` proxy (Verifications/redshift-api) that
this app used to POST to on 127.0.0.1:8001. That service still exists and
still serves its other consumers; it is simply no longer in this app's path.
It had long since stopped being a Redshift service - its own data layer
already spoke to Snowflake's Statement API over REST - so going direct
removes a transport hop and a second process to keep alive, not a
capability. The
"redshift" naming survives in `integrations.py`/`web_api` for backward
compatibility with the React client's existing route names.

Everything here is a port of that service's `app/db.py` plus the read-only
guards from its `app/main.py`. The partition handling in particular is
carried over rather than rewritten: it encodes a live-diagnosed truncation
bug (see `_exec_once`) that is easy to reintroduce from scratch.

Auth is a Programmatic Access Token (PAT) sent as a bearer token, the same
credential `snowflake_lending/connection.py` uses - this org signs in via
SSO, so a PAT is the only non-interactive path.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from ca_bundle import _ca_bundle_path


class WarehouseError(RuntimeError):
    """Any failure talking to (or validating a query against) the warehouse.

    Deliberately not an HTTPException: this module is imported by both the
    Streamlit app and `web_api`, so it stays framework-free and each caller
    maps it to its own error surface.
    """


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


QUERY_TIMEOUT_SECONDS = int(_env("QUERY_TIMEOUT_SECONDS", "120"))
DEFAULT_RESULTS_LIMIT = int(_env("DEFAULT_RESULTS_LIMIT", "100"))

# Memory-safety ceiling on rows x columns for one result. Not a business
# rule about how much data an analyst may pull: fetching
# MAESTRO_SRC.SALMON.SALMON_CREDIT_LINES in full (9.1M rows x 37 columns)
# grew one fetch's Python/JSON materialisation to ~19GB+, exhausted all
# 32GB of RAM, and got the process killed by the OS mid-fetch - which also
# left the job stuck "running" forever, since nothing survived to report
# done or error. Cells rather than rows because every column in these raw
# tables is TEXT, so a narrow table can safely go far wider than a wide one.
MAX_RESULT_CELLS = int(_env("MAX_RESULT_CELLS", "150000000"))

# Schemas the PAT's role may be queried against (defensive allowlist, kept
# even though the role itself is read-only - a typo'd database name should
# fail here rather than reach the warehouse). The warehouse is organised by
# layer and originator: MAESTRO_SRC.<borrower> is the raw landing zone the
# loan tapes live in, MAESTRO_*_ANA / MAESTRO_*_MRT the validated
# analytics/mart layers. Comma-separated via ALLOWED_SCHEMAS so a new layer
# never needs a code edit; a blank value falls back to the defaults rather
# than locking out every schema.
ALLOWED_SCHEMAS: tuple[str, ...] = tuple(
    s.strip()
    for s in (
        _env("ALLOWED_SCHEMAS").strip()
        or "MAESTRO_SRC,MAESTRO_DEV_ANA,MAESTRO_UAT_ANA,MAESTRO_PRD_ANA,"
        "MAESTRO_DEV_MRT,MAESTRO_UAT_MRT,MAESTRO_PRD_MRT"
    ).split(",")
    if s.strip()
)

# Loan-tape layout: each borrower is its own schema inside this database,
# holding a single LOANS table (e.g. MAESTRO_SRC.LEASY.LOANS).
#
# Deliberately NOT defaulted from SNOWFLAKE_DATABASE, unlike the proxy this
# was ported from. In this repo SNOWFLAKE_DATABASE is the session default
# for the Snowpark stored-procedure pipeline (MAESTRO_DEV_ANA, see
# snowflake_lending/README.md), which holds no borrower schemas at all -
# inheriting it made the borrower list silently come back empty rather than
# erroring, which is exactly the kind of quiet wrong answer worth avoiding.
LOAN_TAPE_DATABASE = _env("LOAN_TAPE_DATABASE") or "MAESTRO_SRC"
LOAN_TAPE_TABLE = _env("LOAN_TAPE_TABLE", "LOANS")

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|TRUNCATE|ALTER|CREATE|GRANT|"
    r"REVOKE|COPY|UNLOAD|CALL|VACUUM|REINDEX|REFRESH|COMMENT)\b",
    re.IGNORECASE,
)


def is_configured() -> bool:
    """Whether enough is set in the environment to reach Snowflake at all -
    backs /api/redshift/config's `available` flag, which the React client
    uses to decide whether to offer the warehouse data source."""
    return bool(_env("SNOWFLAKE_ACCOUNT") and _env("SNOWFLAKE_PAT"))


def is_start_of_allowed_schema(schema: str) -> bool:
    # Snowflake identifiers are case-insensitive and canonicalised to upper
    # case, so compare lowercased on both sides.
    return schema.lower().startswith(tuple(s.lower() for s in ALLOWED_SCHEMAS))


def validate_query(sql: str, limit: int | None = None) -> tuple[str, int]:
    """Validate a caller's SQL and return (wrapped_sql, limit).

    Ported from the proxy's `_prepare_query`. Callers build their own SQL
    strings (see `integrations._redshift_table_sql`), so this is the shared
    choke point: statement-type check, forbidden-keyword check, schema
    allowlist, and a LIMIT wrap so a query that forgot one cannot return
    unbounded rows.
    """
    sql = (sql or "").strip()
    if not sql:
        raise WarehouseError("'sql' is required")

    head = sql.lstrip().upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        raise WarehouseError("Query must start with SELECT or WITH")

    if _FORBIDDEN_KEYWORDS.search(sql):
        raise WarehouseError("Only read-only SELECT queries are allowed")

    refs = re.findall(r"(?:from|join|into|update|table)\s+([a-z0-9_]+)\.", sql, re.IGNORECASE)
    for schema in refs:
        if not is_start_of_allowed_schema(schema):
            raise WarehouseError(
                f"Schema '{schema}' is not in the approved allowlist "
                "(MAESTRO_SRC*, MAESTRO_*_ANA, MAESTRO_*_MRT)"
            )

    try:
        limit = int(limit or DEFAULT_RESULTS_LIMIT)
    except (TypeError, ValueError) as exc:
        raise WarehouseError("'limit' must be an integer") from exc
    if limit < 1:
        raise WarehouseError("'limit' must be >= 1")

    has_limit = re.search(r"\blimit\s+\d+\s*$", sql, re.IGNORECASE)
    wrapped = sql if has_limit else f"SELECT * FROM (\n{sql}\n) AS _q LIMIT {limit}"
    return wrapped, limit


def _statement_api_url() -> str:
    return f"https://{_env('SNOWFLAKE_ACCOUNT')}.snowflakecomputing.com/api/v2/statements"


def _fetch_partition(session: requests.Session, handle: str, partition: int) -> list[list[Any]]:
    """Fetch one result partition beyond the first (partition 0 arrives
    inline with the initial POST - see `_exec_once`)."""
    resp = session.get(
        f"{_statement_api_url()}/{handle}",
        params={"partition": partition},
        timeout=(10, QUERY_TIMEOUT_SECONDS + 15),
    )
    if resp.status_code != 200:
        raise WarehouseError(
            f"Snowflake partition {partition} fetch failed ({resp.status_code}): {resp.text[:500]}"
        )
    return resp.json().get("data", [])


def _exec_once(
    sql: str, on_progress: Callable[[int, int], None] | None = None
) -> tuple[list[str], list[list[Any]]]:
    """Run one statement against the Snowflake SQL API, returning
    (column_names, rows).

    `on_progress(partitions_done, partitions_total)` is called once the
    partition count is known and again as each partition beyond the first
    lands, so a caller can report real progress on a big fetch rather than
    blocking silently.

    Snowflake splits a large result into partitions, and the initial POST
    carries only partition 0 inline - `resultSetMetaData.numRows` is the
    true total, but `data`'s length is just that first partition (measured
    live: a 50k-row query came back as 6 partitions of
    302/1982/3999/7365/14023/22329 rows). Every partition past the first
    needs its own GET against the same statement handle. Skipping that is
    exactly the bug once reported against
    MAESTRO_SRC.SALMON.SALMON_INSTALLMENT_LOANS, where a 30M-row table
    returned 301 rows regardless of the requested LIMIT - a silent wrong
    answer, not an error, which is why this logic was ported verbatim
    rather than rewritten.
    """
    pat = _env("SNOWFLAKE_PAT")
    if not pat:
        raise WarehouseError(
            "No Snowflake PAT configured: set SNOWFLAKE_PAT in .env "
            "(see snowflake_lending/README.md for how to generate one)."
        )

    body: dict[str, Any] = {"statement": sql}
    for key, value in (
        ("database", _env("SNOWFLAKE_DATABASE")),
        ("schema", _env("SNOWFLAKE_SCHEMA")),
        ("warehouse", _env("SNOWFLAKE_WAREHOUSE")),
        ("role", _env("SNOWFLAKE_ROLE")),
    ):
        if value:
            body[key] = value

    # One session for the whole call (initial POST plus any partition GETs)
    # so they share a warm TLS connection instead of paying a fresh
    # handshake per partition - which matters on a TLS-inspecting corporate
    # network, where the handshake is the expensive part.
    with requests.Session() as session:
        session.headers.update(
            {
                "Authorization": f"Bearer {pat}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        session.verify = _ca_bundle_path()

        resp = session.post(
            _statement_api_url(), json=body, timeout=(10, QUERY_TIMEOUT_SECONDS + 15)
        )
        if resp.status_code != 200:
            raise WarehouseError(
                f"Snowflake statement failed ({resp.status_code}): {resp.text[:500]}"
            )

        payload = resp.json()
        meta = payload.get("resultSetMetaData", {})
        columns = [c.get("name", f"col_{i}") for i, c in enumerate(meta.get("rowType", []))]

        num_rows = meta.get("numRows")
        if num_rows and columns:
            # Checked here, right after the first response, so an oversized
            # request fails fast instead of after fetching every partition.
            estimated_cells = num_rows * len(columns)
            if estimated_cells > MAX_RESULT_CELLS:
                raise WarehouseError(
                    f"This query would return {num_rows:,} rows x {len(columns)} columns "
                    f"(~{estimated_cells:,} cells) - too large to hold in memory safely "
                    f"(limit: {MAX_RESULT_CELLS:,} cells). Add a WHERE filter or a "
                    "lower row limit to narrow it down."
                )

        data = payload.get("data", [])
        partitions = meta.get("partitionInfo", [])
        handle = payload.get("statementHandle")
        total_partitions = max(len(partitions), 1)
        if on_progress:
            on_progress(1, total_partitions)

        if len(partitions) > 1 and handle:
            # Fetched concurrently: sequential fetching measured ~355s for a
            # 40-partition/2M-row result (dominated by transfer time, not
            # round-trip latency), which blows past every downstream client
            # timeout. urllib3's pool behind Session is thread-safe, so one
            # shared session is fine. as_completed rather than pool.map is
            # what lets progress be reported as each partition finishes
            # instead of only once at the end.
            remaining = range(1, len(partitions))
            done = 1
            with ThreadPoolExecutor(max_workers=min(len(remaining), 10)) as pool:
                futures = [pool.submit(_fetch_partition, session, handle, i) for i in remaining]
                for future in as_completed(futures):
                    data.extend(future.result())
                    done += 1
                    if on_progress:
                        on_progress(done, total_partitions)

    return columns, data


def fetch_all(
    sql: str, on_progress: Callable[[int, int], None] | None = None
) -> tuple[list[str], list[list[Any]]]:
    """Execute a validated read-only query, returning (column_names, rows).

    Retries once on a transient failure (a dropped connection or a momentary
    gateway error), reusing the same PAT.
    """
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            return _exec_once(sql, on_progress)
        except (requests.RequestException, WarehouseError) as exc:
            last_error = exc

    assert last_error is not None
    raise last_error


def query(sql: str, limit: int | None = None) -> dict[str, Any]:
    """Validate then run `sql`, returning the same payload shape the old
    proxy's /query endpoint did, so callers in `integrations.py` did not
    have to change when the transport did."""
    wrapped, effective_limit = validate_query(sql, limit)
    columns, rows = fetch_all(wrapped)
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": len(rows) >= effective_limit,
        "query": wrapped,
    }
