"""Central, human-editable settings for the SC Analysis - LTV & Unit Economics tool.

Loaded once from config.toml (same directory) at import time. Every value
has a hardcoded fallback below matching the app's previous hardcoded
behavior, so a missing file, invalid TOML, or a key removed/renamed by a
future template update degrades to that fallback instead of crashing the
app - config changes must never be able to break it.

API keys are deliberately NOT read from here - they stay in Streamlit
secrets or the DEEPINFRA_API_KEY environment variable, per the existing
_get_deepinfra_api_key() lookup in app.py.
"""

import os
import tomllib

_DEFAULTS = {
    "ai": {
        "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
        "chat_url": "https://api.deepinfra.com/v1/openai/chat/completions",
        "request_timeout_seconds": 30,
        "mapping_suggestion_max_tokens": 768,
        "derive_suggestion_max_tokens": 600,
        "redshift_filter_suggestion_max_tokens": 300,
        "escalation_writeup_max_tokens": 700,
        "chat_assistant_max_tokens": 800,
        "whatif_extract_max_tokens": 200,
        "prompts": {
            "mapping_suggestion": (
                "You are a data-mapping assistant for a lending/leasing portfolio "
                "screening tool. Users upload a raw loan-tape file (Excel or CSV) whose "
                "column headers vary by lender - inconsistent naming, abbreviations, "
                "different languages, or extra columns not relevant to this analysis. "
                "Your job is to map that file's raw column names onto a FIXED set of "
                "template fields this tool needs in order to run its calculations.\n\n"
                "For each template field listed below, choose the single raw column "
                "whose MEANING best matches that field - not just similar spelling. Use "
                "the short description given for each field to judge intent (e.g. a "
                'field described as "principal amount lent" should map to a column like '
                '"loan_amount" or "disbursed_principal", not merely to some other '
                "column that happens to contain numbers).\n\n"
                "Available raw columns (use these exact names, verbatim, or null if "
                "none fit): %%COLUMNS%%\n\n"
                "Template fields to fill (name, whether required, and what each one "
                "means):\n%%FIELDS%%\n\n"
                "Rules:\n"
                "- Each raw column may be used for AT MOST ONE template field - never "
                "map two different template fields to the same raw column, even if both "
                "seem plausible; pick whichever field it fits best and leave the other "
                "null.\n"
                "- Prefer an exact or near-exact name match (ignoring case, spacing, "
                "and punctuation) over a purely semantic guess when one exists.\n"
                "- If a required field has no plausible match at all, still respond "
                "with null for it rather than guessing at a loosely related column - a "
                "human will map it manually afterward.\n"
                "- Do not invent a raw column name that isn't in the list above.\n\n"
                "Separately, decide whether the file needs a ROW EXCLUSION rule: some "
                "raw tapes include rows that must never count toward the analysis at "
                "all - e.g. unbooked/draft applications, cancelled or test records, or "
                "anything the documentation explicitly says to exclude. If you find a "
                "single column and one or more specific values that mark such rows - "
                "from the documentation provided, or from an unambiguous flag column "
                '(e.g. values literally "EXCLUDE" or "test") - include it as '
                '"_exclude_rows": {"column": "<exact raw column name>", "values": '
                '["<value1>", ...]}. Only include this when you have concrete evidence '
                'naming the column and value(s); if there\'s none, set "_exclude_rows" '
                "to null. Never guess at this without a real signal - wrongly excluding "
                "real loans would silently corrupt every downstream figure.\n\n"
                'Separately again, if you mapped a source column to "Loan Status", '
                "decide whether you actually know that column's value vocabulary well "
                "enough (from the documentation provided - raw column names alone never "
                "tell you cell values) to say which specific values mean the loan is "
                "still ACTIVE/OUTSTANDING (not yet paid off, closed, settled, written "
                'off, or cancelled). If so, include "_active_status_values": '
                '["<value1>", ...] - the exact raw values (case-sensitive) that mean '
                "active. If you weren't given the column's actual values anywhere, do "
                "not guess - omit this key or set it to null.\n\n"
                "Respond with ONLY a JSON object, no markdown fences, no commentary - "
                "just the mapping of each template field to a raw column name (exact "
                "string, case-sensitive) or null when no column plausibly matches, plus "
                'the optional "_exclude_rows" and "_active_status_values" fields '
                "described above. Example:\n"
                '{"Loan ID": "account_id", "Disbursement Date": "funded_at", '
                '"Loan Status": "status", "Total Due": null, "_exclude_rows": '
                '{"column": "GhostFlag", "values": ["EXCLUDE - unbooked application"]}, '
                '"_active_status_values": ["OPEN", "IN ARREARS"]}'
            ),
            "mapping_suggestion_context_suffix": (
                "\n\nThe user has also provided additional documentation about this "
                "specific dataset below (e.g. a data dictionary, a description of the "
                "loan product, or notes on quirks in the file). Treat it as "
                "authoritative: when it conflicts with a guess you'd otherwise make "
                "from column names alone, defer to what the documentation says - it "
                "may also clarify a column that would otherwise look ambiguous or "
                "irrelevant.\n"
            ),
            "derive_suggestion": (
                "You are a formula-writing assistant for a lending/leasing portfolio "
                "screening tool. Users have uploaded a raw loan-tape file with the "
                "columns listed below, and want to derive a column that isn't in the "
                "file - for example they may say `Expected Interest = Gross Loan Value - "
                "Principal Value`, or `the fee is 2% of the principal`.\n\n"
                "Available raw columns (use these exact names, verbatim):\n%%COLUMNS%%\n\n"
                "User request:\n%%REQUEST%%\n\n"
                "Your job is to translate that request into a SINGLE spreadsheet-style "
                "formula over the listed columns. Rules:\n"
                "- Respond with ONLY a JSON object, no markdown fences, no commentary: "
                '{"name": "<new column name>", "formula": "<formula>"}\n'
                "- The formula may use the operators + - * / and parentheses (), the "
                "comparisons = <> > >= < <=, numbers, quoted text, TRUE/FALSE, and the "
                "EXACT column names from the list above (names with spaces are allowed, "
                "e.g. `Gross Loan Value - Principal Value`). There are NO cell "
                "references or ranges: a bare column name IS the whole column, so write "
                '`SUMIFS(Principal Value, DPD, ">90")`, never `SUMIFS(L:L, W:W, TRUE)`. '
                "No currency symbols, no percent symbols - write 2% as 0.02.\n"
                "- These functions are available, and NOTHING else. Row-wise (a value "
                "per loan): IF, IFERROR, MIN, MAX, ABS, ROUND, AND, OR, NOT. Aggregate "
                "(ONE number, repeated on every row): SUM, COUNT, AVERAGE, SUMIF, "
                "SUMIFS, COUNTIF, COUNTIFS, AVERAGEIF, AVERAGEIFS. Dates: EDATE, "
                "EOMONTH, YEAR, MONTH, DAY, DATEDIF, DATEVALUE, TODAY. For example "
                "`IF(DPD>90, 1, 0)` flags each delinquent loan, and "
                "`Principal Value / SUM(Principal Value)` is each loan's share of the "
                'book. Criteria take the spreadsheet form (">90", "<=30", "<>0") or a '
                "plain condition (DPD>90).\n"
                "- MIN/MAX over ONE column is that column's minimum/maximum; with "
                "SEVERAL arguments they compare row by row - exactly as in a "
                "spreadsheet.\n"
                "- Dates: a TERM IN MONTHS is added with EDATE, never by adding a "
                "number (a month is not 30 days), so a maturity date is "
                "`EDATE(Disbursement Date, Term Months)`. Subtracting two dates gives a "
                "span in DAYS, so a term in days is "
                "`EDATE(Disbursement Date, Term Months) - Disbursement Date`. Adding a "
                "plain number to a date shifts it by that many DAYS. Use DATEVALUE "
                "around a raw date column when no other date function appears in the "
                "formula.\n"
                "- Never approximate a function that isn't in the list above - return "
                "null instead of an algebraic substitute that is close but wrong.\n"
                "- `name` should be a short, human-readable column label (e.g. "
                '"Expected Interest"), and must NOT be one of the existing column names.\n'
                "- Only derive a column when the request genuinely asks for one; if it "
                'doesn\'t ask to derive anything, return null instead: {"name": null, '
                '"formula": null}.\n'
                "- Do NOT compute or estimate any number yourself - the formula is "
                "evaluated by the tool on the real data. State the relationship, never a "
                "value.\n"
                "- Use no column that isn't listed above."
            ),
            "whatif_extract": (
                "You decide whether an analyst's question to a portfolio screening tool "
                "asks for a what-if on one or more model inputs, e.g. 'what if the funding "
                "cost were 10%?' or 'rerun with a 2x churn stress'. You never compute "
                "anything - the tool re-runs its own engine with the values you return.\n\n"
                "Inputs that can be changed, with their CURRENT values (JSON):\n%%FIELDS%%\n\n"
                "Rules:\n"
                "- Respond with ONLY a JSON object, no markdown: "
                '{"overrides": {"<input name>": <number>}}\n'
                "- Use input names exactly as listed. Only include inputs the question "
                "explicitly asks to change; if it is not a what-if, or names an input that "
                'is not listed, return {"overrides": {}}.\n'
                "- Only use an input when the question names that input itself. A "
                "calculated result (a churn rate, loss rate, margin, LTV, IRR, yield, "
                "PvD) is NOT an input, even when an input has a similar name: 'what if "
                "churn were 5%?' names the churn rate, not the churn stress multiplier, "
                'so return {"overrides": {}}.\n'
                "- Use the same units as the current value: a current value of 0.13 means "
                "13%, so '10%' is 0.10; whole-number inputs (months, counts, years) stay "
                "whole numbers.\n"
                "- A follow-up like 'and at 8%?' refers to the input changed in the recent "
                "conversation.\n"
            ),
            "redshift_filter_suggestion": (
                "You are a SQL-filter-writing assistant for a data warehouse browser. "
                "The user is about to fetch rows from one table and wants to filter it "
                "down using a plain-English description instead of writing SQL "
                "themselves.\n\n"
                "Table: %%TABLE%%\n"
                "Available columns (use these exact names, verbatim; every column in "
                "this table is stored as TEXT, including dates and numbers):\n"
                "%%COLUMNS%%\n\n"
                "User's filter request:\n%%REQUEST%%\n\n"
                "Your job is to translate that request into a single SQL WHERE "
                "condition (everything that would go after the word WHERE - do not "
                "include the word WHERE itself). Rules:\n"
                "- Respond with ONLY a JSON object, no markdown fences, no commentary: "
                '{"where": "<condition>"}\n'
                "- Use ONLY the exact column names listed above - never invent one.\n"
                "- Since every column is TEXT, write date/numeric comparisons as string "
                "comparisons the warehouse can still evaluate correctly, e.g. a date "
                "column compared with >= 'YYYY-MM-DD', a numeric column cast with "
                "TRY_CAST(column AS NUMBER) > 30 when comparing magnitude (not just "
                "matching an exact value).\n"
                "- Only SELECT-shaped filtering is allowed: comparisons, AND/OR/NOT, "
                "IN (...), LIKE, IS NULL/IS NOT NULL, BETWEEN. Never write INSERT, "
                "UPDATE, DELETE, DROP, ALTER, CREATE, or any statement besides a "
                "condition.\n"
                "- No semicolons, no subqueries, no comments.\n"
                "- If the request is too vague to translate into a real condition, "
                'return {"where": null} rather than guessing.'
            ),
            "escalation_writeup": (
                "You help draft a Slack message escalating a data-quality anomaly from "
                "a %%MODEL_NAME%% portfolio screening tool to the analytics team. The "
                "recipient is a credit analyst who has NOT seen the underlying data - "
                "they only have what you write, so be self-contained and precise.\n\n"
                "Uploaded file name: %%FILENAME%%\n\n"
                "Flagged cohort facts (already-aggregated, cohort-level statistics - "
                "you have no access to individual loan rows):\n%%FACTS%%\n\n"
                "Do the following:\n"
                "1. Guess the borrower/company name from the file name (strip file "
                "extensions, dates, and generic words like 'template' or 'loan tape'). "
                'If you can\'t tell, use "Unknown Borrower".\n'
                "2. Rewrite the flagged cohort facts into a clear, well organized Slack "
                "message (Slack mrkdwn: *bold* for emphasis, bullet points with '-') "
                "summarizing what's flagged and the likely causes. Open with one "
                "sentence stating what portfolio/borrower this is and how many issues "
                "were flagged, then list each flagged fact as its own bullet with its "
                "likely cause. Use ONLY the numbers and facts given above - never "
                "invent, round unusually, or estimate a figure that isn't present.\n"
                '3. For any fact whose likely cause is "No obvious data-driven cause", '
                "add a short *Possible lines of inquiry (hypotheses to verify with the "
                "Borrower - not conclusions)* section with 2-3 plausible external "
                "explanations common to a %%MODEL_NAME%% business (e.g. underwriting/"
                "recovery policy changes, a servicing or system migration, a "
                "regulatory change, a one-off portfolio sale or write-off). Make clear "
                "these are hypotheses to ask about, not established facts - never state "
                "them as the actual cause. Omit this section entirely if every fact "
                "already has a data-driven cause.\n"
                "4. Keep the tone professional and the message scannable - short "
                "sentences, no filler, no restating the same fact twice.\n\n"
                "Respond with ONLY a JSON object, no markdown fences, no commentary:\n"
                '{"borrower_name": "<name>", "message": "<Slack mrkdwn write-up>"}'
            ),
            "chat_assistant": (
                "You are a data analyst assistant embedded in a lending/leasing "
                "portfolio screening tool, talking to a credit risk analyst who is "
                "reviewing a specific uploaded portfolio. Answer questions about that "
                "portfolio using ONLY the summary statistics and methodology notes "
                "below - you do not have access to individual loan rows, only these "
                "already-computed aggregates.\n\n"
                "Guidelines:\n"
                "- If a question needs a number that isn't in the summary (e.g. a "
                "specific loan ID, or a breakdown not listed), say so plainly rather "
                "than guessing or inventing a figure.\n"
                '- If asked "why" or "how" a metric is calculated, explain it using '
                "the exact formula/methodology notes given below, not a generic or "
                "simplified description of how such a metric is usually calculated "
                "elsewhere.\n"
                "- You may perform simple arithmetic that directly combines numbers "
                "already given below (e.g. a difference, a ratio, a sum) - but never "
                "estimate or extrapolate a new metric that isn't derivable from the "
                "numbers provided.\n"
                '- When a figure has a caveat attached below (e.g. "n/a because Total '
                'Due isn\'t mapped", or "matured loans only"), always carry that '
                "caveat into your answer rather than stating the number in isolation.\n"
                "- Be concise and precise; use the exact terminology given below (e.g. "
                '"Estimated APR", "Loss Rate Proxy") rather than paraphrasing it into '
                "different words.\n"
                "- Write your answer in a natural, conversational tone - as if a "
                "colleague is talking the analyst through the finding over a call, not "
                "writing a formal report. Lead with the direct answer to their question, "
                "then briefly explain the key point. Use plain language, short sentences "
                "and short paragraphs. Avoid dense bullet dumps, lists, long technical "
                "justifications, and repeating caveats at length - mention a caveat only "
                "when it actually changes the answer to the question asked, and say it in "
                "one plain sentence.\n"
                "- Reply in plain text only, never Markdown: no headings, no bullet or "
                "numbered lists, no bold or italics, no tables and no code blocks - the "
                "answer is shown as raw text.\n\n"
                "Stay strictly within this data's domain: only answer questions about this "
                "portfolio, this analysis, or this conversation's own history - never general "
                "knowledge, current events, today's date, or anything unrelated to the "
                "uploaded file, even if you believe you know the answer. If asked something "
                "outside that scope, reply with only a brief, polite refusal (e.g. 'I can "
                "only help with questions about this portfolio's data and analysis.') - do "
                "not attempt to answer it or speculate."
            ),
        },
    },
    "fx": {
        "api_url": "https://open.er-api.com/v6/latest/USD",
        "cache_ttl_seconds": 3600,
        "request_timeout_seconds": 15,
    },
    "general_inputs_defaults": {
        "days_after_term": 90,
        "min_loans_per_cohort": 10,
    },
    "ue_model_cost_defaults": {
        "upfront_cost_pct": 1.0,
        "ongoing_cost_pct": 2.0,
        "financing_cost_pct": 13.0,
        "facility_fee_pct": 2.0,
        "hedge_cost_pct": 3.0,
    },
    "credit_stress": {
        "fx_stress_factor": 1.2,
        "credit_stress_factor": 1.7,
    },
    "revolving_defaults": {
        "lgd": 1.0,
        "roll_rate_window": "T12",
        "ratio_window_months": 2,
        "writeoff_window_months": 1,
        "min_outstanding_in_sample": 0.0,
        "min_outstanding_in_sample_pct_of_peak": 0.10,
        "ltv_window_months": 12,
        "stress_percentile": 0.95,
        "denominator_lag_months": 5,
        "lagged_blend_weight": 0.5,
        "loss_rate_basis": "Blended",
        "balance_tolerance": 1.0,
        "unexplained_warning_pct": 0.05,
        "lgd_basis": "Input",
        "cost_of_funds_pct": 0.13,
        "opex_pct": 0.02,
        "advance_haircut": 0.0,
        "backtest_min_history_months": 12,
        "early_warning_lookback_months": 3,
        "early_warning_threshold": 0.2,
        "approx_writeoff_lag_months": 2,
        "stress_roll_rate_cap": 1.0,
        "roll_rate_windows": {"T3": 3, "T6": 6, "T12": 12, "ALL": 0},
        "scenarios": [
            {"name": "Base", "roll_rate_multiplier": 1.0, "lgd_multiplier": 1.0},
            {"name": "Stress", "roll_rate_multiplier": 1.25, "lgd_multiplier": 1.1},
            {"name": "Severe", "roll_rate_multiplier": 1.5, "lgd_multiplier": 1.25},
        ],
        # Deal-specific, so none by default (see config.toml).
        "covenants": [],
    },
    # Usage-tracker Google Sheet; empty sheet_id disables logging.
    "usage_log": {"sheet_id": "", "tab": "Usage log", "feedback_tab": "Feedback"},
    "field_aliases": {
        "lending": {
            "Loan ID": [
                "loan id",
                "loan_id",
                "account id",
                "account_id",
                "id",
                "loan no",
                "loan number",
                "contract no",
                "contract number",
            ],
            "Disbursement Date": [
                "disbursement date",
                "disbursal date",
                "disbursed date",
                "funded date",
                "funding date",
                "start date",
                "origination date",
                "loan date",
                "issue date",
            ],
            "Expected Completion Date": [
                "expected completion date",
                "maturity date",
                "expected end date",
                "end date",
                "expected maturity",
                "due date",
            ],
            "Principal Value": [
                "principal value",
                "principal",
                "loan amount",
                "disbursement amount",
                "principal amount",
            ],
            "Expected Interest": [
                "expected interest",
                "interest",
                "interest amount",
                "expected interest amount",
            ],
            "Gross Loan Value": [
                "gross loan value",
                "total loan value",
                "gross value",
            ],
            "Expected Fee": ["expected fee", "fee", "fee amount", "upfront fee", "expected fees"],
            "Total Paid": [
                "total paid",
                "amount paid",
                "total repayments",
                "payments",
                "total amount paid",
            ],
            "Total Due": [
                "total due",
                "total dues calculated",
                "total due calculated",
                "pos",
                "outstanding",
                "balance",
                "amount due",
            ],
            "Loan Status": ["loan status", "status", "loan status label", "account status"],
            "Days Late": ["days late", "days past due", "dpd", "days overdue", "days in arrears"],
            "Term (days)": [
                "term (days)",
                "term",
                "tenor",
                "tenor (days)",
                "loan term",
                "term in days",
                "loan tenor",
            ],
            "Payment per Period": [
                "payment per period",
                "payment",
                "installment",
                "instalment",
                "monthly payment",
                "periodic payment",
            ],
            "Payment Frequency": [
                "payment frequency",
                "frequency",
                "payment terms",
                "repayment frequency",
            ],
        },
        "rental": {},
    },
}

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            loaded = tomllib.load(f)
        return _deep_merge(_DEFAULTS, loaded)
    except Exception:
        # Missing file, invalid TOML, wrong types, a key a future template
        # update renamed/removed - never let a config problem take the app
        # down; fall back to the built-in defaults above instead.
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in _DEFAULTS.items()}


CONFIG = _load()

AI = CONFIG["ai"]
AI_PROMPTS = AI["prompts"]
FX = CONFIG["fx"]
GENERAL_INPUTS_DEFAULTS = CONFIG["general_inputs_defaults"]
UE_MODEL_COST_DEFAULTS = CONFIG["ue_model_cost_defaults"]
CREDIT_STRESS = CONFIG["credit_stress"]
REVOLVING_DEFAULTS = CONFIG["revolving_defaults"]
FIELD_ALIASES = CONFIG["field_aliases"]
USAGE_LOG = CONFIG["usage_log"]


def render_prompt(template: str, **kwargs) -> str:
    """Fill a %%TOKEN%% placeholder template. Deliberately not str.format() -
    these prompts embed literal JSON examples full of unescaped {}, which
    .format() would try (and fail) to interpret as fields."""
    text = template
    for key, value in kwargs.items():
        text = text.replace(f"%%{key.upper()}%%", str(value))
    return text
