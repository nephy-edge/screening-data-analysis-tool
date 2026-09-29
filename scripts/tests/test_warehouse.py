"""The warehouse client builds SQL from caller-supplied strings, so
`warehouse.validate_query` is the only thing standing between a filter
expression and the live Snowflake account.

These tests pin that boundary. It used to live in a separate service
(Verifications/redshift-api), where the app could not reach the warehouse
except through it; now that this app holds the PAT and queries Snowflake
directly, the guard is in-process and nothing else enforces it - so it
needs coverage the proxy-era code never had here.

Everything here is offline: validate_query does no I/O, so none of this
touches the network or needs credentials.
"""

import sys

import pytest

sys.path.insert(0, "scripts")
import warehouse  # noqa: E402


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE MAESTRO_SRC.SALMON.LOANS",
        "DELETE FROM MAESTRO_SRC.SALMON.LOANS",
        "UPDATE MAESTRO_SRC.SALMON.LOANS SET X = 1",
        "INSERT INTO MAESTRO_SRC.SALMON.LOANS VALUES (1)",
        "CREATE TABLE MAESTRO_SRC.SALMON.T AS SELECT 1",
        "GRANT SELECT ON MAESTRO_SRC.SALMON.LOANS TO PUBLIC",
        "CALL MAESTRO_SRC.SALMON.SOME_PROC()",
    ],
)
def test_rejects_write_statements(sql):
    with pytest.raises(warehouse.WarehouseError):
        warehouse.validate_query(sql, 10)


def test_rejects_write_smuggled_after_a_select():
    """The statement-type check alone only looks at the first keyword, so a
    query that opens with SELECT and appends a write must still be caught by
    the forbidden-keyword scan."""
    with pytest.raises(warehouse.WarehouseError):
        warehouse.validate_query(
            "SELECT 1 FROM MAESTRO_SRC.SALMON.LOANS; DROP TABLE MAESTRO_SRC.SALMON.LOANS", 10
        )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY",
        "SELECT * FROM SECRETS.INTERNAL.TOKENS",
        "SELECT a FROM MAESTRO_SRC.SALMON.LOANS JOIN OTHERDB.X.Y ON 1=1",
    ],
)
def test_rejects_schemas_outside_the_allowlist(sql):
    with pytest.raises(warehouse.WarehouseError):
        warehouse.validate_query(sql, 10)


@pytest.mark.parametrize(
    "database",
    ["MAESTRO_SRC", "MAESTRO_PRD_ANA", "MAESTRO_DEV_MRT"],
)
def test_allows_the_approved_layers(database):
    wrapped, limit = warehouse.validate_query(f"SELECT * FROM {database}.SALMON.LOANS", 25)
    assert limit == 25
    assert database in wrapped


def test_rejects_a_statement_that_is_not_a_query():
    with pytest.raises(warehouse.WarehouseError):
        warehouse.validate_query("SHOW TABLES IN MAESTRO_SRC.SALMON", 10)


def test_missing_sql_is_rejected():
    with pytest.raises(warehouse.WarehouseError):
        warehouse.validate_query("   ", 10)


def test_a_query_without_its_own_limit_is_wrapped():
    """A caller that forgets a LIMIT must not be able to pull an unbounded
    result - the wrap is what stops one dropdown query from streaming a
    30M-row table into memory."""
    wrapped, limit = warehouse.validate_query("SELECT * FROM MAESTRO_SRC.SALMON.LOANS", 500)
    assert limit == 500
    assert wrapped.rstrip().endswith("LIMIT 500")


def test_a_query_with_its_own_limit_is_left_alone():
    sql = "SELECT * FROM MAESTRO_SRC.SALMON.LOANS LIMIT 10"
    wrapped, _ = warehouse.validate_query(sql, 500)
    assert wrapped == sql


def test_rejects_a_negative_limit():
    with pytest.raises(warehouse.WarehouseError):
        warehouse.validate_query("SELECT * FROM MAESTRO_SRC.SALMON.LOANS", -1)


def test_a_zero_limit_falls_back_to_the_default():
    """Documenting inherited behaviour rather than endorsing it: `limit or
    DEFAULT_RESULTS_LIMIT` treats 0 as "unset", so an explicit 0 quietly
    becomes 100 rather than being rejected like -1 is. Carried over from the
    proxy this replaced. No caller passes 0 today (the API's own default is
    100_000), so this is pinned rather than changed - but if a caller ever
    does, it will silently get 100 rows, not an error."""
    _, limit = warehouse.validate_query("SELECT * FROM MAESTRO_SRC.SALMON.LOANS", 0)
    assert limit == warehouse.DEFAULT_RESULTS_LIMIT


def test_loan_tape_database_does_not_follow_the_snowpark_session_default(monkeypatch):
    """SNOWFLAKE_DATABASE in this repo is MAESTRO_DEV_ANA, the session
    default for the Snowpark stored-procedure pipeline - a database with no
    borrower schemas in it. An earlier version of this module defaulted
    LOAN_TAPE_DATABASE from it, which made the borrower list come back empty
    instead of erroring. Pin that the loan-tape database is independent.
    """
    monkeypatch.setenv("SNOWFLAKE_DATABASE", "MAESTRO_DEV_ANA")
    monkeypatch.delenv("LOAN_TAPE_DATABASE", raising=False)
    import importlib

    reloaded = importlib.reload(warehouse)
    try:
        assert reloaded.LOAN_TAPE_DATABASE == "MAESTRO_SRC"
    finally:
        importlib.reload(warehouse)
