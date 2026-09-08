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
        "mapping_suggestion_max_tokens": 512,
        "escalation_writeup_max_tokens": 700,
        "chat_assistant_max_tokens": 800,
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
                "Respond with ONLY a JSON object, no markdown fences, no commentary - "
                "just the mapping of each template field to a raw column name (exact "
                "string, case-sensitive) or null when no column plausibly matches. "
                "Example:\n"
                '{"Loan ID": "account_id", "Disbursement Date": "funded_at", '
                '"Total Due": null}'
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
                "different words.\n\n"
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


def render_prompt(template: str, **kwargs) -> str:
    """Fill a %%TOKEN%% placeholder template. Deliberately not str.format() -
    these prompts embed literal JSON examples full of unescaped {}, which
    .format() would try (and fail) to interpret as fields."""
    text = template
    for key, value in kwargs.items():
        text = text.replace(f"%%{key.upper()}%%", str(value))
    return text
