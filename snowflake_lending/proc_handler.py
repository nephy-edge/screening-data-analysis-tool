"""Body of the RUN_LENDING_ANALYSIS Snowpark Python stored procedure.

This module gets registered into Snowflake by deploy.py - the function below
then runs *inside* a Snowflake warehouse, not in this app's process. It pulls
the input table into pandas inside that warehouse, runs the same, unmodified
scripts/template_analysis engine app.py/web_api use today, and returns the
results directly as a JSON string (the procedure's RETURN value) - no output
tables are created. No raw rows ever leave Snowflake - only the finished
summary/cohort numbers cross back to the caller.

Returning JSON directly (rather than writing result tables via
session.write_pandas) sidesteps this account's MAESTRO_DEV_ANA clone-refresh
automation, which reclaims ownership of any table created there before this
role can finish writing to it - see snowflake_lending/README.md's ownership
notes. A persistent-table version can be reinstated once that's resolved.

column_mapping.COLUMN_MAP must be edited to match your actual input table's
column names before deploying (see that file's docstring).
"""

from __future__ import annotations

from snowflake.snowpark import Session


def run_lending_analysis(
    session: Session,
    input_table: str,
    days_after_term: int,
    min_loans_per_cohort: int,
    extraction_date: str,
) -> str:
    import json

    import pandas as pd

    # These two imports are staged into the procedure via deploy.py's
    # `imports=[...]` (scripts/template_analysis, unmodified) - available on
    # sys.path only inside the Snowflake sandbox, not in this repo's own
    # import graph, so they must stay deferred to call time.
    from column_mapping import COLUMN_MAP
    from template_analysis.cohorts import build_cohorts
    from template_analysis.cohorts_for_x_or_more_loans import filter_cohorts
    from template_analysis.data_input import process_data_input
    from template_analysis.general_inputs import GeneralInputs
    from template_analysis.ltv_analysis import LtvAnalysis
    from template_analysis.ue_analysis import UeAnalysis

    raw = session.table(input_table).to_pandas()
    if COLUMN_MAP:
        raw = raw.rename(columns=COLUMN_MAP)

    # session.table(...).to_pandas() returns native datetime.date objects for
    # DATE columns, not pandas Timestamp/datetime64 - unlike
    # pd.read_csv(..., parse_dates=[...]), which is what the engine has
    # always been fed (app.py, web_api). GeneralInputs computes
    # extraction_date from this raw column before process_data_input's own
    # _coerce_dates runs, so a plain `date` leaks through and later fails
    # datetime64 comparisons in process_data_input. Coercing here up front
    # matches the dtype the engine has always assumed, unmodified.
    for col in ("Disbursement Date", "Expected Completion Date"):
        if col in raw.columns:
            raw[col] = pd.to_datetime(raw[col])

    # A small fraction of loans have a NULL Disbursement/Expected Completion
    # Date (source ORIGINATION_DATE/ORIGINAL_TERM failed to parse for a few
    # rows) - left in, these produce a mixed date/NaN column that GeneralInputs'
    # df["Disbursement Date"].max() can't compare. Confirmed negligible (4 and
    # ~15k out of 3.46M loans respectively) against the real SALMON data, so
    # dropping them is safe rather than something needing investigation.
    n_before = len(raw)
    raw = raw.dropna(subset=["Disbursement Date", "Expected Completion Date"])
    n_dropped = n_before - len(raw)

    gi_kwargs: dict = {
        "days_after_term": days_after_term,
        "min_loans_per_cohort": min_loans_per_cohort,
    }
    if extraction_date:
        gi_kwargs["extraction_date"] = pd.Timestamp(extraction_date)
    gi = GeneralInputs(raw, **gi_kwargs)

    processed = process_data_input(raw, gi.extraction_date, gi.days_after_term)
    cohorts = build_cohorts(processed, min_matured=gi.min_loans_per_cohort)
    filtered = filter_cohorts(cohorts, gi.min_loans_per_cohort)
    ue = UeAnalysis(processed)
    ltv = LtvAnalysis(processed, filtered)

    summary_row = {
        **{k: str(v) for k, v in gi.as_dict().items()},
        **ue.as_dict(),
        **ltv.as_dict(),
        "rows": int(len(processed)),
        "cohorts": int(len(filtered)),
        "rows_dropped_missing_dates": n_dropped,
    }

    cohorts_records = cohorts.reset_index(drop=True).to_dict("records")

    return json.dumps({"summary": summary_row, "cohorts": cohorts_records}, default=str)
