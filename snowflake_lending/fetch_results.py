"""App-side entry point: calls the already-deployed RUN_LENDING_ANALYSIS
stored procedure and returns its JSON result directly. Only the finished
summary/cohort numbers cross the wire here - the input table's raw rows
never leave Snowflake, and no output tables are created (see
proc_handler.py's docstring for why - this account's MAESTRO_DEV_ANA
clone-refresh automation blocks writing persistent result tables there).

Not wired into web_api/main.py or app.py on purpose (this folder is an
isolated first cut) - import run_and_fetch() from there once this pattern's
been validated against a real deal.

CLI usage (from the repo root):

    .venv\\Scripts\\python.exe -m snowflake_lending.fetch_results MY_INPUT_TABLE
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .connection import build_session

PROC_NAME = "RUN_LENDING_ANALYSIS"


def run_and_fetch(
    input_table: str,
    days_after_term: int = 90,
    min_loans_per_cohort: int = 10,
    extraction_date: str | None = None,
) -> dict[str, Any]:
    session = build_session()
    try:
        result_json = session.call(
            PROC_NAME,
            input_table,
            days_after_term,
            min_loans_per_cohort,
            extraction_date,
        )
        return json.loads(result_json)
    finally:
        session.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m snowflake_lending.fetch_results <input_table>")
        sys.exit(1)

    result = run_and_fetch(sys.argv[1])
    print(json.dumps(result, indent=2, default=str))
