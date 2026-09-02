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
        "derived_column_max_tokens": 512,
        "escalation_writeup_max_tokens": 700,
        "chat_assistant_max_tokens": 800,
        "prompts": {
            "mapping_suggestion": (
                "You are mapping a model's raw column names to a fixed set of template "
                "fields. For each template field below, choose the single raw column "
                "that best matches its meaning.\n\n"
                "Available raw columns (use these exact names, or null if none fit): "
                "%%COLUMNS%%\n\n"
                "Template fields to fill:\n%%FIELDS%%\n\n"
                "Respond with ONLY a JSON object, no markdown fences, mapping each "
                "template field to a raw column name (exact string) or null when no "
                "column plausibly matches. Example:\n"
                '{"Loan ID": "account_id", "Disbursement Date": "funded_at", '
                '"Total Due": null}'
            ),
            "mapping_suggestion_context_suffix": (
                "\n\nUser-provided documentation about this dataset - prefer it when "
                "deciding which raw column corresponds to which template field:\n"
            ),
            "derived_column_suggestion": (
                "You help map a %%DOMAIN_HINT%%'s raw columns to a derived column. "
                "You can only combine exactly two existing columns with one of these "
                "operators: %%OPS%% (division). Pick the two columns and operator that "
                "best satisfy the user's request. If the request truly needs more than "
                "two columns or a non-arithmetic transform, still return your best "
                "two-column approximation and say so in the explanation.\n\n"
                "Available columns (use these exact names): %%COLUMNS%%\n"
                "Available operators (use exactly one of these characters): %%OPS%%\n\n"
                "Respond with ONLY a JSON object, no markdown fences, matching this shape:\n"
                '{"name": "<short column name>", "col_a": "<one of the available columns>", '
                '"op": "<one of the available operators>", "col_b": "<one of the available '
                'columns>", "explanation": "<one sentence>"}'
            ),
            "derived_column_context_suffix": (
                "\n\nUser-provided documentation about this dataset - prefer it when "
                "deciding what a column means or how a metric should be calculated:\n"
            ),
            "escalation_writeup": (
                "You help draft a Slack message escalating a data-quality anomaly from "
                "a %%MODEL_NAME%% portfolio screening tool to the analytics team.\n\n"
                "Uploaded file name: %%FILENAME%%\n\n"
                "Flagged cohort facts:\n%%FACTS%%\n\n"
                "1. Guess the borrower/company name from the file name (strip file "
                "extensions, dates, and generic words like 'template' or 'loan tape'). "
                'If you can\'t tell, use "Unknown Borrower".\n'
                "2. Rewrite the flagged cohort facts into a clear, well organized Slack "
                "message (Slack mrkdwn: *bold*, bullet points with '-') for an analyst "
                "who hasn't seen the data, summarizing what's flagged and the likely "
                "causes. Do not invent facts not present above.\n"
                "3. For any fact whose likely cause is \"No obvious data-driven cause\", "
                "add a short *Possible lines of inquiry (hypotheses to verify with the "
                "Borrower - not conclusions)* section with 2-3 plausible external "
                "explanations common to a %%MODEL_NAME%% business (e.g. underwriting/"
                "recovery policy changes, a servicing or system migration, a "
                "regulatory change, a one-off portfolio sale or write-off). Make clear "
                "these are hypotheses to ask about, not established facts - never state "
                "them as the actual cause. Omit this section entirely if every fact "
                "already has a data-driven cause.\n\n"
                "Respond with ONLY a JSON object, no markdown fences:\n"
                '{"borrower_name": "<name>", "message": "<Slack mrkdwn write-up>"}'
            ),
            "chat_assistant": (
                "You are a data analyst assistant embedded in a lending/leasing portfolio "
                "screening tool. Answer questions about the user's uploaded portfolio using "
                "ONLY the summary statistics and methodology notes below - you do not have "
                "access to individual loan rows, only these already-computed aggregates. "
                "If a question needs a number that isn't in the summary (e.g. a specific "
                "loan ID, or a breakdown not listed), say so plainly rather than guessing "
                "or inventing a figure. Be concise and precise; use the exact terminology "
                "and caveats given below rather than simplifying them away."
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
