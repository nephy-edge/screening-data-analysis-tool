"""DeepInfra (DeepSeek V4 Flash) client for the three AI features - column
mapping auto-fill, variance-escalation write-ups, and the Ask AI chatbot -
plus the small on-disk JSON cache their results are memoized through."""

import json
import os

import requests
import streamlit as st
from ca_bundle import _ca_bundle_path

import config as app_config

DEEPINFRA_MODEL = app_config.AI["model"]
DEEPINFRA_CHAT_URL = app_config.AI["chat_url"]


def _get_deepinfra_api_key():
    try:
        return st.secrets["DEEPINFRA_API_KEY"]
    except Exception:
        return os.environ.get("DEEPINFRA_API_KEY")


def _cache_key(values) -> str:
    return "|".join(sorted(str(v) for v in values))


def _invalidate_ai_mapping_guesses() -> None:
    """Drop cached AI mapping auto-fill results so they are recomputed with the
    current user-provided context on the next rerun (called when the context
    text area changes)."""
    for k in list(st.session_state):
        if k.startswith("ai_mapping_guess_"):
            st.session_state.pop(k, None)


def _load_cache(path, key) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return cache.get(key)


def _save_cache(path, key, value) -> None:
    cache = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            cache = {}
    cache[key] = value
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except OSError:
        pass


# Short human-readable meaning for each Data Input template field, used to prompt
# the auto-fill model so it maps raw file columns to the right template fields.
FIELD_DESCRIPTIONS = {
    # Lending
    "Loan ID": "unique loan/account identifier",
    "Disbursement Date": "date the loan was disbursed/funded",
    "Expected Completion Date": "expected maturity or completion date",
    "Principal Value": "principal amount lent",
    "Expected Interest": "expected interest amount",
    "Gross Loan Value": "total value of the loan (principal + interest); if Expected Interest "
    "isn't available directly, mapping this lets the tool derive it as Gross Loan Value minus "
    "Principal Value",
    "Expected Fee": "expected fee amount",
    "Total Paid": "total amount the borrower has paid",
    "Total Due": "total amount owed (principal + interest + fees)",
    "Loan Status": "loan/account status label (e.g. active, closed, paid off)",
    "Days Late": "number of days the loan is past due",
    "Term (days)": "loan tenor/term in days",
    # Rental & Subscription
    "contract_id": "unique rental/lease contract identifier",
    "asset_id": "identifier of the physical asset tied to this contract",
    "start_date": "date the rental/lease contract started",
    "status": "contract status label (e.g. active/open, closed, defaulted)",
    "downpayment": "upfront payment made by the customer at contract start",
    "monthly_expected_payment": "contractually expected monthly rental/subscription payment",
    "total_paid": "total amount the customer has paid to date",
    "cost_of_asset": "cost/book value of the underlying asset to the lessor",
    "expected_end_date": "expected contract end/maturity date",
    "closed_date": "date the contract was closed or terminated",
    "asset_recovery_date": "date the physical asset was recovered from the customer",
    "total_contract_value": "total value of all payments due over the full contract term",
    "amount_expected_to_date": "cumulative amount expected to have been paid by now",
    "current_asset_value": "current (depreciated) value of the asset",
    "recovery_date": "date any recovery/collection amount was received after default",
    "recovery_amount": "amount recovered after default (e.g. asset resale or collections)",
    "last_payment_date": "date of the customer's most recent payment",
}


def _suggest_mapping(
    columns: list,
    input_columns: list,
    context: str = "",
    known_loan_status_column: str | None = None,
) -> tuple[dict, dict | None, list | None]:
    """Ask DeepSeek V4 Flash (via DeepInfra) to guess the best mapping of the
    uploaded file's raw columns to each Data Input template field for the active
    model. Returns ({target_field: source_column}, exclude_suggestion,
    active_status_values) using only columns actually present; fields with no
    sensible source are left unmapped (None). exclude_suggestion is
    {"column": ..., "values": [...]} or None - a row-exclusion rule (e.g. an
    unbooked/test-record flag) the model found explicit evidence for in the
    columns or context, never a guess. active_status_values is a list of the
    Loan Status column's own values that mean "still active/outstanding", or
    None if the model wasn't given enough evidence (e.g. context documentation)
    to know that column's vocabulary. `context` is optional user-provided
    documentation about the dataset that the model should prefer when a
    column's meaning isn't obvious. `known_loan_status_column` is set when
    "Loan Status" was already resolved programmatically (an exact header
    match) before this call, so it's excluded from `input_columns`/`columns`
    - passing it separately still lets the model use it for
    active_status_values without being asked to (re-)guess the mapping."""
    api_key = _get_deepinfra_api_key()
    if not api_key:
        return {}, None, None

    fields = "\n".join(
        f"- {target} ({'required' if required else 'optional'}): "
        f"{FIELD_DESCRIPTIONS.get(target, '')}"
        for target, required in input_columns
    )
    system_prompt = app_config.render_prompt(
        app_config.AI_PROMPTS["mapping_suggestion"],
        columns=", ".join(columns),
        fields=fields,
    ).strip()
    if context.strip():
        system_prompt += (
            app_config.AI_PROMPTS["mapping_suggestion_context_suffix"] + context.strip()
        )
    if known_loan_status_column:
        system_prompt += (
            '\n\n"Loan Status" is already mapped to the column '
            f'"{known_loan_status_column}" (not among the fields above - do not '
            'include "Loan Status" in your response). Use this only to decide '
            '"_active_status_values" as described above, if you have evidence for it.'
        )

    resp = requests.post(
        DEEPINFRA_CHAT_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": DEEPINFRA_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Guess the best mapping for these columns."},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": app_config.AI["mapping_suggestion_max_tokens"],
        },
        timeout=app_config.AI["request_timeout_seconds"],
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    suggestions = json.loads(content)

    valid_columns = set(str(c) for c in columns)
    mapping = {}
    for target, _ in input_columns:
        src = suggestions.get(target)
        if src is None:
            mapping[target] = None
            continue
        src = str(src).strip()
        mapping[target] = src if src in valid_columns else None

    exclude_suggestion = None
    raw_exclude = suggestions.get("_exclude_rows")
    if isinstance(raw_exclude, dict):
        excl_col = str(raw_exclude.get("column") or "").strip()
        excl_vals = raw_exclude.get("values")
        if isinstance(excl_vals, str):
            excl_vals = [excl_vals]
        excl_vals = [str(v).strip() for v in (excl_vals or []) if str(v).strip()]
        # Only trust it if the model named a real column and at least one value -
        # never invent a filter that could silently drop real loans.
        if excl_col in valid_columns and excl_vals:
            exclude_suggestion = {"column": excl_col, "values": excl_vals}

    active_status_values = None
    raw_active = suggestions.get("_active_status_values")
    # Only meaningful if a source column for "Loan Status" is actually known -
    # otherwise there's no column for these values to belong to.
    if (mapping.get("Loan Status") or known_loan_status_column) and isinstance(raw_active, list):
        vals = [str(v).strip() for v in raw_active if str(v).strip()]
        if vals:
            active_status_values = vals
    return mapping, exclude_suggestion, active_status_values


def _suggest_escalation_writeup(filename: str, facts: list, model_name: str) -> dict:
    """Ask DeepSeek V4 Flash (via DeepInfra) to guess the borrower from the
    uploaded file name and turn already-aggregated flagged-cohort facts into a
    well organized Slack write-up. Only the file name and cohort-level
    statistics are sent - never raw loan rows. Returns {} if no API key is
    configured; raises on network/parsing errors for the caller to handle."""
    api_key = _get_deepinfra_api_key()
    if not api_key:
        return {}

    system_prompt = app_config.render_prompt(
        app_config.AI_PROMPTS["escalation_writeup"],
        model_name=model_name,
        filename=filename,
        facts="\n".join(facts),
    ).strip()

    resp = requests.post(
        DEEPINFRA_CHAT_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": DEEPINFRA_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Draft the escalation."},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": app_config.AI["escalation_writeup_max_tokens"],
        },
        timeout=app_config.AI["request_timeout_seconds"],
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    result = json.loads(content)
    if "borrower_name" not in result or "message" not in result:
        raise ValueError("Model response missing borrower_name/message")
    return result


def _ask_data_chatbot(question: str, context: str, history: list) -> str:
    """Ask DeepSeek V4 Flash (via DeepInfra) a question about the current
    analysis. `context` is a text summary of already-computed portfolio/cohort
    statistics and calculation methodology - never the raw loan-level rows, so
    no per-loan data leaves the app for this feature."""
    api_key = _get_deepinfra_api_key()
    if not api_key:
        raise RuntimeError(
            "No DEEPINFRA_API_KEY found. Add it to Streamlit secrets or the environment."
        )

    system_prompt = app_config.AI_PROMPTS["chat_assistant"].strip() + "\n\n" + context
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": question})

    resp = requests.post(
        DEEPINFRA_CHAT_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": DEEPINFRA_MODEL,
            "messages": messages,
            "max_tokens": app_config.AI["chat_assistant_max_tokens"],
        },
        timeout=app_config.AI["request_timeout_seconds"],
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
