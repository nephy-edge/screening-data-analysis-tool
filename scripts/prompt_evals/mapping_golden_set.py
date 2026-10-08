"""Golden set of realistic, messy raw-file column headers with their known-
correct mapping onto this tool's fixed template fields.

Used by test_mapping_llm_eval.py to check the mapping prompt empirically -
run it by hand whenever `mapping_suggestion` (config.toml) or
FIELD_DESCRIPTIONS/INPUT_COLUMNS (app.py) change. Not part of the default
suite: it calls the real DeepSeek model via DeepInfra, so it costs real API
calls and is only as deterministic as the model itself.

Add a case here whenever a real uploaded file's headers trip up the mapper -
that failure is exactly what this file exists to catch next time.
"""

LENDING_CASES = [
    {
        "name": "close-to-template headers, one ambiguous pair",
        "columns": [
            "loan_id",
            "disb_date",
            "maturity_date",
            "principal_amt",
            "exp_interest",
            "exp_fee",
            "amt_paid",
            "amt_outstanding",
            "acct_status",
            "days_past_due",
        ],
        "expected": {
            "Loan ID": "loan_id",
            "Disbursement Date": "disb_date",
            "Expected Completion Date": "maturity_date",
            "Principal Value": "principal_amt",
            "Expected Interest": "exp_interest",
            "Expected Fee": "exp_fee",
            "Total Paid": "amt_paid",
            "Total Due": "amt_outstanding",
            "Loan Status": "acct_status",
            "Days Late": "days_past_due",
        },
    },
    {
        "name": "abbreviated headers, two fields genuinely absent",
        "columns": [
            "ID",
            "Date_Disbursed",
            "Date_Due",
            "Capital",
            "Interest_Exp",
            "Fee_Exp",
            "Total_Paid",
            "Status",
        ],
        "expected": {
            "Loan ID": "ID",
            "Disbursement Date": "Date_Disbursed",
            "Expected Completion Date": "Date_Due",
            "Principal Value": "Capital",
            "Expected Interest": "Interest_Exp",
            "Expected Fee": "Fee_Exp",
            "Total Paid": "Total_Paid",
            "Loan Status": "Status",
            "Total Due": None,
            "Days Late": None,
        },
    },
]

RENTAL_CASES = [
    {
        "name": "close-to-template rental headers",
        "columns": [
            "contract_ref",
            "device_id",
            "lease_start",
            "contract_status",
            "initial_payment",
            "monthly_fee",
            "amount_paid_to_date",
            "asset_cost",
        ],
        "expected": {
            "contract_id": "contract_ref",
            "asset_id": "device_id",
            "start_date": "lease_start",
            "status": "contract_status",
            "downpayment": "initial_payment",
            "monthly_expected_payment": "monthly_fee",
            "total_paid": "amount_paid_to_date",
            "cost_of_asset": "asset_cost",
        },
    },
]
