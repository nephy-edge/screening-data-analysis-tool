"""Stream a Snowflake query to Parquet on disk, unaggregated, row for row.

Built for the case the in-app fetch cannot serve: pulling
MAESTRO_SRC.SALMON.SALMON_INSTALLMENT_LOANS as it actually is - all
30,114,880 monthly panel rows across 38 columns, with no flattening and no
GROUP BY.

WHY THIS IS NOT JUST A BIGGER `MAX_RESULT_CELLS`. That table is ~1.14
billion cells. Materialised as Python objects (a list-of-lists of strings,
which is what the SQL Statement API path in scripts/warehouse.py builds),
that is on the order of 60-70GB - it does not fit in 32GB of RAM no matter
how the rows are batched, and a previous attempt at a 9.1M-row fetch already
got an API process OOM-killed by the OS mid-run. Raising the cap would just
move the failure later and make it harder to diagnose.

So this deliberately never holds the whole result:

  * It uses snowflake-connector-python's `fetch_arrow_batches()`, not the
    REST Statement API. Batches arrive as Arrow record batches - columnar,
    typed, and far cheaper than decoding JSON into Python strings.
  * Each batch is written straight out through a single ParquetWriter and
    then dropped. Peak memory is one batch, not one result set.
  * Parquet on disk is compressed and dictionary-encoded, so the all-TEXT
    panel lands in single-digit GB rather than tens.

WHAT TO DO WITH THE FILE. It is not meant to be loaded whole into pandas
either - that would reintroduce the same wall. Read it with a columnar
engine that can scan lazily and push filters down (DuckDB and Polars both
read Parquet directly and neither is installed here yet), or read back a
column subset / filtered slice with
`pyarrow.parquet.read_table(path, columns=[...], filters=[...])`.

Usage:
    python -m snowflake_lending.export_panel --out data/salmon_panel.parquet
    python -m snowflake_lending.export_panel --limit 100000 --out sample.parquet
    python -m snowflake_lending.export_panel --sql "SELECT ..." --out other.parquet
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import snowflake.connector
from dotenv import load_dotenv

load_dotenv()

# Same corporate-TLS handling as connection.py - this network re-signs
# outbound TLS with Cisco Umbrella's CA, which Python's own bundle does not
# trust. See that file for how the bundle was built.
_CA_BUNDLE = Path(__file__).resolve().parent / "_corporate_ca_bundle.pem"
if _CA_BUNDLE.is_file() and not os.environ.get("REQUESTS_CA_BUNDLE"):
    os.environ["REQUESTS_CA_BUNDLE"] = str(_CA_BUNDLE)

DEFAULT_SQL = "SELECT * FROM MAESTRO_SRC.SALMON.SALMON_INSTALLMENT_LOANS"


def _connect():
    missing = [
        k
        for k in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PAT", "SNOWFLAKE_WAREHOUSE")
        if not os.environ.get(k)
    ]
    if missing:
        raise SystemExit(
            f"Missing env var(s): {', '.join(missing)} - see snowflake_lending/README.md"
        )

    params = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        "authenticator": "PROGRAMMATIC_ACCESS_TOKEN",
        "token": os.environ["SNOWFLAKE_PAT"],
        "warehouse": os.environ["SNOWFLAKE_WAREHOUSE"],
        # Revocation checks break on networks that TLS-inspect or block the
        # OCSP responder; this skips that check only, not certificate
        # verification. Off by default - see connection.py.
        "disable_ocsp_checks": os.environ.get("SNOWFLAKE_DISABLE_OCSP", "false").lower()
        == "true",
    }
    for key, env in (
        ("database", "SNOWFLAKE_DATABASE"),
        ("schema", "SNOWFLAKE_SCHEMA"),
        ("role", "SNOWFLAKE_ROLE"),
    ):
        if os.environ.get(env):
            params[key] = os.environ[env]
    return snowflake.connector.connect(**params)


def export(sql: str, out_path: str, limit: int | None = None) -> tuple[int, float]:
    """Run `sql` and stream every row to `out_path` as Parquet.

    Returns (rows_written, seconds). Nothing but the current batch is held in
    memory, so this is bounded by batch size rather than result size.
    """
    if limit:
        sql = f"SELECT * FROM (\n{sql}\n) AS _q LIMIT {int(limit)}"

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    started = time.time()
    rows = 0
    writer = None
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        for batch in cur.fetch_arrow_batches():
            # The connector yields a Table on current versions and a
            # RecordBatch on older ones; normalise so either works.
            table = batch if isinstance(batch, pa.Table) else pa.Table.from_batches([batch])
            if writer is None:
                # Schema comes from the first batch, so the file's types are
                # whatever Snowflake actually returned - no guessing, and no
                # second pass to fix a schema chosen up front.
                writer = pq.ParquetWriter(out, table.schema, compression="snappy")
            writer.write_table(table)
            rows += table.num_rows
            print(f"  {rows:,} rows written", end="\r", flush=True)
    finally:
        if writer is not None:
            writer.close()
        conn.close()

    elapsed = time.time() - started
    size = f", {out.stat().st_size / 1e9:.2f} GB" if out.exists() else ""
    print(f"  {rows:,} rows written in {elapsed:.1f}s -> {out}{size}")
    return rows, elapsed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sql", default=DEFAULT_SQL, help="Query to export (default: the full SALMON panel)."
    )
    ap.add_argument("--out", required=True, help="Destination .parquet path.")
    ap.add_argument("--limit", type=int, default=None, help="Row cap, for a sample run.")
    args = ap.parse_args()
    export(args.sql, args.out, args.limit)


if __name__ == "__main__":
    main()
