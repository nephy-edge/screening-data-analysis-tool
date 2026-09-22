"""EDIT THIS FILE to match your real Snowflake input table's columns.

The Lending engine (scripts/template_analysis) expects specific, exact
canonical column names (mixed case, with spaces - e.g. "Disbursement Date").
Snowflake tables typically store unquoted identifiers as upper-cased,
underscored names, so `session.table(...).to_pandas()` will hand back
columns like DISBURSEMENT_DATE rather than "Disbursement Date".

COLUMN_MAP renames the former to the latter right after the table is pulled
into pandas inside the stored procedure - nothing in the engine itself
changes. Left-hand side = your Snowflake column name (as it comes back from
to_pandas()); right-hand side = the canonical engine column name.

Only the columns your input table actually has need to be listed here -
process_data_input()/GeneralInputs() handle optional columns (e.g. "Expected
Fee", "Term (days)") themselves. Required at minimum: "Disbursement Date",
"Expected Completion Date", "Principal Value", "Total Paid", and either
"Total Due" or the raw fields it can be derived from (see
scripts/template_analysis/data_input.py's _TOTAL_DUE_ALIASES /
_compute_total_due).
"""

COLUMN_MAP: dict[str, str] = {
    "LOANID": "Loan ID",
    "DISBURSEMENT_DATE": "Disbursement Date",
    "EXPECTED_COMPLETION_DATE": "Expected Completion Date",
    "PRINCIPAL_VALUE": "Principal Value",
    "EXPECTED_INTEREST": "Expected Interest",
    "EXPECTED_FEE": "Expected Fee",
    "TOTAL_DUE": "Total Due",
    "TOTAL_PAID": "Total Paid",
}
