"""Streamlit SC Analysis tool for the Lending and Rental & Subscription models.

Reproduces the SC Analysis for LTV and UE workbook's generic lending template
(port of the Lending/ project in this monorepo, itself derived from
github.com/nephy-edge/screening-data-analysis-tool) and the Rental &
Subscription (asset-lease) template (rental_and_subscription/rental_analysis/).
Pick the model at the top of the page; each has its own column mapping,
General Inputs, analysis pipeline and tab layout.
"""

# App.py is string-dominated: most physical lines that exceed the project's
# 100-char limit are CSS/HTML/JavaScript emitted from render functions and
# human-readable help/caption text - none can be reflowed without altering
# the delivered markup or UI copy. E501 is suppressed at file level rather
# than disabled project-wide, so logic-only files still enforce it.
# ruff: noqa: E501

import io
import json
import os
import re
import secrets
import sys
from datetime import datetime as _dt
from pathlib import Path

import altair as alt
import numpy_financial as npf
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv
from openpyxl import load_workbook
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

import config as app_config
from theme import inject_style, render_cover, render_masthead

load_dotenv()

# The template_analysis package lives under scripts/, which is not on
# sys.path by default when the app is launched from the repo root - add it
# before importing that package. The deferred import is deliberate, so the
# block below is annotated to skip ruff's top-of-file import rule (E402).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))

from template_analysis.cohorts import build_cohorts as lending_build_cohorts  # noqa: E402
from template_analysis.cohorts_for_x_or_more_loans import (  # noqa: E402
    filter_cohorts as lending_filter_cohorts,
)
from template_analysis.data_input import (  # noqa: E402
    process_data_input as lending_process_data_input,
)
from template_analysis.date_detection import detect_date as _detect_date  # noqa: E402
from template_analysis.general_inputs import GeneralInputs as LendingGeneralInputs  # noqa: E402
from template_analysis.ltv_analysis import LtvAnalysis as LendingLtvAnalysis  # noqa: E402
from template_analysis.ltv_calculator import (  # noqa: E402
    compute_ltv as ltv_calculator_compute,
    COUNTRIES as LTV_CALC_COUNTRIES,
    DATA_INPUT_SOURCES as LTV_CALC_DATA_SOURCES,
    FX_DEVAL_TABLE as LTV_CALC_FX_DEVAL_TABLE,
    FX_RISK_RATINGS as LTV_CALC_FX_RISK_RATINGS,
    HEDGE_TYPES as LTV_CALC_HEDGE_TYPES,
    nearest_tenor_bucket as ltv_calculator_nearest_tenor,
    sector_min_loss_rate as ltv_calculator_sector_min_loss_rate,
    SEGMENTATIONS as LTV_CALC_SEGMENTATIONS,
    TENORS as LTV_CALC_TENORS,
)
from template_analysis.ue_analysis import UeAnalysis as LendingUeAnalysis  # noqa: E402

import document_text  # noqa: E402
import drive_store  # noqa: E402
import profile_store  # noqa: E402
import s3_store  # noqa: E402
from data_normalize import _clean_numeric, _coerce_dates, _format_normalize  # noqa: E402
from html_tables import (  # noqa: E402
    _render_big_number_card,
    _render_matrix_table,
    _render_snapshot_table,
    _ue_model_badge,
    fmt,
)
from quality_checks import (  # noqa: E402
    _lending_data_quality_checks,
    _rental_data_quality_checks,
    _validate_mapping,
)
from summary_metrics import (  # noqa: E402
    lending_summary_metrics,
    rental_summary_metrics,
)
from ca_bundle import _ca_bundle_path  # noqa: E402
from ai_client import (  # noqa: E402
    _ask_data_chatbot,
    _cache_key,
    _get_deepinfra_api_key,
    _invalidate_ai_mapping_guesses,
    _load_cache,
    _save_cache,
    _suggest_escalation_writeup,
    _suggest_mapping,
)
from chat_context import _build_lending_chat_context, _build_rental_chat_context  # noqa: E402
from integrations import (  # noqa: E402
    _fetch_usd_fx_rates,
    _redshift_available,
    _get_slack_webhook_url,
    _redshift_fetch_loan_tape,
    _redshift_list_borrowers,
    _send_slack_feedback,
)
from google_oauth import (  # noqa: E402
    _exchange_google_code,
    _gdrive_access_token,
    _gdrive_token,
    _get_google_client_id,
    _get_google_client_secret,
    _google_auth_url,
    _google_redirect_uri,
)
from session_cookie import (  # noqa: E402
    _clear_session_cookie_and_reload,
    _consume_oauth_state,
    _issue_oauth_state,
)
from login_gates import _auth_mode, _render_login_gate  # noqa: E402

# The rental_and_subscription package lives in its own folder at the repo root
# (moved out of scripts/ when the model was archived) - add it to sys.path the
# same way scripts/ is added above.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "rental_and_subscription"))

from rental_analysis.asset_view import (  # noqa: E402
    build_asset_view,
    count_missing_asset_id_rows,
)
from rental_analysis.churn_analysis import ChurnAnalysis  # noqa: E402
from rental_analysis.cohorts_for_x_or_more_loans import filter_cohorts  # noqa: E402
from rental_analysis.data_input import (  # noqa: E402
    REQUIRED_COLUMNS,
    OPTIONAL_COLUMNS,
    apply_fallbacks,
    process_data_input,
)
from rental_analysis.general_analysis import describe as general_analysis  # noqa: E402
from rental_analysis.general_inputs import (  # noqa: E402
    DEFAULT_STATUS_MAP,
    GeneralInputs,
)
from rental_analysis.lease_cohorts import (  # noqa: E402
    build_cohorts,
    count_unparsed_cohort_rows,
)
from rental_analysis.ltv_analysis import LtvAnalysis  # noqa: E402
from rental_analysis.repayment_curve import build_repayment_curve  # noqa: E402
from rental_analysis.ts_covenants import build_ts_covenants  # noqa: E402
from rental_analysis.ue_analysis import UeAnalysis  # noqa: E402

# export_workbook imports from both template_analysis and rental_analysis, so
# it can only be imported after both sys.path.insert calls above.
from export_workbook import (  # noqa: E402
    _add_chat_history_sheet,
    _build_lending_export_workbook,
    _build_rental_export_workbook,
)


GOOGLE_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"


# ISO 4217 currencies covered by the open.er-api.com free FX endpoint - kept
# in sync with that API's own currency set (checked via its /latest/USD
# response) so every option in the picker below is actually convertible.
CURRENCY_NAMES = {
    "AED": "UAE Dirham",
    "AFN": "Afghan Afghani",
    "ALL": "Albanian Lek",
    "AMD": "Armenian Dram",
    "ANG": "Netherlands Antillean Guilder",
    "AOA": "Angolan Kwanza",
    "ARS": "Argentine Peso",
    "AUD": "Australian Dollar",
    "AWG": "Aruban Florin",
    "AZN": "Azerbaijani Manat",
    "BAM": "Bosnia-Herzegovina Convertible Mark",
    "BBD": "Barbadian Dollar",
    "BDT": "Bangladeshi Taka",
    "BGN": "Bulgarian Lev",
    "BHD": "Bahraini Dinar",
    "BIF": "Burundian Franc",
    "BMD": "Bermudan Dollar",
    "BND": "Brunei Dollar",
    "BOB": "Bolivian Boliviano",
    "BRL": "Brazilian Real",
    "BSD": "Bahamian Dollar",
    "BTN": "Bhutanese Ngultrum",
    "BWP": "Botswanan Pula",
    "BYN": "Belarusian Ruble",
    "BZD": "Belize Dollar",
    "CAD": "Canadian Dollar",
    "CDF": "Congolese Franc",
    "CHF": "Swiss Franc",
    "CLF": "Chilean Unit of Account (UF)",
    "CLP": "Chilean Peso",
    "CNH": "Chinese Yuan (Offshore)",
    "CNY": "Chinese Yuan",
    "COP": "Colombian Peso",
    "CRC": "Costa Rican Colon",
    "CUP": "Cuban Peso",
    "CVE": "Cape Verdean Escudo",
    "CZK": "Czech Koruna",
    "DJF": "Djiboutian Franc",
    "DKK": "Danish Krone",
    "DOP": "Dominican Peso",
    "DZD": "Algerian Dinar",
    "EGP": "Egyptian Pound",
    "ERN": "Eritrean Nakfa",
    "ETB": "Ethiopian Birr",
    "EUR": "Euro",
    "FJD": "Fijian Dollar",
    "FKP": "Falkland Islands Pound",
    "FOK": "Faroese Krona",
    "GBP": "British Pound",
    "GEL": "Georgian Lari",
    "GGP": "Guernsey Pound",
    "GHS": "Ghanaian Cedi",
    "GIP": "Gibraltar Pound",
    "GMD": "Gambian Dalasi",
    "GNF": "Guinean Franc",
    "GTQ": "Guatemalan Quetzal",
    "GYD": "Guyanaese Dollar",
    "HKD": "Hong Kong Dollar",
    "HNL": "Honduran Lempira",
    "HRK": "Croatian Kuna",
    "HTG": "Haitian Gourde",
    "HUF": "Hungarian Forint",
    "IDR": "Indonesian Rupiah",
    "ILS": "Israeli New Shekel",
    "IMP": "Isle of Man Pound",
    "INR": "Indian Rupee",
    "IQD": "Iraqi Dinar",
    "IRR": "Iranian Rial",
    "ISK": "Icelandic Krona",
    "JEP": "Jersey Pound",
    "JMD": "Jamaican Dollar",
    "JOD": "Jordanian Dinar",
    "JPY": "Japanese Yen",
    "KES": "Kenyan Shilling",
    "KGS": "Kyrgystani Som",
    "KHR": "Cambodian Riel",
    "KID": "Kiribati Dollar",
    "KMF": "Comorian Franc",
    "KRW": "South Korean Won",
    "KWD": "Kuwaiti Dinar",
    "KYD": "Cayman Islands Dollar",
    "KZT": "Kazakhstani Tenge",
    "LAK": "Laotian Kip",
    "LBP": "Lebanese Pound",
    "LKR": "Sri Lankan Rupee",
    "LRD": "Liberian Dollar",
    "LSL": "Lesotho Loti",
    "LYD": "Libyan Dinar",
    "MAD": "Moroccan Dirham",
    "MDL": "Moldovan Leu",
    "MGA": "Malagasy Ariary",
    "MKD": "Macedonian Denar",
    "MMK": "Myanmar Kyat",
    "MNT": "Mongolian Tugrik",
    "MOP": "Macanese Pataca",
    "MRU": "Mauritanian Ouguiya",
    "MUR": "Mauritian Rupee",
    "MVR": "Maldivian Rufiyaa",
    "MWK": "Malawian Kwacha",
    "MXN": "Mexican Peso",
    "MYR": "Malaysian Ringgit",
    "MZN": "Mozambican Metical",
    "NAD": "Namibian Dollar",
    "NGN": "Nigerian Naira",
    "NIO": "Nicaraguan Cordoba",
    "NOK": "Norwegian Krone",
    "NPR": "Nepalese Rupee",
    "NZD": "New Zealand Dollar",
    "OMR": "Omani Rial",
    "PAB": "Panamanian Balboa",
    "PEN": "Peruvian Sol",
    "PGK": "Papua New Guinean Kina",
    "PHP": "Philippine Peso",
    "PKR": "Pakistani Rupee",
    "PLN": "Polish Zloty",
    "PYG": "Paraguayan Guarani",
    "QAR": "Qatari Riyal",
    "RON": "Romanian Leu",
    "RSD": "Serbian Dinar",
    "RUB": "Russian Ruble",
    "RWF": "Rwandan Franc",
    "SAR": "Saudi Riyal",
    "SBD": "Solomon Islands Dollar",
    "SCR": "Seychellois Rupee",
    "SDG": "Sudanese Pound",
    "SEK": "Swedish Krona",
    "SGD": "Singapore Dollar",
    "SHP": "Saint Helena Pound",
    "SLE": "Sierra Leonean Leone",
    "SLL": "Sierra Leonean Leone (old)",
    "SOS": "Somali Shilling",
    "SRD": "Surinamese Dollar",
    "SSP": "South Sudanese Pound",
    "STN": "Sao Tome & Principe Dobra",
    "SYP": "Syrian Pound",
    "SZL": "Swazi Lilangeni",
    "THB": "Thai Baht",
    "TJS": "Tajikistani Somoni",
    "TMT": "Turkmenistani Manat",
    "TND": "Tunisian Dinar",
    "TOP": "Tongan Pa'anga",
    "TRY": "Turkish Lira",
    "TTD": "Trinidad & Tobago Dollar",
    "TVD": "Tuvaluan Dollar",
    "TWD": "New Taiwan Dollar",
    "TZS": "Tanzanian Shilling",
    "UAH": "Ukrainian Hryvnia",
    "UGX": "Ugandan Shilling",
    "USD": "US Dollar",
    "UYU": "Uruguayan Peso",
    "UZS": "Uzbekistani Som",
    "VES": "Venezuelan Bolivar",
    "VND": "Vietnamese Dong",
    "VUV": "Vanuatu Vatu",
    "WST": "Samoan Tala",
    "XAF": "Central African CFA Franc",
    "XCD": "East Caribbean Dollar",
    "XCG": "Caribbean Guilder",
    "XDR": "IMF Special Drawing Rights",
    "XOF": "West African CFA Franc",
    "XPF": "CFP Franc",
    "YER": "Yemeni Rial",
    "ZAR": "South African Rand",
    "ZMW": "Zambian Kwacha",
    "ZWG": "Zimbabwe Gold",
    "ZWL": "Zimbabwean Dollar",
}
# The subset of NUMERIC_FIELDS that are actually money (as opposed to day
# counts) - only these get rescaled when converting to USD. Covers both
# models' monetary fields; the conversion loop only touches whichever ones
# are present in the loaded file.
CURRENCY_FIELDS = {
    "Principal Value",
    "Expected Interest",
    "Gross Loan Value",
    "Expected Fee",
    "Total Paid",
    "Total Due",
    "downpayment",
    "total_contract_value",
    "monthly_expected_payment",
    "amount_expected_to_date",
    "total_paid",
    "cost_of_asset",
    "current_asset_value",
    "recovery_amount",
}


MODEL_LABELS = {"rental": "Rental & Subscription", "lending": "Lending"}
# model_key is driven by the st.selectbox below; reading it from session_state
# here (a pure read, before set_page_config, which must be the first Streamlit
# call) lets the page title/masthead reflect the current model on every rerun.
_model_key = st.session_state.get("model_key", "lending")

st.set_page_config(
    page_title=f"SC Analysis - {MODEL_LABELS[_model_key]}",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_style()
render_masthead(f"SC Analysis - {MODEL_LABELS[_model_key]}")
render_cover(
    "Structured Credit Analysis - LTV & Unit Economics",
    "Upload loan-level portfolio data, map your columns to the Data Input "
    "template, and the analysis is computed automatically across all sheets."
    if _model_key == "lending"
    else "Upload contract-level lease/rental data, map your columns to the Data Input "
    "template, and the analysis is computed automatically across all sheets.",
)

model_key = st.selectbox(
    "Select model",
    options=list(MODEL_LABELS.keys()),
    format_func=lambda k: MODEL_LABELS[k],
    index=list(MODEL_LABELS.keys()).index(_model_key) if _model_key in MODEL_LABELS else 0,
    key="model_key",
)
# Switching models must clear every analysis/session artifact tied to the old
# model - the upload, mapping, general inputs, context docs, and any rendered
# export/custom-viz state - so nothing from the other model leaks across.
if st.session_state.get("_active_model") != model_key:
    st.session_state["_active_model"] = model_key
    for k in (
        "uploaded_file_id",
        "analysis_ran",
        "analysis_mapping",
        "analysis_mapping_warnings",
        "analysis_gi_overrides",
        "analysis_currency",
        "analysis_exclude_rule",
        "analysis_active_status_values",
        "restored_session",
        "restored_session_checked_id",
        "derived_columns",
        "dc_ai_suggestion",
        "custom_chart_cards",
        "custom_chart_next_id",
        "export_charts",
        "context_files",
        "context_documents",
        "context_doc_ids",
        "gs_download_id",
        "gs_bytes",
        "gs_name",
        "ai_chat_history",
        "analysis_context",
        "data_source",
        "uploader",
    ):
        st.session_state.pop(k, None)
is_lending = model_key == "lending"


@st.cache_data(ttl=30, show_spinner=False)
def _cached_list_profiles(auth_mode: str, identity: str) -> list:
    if auth_mode == "google":
        return drive_store.list_profiles(identity)
    if auth_mode == "cognito":
        return s3_store.list_profiles(identity)
    return profile_store.list_profiles(identity)


def _store_list_profiles() -> list:
    """List this user's saved deals (each a {session_id, label, saved_at}),
    newest first - powers the "Resume my saved session" picker.

    This is computed on every script rerun (it decides whether "Resume my
    saved session" even appears as a data-source option), so it's cached for
    30s: for a Google Drive account, listing means one Drive API round trip
    per saved deal (list the matching files, then download each one's small
    metadata to read its label) - without caching, every click or keystroke
    anywhere in the app would re-pay that cost, not just interactions with
    the resume picker itself. _save_profile_snapshot clears this cache so a
    session you just saved shows up immediately rather than after 30s."""
    if _auth_mode() == "google":
        try:
            token = _gdrive_access_token(st.session_state["gdrive_token"])
        except Exception:
            return []
        return _cached_list_profiles("google", token)
    identity = st.session_state.get("current_user", "")
    return _cached_list_profiles(_auth_mode(), identity)


def _store_load_profile(session_id: str) -> dict | None:
    if _auth_mode() == "google":
        try:
            token = _gdrive_access_token(st.session_state["gdrive_token"])
        except Exception:
            return None
        return drive_store.load_profile(token, session_id)
    identity = st.session_state.get("current_user", "")
    if _auth_mode() == "cognito":
        return s3_store.load_profile(identity, session_id)
    return profile_store.load_profile(identity, session_id)


def _store_save_profile(session_id: str, **snapshot_kwargs) -> None:
    """Best-effort: never raises, since auto-save must not block the analysis."""
    try:
        auth_mode = _auth_mode()
        identity = st.session_state.get("current_user", "")
        if auth_mode == "google":
            token = _gdrive_access_token(st.session_state["gdrive_token"])
            drive_store.save_profile(token, session_id, **snapshot_kwargs)
        elif auth_mode == "cognito":
            s3_store.save_profile(identity, session_id, **snapshot_kwargs)
        else:
            profile_store.save_profile(identity, session_id, **snapshot_kwargs)
    except Exception:
        pass


def _store_create_share(**snapshot_kwargs) -> str:
    current_user = st.session_state.get("current_user", "")
    if _auth_mode() == "google":
        token = _gdrive_access_token(st.session_state["gdrive_token"])
        return drive_store.create_share(token, current_user, **snapshot_kwargs)
    if _auth_mode() == "cognito":
        return s3_store.create_share(current_user, **snapshot_kwargs)
    return profile_store.create_share(current_user, **snapshot_kwargs)


def _store_load_share(share_id: str) -> dict | None:
    if _auth_mode() == "google":
        try:
            token = _gdrive_access_token(st.session_state["gdrive_token"])
        except Exception:
            return None
        return drive_store.load_share(token, share_id)
    if _auth_mode() == "cognito":
        return s3_store.load_share(share_id)
    return profile_store.load_share(share_id)


current_user = _render_login_gate()
_user_bar_l, _user_bar_r = st.columns([5, 1], vertical_alignment="center")
with _user_bar_l:
    if _auth_mode() == "google":
        st.caption(f"Signed in as **{current_user}** via Google")
    elif _auth_mode() == "cognito":
        st.caption(f"Signed in as **{current_user}**")
    else:
        st.caption(f"Signed in as **{current_user}** (temporary sign-in, not yet real SSO)")
with _user_bar_r:
    if st.button("Log out", key="logout_btn"):
        if _auth_mode() == "cognito":
            # Streamlit's native auth owns its own session cookie - separate
            # from session_cookie.py, which the google/dummy paths use - so
            # it needs its own logout call, not a clear-and-reload.
            st.logout()
        else:
            st.session_state.clear()
            _clear_session_cookie_and_reload()

feedback_col = st.columns([5, 1], vertical_alignment="bottom")[1]

with feedback_col:
    with st.popover("💬 Feedback"):
        st.caption("Share what worked or didn't work for you.")
        if not _get_slack_webhook_url():
            st.caption("Feedback routing not configured (SLACK_WEBHOOK_URL).")
        if st.session_state.pop("fb_just_sent", False):
            # Shown on the rerun triggered below, not in the same run as the
            # button click - st.rerun() interrupts the script immediately, so
            # a st.success() called right before it never reaches the browser.
            st.success("Thanks — feedback sent.")
        # Both fields' keys include a version counter so a successful send
        # can force brand-new, empty widget instances on the next run -
        # popping the old keys alone didn't reliably clear them in
        # production (widgets inside a popover can retain their prior value
        # across a rerun even once the session_state key is removed).
        fb_form_version = st.session_state.setdefault("fb_form_version", 0)
        fb_user = st.text_input(
            "Your name or email (optional)",
            key=f"fb_user_{fb_form_version}",
        )
        fb_text = st.text_area(
            "What worked / what didn't",
            key=f"fb_text_{fb_form_version}",
            height=120,
        )
        if st.button("Send feedback", key="fb_send"):
            if not fb_text.strip():
                st.warning("Please enter some feedback first.")
            else:
                ok, err = _send_slack_feedback(fb_text, MODEL_LABELS[model_key], fb_user)
                if ok:
                    st.session_state["fb_form_version"] = fb_form_version + 1
                    st.session_state["fb_just_sent"] = True
                    st.rerun()
                else:
                    st.error(f"Could not send feedback: {err}")

LENDING_CONFIG = dict(
    id_field="Loan ID",
    input_columns=[
        ("Loan ID", True),
        ("Disbursement Date", True),
        ("Expected Completion Date", True),
        ("Principal Value", True),
        ("Expected Interest", True),
        ("Gross Loan Value", False),
        ("Expected Fee", False),
        ("Total Paid", True),
        ("Total Due", False),
        ("Loan Status", False),
        ("Days Late", False),
        ("Term (days)", False),
    ],
    date_fields={"Disbursement Date", "Expected Completion Date"},
    numeric_fields={
        "Principal Value",
        "Expected Interest",
        "Gross Loan Value",
        "Expected Fee",
        "Total Paid",
        "Total Due",
        "Days Late",
        "Term (days)",
    },
    dayfirst=True,
    primary_date_field="Disbursement Date",
    mapping_cache_path=os.path.join(os.path.expanduser("~"), ".sc_analysis_column_mappings.json"),
    needs_status_map=False,
    domain_hint="loan-portfolio spreadsheet",
)
RENTAL_CONFIG = dict(
    id_field="contract_id",
    input_columns=[(c, True) for c in REQUIRED_COLUMNS] + [(c, False) for c in OPTIONAL_COLUMNS],
    date_fields={
        "start_date",
        "expected_end_date",
        "closed_date",
        "asset_recovery_date",
        "recovery_date",
        "last_payment_date",
    },
    numeric_fields={
        "downpayment",
        "total_contract_value",
        "monthly_expected_payment",
        "amount_expected_to_date",
        "total_paid",
        "cost_of_asset",
        "current_asset_value",
        "recovery_amount",
    },
    dayfirst=False,
    primary_date_field="start_date",
    mapping_cache_path=os.path.join(
        os.path.expanduser("~"), ".rental_sc_analysis_column_mappings.json"
    ),
    needs_status_map=True,
    domain_hint="rental/lease contract-level dataset",
)
active_cfg = LENDING_CONFIG if is_lending else RENTAL_CONFIG
INPUT_COLUMNS = active_cfg["input_columns"]
DATE_FIELDS = active_cfg["date_fields"]
NUMERIC_FIELDS = active_cfg["numeric_fields"]
REQUIRED_FIELDS = {t for t, required in INPUT_COLUMNS if required}
MAPPING_CACHE_PATH = active_cfg["mapping_cache_path"]
STATUS_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".rental_sc_analysis_status_mappings.json"
)


def _read_uploaded_bytes(uploaded) -> bytes:
    try:
        return uploaded.getvalue()
    except AttributeError:
        uploaded.seek(0)
        return uploaded.read()


def _extract_document_text(uploaded) -> dict:
    """Extract readable text from a Streamlit-uploaded document - see
    scripts/document_text.py (shared with web_api/main.py's
    /api/context-docs so both apps extract identically)."""
    return document_text.extract_document_text(uploaded.name, _read_uploaded_bytes(uploaded))


def _truncate_text(text: str, limit: int) -> str:
    return document_text.truncate_text(text, limit)


def _ai_context_text(max_chars: int = 60000, per_doc: int = 15000) -> str:
    """Combine the user's free-text notes with text extracted from uploaded
    documents into one prompt block, truncated to keep AI calls bounded."""
    return document_text.combine_context_text(
        st.session_state.get("analysis_context", ""),
        st.session_state.get("context_documents", []),
        max_chars=max_chars,
        per_doc=per_doc,
    )


def _render_ue_model_ai_tab(df: pd.DataFrame, ue_data: dict) -> None:
    """Replicates the master UE Model workbook's 'For AI' sheet: a checklist of unit-economics
    outputs split into what's already derivable from a loan tape (green) and what needs a
    manually-supplied assumption because a loan tape doesn't record it (yellow) - variable costs
    and cost of financing. 'GBV' below means total Principal Value disbursed, matching the
    Summary tab's 'Value of Principal Disbursed'.

    Income/Margin/Product Contribution deliberately use ONLY matured loans (Reached T+3? = True),
    not the full book: netting full-book revenue (including unrealized interest on loans too young
    to have defaulted) against losses from a small matured subset would overstate the margin. Using
    the same matured population for revenue, origination income, losses and GBV gives a smaller-
    sample but internally consistent "at maturity" view instead.

    Two workbook rows aren't replicated: 'IRR of cash flow' and 'Net Unit Economics (Cash Flow)'
    both need a full monthly repayment + loss + cost cash-flow schedule (XIRR/XNPV at WACC) that
    this app doesn't build - 'Net IRR' above is a straight-line approximation, not that schedule.
    """
    principal = df["Principal Value"].sum()
    revenue = df["Expected Interest"].sum()
    origination_income = df["Expected Fee"].sum()

    matured = df[df["Reached T+3?"]]
    matured_principal = matured["Principal Value"].sum()
    matured_revenue = matured["Expected Interest"].sum()
    matured_origination_income = matured["Expected Fee"].sum()
    matured_owed = matured_principal + matured_revenue + matured_origination_income

    actual_loss_rate = (
        (matured_owed - matured["Total Paid"].sum()) / matured_owed
        if matured_owed
        else float("nan")
    )
    proxy_loss_rate = ue_data["Loss Rate Proxy (1-PvD)"]

    matured_term_m = (
        (matured["Term (days)"] * matured["Principal Value"]).sum() / matured_principal / 30.4375
        if matured_principal
        else float("nan")
    )

    term_m = ue_data["Average Expected Term"] / 30.4375
    # Internal estimated APR: a simple flat-rate annualization (Product Interest Rate / term in
    # months x 12), doubled - the x2 approximates the jump from a flat rate (quoted against the
    # full, undeclining principal) to the equivalent rate against a declining balance, where the
    # borrower's average outstanding principal over the loan's life is roughly half of the
    # original amount. Replaces the previous amortization-solved nominal APR (this function no
    # longer calls principal_weighted_average_rates).
    implied_interest = (ue_data["Average Interest %"] / term_m) * 12 * 2 if term_m else float("nan")

    st.markdown("")
    _ue_model_badge("Manual inputs (not in a loan tape)", "#f5d90a", "#16312E")
    st.caption(
        "The master workbook needs these to complete the Unit Economics picture, but they're cost "
        "and financing assumptions, not something a loan tape records - enter cost % against "
        "matured-loan GBV (matching the Derived section below), financing % as per annum; 0 if not "
        "applicable."
    )
    _cost_defaults = app_config.UE_MODEL_COST_DEFAULTS
    m1, m2 = st.columns(2)
    upfront_cost_pct = (
        m1.number_input(
            "Upfront variable costs (% of matured-loan GBV)",
            min_value=0.0,
            value=_cost_defaults["upfront_cost_pct"],
            step=0.1,
            format="%.2f",
            key="ue_ai_upfront_cost",
        )
        / 100
    )
    ongoing_cost_pct = (
        m2.number_input(
            "Ongoing variable costs (% of matured-loan GBV)",
            min_value=0.0,
            value=_cost_defaults["ongoing_cost_pct"],
            step=0.1,
            format="%.2f",
            key="ue_ai_ongoing_cost",
        )
        / 100
    )
    m4, m5, m6 = st.columns(3)
    financing_cost_pct = (
        m4.number_input(
            "Financing cost (% p.a.)",
            min_value=0.0,
            value=_cost_defaults["financing_cost_pct"],
            step=0.1,
            format="%.2f",
            key="ue_ai_financing_cost",
        )
        / 100
    )
    facility_fee_pct = (
        m5.number_input(
            "Facility fee - annualised (% p.a.)",
            min_value=0.0,
            value=_cost_defaults["facility_fee_pct"],
            step=0.1,
            format="%.2f",
            key="ue_ai_facility_fee",
        )
        / 100
    )
    hedge_cost_pct = (
        m6.number_input(
            "Hedge cost - annualised (% p.a.)",
            min_value=0.0,
            value=_cost_defaults["hedge_cost_pct"],
            step=0.1,
            format="%.2f",
            key="ue_ai_hedge_cost",
        )
        / 100
    )

    st.markdown("")
    st.markdown("**Loss Rate**")
    loss_rate_choice = st.selectbox(
        "Loss Rate used below",
        options=["Actual Loss Rate", "Proxy Loss Rate (1-PvD)", "Input"],
        key="ue_ai_loss_rate_choice",
        help="Feeds Loss %, Income (net losses), Product contribution and the IRR cards below. "
        "Actual Loss Rate: (matured owed - matured paid) / matured owed - "
        "the same figure as the Summary tab's Loss Rate. Proxy Loss Rate (1-PvD): "
        "1 - Total Paid / Total Due across the whole book (needs Total Due mapped - n/a "
        "otherwise). Input: type in your own assumption.",
    )
    if loss_rate_choice == "Actual Loss Rate":
        loss_rate = actual_loss_rate
    elif loss_rate_choice == "Proxy Loss Rate (1-PvD)":
        loss_rate = proxy_loss_rate
    else:
        loss_rate = (
            st.number_input(
                "Loss Rate (%)",
                min_value=0.0,
                value=(actual_loss_rate * 100 if pd.notna(actual_loss_rate) else 0.0),
                step=0.1,
                format="%.2f",
                key="ue_ai_loss_rate_input",
            )
            / 100
        )

    losses_dollar = (
        loss_rate * matured_owed if pd.notna(loss_rate) and matured_owed else float("nan")
    )
    losses_pct_of_gbv = (
        losses_dollar / principal if principal and pd.notna(losses_dollar) else float("nan")
    )

    n_matured = len(matured)
    avg_principal_matured = matured_principal / n_matured if n_matured else float("nan")
    avg_fee_matured = matured_origination_income / n_matured if n_matured else float("nan")
    avg_gbv_matured = matured_owed / n_matured if n_matured and matured_owed else float("nan")
    n_periods = (
        int(round(matured_term_m)) + 3 if pd.notna(matured_term_m) and matured_term_m > 0 else None
    )

    gross_irr = float("nan")
    if (
        n_periods
        and pd.notna(avg_principal_matured)
        and pd.notna(avg_gbv_matured)
        and pd.notna(losses_dollar)
        and matured_owed
    ):
        cf0 = -avg_principal_matured + avg_fee_matured - (upfront_cost_pct * avg_principal_matured)
        monthly_cf = (avg_gbv_matured / n_periods) * (1 - loss_rate) - (
            ongoing_cost_pct * avg_principal_matured / n_periods
        )
        cashflows = [cf0] + [monthly_cf] * n_periods
        try:
            monthly_irr = npf.irr(cashflows)
        except Exception:
            monthly_irr = float("nan")
        if pd.notna(monthly_irr):
            gross_irr = (1 + monthly_irr) ** 12 - 1

    cost_of_finance_pct = financing_cost_pct + facility_fee_pct + hedge_cost_pct
    net_irr = gross_irr - cost_of_finance_pct if pd.notna(gross_irr) else float("nan")

    st.markdown("")
    irr1, irr2 = st.columns(2)
    with irr1:
        _render_big_number_card(
            "Gross IRR",
            gross_irr,
            help_text=(
                "The annualized return on an average matured loan before cost of finance: what you pay "
                "out to fund it versus what you collect back over its life, net of losses and variable "
                "costs. A simplified straight-line estimate, not the workbook's full cash-flow schedule."
            ),
        )
    with irr2:
        _render_big_number_card(
            "Net IRR",
            net_irr,
            help_text=(
                "Gross IRR minus Cost of Finance (Financing cost + Facility fee + Hedge cost, % p.a.) - "
                "the return after what it costs to fund the book, not just the loan itself."
            ),
        )
    st.markdown("")

    variable_costs_pct = upfront_cost_pct + ongoing_cost_pct

    # Cost of Finance is a per-annum rate, but Product Margin is cumulative over the matured
    # loans' own life - subtracting the raw annual rate from it would mix units. Gross it up by
    # the MATURED subset's own weighted term (not the full-book Term (m) above, which is a
    # different - here, much longer - population) so both figures cover the same horizon.
    cost_of_finance_over_term = (
        cost_of_finance_pct * (matured_term_m / 12) if pd.notna(matured_term_m) else float("nan")
    )

    if matured_principal and pd.notna(losses_dollar):
        income_net_losses = matured_revenue + matured_origination_income - losses_dollar
        income_net_losses_margin = income_net_losses / matured_principal
        variable_costs_dollar = variable_costs_pct * matured_principal
        product_contribution = income_net_losses - variable_costs_dollar
        product_margin = product_contribution / matured_principal
    else:
        income_net_losses = income_net_losses_margin = float("nan")
        variable_costs_dollar = float("nan")
        product_contribution = product_margin = float("nan")

    st.markdown("")
    _ue_model_badge("Auto-calculated from your data", "#2ca02c", "#fff")
    _render_snapshot_table(
        [
            (
                "Product Interest Rate",
                fmt(ue_data["Average Interest %"]),
                "Total expected interest income as a % of total principal disbursed, across all loans - "
                "the stated interest on the product, not the estimated APR below.",
            ),
            (
                "Origination Income %",
                fmt(ue_data["Average Fee %"]),
                "Total expected fee income as a % of total principal disbursed, across all loans.",
            ),
            (
                "Estimated APR",
                fmt(implied_interest),
                "Internal estimated APR: Product Interest Rate divided by the average weighted "
                "monthly term, times 12, times 2 - a flat-rate annualization doubled to "
                "approximate the equivalent declining-balance rate (not the Summary tab's EAR, "
                "which is solved independently via amortization).",
            ),
            (
                "Implied Monthly Interest %",
                fmt(implied_interest / 12),
                "Implied interest divided by 12.",
            ),
            (
                "Loss %",
                fmt(losses_pct_of_gbv),
                "Losses in dollars divided by total Principal Value disbursed across the whole book - a "
                "literal % of GBV (not the Summary tab's Loss Rate, which divides by matured owed instead "
                "and is scoped to a much smaller base).",
            ),
            ("Term", fmt(term_m, "{:,.1f} months"), "Principal-weighted average term."),
        ]
    )
    st.caption(
        f"{len(matured):,} of {len(df):,} loans have matured (Reached T+3? = True), "
        f"{matured_principal / principal:.1%} of GBV"
        if principal
        else "No loans in view."
    )

    st.markdown("")
    _ue_model_badge(
        "Derived - matured loans only (auto-calculated + manual inputs)", "#2ca02c", "#fff"
    )
    st.caption(
        "Revenue, Origination Income and GBV below are re-summed over matured loans only, to match "
        "Losses' scope - not the full-book totals shown above."
    )
    _render_snapshot_table(
        [
            (
                "Income (net losses)",
                fmt(income_net_losses_margin),
                "Matured-only Revenue + Origination Income - Losses, as a % of matured-loan GBV.",
            ),
            (
                "Variable costs (% of GBV)",
                fmt(variable_costs_pct),
                "Sum of the two cost %s entered above.",
            ),
            (
                "Product contribution",
                fmt(product_margin),
                "Income (net losses) - Variable costs, as a % of matured-loan GBV - the workbook's "
                "'simple' (non-time-value-of-money) unit economics basis, computed only on the subset "
                "of the book that's actually matured.",
            ),
            (
                "Cost of Finance (% p.a.)",
                fmt(cost_of_finance_pct),
                "Financing cost + Facility fee + Hedge cost, as entered (per annum).",
            ),
            (
                "Cost of Finance (over loan life)",
                fmt(cost_of_finance_over_term),
                f"Cost of Finance (% p.a.) x matured loans' own weighted term ({fmt(matured_term_m, '{:,.1f}')} "
                "months) / 12 - not the full-book Term (m) above, which covers a different, longer-duration "
                "population. Grossed up so it's comparable to Product contribution.",
            ),
        ]
    )
    return {
        "revenue": revenue,
        "origination_income": origination_income,
        "implied_interest": implied_interest,
        "losses_dollar": losses_dollar,
        "losses_pct_of_gbv": losses_pct_of_gbv,
        "term_m": term_m,
        "n_matured": len(matured),
        "n_total": len(df),
        "matured_gbv_pct": matured_principal / principal if principal else float("nan"),
        "income_net_losses": income_net_losses,
        "income_net_losses_margin": income_net_losses_margin,
        "product_margin": product_margin,
        "cost_of_finance_pct": cost_of_finance_pct,
        "gross_irr": gross_irr,
        "net_irr": net_irr,
    }


_LENDING_EXTRA_FIELDS = {
    "Begin Date",
    "Total Dues Calculated",
    "Delinquent Amount",
    "Write-off amount",
}


def _render_variance_escalation(
    detail: "pd.DataFrame",
    metric_label: str,
    model_name: str,
    button_key: str,
    uploaded_name: str,
) -> None:
    """Shared by the Lending unexplained-variance check: renders the
    "escalate to analytics" button, drafts an AI write-up from the flagged
    cohort facts (falling back to a plain summary if the AI call fails), and
    sends it to the existing Slack feedback webhook.
    `detail` must have columns Cohort, <metric_label>, 'Swing vs Prior Cohort
    (pp)', 'Likely Contributing Factor(s)'."""
    if st.session_state.pop("dq_just_escalated", False):
        ai_warning = st.session_state.pop("dq_escalate_ai_warning", None)
        if ai_warning:
            st.warning(f"AI write-up unavailable ({ai_warning}); sent a plain summary instead.")
        else:
            st.success("Sent to analytics.")
    if st.button("Escalate flagged cohorts to analytics", key=button_key):
        lines = [
            f"- {r['Cohort']}: {r[metric_label]}% "
            f"({r['Swing vs Prior Cohort (pp)']:+.1f}pp swing) - "
            f"{r['Likely Contributing Factor(s)']}"
            for _, r in detail.iterrows()
        ]
        borrower_name, message, ai_warning = None, None, None
        try:
            with st.spinner("Drafting the escalation with AI..."):
                ai = _suggest_escalation_writeup(uploaded_name, lines, model_name)
            borrower_name = ai.get("borrower_name")
            message = ai.get("message")
        except Exception as e:
            ai_warning = str(e)
        if not message:
            message = (
                f"Unexplained variance flagged in {model_name} screening "
                "analysis:\n" + "\n".join(lines)
            )
        ok, err = _send_slack_feedback(message, borrower_name or model_name)
        if ok:
            if ai_warning:
                st.session_state["dq_escalate_ai_warning"] = ai_warning
            st.session_state["dq_just_escalated"] = True
            st.rerun()
        else:
            st.error(f"Could not escalate: {err}")


def _render_data_quality_checks(
    checks: list,
    variance_metric_label: str,
    model_name: str,
    button_key: str,
    uploaded_name: str,
) -> None:
    """Renders the "Data Quality Checks" section, shared by every model so
    Step 2 (duplicates, completion, portfolio size, history) and Step 4
    (variance diagnosis + escalation) always render identically regardless
    of which model is active - one implementation, not one per model."""
    st.subheader("Data Quality Checks")
    if not checks:
        st.success("No data quality issues flagged.")
        return
    for check in checks:
        (st.warning if check["level"] == "warning" else st.info)(check["message"])
        detail = check.get("detail")
        if detail is None:
            continue
        if check.get("detail_expander"):
            with st.expander(check["detail_expander"]):
                st.dataframe(detail, width="stretch", hide_index=True)
                if check.get("check_id") == "duplicate_loan_id":
                    st.checkbox(
                        "Deduplicate: keep only the first row for each "
                        "duplicated ID, then re-run analysis",
                        key="dedupe_loan_ids",
                    )
        else:
            st.dataframe(detail, width="stretch", hide_index=True)
            if check.get("check_id") == "unexplained_variance":
                _render_variance_escalation(
                    detail,
                    variance_metric_label,
                    model_name,
                    button_key,
                    uploaded_name,
                )


def _add_reference_line(chart, value, label, color="#d62728", x_anchor=None):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return chart
    ref = pd.DataFrame({"v": [value], "label": [label]})
    rule = alt.Chart(ref).mark_rule(color=color, strokeDash=[6, 6]).encode(y=alt.Y("v:Q"))
    if x_anchor is not None:
        ref["xa"] = [x_anchor]
        text = (
            alt.Chart(ref)
            .mark_text(color=color, dx=8, dy=-6, align="left", fontSize=11, fontWeight="bold")
            .encode(x="xa:T", y=alt.Y("v:Q"), text="label:N")
        )
    else:
        text = (
            alt.Chart(ref)
            .mark_text(color=color, dy=-7, dx=6, align="left", fontSize=11, fontWeight="bold")
            .encode(x=alt.value(70), y=alt.Y("v:Q"), text="label:N")
        )
    return (chart + rule + text).properties(padding={"right": 95})


_HISTOGRAM_HEADER_JS = JsCode("""
class HistogramHeader {
  init(params) {
    this.params = params;
    const bins = params.histogram || [];
    const max = Math.max(...bins, 1);
    const bars = bins.map(v => {
      const h = Math.max(2, Math.round((v / max) * 22));
      return `<div style="flex:1;height:${h}px;background:#4C8577;margin:0 1px;border-radius:1px;"></div>`;
    }).join('');
    this.eGui = document.createElement('div');
    this.eGui.style.width = '100%';
    this.eGui.style.cursor = 'pointer';
    this.eGui.innerHTML = `
      <div style="display:flex;align-items:flex-end;height:24px;width:100%;padding:0 2px;">${bars}</div>
      <div style="display:flex;align-items:center;justify-content:center;margin-top:3px;">
        <span style="font-size:12px;font-weight:600;">${params.displayName}</span>
        <span class="sort-icon" style="margin-left:4px;font-size:10px;"></span>
      </div>
    `;
    this.eSortIcon = this.eGui.querySelector('.sort-icon');
    this.onSortChanged = this.onSortChanged.bind(this);
    this.onClick = this.onClick.bind(this);
    this.eGui.addEventListener('click', this.onClick);
    params.column.addEventListener('sortChanged', this.onSortChanged);
    this.onSortChanged();
  }
  onClick(e) {
    this.params.progressSort(e.shiftKey);
  }
  onSortChanged() {
    const sort = this.params.column.getSort();
    this.eSortIcon.innerText = sort === 'asc' ? '▲' : sort === 'desc' ? '▼' : '';
  }
  getGui() { return this.eGui; }
  refresh(params) { return false; }
  destroy() {
    this.eGui.removeEventListener('click', this.onClick);
    this.params.column.removeEventListener('sortChanged', this.onSortChanged);
  }
}
""")


def _column_stats_tooltip(series: pd.Series, numeric: bool) -> str:
    non_null = series.dropna()
    nulls = int(series.isna().sum())
    if numeric:
        s = pd.to_numeric(non_null, errors="coerce").dropna()
        if s.empty:
            return f"Count: 0\nNulls: {nulls:,}"
        return (
            f"Count: {s.count():,}\n"
            f"Nulls: {nulls:,}\n"
            f"Sum: {s.sum():,.2f}\n"
            f"Mean: {s.mean():,.2f}\n"
            f"Median: {s.median():,.2f}\n"
            f"Std Dev: {s.std():,.2f}\n"
            f"Min: {s.min():,.2f}\n"
            f"Max: {s.max():,.2f}"
        )
    return f"Count: {non_null.count():,}\nNulls: {nulls:,}\nDistinct: {non_null.nunique():,}"


# ag-grid valueFormatters for the cohorts table columns. These must be
# JsCode (JS functions), NOT Python functions - st_aggrid serialises
# gridOptions to JSON and only converts JsCode objects to JS when
# allow_unsafe_jscode=True; a raw Python function in the column defs
# makes Streamlit's component-args JSON serialization fail with
# "Object of type function is not JSON serializable".
_FMT_MONEY_JS = JsCode(
    "params => params.value == null ? '' : '$' + params.value.toLocaleString('en-US', {maximumFractionDigits: 0})"
)
_FMT_INT_JS = JsCode(
    "params => params.value == null ? '' : params.value.toLocaleString('en-US', {maximumFractionDigits: 0})"
)
_FMT_PCT1_JS = JsCode("params => params.value == null ? '' : (params.value * 100).toFixed(1) + '%'")


# Display formatting applied to the cohorts table columns. Keys are exact
# column names produced by build_cohorts(); each maps to an ag-grid
# valueFormatter so the raw number stays numeric (sort/filter/histogram)
# while the cell renders the requested human format.
def _render_cohorts_grid(df: pd.DataFrame, bins: int = 10, height: int = 420):
    """Snowflake-style table: distribution histogram, hover stats, and a filter per column header.

    Copies `df` before handing it to AgGrid - st_aggrid mutates its `data`
    argument in place (converts every datetime64 column to ISO strings for
    JSON transport, with no defensive copy of its own), which would silently
    downgrade the caller's own DataFrame - e.g. turning a Cohort column from
    datetime64 into plain strings for every consumer downstream, not just
    this grid."""
    df = df.copy()
    money_cols = {
        "Total Principal",
        "Total Interest",
        "Total Fee",
        "Total Due",
        "Total Paid",
        "Avg Loan Principal Size",
    }
    term_cols = {"Weighted Avg Term"}
    pct_cols = {
        "PvD Ratio",
        "Loss Rate",
        "Avg Loan Fee % per cohort",
        "Avg Annualized Flat Interest Rate",
    }
    column_formatters = {
        **{c: _FMT_MONEY_JS for c in money_cols},
        **{c: _FMT_INT_JS for c in term_cols},
        **{c: _FMT_PCT1_JS for c in pct_cols},
    }
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(
        resizable=True,
        sortable=True,
        filter=True,
        floatingFilter=True,
    )
    for col in df.columns:
        is_numeric = pd.api.types.is_numeric_dtype(df[col])
        tooltip = _column_stats_tooltip(df[col], is_numeric)
        if not is_numeric:
            gb.configure_column(col, headerTooltip=tooltip)
            continue
        series = pd.to_numeric(df[col], errors="coerce").dropna()
        counts = (
            pd.cut(series, bins=bins).value_counts(sort=False).tolist()
            if series.nunique() > 1
            else []
        )
        kwargs = dict(
            headerComponent=_HISTOGRAM_HEADER_JS,
            headerComponentParams={"histogram": counts},
            headerTooltip=tooltip,
            filter="agNumberColumnFilter",
        )
        if col in column_formatters:
            kwargs["valueFormatter"] = column_formatters[col]
        gb.configure_column(col, **kwargs)
    grid_options = gb.build()
    grid_options["headerHeight"] = 54
    grid_options["tooltipShowDelay"] = 200
    AgGrid(
        df,
        gridOptions=grid_options,
        height=height,
        allow_unsafe_jscode=True,
        theme="streamlit",
        show_toolbar=False,
        show_search=False,
        show_download_button=False,
    )


def _render_data_input_grid(df: pd.DataFrame, bins: int = 10, height: int = 400):
    """Raw uploaded-data preview, styled like the Cohorts table: a
    distribution histogram + hover stats (count/nulls/sum/mean/median/std/
    min/max) in each numeric column's header, a filter per column, and the
    column menu (opened via the header's "..." icon) always visible instead
    of only on hover.

    Streamlit's native st.dataframe can't do the always-visible menu icon -
    it's built on glide-data-grid, which renders to a <canvas>, so there's no
    DOM/CSS to target and no exposed config for the hover behavior. AgGrid's
    suppressMenuHide is the documented option for it - but two things must be
    true for it to actually work in this AG Grid build: columnMenu must be
    forced back to "legacy" mode (this bundle's newer default "new" mode
    drops the classic header icon entirely, regardless of suppressMenuHide),
    and suppressMenuHide itself must be set as a grid-level option, not a
    per-column one - it moved from colDef to GridOptions in a later AG Grid
    version, so setting it per-column (the documented colDef-era usage) is
    silently ignored here. Note the histogram header (numeric columns only,
    same as the Cohorts table) replaces AG Grid's default header rendering
    entirely, which does not include the menu-icon markup - so numeric
    columns show the histogram instead of the always-visible menu icon,
    matching how the Cohorts table already behaves."""
    df = df.copy()
    money_cols = {"Total Paid", "Expected Interest", "Expected Fee", "Principal Value"}
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(
        resizable=True,
        sortable=True,
        filter=True,
        floatingFilter=True,
    )
    for col in df.columns:
        is_numeric = pd.api.types.is_numeric_dtype(df[col])
        tooltip = _column_stats_tooltip(df[col], is_numeric)
        if not is_numeric:
            gb.configure_column(col, headerTooltip=tooltip, menuTabs=["filterMenuTab"])
            continue
        series = pd.to_numeric(df[col], errors="coerce").dropna()
        counts = (
            pd.cut(series, bins=bins).value_counts(sort=False).tolist()
            if series.nunique() > 1
            else []
        )
        kwargs = dict(
            headerComponent=_HISTOGRAM_HEADER_JS,
            headerComponentParams={"histogram": counts},
            headerTooltip=tooltip,
            filter="agNumberColumnFilter",
            menuTabs=["filterMenuTab"],
        )
        if col in money_cols:
            kwargs["valueFormatter"] = _FMT_MONEY_JS
        gb.configure_column(col, **kwargs)
    gb.configure_grid_options(columnMenu="legacy", suppressMenuHide=True)
    grid_options = gb.build()
    grid_options["headerHeight"] = 54
    grid_options["tooltipShowDelay"] = 200
    AgGrid(
        df,
        gridOptions=grid_options,
        height=height,
        allow_unsafe_jscode=True,
        theme="streamlit",
        show_toolbar=False,
        show_search=False,
        show_download_button=False,
    )


_CURRENCY_NAME_HINTS = ("principal", "interest", "fee", "paid", "due", "revenue", "gbv", "amount")
_RATIO_NAME_HINTS = ("rate", "ratio", "%", "pct")


def _smart_quant_format(col_name: str, max_abs) -> tuple:
    """Infer a sensible (axis, tooltip_format) pair for a quantitative field
    from its name and the actual magnitude of the values being plotted:
    money-like names get a "$"+comma tooltip, rate/ratio-like names get a
    percentage, and any large-magnitude field (>= 1000, regardless of name)
    gets a short SI-style axis (k/M/B) - never hardcoded to one field, since
    it's re-derived from whatever's actually plotted each rerun. Shared
    between the primary Y encoding and an optional secondary line series in
    the chart builder below."""
    name_lower = col_name.lower()
    is_ratio_like = any(h in name_lower for h in _RATIO_NAME_HINTS)
    is_currency_like = not is_ratio_like and any(h in name_lower for h in _CURRENCY_NAME_HINTS)
    is_large = pd.notna(max_abs) and max_abs >= 1000

    if is_ratio_like:
        axis = alt.Axis(format="%")
    elif is_large:
        axis = alt.Axis(format="~s", labelExpr="replace(datum.label, 'G', 'B')")
    else:
        axis = alt.Undefined
    if is_currency_like:
        tooltip_format = "$,.0f"
    elif is_ratio_like:
        tooltip_format = ".1%"
    elif is_large:
        tooltip_format = ",.0f"
    else:
        tooltip_format = alt.Undefined
    return axis, tooltip_format


def _render_custom_visualizations_tab(
    data_sources: dict,
    key_prefix: str = "cv",
    header: bool = True,
    simple: bool = False,
    fixed_x: str | None = None,
    y_label: str = "Y axis",
):
    """Generic ad-hoc chart builder shared by both models. `data_sources` maps
    a display name to the DataFrame it plots from for the active model - the
    first entry is the data source pre-selected when a chart card is first
    added. `key_prefix` namespaces all widget/session-state keys so this can
    be embedded in more than one place (e.g. the Cohorts tab) on the same
    page run without colliding with another instance's state. `simple=True`
    drops the multi-card/Excel-export machinery (per-card header, "add to
    export"/"remove card"/"add another chart" buttons, the export queue) for
    a single-chart embedding like the Cohorts tab's.

    The Data source selector is dropped automatically when `data_sources` has
    only one entry (that entry is used directly) - the Cohorts tab embedding
    passes a single-source dict for exactly this reason, since letting the
    user switch away from the cohort-level table there doesn't make sense.
    `fixed_x`, when set, drops the X axis selector and always plots against
    that column instead (again, the Cohorts tab: every chart there is "by
    Cohort"). `y_label` renames the Y axis selector - e.g. "Metric" for the
    Cohorts tab, since its Y options are all per-cohort metrics, not just one
    of several plottable fields."""
    if header:
        st.subheader("Custom Visualizations")
        st.caption(
            "Build your own charts from the mapped and computed data. Add as many chart cards as you like."
        )

    cards_state_key = f"{key_prefix}_chart_cards"
    next_id_state_key = f"{key_prefix}_chart_next_id"
    if cards_state_key not in st.session_state:
        st.session_state[cards_state_key] = [0]
        st.session_state[next_id_state_key] = 1

    def _render_chart_card(card_id):
        def k(name):
            return f"{key_prefix}_{name}_{card_id}"

        show_source = len(data_sources) > 1
        if show_source:
            cv1, cv2 = st.columns([1, 1])
            with cv1:
                source_name = st.selectbox(
                    "Data source", options=list(data_sources.keys()), key=k("source")
                )
        else:
            source_name = next(iter(data_sources))
            cv2 = st.container()
        source_df = data_sources[source_name]
        if source_df is None or source_df.empty:
            st.info("This data source has no rows to plot.")
            return

        all_cols = list(source_df.columns)
        numeric_cols = [c for c in all_cols if pd.api.types.is_numeric_dtype(source_df[c])]
        datetime_cols = [c for c in all_cols if pd.api.types.is_datetime64_any_dtype(source_df[c])]
        categorical_cols = [c for c in all_cols if c not in numeric_cols and c not in datetime_cols]

        # Smart defaults for a fresh chart card: an origination-date-like column on X
        # and a principal-like column on Y, summed - so it opens showing something
        # meaningful (e.g. Principal by Cohort) instead of an arbitrary first column.
        # Only affects the initial selectbox index; once a user picks a value for this
        # card, Streamlit remembers it under that widget's key on reruns.
        def _pick(cols, keywords, fallback):
            for kw in keywords:
                for c in cols:
                    if kw in c.lower():
                        return c
            return fallback

        default_x = _pick(
            datetime_cols,
            ["cohort", "disbursement", "origination", "start"],
            datetime_cols[0] if datetime_cols else all_cols[0],
        )
        y_options = numeric_cols if numeric_cols else all_cols
        default_y = _pick(numeric_cols, ["principal"], y_options[0])
        chart_options = ["Line", "Bar", "Scatter", "Area"]
        default_kind = "Bar" if default_x in datetime_cols and default_y in numeric_cols else "Line"

        with cv2:
            chart_kind = st.selectbox(
                "Chart type",
                options=chart_options,
                index=chart_options.index(default_kind),
                key=k("kind"),
            )

        show_x = fixed_x is None
        if show_x:
            cv3, cv4, cv5, cv6 = st.columns(4)
            with cv3:
                x_col = st.selectbox(
                    "X axis", options=all_cols, index=all_cols.index(default_x), key=k("x")
                )
        else:
            x_col = fixed_x if fixed_x in all_cols else default_x
            cv4, cv5, cv6 = st.columns(3)
        with cv4:
            y_col = st.selectbox(
                y_label, options=y_options, index=y_options.index(default_y), key=k("y")
            )
        with cv5:
            color_col = st.selectbox(
                "Group / color by (optional)",
                options=["(none)"] + [c for c in categorical_cols if c != x_col],
                key=k("color"),
            )
        with cv6:
            # A secondary metric line (e.g. Loss Rate alongside a Principal
            # bar chart) reads its own axis on the right - more useful here
            # than a Sum/Mean/etc. picker, which was a no-op on data that's
            # already one row per X (like the Cohorts table: aggregating a
            # single value any way you slice it just returns that value).
            line_options = ["(none)"] + [c for c in numeric_cols if c not in (x_col, y_col)]
            default_line = "Loss Rate" if "Loss Rate" in line_options else "(none)"
            line_choice = st.selectbox(
                "Add secondary line (optional)",
                options=line_options,
                index=line_options.index(default_line),
                key=k("line"),
                help="Overlay a second metric (e.g. Loss Rate) as a line with its own axis, "
                "aggregated by the mean of X (and Group/color by, if set).",
            )
        line_col = None if line_choice == "(none)" else line_choice

        base_cols = [c for c in {x_col, y_col, color_col} if c in source_df.columns]
        plot_df = source_df[base_cols].dropna(subset=[x_col, y_col])
        group_cols = [x_col] + ([color_col] if color_col != "(none)" else [])

        line_df = None
        if line_col:
            line_cols = [c for c in {x_col, line_col} if c in source_df.columns]
            line_df = source_df[line_cols].dropna(subset=[x_col, line_col])
            if not line_df.empty:
                line_df = line_df.groupby(x_col, dropna=False)[line_col].mean().reset_index()

        # Auto-sum whenever the X (+ Group/color) selection doesn't already
        # uniquely identify each row - e.g. loan-level data has many rows per
        # Cohort, so "Total Principal by Cohort" needs a sum to be
        # meaningful. Data that's already one row per X (like the Cohorts
        # table) is left as-is, since summing a single value is a no-op.
        if not plot_df.empty and plot_df.duplicated(subset=group_cols).any():
            agg_series = plot_df.groupby(group_cols, dropna=False)[y_col].sum()
            # y_col can be the same column as x_col or color_col (e.g. "Total
            # Principal by Principal Value") - reset_index() would then try
            # to insert the aggregated value under a name that's already a
            # group-key column and raise. Give it its own column name in
            # that case (and point y_col at it for the rest of the chart).
            value_col = f"{y_col} (Sum)" if y_col in group_cols else y_col
            plot_df = agg_series.rename(value_col).reset_index()
            y_col = value_col

        if plot_df.empty:
            st.info("No rows with data for the selected columns.")
            return

        x_type = "T" if x_col in datetime_cols else ("O" if x_col in categorical_cols else "Q")
        mark_map = {
            "Line": alt.Chart(plot_df).mark_line(point=True),
            "Bar": alt.Chart(plot_df).mark_bar(),
            "Scatter": alt.Chart(plot_df).mark_circle(size=60),
            "Area": alt.Chart(plot_df).mark_area(opacity=0.6),
        }
        base = mark_map[chart_kind]
        chart_title = f"{y_col} by {x_col}" + (f" ({color_col})" if color_col != "(none)" else "")
        if line_col:
            chart_title += f" & {line_col}"

        x_axis = (
            # "Mon YYYY" per tick so a multi-year monthly axis doesn't repeat
            # "June, August, ..." with no way to tell which June is which.
            # A plain D3 format string (not a custom labelExpr) so Vega-Lite
            # can still pre-measure label width and auto-thin overlapping
            # ticks itself - true two-line (month/year-on-its-own-row) axis
            # labels aren't reliably supported by Vega's SVG axis renderer,
            # which collapses an embedded newline back to a single line.
            # tickCount="month" pins ticks to calendar-month boundaries -
            # without it, Vega's default continuous-time tick picker can
            # choose a sub-monthly interval (e.g. every ~15 days), which
            # under this format shows the same "Mon YYYY" label twice in a
            # row for two ticks landing in the same month.
            alt.Axis(format="%b %Y", tickCount="month") if x_type == "T" else alt.Undefined
        )
        y_max_abs = plot_df[y_col].abs().max() if not plot_df.empty else 0
        y_axis, y_tooltip_format = _smart_quant_format(y_col, y_max_abs)

        encode_kwargs = dict(
            x=alt.X(f"{x_col}:{x_type}", title=x_col, axis=x_axis),
            y=alt.Y(f"{y_col}:Q", title=y_col, axis=y_axis),
            tooltip=[x_col, alt.Tooltip(f"{y_col}:Q", title=y_col, format=y_tooltip_format)]
            + ([color_col] if color_col != "(none)" else []),
        )
        if color_col != "(none)":
            encode_kwargs["color"] = alt.Color(f"{color_col}:N", title=color_col)

        primary_chart = base.encode(**encode_kwargs)

        if line_df is not None and not line_df.empty:
            line_max_abs = line_df[line_col].abs().max()
            line_axis, line_tooltip_format = _smart_quant_format(line_col, line_max_abs)
            line_chart = (
                alt.Chart(line_df)
                .mark_line(point=True, color="#E8632A", strokeWidth=2.5)
                .encode(
                    x=alt.X(f"{x_col}:{x_type}", axis=x_axis),
                    y=alt.Y(f"{line_col}:Q", title=line_col, axis=line_axis),
                    tooltip=[
                        x_col,
                        alt.Tooltip(f"{line_col}:Q", title=line_col, format=line_tooltip_format),
                    ],
                )
            )
            custom_chart = (
                alt.layer(primary_chart, line_chart)
                .resolve_scale(y="independent")
                .properties(
                    title=chart_title,
                    height=400,
                )
            )
        else:
            custom_chart = primary_chart.properties(title=chart_title, height=400)

        st.altair_chart(custom_chart, width="stretch")

        if simple:
            return

        bc1, bc2 = st.columns([1, 1])
        with bc1:
            if st.button("Add this chart to the Excel export", key=k("add_export")):
                export_charts = st.session_state.setdefault("export_charts", [])
                export_charts.append(
                    {
                        "title": chart_title,
                        "kind": chart_kind,
                        "x_col": x_col,
                        "y_col": y_col,
                        "color_col": None if color_col == "(none)" else color_col,
                        "y_title": y_col,
                        "data": plot_df[
                            [c for c in {x_col, y_col, color_col} if c in plot_df.columns]
                        ].copy(),
                    }
                )
                st.success(f"Added '{chart_title}' to the Excel export queue.")
        with bc2:
            if len(st.session_state[cards_state_key]) > 1:
                if st.button("Remove this chart card", key=k("remove_card")):
                    st.session_state[cards_state_key].remove(card_id)
                    st.rerun()

    for i, card_id in enumerate(st.session_state[cards_state_key]):
        if not simple:
            st.markdown(f"#### Chart {i + 1}")
        _render_chart_card(card_id)
        if not simple:
            st.markdown("---")

    if simple:
        return

    if st.button("Add another chart", key=f"{key_prefix}_add_card"):
        st.session_state[cards_state_key].append(st.session_state[next_id_state_key])
        st.session_state[next_id_state_key] += 1
        st.rerun()

    export_charts = st.session_state.get("export_charts", [])
    if export_charts:
        st.markdown("**Charts queued for the Excel export:**")
        for i, ec in enumerate(export_charts):
            qc1, qc2 = st.columns([5, 1])
            qc1.write(f"{i + 1}. {ec['title']} ({ec['kind']})")
            if qc2.button("Remove", key=f"{key_prefix}_remove_{i}"):
                export_charts.pop(i)
                st.rerun()
        if st.button("Clear all queued charts", key=f"{key_prefix}_clear_export"):
            st.session_state["export_charts"] = []
            st.rerun()


def _render_ai_chat_tab(context: str) -> None:
    st.subheader("Ask AI")
    st.caption(
        "Ask about the metrics and calculations on the other tabs. The assistant only sees the "
        "aggregated summary statistics below (cohort tables and portfolio-level figures) - never "
        "individual loan rows - so it can't answer questions about a specific loan ID. It's also "
        "restricted to this portfolio's data - it won't answer general-knowledge questions."
    )
    st.warning(
        "AI-generated - it can make mistakes. Please verify anything important against the "
        "other tabs before relying on it."
    )
    with st.expander("What the assistant can see"):
        st.text(context)

    if "ai_chat_history" not in st.session_state:
        st.session_state["ai_chat_history"] = []

    for msg in st.session_state["ai_chat_history"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if st.button("Clear chat", key="ai_chat_clear"):
        st.session_state["ai_chat_history"] = []
        st.rerun()

    question = st.chat_input("Ask a question about this portfolio's data or calculations...")
    if question:
        st.session_state["ai_chat_history"].append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            try:
                with st.spinner("Thinking..."):
                    answer = _ask_data_chatbot(
                        question, context, st.session_state["ai_chat_history"][:-1]
                    )
            except Exception as e:
                answer = f"Unable to get a response: {e}"
            st.markdown(answer)
        st.session_state["ai_chat_history"].append({"role": "assistant", "content": answer})
        _save_profile_snapshot()


class _NamedBytes(io.BytesIO):
    """Bytes with a filename, so _read_tabular_file's .name checks and
    pd.read_csv / pd.ExcelFile work on remote downloads that aren't real
    Streamlit UploadedFile objects."""

    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


def _extract_google_id(url: str) -> str:
    """Pull the document ID out of a Google Sheets or Google Drive share link."""
    for pattern in (
        r"/spreadsheets/d/([a-zA-Z0-9-_]+)",
        r"drive\.google\.com/file/d/([a-zA-Z0-9-_]+)",
    ):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    raise ValueError(
        "That link doesn't look like a Google Sheets or Google Drive document - paste the "
        "full share link (https://docs.google.com/spreadsheets/d/...)."
    )


def _download_google_sheet(url: str) -> tuple[bytes, str]:
    """Download a Google Sheets / Google Drive document over HTTPS, returning
    (bytes, suggested filename). The document must be shared with 'Anyone with
    the link can view'; no Google API key is needed for public documents. A
    Sheets link is exported as a full Excel workbook (every tab, not just
    whichever one was open in the URL) so _read_tabular_file's sheet picker
    can offer all of them, same as a local upload; a generic Drive file link
    is fetched as-is and sniffed for CSV vs Excel."""
    url = url.strip()
    doc_id = _extract_google_id(url)
    if "spreadsheets" in url:
        export = f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=xlsx"
        resp = requests.get(export, timeout=30, verify=_ca_bundle_path())
        if resp.status_code != 200:
            raise RuntimeError(
                f"Google returned HTTP {resp.status_code} - the sheet must be shared with "
                "'Anyone with the link can view' (File > Share > General access)."
            )
        return resp.content, "google_sheet.xlsx"
    resp = requests.get(
        f"https://drive.google.com/uc?export=download&id={doc_id}",
        timeout=30,
        verify=_ca_bundle_path(),
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Google Drive returned HTTP {resp.status_code} - the file must be shared with "
            "'Anyone with the link can view'."
        )
    data = resp.content
    name = "google_file.xlsx" if data[:4] == b"PK\x03\x04" else "google_file.csv"
    return data, name


def _list_drive_files(access_token: str) -> list[dict]:
    query = (
        "(mimeType='application/vnd.google-apps.spreadsheet' or "
        "mimeType='text/csv' or "
        "mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' or "
        "mimeType='application/vnd.ms-excel') and trashed=false"
    )
    resp = requests.get(
        GOOGLE_DRIVE_FILES_URL,
        params={
            "q": query,
            "pageSize": 200,
            "fields": "files(id,name,mimeType,modifiedTime)",
            "orderBy": "modifiedTime desc",
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    return resp.json().get("files", [])


def _drive_mime_label(mime: str) -> str:
    if mime == "application/vnd.google-apps.spreadsheet":
        return "Google Sheet"
    if mime == "text/csv":
        return "CSV"
    if mime in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    ):
        return "Excel"
    return mime.rsplit(".", 1)[-1]


def _download_drive_file(access_token: str, drive_file: dict) -> tuple[bytes, str]:
    """Download a Drive file (or export a Google Sheet as a full Excel
    workbook - every tab, not just its first - so _read_tabular_file's sheet
    picker can offer all of them), returning (bytes, suggested filename)."""
    mid = drive_file["mimeType"]
    name = drive_file["name"]
    if mid == "application/vnd.google-apps.spreadsheet":
        url = (
            f"{GOOGLE_DRIVE_FILES_URL}/{drive_file['id']}/export"
            "?mimeType=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        if not name.lower().endswith(".xlsx"):
            name += ".xlsx"
    else:
        url = f"{GOOGLE_DRIVE_FILES_URL}/{drive_file['id']}?alt=media"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=60,
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    data = resp.content
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data, name


def _render_gdrive_picker() -> dict | None:
    """Google Drive OAuth connect + file picker for the "My Google Drive" data
    source. Returns a dict {file_id, file_name, data, name} for the chosen file,
    or None after a st.stop() when nothing is connected/chosen yet."""
    if not (_get_google_client_id() and _get_google_client_secret()):
        st.error(
            "Google Drive isn't configured - add GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET to Streamlit secrets or .env, and register the "
            f"redirect URI '{_google_redirect_uri()}' in your Google Cloud OAuth client."
        )
        st.stop()
        return None

    token = _gdrive_token()
    if not token:
        # In practice unreachable once Google sign-in is configured (the login
        # gate already obtains a Drive-scoped token) - kept as a fallback path,
        # fixed to use the same process-global state store as the login gate
        # (see _issue_oauth_state) since st.session_state can't survive the
        # redirect round-trip either.
        code = st.query_params.get("code")
        state = st.query_params.get("state")
        if code and state and _consume_oauth_state(state) is not None:
            try:
                with st.spinner("Connecting to Google Drive..."):
                    token = _exchange_google_code(code)
            except Exception as e:
                st.error(f"Couldn't complete Google sign-in: {e}")
                st.query_params.clear()
                st.stop()
                return None
            st.query_params.clear()
            st.session_state["gdrive_token"] = token
            st.rerun()
            return None
        state_param = _issue_oauth_state(None)
        st.markdown("**Pick a file from your Google Drive**")
        st.caption(
            "Connect your Google account to browse and load files directly from your "
            "Drive. Only read access is requested; your files stay in your account."
        )
        auth_url = _google_auth_url(state_param)
        st.link_button("Connect Google Drive", auth_url)
        st.stop()
        return None

    access_token = _gdrive_access_token(token)
    cached = st.session_state.get("gdrive_files_cache") or {}
    if cached.get("token") != access_token:
        try:
            with st.spinner("Listing your Drive..."):
                files = _list_drive_files(access_token)
        except Exception as e:
            st.error(f"Couldn't list your Google Drive: {e}")
            st.stop()
            return None
        st.session_state["gdrive_files_cache"] = {"token": access_token, "files": files}
        cached = st.session_state["gdrive_files_cache"]
    files = cached.get("files", [])

    if not files:
        st.info("No spreadsheets, CSV or Excel files found in your Google Drive.")
        st.stop()
        return None

    labels = {f"{f['name']}  ({_drive_mime_label(f['mimeType'])})": f for f in files}
    choice = st.selectbox("Choose a file from your Google Drive", options=list(labels.keys()))
    sel = labels[choice]

    if st.session_state.get("gdrive_dl_id") != sel["id"]:
        try:
            with st.spinner("Downloading from your Drive..."):
                data, dl_name = _download_drive_file(access_token, sel)
        except Exception as e:
            st.error(f"Couldn't download '{sel['name']}': {e}")
            st.stop()
            return None
        st.session_state["gdrive_dl_id"] = sel["id"]
        st.session_state["gdrive_dl_bytes"] = data
        st.session_state["gdrive_dl_name"] = dl_name
    return {
        "file_id": "gdrive:" + sel["id"],
        "file_name": sel["name"],
        "data": st.session_state["gdrive_dl_bytes"],
        "name": st.session_state["gdrive_dl_name"],
    }


@st.cache_data(
    show_spinner=False,
    max_entries=16,
)
def _parse_uploaded(file_bytes: bytes, name: str, sheet_name: str | None) -> pd.DataFrame:
    """Pure file parse (no st.* calls), keyed on the file bytes + chosen sheet
    so re-running the script on every Streamlit interaction doesn't re-read and
    re-parse the whole upload. Returns a freshly-parsed DataFrame; its only
    downstream consumer (_format_normalize) copies before mutating, so the
    cached value is never corrupted in place."""
    if name.endswith(".csv"):
        return pd.read_csv(io.BytesIO(file_bytes))
    # engine="calamine" (a Rust reader) vs pandas' openpyxl default: ~16x
    # faster end-to-end on a real 23.8MB/39k-row upload (49.7s -> 3.0s, most
    # of it openpyxl's ~38.7s just to open the workbook), output verified
    # byte-identical (df.equals() True) on that same file - openpyxl scales
    # badly with workbook size since it eagerly builds an in-memory object
    # model of the whole file.
    xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="calamine")
    df = xls.parse(sheet_name)
    unnamed_frac = sum(str(c).startswith("Unnamed:") for c in df.columns) / max(len(df.columns), 1)
    if unnamed_frac >= 0.5:
        preview = xls.parse(sheet_name, header=None, nrows=10)
        for i in range(1, len(preview)):
            row = preview.iloc[i]
            if (
                row.notna().mean() > 0.7
                and row.dropna().map(lambda v: isinstance(v, str)).mean() > 0.7
            ):
                df = xls.parse(sheet_name, header=i)
                break
    return df


def _read_tabular_file(uploaded, key_suffix: str = "") -> pd.DataFrame:
    """Read an uploaded CSV/XLSX, robust to a workbook with more than one
    sheet (defaults to reading the first, but lets the user pick) and to a
    header row that isn't row 0 - e.g. a title/banner row above the real
    column headers, which would otherwise silently produce "Unnamed: N"
    columns instead of an error.

    `key_suffix` disambiguates the sheet-picker widget when this is called
    more than once in the same run (e.g. combining several uploaded files),
    since Streamlit widget keys must be unique per script run."""
    file_bytes = _read_uploaded_bytes(uploaded)
    name = uploaded.name
    if name.endswith(".csv"):
        with st.spinner("Reading file..."):
            return _parse_uploaded(file_bytes, name, None)

    with st.spinner("Reading file..."):
        xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="calamine")
        # "_App Session" is a hidden bookkeeping sheet this app writes into
        # its own exports (see _detect_restored_session) - never a candidate
        # for loan-level data, so hide it from the picker entirely.
        pickable_sheets = [s for s in xls.sheet_names if s != "_App Session"]
        # Prefer "Data Input" as the default when present (e.g. re-uploading
        # this app's own export) instead of blindly defaulting to whichever
        # sheet happens to be first in the workbook.
        default_sheet = "Data Input" if "Data Input" in pickable_sheets else pickable_sheets[0]
        sheet_name = default_sheet
    if len(pickable_sheets) > 1:
        label = (
            f"'{name}' has multiple sheets - which one has your loan-level data?"
            if key_suffix
            else "This file has multiple sheets - which one has your loan-level data?"
        )
        sheet_name = st.selectbox(
            label,
            options=pickable_sheets,
            index=pickable_sheets.index(default_sheet),
            key=f"upload_sheet_name{key_suffix}",
        )

    with st.spinner("Reading file..."):
        return _parse_uploaded(file_bytes, name, sheet_name)


def _combine_uploaded_files(uploaded_files: list) -> tuple[bytes, str, str]:
    """Stack multiple uploaded CSV/Excel files into a single dataset - union
    of columns, rows concatenated - for the multi-upload "combine" mode.
    Returns (csv_bytes, parse_safe_name, display_label): the first two let the
    result flow through the rest of the app exactly like a single uploaded
    file (see _NamedBytes), while the label is for human-facing display (e.g.
    auto-naming a saved session) since parse_safe_name is a generic
    placeholder, not a real filename."""
    frames = [_read_tabular_file(f, key_suffix=f"_{i}") for i, f in enumerate(uploaded_files)]
    source_col = "Source file"
    if any(source_col in df.columns for df in frames):
        source_col = "_source_file"
    for df, f in zip(frames, uploaded_files):
        df[source_col] = f.name
    combined = pd.concat(frames, ignore_index=True, sort=False)
    st.caption(
        "Combined "
        + ", ".join(f"{f.name} ({len(df):,} rows)" for f, df in zip(uploaded_files, frames))
    )
    label = "Combined: " + ", ".join(Path(f.name).stem for f in uploaded_files)
    # A real ".csv" name (not a descriptive label) so this round-trips
    # correctly through _read_tabular_file and the resume/share flows, which
    # branch on the file extension.
    return combined.to_csv(index=False).encode("utf-8"), "combined_upload.csv", label


def _detect_restored_session(file_bytes: bytes, name: str) -> dict | None:
    """If `file_bytes` is a workbook previously produced by 'Download full
    workbook as Excel' (i.e. it carries the hidden '_App Session' sheet
    written by _build_lending_export_workbook), return its embedded mapping/
    currency/GI-overrides config so the upload flow can pre-fill the mapping
    form to reproduce that run. Returns None for any other file, or if the
    sheet is present but unreadable - this must never block a normal
    upload."""
    if not name or not name.lower().endswith(".xlsx"):
        return None
    try:
        wb = load_workbook(io.BytesIO(file_bytes), read_only=True)
        if "_App Session" not in wb.sheetnames:
            return None
        raw_cfg = wb["_App Session"]["A1"].value
        return json.loads(raw_cfg) if raw_cfg else None
    except Exception:
        return None


def _save_profile_snapshot() -> None:
    """Best-effort auto-save of the current upload + mapping/settings + Ask AI
    chat history to this deal's saved-session record (local disk or the
    user's own Drive, depending on auth mode - see _store_save_profile), so
    it can be resumed later by picking it from the "Resume my saved session"
    dropdown. Silently gives up if there's nothing to save yet, or no active
    session id (set alongside the upload - see the "uploaded_file_id" change
    block below), this is a convenience, never something that should block
    the actual analysis."""
    upload_bytes = st.session_state.get("_current_upload_bytes")
    session_id = st.session_state.get("_active_session_id")
    if not upload_bytes or not session_id:
        return
    _store_save_profile(
        session_id,
        upload_bytes=upload_bytes,
        upload_name=st.session_state.get("_current_upload_name") or "upload",
        label=st.session_state.get("_active_deal_label") or "Untitled deal",
        mapping=st.session_state.get("analysis_mapping"),
        currency=st.session_state.get("analysis_currency"),
        gi_overrides=st.session_state.get("analysis_gi_overrides"),
        exclude_rule=st.session_state.get("analysis_exclude_rule"),
        active_status_values=st.session_state.get("analysis_active_status_values"),
        analysis_context=st.session_state.get("analysis_context", ""),
        chat_history=st.session_state.get("ai_chat_history", []),
    )
    _cached_list_profiles.clear()


uploaded = None
file_id = None
file_name = None
gdrive_pick = None
profile = None
deal_label = None
resume_session_id = None

_share_token = st.query_params.get("share")
if _share_token:
    if st.session_state.get("_share_cache_token") != _share_token:
        st.session_state["_share_cache_token"] = _share_token
        st.session_state["_share_cache_data"] = _store_load_share(_share_token)
    profile = st.session_state["_share_cache_data"]
    if profile is None:
        st.error("This share link is invalid or has expired.")
        st.stop()
    data_source = "Shared link"
    file_id = "share:" + _share_token
    file_name = profile["upload_name"]
    st.info(
        f"Viewing an analysis shared by **{profile.get('shared_by', 'a colleague')}** - "
        "mapping and inputs below have been pre-filled to match it. Anything you change or "
        "ask here is saved to your own account only, never back into the shared link."
    )
else:
    _saved_profiles = _store_list_profiles()
    _data_source_options = ["Local file", "Google Sheets / Drive link", "My Google Drive"]
    # Offered for every model, matching the React app: the fetch lands as a
    # normal ingest, so each model's own column mapping applies.
    if _redshift_available():
        _data_source_options.append("Redshift (Warehouse)")
    if _saved_profiles:
        _data_source_options.insert(0, "Resume my saved session")
    data_source = st.radio(
        "Data source",
        _data_source_options,
        key="data_source",
        horizontal=True,
    )

    if data_source == "Resume my saved session":
        _profile_choices = {
            f"{p['label']} (saved {_dt.fromtimestamp(p['saved_at']):%Y-%m-%d %H:%M})": p
            for p in _saved_profiles
        }
        _profile_pick = st.selectbox(
            "Which deal do you want to continue?",
            options=list(_profile_choices.keys()),
            key="resume_session_choice",
        )
        _picked_profile_meta = _profile_choices[_profile_pick]
        resume_session_id = _picked_profile_meta["session_id"]
        profile = _store_load_profile(resume_session_id)
        if profile is None:
            st.error("Couldn't load that saved session - pick another data source to begin.")
            st.stop()
        file_id = "profile:" + current_user + ":" + resume_session_id
        file_name = profile["upload_name"]
        st.caption(
            f"Resuming '{_picked_profile_meta['label']}', saved "
            f"{_dt.fromtimestamp(profile['saved_at']):%Y-%m-%d %H:%M}."
        )
    elif data_source == "Local file":
        uploaded_files = st.file_uploader(
            "Choose one or more CSV or Excel files",
            type=["csv", "xlsx"],
            accept_multiple_files=True,
            key="uploader",
        )
        if not uploaded_files:
            st.info("Upload a contract-level file to begin.")
            st.stop()
        if len(uploaded_files) == 1:
            uploaded = uploaded_files[0]
            file_id = uploaded.file_id
            file_name = uploaded.name
            deal_label = Path(uploaded.name).stem
        else:
            multi_mode = st.radio(
                f"{len(uploaded_files)} files uploaded - how should they be used?",
                ["Analyze one file", "Combine all files (stack rows into one dataset)"],
                key="multi_file_mode",
                horizontal=True,
            )
            if multi_mode == "Analyze one file":
                names = [f.name for f in uploaded_files]
                picked_name = st.selectbox("Which file?", options=names, key="active_upload_name")
                uploaded = next(f for f in uploaded_files if f.name == picked_name)
                file_id = uploaded.file_id
                file_name = uploaded.name
                deal_label = Path(uploaded.name).stem
            else:
                combined_bytes, file_name, deal_label = _combine_uploaded_files(uploaded_files)
                uploaded = _NamedBytes(combined_bytes, file_name)
                file_id = "combined:" + "|".join(sorted(f.file_id for f in uploaded_files))
    elif data_source == "Google Sheets / Drive link":
        gs_url = st.text_input(
            "Paste a Google Sheets or Google Drive link",
            placeholder="https://docs.google.com/spreadsheets/d/<ID>/edit#gid=0",
            key="gs_url_input",
        )
        if not gs_url.strip():
            st.info("Paste a Google Sheets / Google Drive link to begin.")
            st.stop()
        try:
            doc_id = _extract_google_id(gs_url)
        except ValueError as e:
            st.error(str(e))
            st.stop()
        file_id = "gs:" + doc_id
        file_name = f"Google Sheet ({doc_id})"
        deal_label = file_name
    elif data_source == "My Google Drive":
        gdrive_pick = _render_gdrive_picker()
        if gdrive_pick is None:
            st.stop()
        file_id = gdrive_pick["file_id"]
        file_name = gdrive_pick["file_name"]
        deal_label = Path(file_name).stem
    else:
        try:
            with st.spinner("Listing borrowers..."):
                redshift_borrowers = _redshift_list_borrowers()
        except Exception as e:
            st.error(f"Couldn't list borrowers from the warehouse: {e}")
            st.stop()
        if not redshift_borrowers:
            st.info("No borrowers available from the warehouse yet.")
            st.stop()
        redshift_borrower = st.selectbox(
            "Which borrower's loan tape?", options=redshift_borrowers, key="redshift_borrower"
        )
        redshift_row_limit = st.number_input(
            "Max rows to fetch",
            min_value=100,
            value=100_000,
            step=1000,
            key="redshift_row_limit",
            help="Loan tapes can be very large - raise this if you need the full portfolio. "
            "The warehouse API itself has no row cap.",
        )
        file_id = f"redshift:{redshift_borrower}:{int(redshift_row_limit)}"
        file_name = f"{redshift_borrower}_loan_tape.csv"
        deal_label = redshift_borrower

if st.session_state.get("uploaded_file_id") != file_id:
    st.session_state["uploaded_file_id"] = file_id
    st.session_state["analysis_ran"] = False
    st.session_state.pop("analysis_mapping", None)
    st.session_state.pop("analysis_gi_overrides", None)
    st.session_state.pop("analysis_context", None)
    st.session_state.pop("context_files", None)
    st.session_state.pop("context_documents", None)
    st.session_state.pop("context_doc_ids", None)
    # A new deal's Ask AI chat shouldn't carry over the previous deal's chat
    # history. "Resume my saved session" / "Shared link" are the one
    # exception - the restored-session block just below repopulates this
    # from the saved/shared chat history for those two data sources.
    st.session_state.pop("ai_chat_history", None)
    for target, _ in INPUT_COLUMNS:
        st.session_state.pop(f"map_{target}", None)

    # Which saved-session record subsequent auto-saves (Run analysis, Ask AI
    # chat) update. Resuming continues writing to the deal you picked;
    # anything else (a fresh upload, or opening someone else's shared link)
    # starts a brand-new record rather than overwriting an unrelated one.
    if data_source == "Resume my saved session":
        st.session_state["_active_session_id"] = resume_session_id
        st.session_state["_active_deal_label"] = _picked_profile_meta["label"]
    else:
        st.session_state["_active_session_id"] = secrets.token_urlsafe(8)
        if data_source == "Shared link":
            st.session_state["_active_deal_label"] = (
                f"Shared: {profile.get('upload_name', file_name)}"
            )
        else:
            st.session_state["_active_deal_label"] = deal_label or Path(file_name).stem
        # Resuming already has a record (that's what's being loaded); anything
        # else - a fresh upload, or opening someone else's shared link - is a
        # brand-new deal, so it needs its first save right away rather than
        # only becoming resumable once "Run analysis" or Ask AI is used.
        st.session_state["_pending_initial_save"] = True

try:
    if data_source in ("Resume my saved session", "Shared link"):
        raw = _read_tabular_file(_NamedBytes(profile["upload_bytes"], profile["upload_name"]))
    elif data_source == "Local file":
        raw = _read_tabular_file(uploaded)
    elif data_source == "Google Sheets / Drive link":
        if st.session_state.get("gs_download_id") != file_id:
            with st.spinner("Loading Google Sheet..."):
                gs_bytes, gs_name = _download_google_sheet(gs_url)
            st.session_state["gs_download_id"] = file_id
            st.session_state["gs_bytes"] = gs_bytes
            st.session_state["gs_name"] = gs_name
        raw = _read_tabular_file(
            _NamedBytes(st.session_state["gs_bytes"], st.session_state["gs_name"])
        )
    elif data_source == "My Google Drive":
        raw = _read_tabular_file(_NamedBytes(gdrive_pick["data"], gdrive_pick["name"]))
    else:
        with st.spinner(f"Fetching {redshift_borrower}'s loan tape from the warehouse..."):
            raw = _redshift_fetch_loan_tape(redshift_borrower, int(redshift_row_limit))
except Exception as e:
    if data_source == "Redshift (Warehouse)":
        st.error(f"Couldn't fetch that loan tape from the warehouse: {e}")
    else:
        st.error(
            f"Couldn't read '{file_name}': {e}. Check that the file is a valid, non-empty "
            "CSV or Excel."
        )
    st.stop()
if raw.empty or not len(raw.columns):
    st.error(f"'{file_name}' has no data to read.")
    st.stop()

if data_source in ("Resume my saved session", "Shared link"):
    _source_bytes = profile["upload_bytes"]
elif data_source == "Local file":
    _source_bytes = _read_uploaded_bytes(uploaded)
elif data_source == "Google Sheets / Drive link":
    _source_bytes = st.session_state.get("gs_bytes")
elif data_source == "My Google Drive":
    _source_bytes = gdrive_pick["data"] if gdrive_pick else None
else:
    # No original file for a warehouse fetch - serialize the fetched rows so
    # save/resume/share still work exactly like any other data source.
    _source_bytes = raw.to_csv(index=False).encode("utf-8")
st.session_state["_current_upload_bytes"] = _source_bytes
st.session_state["_current_upload_name"] = file_name

if st.session_state.pop("_pending_initial_save", False):
    _save_profile_snapshot()

if st.session_state.get("restored_session_checked_id") != file_id:
    st.session_state["restored_session_checked_id"] = file_id
    if data_source in ("Resume my saved session", "Shared link"):
        restored = {
            "mapping": profile.get("mapping"),
            "currency": profile.get("currency"),
            "gi_overrides": profile.get("gi_overrides") or {},
            "exclude_rule": profile.get("exclude_rule") or {},
            "active_status_values": profile.get("active_status_values") or [],
        }
        st.session_state["analysis_context"] = profile.get("analysis_context", "")
        st.session_state["ai_chat_history"] = profile.get("chat_history", [])
    else:
        restored = _detect_restored_session(_source_bytes, file_name) if _source_bytes else None
    st.session_state["restored_session"] = restored

_active_model_fields = {c for c, _ in INPUT_COLUMNS} | (
    _LENDING_EXTRA_FIELDS if is_lending else set()
)
_active_aliases = app_config.FIELD_ALIASES.get("lending" if is_lending else "rental", {})
# frozenset args keep _format_normalize cacheable (set/dict is unhashable);
# .copy() gives downstream (currency conversion, fillna, dedupe) a private
# object so those in-place mutations never poison the cached value.
raw = _format_normalize(
    raw,
    frozenset(DATE_FIELDS),
    frozenset(NUMERIC_FIELDS),
    frozenset(_active_model_fields),
    frozenset((field, tuple(aliases)) for field, aliases in _active_aliases.items()),
).copy()

st.success(f"Loaded {len(raw):,} rows - {len(raw.columns)} columns.")
st.dataframe(raw.head(10), width="stretch", height=300)
st.caption("Columns in your file: " + ", ".join(map(str, raw.columns)))

st.subheader("Additional context for the AI")
st.caption(
    "Optional documentation about this dataset. The AI uses it to answer questions about "
    "the analysis and to auto-fill the column mapping - for example the loan product and "
    "currency, what each field means, or how interest and fees are charged. You can type "
    "notes and/or upload supporting documents (PDF, Excel, Word, text). Editing either "
    "re-runs the AI column-mapping suggestion."
)
st.text_area(
    "Context / notes",
    placeholder=(
        "e.g. 'Portfolio of 12-month invoice-financing loans in Indonesia (IDR). Expected "
        "Interest and Expected Fee are blank because no interest or fees are charged; Total "
        "Paid is the invoiced amount actually collected. Refinanced loans are flagged in the "
        "Memo column. Principal is disbursed on the Disbursement Date and fully due by the "
        "Expected Completion Date.'"
    ),
    key="analysis_context",
    height=120,
    on_change=_invalidate_ai_mapping_guesses,
)

context_files = st.file_uploader(
    "Upload supporting documents",
    type=["pdf", "xlsx", "xlsm", "docx", "txt", "csv", "md"],
    accept_multiple_files=True,
    key="context_files",
)
current_doc_ids = tuple(sorted(f.file_id for f in context_files)) if context_files else ()
if st.session_state.get("context_doc_ids") != current_doc_ids:
    docs = []
    if context_files:
        with st.spinner("Extracting text from uploaded documents..."):
            docs = [_extract_document_text(f) for f in context_files]
    st.session_state["context_documents"] = docs
    st.session_state["context_doc_ids"] = current_doc_ids
    _invalidate_ai_mapping_guesses()

for doc in st.session_state.get("context_documents", []):
    if doc.get("error"):
        st.warning(f"**{doc['name']}** could not be used - {doc['error']}")
    else:
        st.caption(f"{doc['name']} - {len(doc['text']):,} characters extracted")
if st.session_state.get("context_documents"):
    with st.expander("Preview extracted text"):
        for doc in st.session_state["context_documents"]:
            st.markdown(f"**{doc['name']}**")
            st.text(_truncate_text(doc.get("text", ""), 3000))

cached_mapping = _load_cache(MAPPING_CACHE_PATH, _cache_key(raw.columns)) or {}
if cached_mapping:
    st.caption(
        "A saved mapping was found for a file with these same column headers - pre-filled below."
    )

# AI auto-fill: guess the best column mapping for this file's headers (per model).
# A field whose exact template name is already present in raw.columns (post-alias
# normalization, see _normalize_columns above) is a 100% programmatic match - skip asking the AI
# about it entirely, and only send the AI the fields/columns that are still unresolved. Cuts
# unnecessary API calls (and the tokens/cost that come with them) whenever a file's headers
# already line up with the template, per Niraj's suggestion in the 2026-09-03 catch-up.
guess_key = f"ai_mapping_guess_{_cache_key(raw.columns)}"
if guess_key not in st.session_state:
    programmatic_matches = {target: target for target, _ in INPUT_COLUMNS if target in raw.columns}
    remaining_fields = [(t, r) for t, r in INPUT_COLUMNS if t not in programmatic_matches]
    remaining_columns = [c for c in raw.columns if c not in programmatic_matches.values()]
    ai_part = {}
    ai_exclude = None
    ai_active_status = None
    if remaining_fields and _get_deepinfra_api_key():
        try:
            with st.spinner("Asking the AI to auto-fill the remaining column mappings..."):
                ai_part, ai_exclude, ai_active_status = _suggest_mapping(
                    remaining_columns,
                    remaining_fields,
                    _ai_context_text(max_chars=20000, per_doc=6000),
                    known_loan_status_column=programmatic_matches.get("Loan Status"),
                )
        except Exception as e:
            st.warning(f"AI auto-fill wasn't available ({e}). You can still map columns manually.")
    st.session_state[guess_key] = {
        "mapping": {**programmatic_matches, **ai_part},
        "ai_fields": set(ai_part.keys()),
        "exclude": ai_exclude,
        "active_status": ai_active_status,
    }
_guess_state = st.session_state.get(guess_key) or {
    "mapping": {},
    "ai_fields": set(),
    "exclude": None,
    "active_status": None,
}
ai_guess = _guess_state["mapping"]
ai_guessed_fields = _guess_state["ai_fields"]
ai_exclude_suggestion = _guess_state.get("exclude") or {}
ai_active_status_suggestion = _guess_state.get("active_status") or []
if ai_guessed_fields:
    st.warning(
        "The mapping(s) below for **"
        + ", ".join(sorted(ai_guessed_fields))
        + "** were suggested by AI (no exact column-name match was found) - please verify "
        "them before running the analysis."
    )
restored_session = st.session_state.get("restored_session")
restored_gi = (restored_session or {}).get("gi_overrides") or {}
if restored_session:
    if data_source == "Resume my saved session":
        st.info(
            "Resumed from your saved session - mapping and inputs below have been "
            "pre-filled to match it. Review and click 'Run analysis' to continue."
        )
    elif data_source != "Shared link":
        # "Shared link" already showed its own "Viewing an analysis shared by..." info
        # banner above, right where the data source was resolved - avoid a duplicate.
        st.info(
            "This file was exported from a previous session - mapping and inputs below have "
            "been pre-filled to match it. Review and click 'Run analysis' to reproduce it."
        )

# Deliberately not inside an st.form: widgets in a form only report their new
# value (and trigger a rerun) when the form itself is submitted, so a
# value-picker that only appears once a column is chosen would never show up
# in time for the user to use it - see the same reasoning inline below, next
# to the Loan Status field. Living out here as plain reactive widgets lets
# everything rerun immediately, all the way down to the "Run analysis"
# button, which is now a plain st.button rather than a form submit button.
st.subheader("Row exclusions")
st.caption(
    "Optionally drop rows that shouldn't count toward this analysis at all - e.g. "
    "unbooked/draft applications, cancelled records, or test data marked by a "
    "status/flag column in your file. Excluded rows are dropped before anything "
    "else runs, so they never appear in any metric, chart, or export."
)
_restored_exclude = (restored_session or {}).get("exclude_rule") or {}
_exclude_col_options = ["(none)"] + list(raw.columns)
_default_exclude_col = _restored_exclude.get("column") or ai_exclude_suggestion.get("column")
_exclude_col_index = (
    _exclude_col_options.index(_default_exclude_col)
    if _default_exclude_col in _exclude_col_options
    else 0
)
exclude_column = st.selectbox(
    "Exclude rows where this column...",
    options=_exclude_col_options,
    index=_exclude_col_index,
    key="exclude_column",
    help="If the AI mapping guess found explicit evidence of a row-exclusion flag "
    "in your context notes/documents (e.g. a data dictionary defining a 'ghost' or "
    "'unbooked' flag), it's pre-selected here - review it before running.",
)
exclude_values = []
if exclude_column != "(none)":
    _unique_vals = sorted(raw[exclude_column].dropna().astype(str).unique().tolist())
    if len(_unique_vals) > 500:
        st.caption(
            f"'{exclude_column}' has {len(_unique_vals):,} distinct values - showing the "
            "first 500. Pick a lower-cardinality column (a status/flag column, not an ID) "
            "if the value you need isn't listed."
        )
        _unique_vals = _unique_vals[:500]
    _restored_values = [v for v in (_restored_exclude.get("values") or []) if v in _unique_vals]
    _ai_values = [v for v in (ai_exclude_suggestion.get("values") or []) if v in _unique_vals]
    exclude_values = st.multiselect(
        "...equals any of these values (matching rows are dropped)",
        options=_unique_vals,
        default=_restored_values or _ai_values,
        key="exclude_values",
    )
    if exclude_values:
        _n_match = raw[exclude_column].astype(str).isin(exclude_values).sum()
        st.caption(f"{_n_match:,} of {len(raw):,} row(s) will be excluded.")

with st.container(border=True):
    st.subheader("Map your columns to the Data Input template")
    st.caption(
        "For each template field below, select the matching column in your file. Required fields must be mapped to run the analysis. "
        "If a field you need doesn't exist in your file, create it there first (e.g. a subtraction to derive Expected Interest) before mapping it here."
    )
    used = set()
    mapping = {}
    fillna_zero = {}
    active_status_values = []
    for target, required in INPUT_COLUMNS:
        options = ["(not provided)"] + [c for c in raw.columns if c not in used]
        # Precedence: restored session (re-uploaded export) > saved mapping > AI guess > "(not provided)".
        default_choice = (
            ((restored_session or {}).get("mapping") or {}).get(target)
            or cached_mapping.get(target)
            or ai_guess.get(target)
        )
        default_index = options.index(default_choice) if default_choice in options else 0
        chosen = st.selectbox(
            f"Map to **{target}** ({'required' if required else 'optional'})",
            options=options,
            index=default_index,
            key=f"map_{target}",
        )
        mapping[target] = None if chosen == "(not provided)" else chosen
        if chosen != "(not provided)":
            used.add(chosen)
        # Required numeric fields normally hard-block the run if the mapped
        # column has gaps (or is entirely empty, e.g. a lender who never
        # recorded payment amounts) - offer to treat missing values as 0
        # instead, mirroring how Excel's own SUM()/SUMIFS() formulas already
        # treat a blank cell as 0, rather than fabricating a number.
        if required and target in NUMERIC_FIELDS and chosen != "(not provided)":
            n_missing = raw[chosen].isna().sum()
            fillna_zero[target] = st.checkbox(
                f"Treat missing values in **{target}** as 0 (instead of blocking if this column has gaps)",
                key=f"fillna0_{target}",
                value=False,
            )
            if fillna_zero[target] and n_missing:
                st.caption(f"{n_missing:,} row(s) with a blank '{chosen}' will be treated as 0.")
        # Loan Status vocabularies aren't standardized across deals (e.g.
        # "active"/"open"/"current" all mean the same thing on different
        # tapes), so instead of assuming a value, let the user pick which of
        # this column's own values mean the loan is still active/outstanding.
        # This dropdown can react immediately to the column choice above only
        # because the whole section is a plain st.container(), not an
        # st.form() - a form only reports widget changes (and reruns) on
        # submit, so a dependent widget like this would never appear in time.
        if target == "Loan Status" and chosen != "(not provided)":
            _status_unique_vals = sorted(raw[chosen].dropna().astype(str).unique().tolist())
            _restored_active_values = [
                v
                for v in (restored_session or {}).get("active_status_values") or []
                if v in _status_unique_vals
            ]
            _ai_active_values = [v for v in ai_active_status_suggestion if v in _status_unique_vals]
            active_status_values = st.multiselect(
                "Which of these values mean the loan is still active/outstanding?",
                options=_status_unique_vals,
                default=_restored_active_values or _ai_active_values,
                key="active_status_values",
                help="Leave empty to skip this filter - Outstanding GBV then relies only on "
                "the maturity check, the same as when Loan Status isn't mapped at all.",
            )

    for target, checked in fillna_zero.items():
        if checked and mapping.get(target):
            raw[mapping[target]] = raw[mapping[target]].fillna(0)

    st.subheader("Currency")
    _currency_options = ["USD"] + sorted(c for c in CURRENCY_NAMES if c != "USD")
    _restored_currency = (restored_session or {}).get("currency")
    _currency_index = (
        _currency_options.index(_restored_currency)
        if _restored_currency in _currency_options
        else 0
    )
    portfolio_currency = st.selectbox(
        "Portfolio currency",
        options=_currency_options,
        format_func=lambda c: f"{c} - {CURRENCY_NAMES[c]}",
        index=_currency_index,
        help="The currency the mapped monetary columns (Principal Value, Expected Interest, "
        "Gross Loan Value, Expected Fee, Total Paid, Total Due) are denominated in. If not USD, "
        "every one of those columns is converted to USD using a live exchange rate before analysis runs.",
    )

    # Date of extraction defaults to the max of whichever raw column the user
    # just mapped to the primary date field (Disbursement Date / start_date) -
    # not a guess at the raw header's name, since that column could be called
    # anything before mapping renames it.
    default_extraction = pd.Timestamp.now().normalize()
    primary_col = mapping.get(active_cfg["primary_date_field"])
    if primary_col:
        parsed_primary = _detect_date(raw[primary_col])
        if parsed_primary.notna().any():
            default_extraction = parsed_primary.max()
    elif restored_gi.get("extraction_date"):
        default_extraction = pd.Timestamp(restored_gi["extraction_date"])

    st.subheader("General Inputs")
    gc1, gc2, gc3 = st.columns(3)
    with gc1:
        extraction_date = st.date_input(
            "Date of extraction",
            value=default_extraction.date(),
            help="The max date in the loan/contract tape. Used to identify which loans/contracts have reached their maturity.",
        )
    with gc2:
        days_after_term = st.number_input(
            "Days after term",
            value=restored_gi.get(
                "days_after_term", app_config.GENERAL_INPUTS_DEFAULTS["days_after_term"]
            ),
            min_value=0,
            help="Days after term used to compute loss rate (default 90 = Term + 3 months). "
            "Modify only if the company has significant repayments after 3 months from term.",
        )
    with gc3:
        min_loans_per_cohort = st.number_input(
            "Minimum loans per cohort",
            value=restored_gi.get(
                "min_loans_per_cohort", app_config.GENERAL_INPUTS_DEFAULTS["min_loans_per_cohort"]
            ),
            min_value=0,
            help="Affects the cohort stressed loss rate: requires a minimum number of observations to "
            "include a cohort in the cohort loss rate distribution (default 10).",
        )

    if not is_lending:
        # Rental & Subscription-specific General Inputs (the asset-lease model
        # has no "Days after term" loss-rate concept; it uses the useful life
        # and churn assumptions instead).
        rc1, rc2, rc3 = st.columns(3)
        with rc1:
            months_since_default = st.number_input(
                "Months since default",
                value=restored_gi.get("months_since_default", 3),
                min_value=0,
                help="Includes only contracts that defaulted over this many months ago in the "
                "recovery-rate and churn diagnostics (default 3).",
            )
        with rc2:
            useful_life_years = st.number_input(
                "Useful life of asset (years)",
                value=restored_gi.get("useful_life_years", 3.0),
                min_value=0.1,
                help="Assumed useful life of the asset, used for the straight-line depreciation of "
                "the Lendable asset value and for the repayment-curve cohort buckets. A per-deal "
                "assumption (real files range from 3 to 5+ years) - a mismatch is flagged in Data Checks.",
            )
        with rc3:
            churn_stress_multiplier = st.number_input(
                "Churn stress multiplier",
                value=restored_gi.get("churn_stress_multiplier", 1.7),
                min_value=1.0,
                help="Multiplies the 95th-percentile monthly churn rate to get the stressed churn "
                "used in the MRR multiple (default 1.7x per the template; some deals use a higher "
                "stress, e.g. 2.0x).",
            )
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            funding_cost_pct = st.number_input(
                "Funding cost (% p.a.)",
                value=float(restored_gi.get("funding_cost_pct", 0.13)),
                min_value=0.0,
                max_value=1.0,
                step=0.01,
                format="%.4f",
                help="Lendable's cost of funding, per annum, as a share of the asset cost - netted "
                "out of the UE margin over the useful life (Guide: UE = cash margin after the cost "
                "of funding and direct costs).",
            )
        with fc2:
            direct_cost_pct = st.number_input(
                "Direct cost (% p.a.)",
                value=float(restored_gi.get("direct_cost_pct", 0.02)),
                min_value=0.0,
                max_value=1.0,
                step=0.01,
                format="%.4f",
                help="Direct operating/service cost, per annum, as a share of the asset cost - "
                "netted out of the UE margin over the useful life.",
            )
        with fc3:
            debt_service_180d = st.number_input(
                "Debt service due over next 180 days",
                value=float(restored_gi.get("debt_service_180d") or 0.0),
                min_value=0.0,
                step=1000.0,
                format="%.0f",
                help="Total facility debt service payments due to Lendable over the following 180 "
                "days. This is a facility-level input (not in the borrower tape) - required to "
                "compute the Receivables Coverage Ratio covenant; leave 0 to show it as n/a.",
            )
        xf1, xf2 = st.columns(2)
        with xf1:
            fx_deval_pct = st.number_input(
                "FX devaluation (95th %ile, % p.a.)",
                value=float(restored_gi.get("fx_deval_pct") or 0.0),
                min_value=0.0,
                max_value=1.0,
                step=0.01,
                format="%.4f",
                help="The 95th percentile FX devaluation over the receivable term (up to 24 months), "
                "used as the FX-risk component of the asset-value LTV. Enter 0 to ignore FX.",
            )
        with xf2:
            fx_discount_50pct = st.checkbox(
                "Apply max 50% FX discount",
                value=restored_gi.get("fx_discount_50pct", True),
                help="The Guide applies a max 50% discount to the FX devaluation when the asset is "
                "priced/traded in a stable currency locally. Uncheck to apply the full devaluation.",
            )
        st.subheader("Lendable status labels")
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            open_label = st.text_input(
                "Status used for active", value=restored_gi.get("open_label", "Open")
            )
        with sc2:
            closed_label = st.text_input(
                "Status used for canceled", value=restored_gi.get("closed_label", "Closed")
            )
        with sc3:
            paidoff_label = st.text_input(
                "Status used for paid-off", value=restored_gi.get("paidoff_label", "Paid-off")
            )

    submitted = st.button("Run analysis", key="run_analysis_button")

if submitted:
    missing_required = [
        t
        for t, required in INPUT_COLUMNS
        if required
        and not mapping[t]
        # Expected Interest can be derived (Gross Loan Value - Principal Value) instead of
        # mapped directly, since many lenders only supply the loan's total value.
        and not (
            t == "Expected Interest"
            and mapping.get("Gross Loan Value")
            and mapping.get("Principal Value")
        )
    ]
    if missing_required:
        st.error("Map these required fields before running: " + ", ".join(missing_required))
        st.stop()
    mapping_errors, mapping_warnings = _validate_mapping(
        raw, mapping, REQUIRED_FIELDS, DATE_FIELDS, NUMERIC_FIELDS, active_cfg["dayfirst"]
    )
    if mapping_errors:
        st.error(
            "Fix these mappings before running:\n\n" + "\n".join(f"- {e}" for e in mapping_errors)
        )
        st.stop()
    with st.spinner("Running analysis..."):
        st.session_state["analysis_mapping"] = mapping
        st.session_state["analysis_mapping_warnings"] = mapping_warnings
        st.session_state["analysis_currency"] = portfolio_currency
        st.session_state["analysis_exclude_rule"] = (
            {"column": exclude_column, "values": exclude_values}
            if exclude_column != "(none)" and exclude_values
            else None
        )
        st.session_state["analysis_active_status_values"] = (
            active_status_values if mapping.get("Loan Status") else []
        )
        # Date of extraction follows the workbook formula =MAX(primary date column)
        # (General Inputs C3 in the sheets), overriding the calendar default once
        # the primary-date-field mapping is known.
        analysis_extraction = pd.Timestamp(extraction_date)
        start_src = mapping.get(active_cfg["primary_date_field"])
        if start_src:
            parsed_start = _detect_date(raw[start_src])
            if parsed_start.notna().any():
                analysis_extraction = pd.Timestamp(parsed_start.max())
        overrides = {
            "extraction_date": analysis_extraction,
            "days_after_term": days_after_term,
            "min_loans_per_cohort": min_loans_per_cohort,
        }
        if not is_lending:
            overrides.update(
                {
                    "months_since_default": months_since_default,
                    "useful_life_years": useful_life_years,
                    "churn_stress_multiplier": churn_stress_multiplier,
                    "open_label": open_label,
                    "closed_label": closed_label,
                    "paidoff_label": paidoff_label,
                    "funding_cost_pct": funding_cost_pct,
                    "direct_cost_pct": direct_cost_pct,
                    "debt_service_180d": debt_service_180d if debt_service_180d else None,
                    "fx_deval_pct": fx_deval_pct if fx_deval_pct else None,
                    "fx_discount_50pct": fx_discount_50pct,
                }
            )
        st.session_state["analysis_gi_overrides"] = overrides
        st.session_state["analysis_ran"] = True
        _save_cache(MAPPING_CACHE_PATH, _cache_key(raw.columns), mapping)
        _save_profile_snapshot()

if not st.session_state.get("analysis_ran"):
    st.info("Map your file's columns above, then click 'Run analysis' to compute all sheets.")
    st.stop()


@st.cache_data(
    show_spinner=False,
    max_entries=8,
)
def _compute_lending_analysis(raw, extraction_date, days_after_term, min_loans_per_cohort):
    """Run the full Lending analysis pipeline. Cached on the mapped/normalized
    `raw` table and the general-input scalars so Streamlit reruns (e.g.
    toggling an unrelated checkbox or editing the AI context) don't recompute
    the whole pipeline from scratch on every interaction - at scale (hundreds
    of thousands of rows) that rebuild cost real seconds each rerun, mirroring
    the earlier fix to _build_lending_export_workbook. `gi` (a GeneralInputs)
    is NOT passed in because Streamlit can't hash it; only its scalar fields
    are, which is all the pipeline reads. show_spinner is off here since the
    call below already runs inside one continuous "Running analysis..."
    spinner covering every step from row-exclusion through this compute -
    a second, narrower spinner just for this part would flash in and out."""
    df = lending_process_data_input(raw, extraction_date, days_after_term)
    cohorts = lending_build_cohorts(df, min_matured=min_loans_per_cohort)
    filtered = lending_filter_cohorts(cohorts, min_loans_per_cohort)
    ltv = LendingLtvAnalysis(df, filtered)
    ue = LendingUeAnalysis(df)
    ue_data, ltv_data = ue.as_dict(), ltv.as_dict()
    lending_chart_data = cohorts.dropna(subset=["Loss Rate"]).copy()
    if not lending_chart_data.empty:
        lending_chart_data["Fee %"] = (
            lending_chart_data["Total Fee"] / lending_chart_data["Total Principal"]
        )
        lending_chart_data["Interest %"] = (
            lending_chart_data["Total Interest"] / lending_chart_data["Total Principal"]
        )
    dq_checks = _lending_data_quality_checks(
        raw,
        cohorts,
        LENDING_CONFIG,
        LendingGeneralInputs(
            raw,
            extraction_date=extraction_date,
            days_after_term=days_after_term,
            min_loans_per_cohort=min_loans_per_cohort,
        ),
        filtered,
    )
    return df, cohorts, filtered, ue_data, ltv_data, lending_chart_data, dq_checks


@st.cache_data(
    show_spinner="Running analysis...",
    max_entries=8,
)
def _compute_rental_analysis(raw, gi_calc):
    """Run the full Rental & Subscription analysis pipeline, cached on the
    mapped/normalized contract tape and the general-inputs dict the same way
    _compute_lending_analysis caches the lending pipeline. The data-quality
    checks are computed by the caller (they need the live GeneralInputs
    object, which is not hashable for the cache)."""
    fallback_df, fallback_notes = apply_fallbacks(
        raw, gi_calc["useful_life_years"], gi_calc["extraction_date"]
    )
    df = process_data_input(fallback_df, gi_calc)
    av = build_asset_view(df, gi_calc)
    curve = build_repayment_curve(av, gi_calc)
    # Rows dropped from Asset View / Lease Cohorts surface as an un-collapsed
    # warning (not buried in the routine fallback notes).
    exclusion_notes = []
    n_missing_asset_id = count_missing_asset_id_rows(df)
    if n_missing_asset_id:
        pct = n_missing_asset_id / len(df) if len(df) else 0
        exclusion_notes.append(
            f"{n_missing_asset_id:,} row(s) ({pct:.0%} of the file) have a blank asset ID and "
            "are excluded entirely from Asset View (and everything downstream of it)."
        )
    n_unparsed_cohort = count_unparsed_cohort_rows(df)
    if n_unparsed_cohort:
        pct = n_unparsed_cohort / len(df) if len(df) else 0
        exclusion_notes.append(
            f"{n_unparsed_cohort:,} row(s) ({pct:.0%} of the file) have an unparseable start "
            "date and are excluded entirely from Lease Cohorts (and everything downstream of it)."
        )
    cohorts = build_cohorts(df, gi_calc)
    filtered = filter_cohorts(cohorts, gi_calc["min_loans_per_cohort"])
    churn = ChurnAnalysis(cohorts, gi_calc)
    ue = UeAnalysis(df, av, curve, gi_calc, churn)
    ltv = LtvAnalysis(df, churn, gi_calc, av)
    ts = build_ts_covenants(ue.as_dict(), ltv.as_dict(), curve, gi_calc.get("debt_service_180d"))
    ue_data, ltv_data, ca_data = ue.as_dict(), ltv.as_dict(), churn.as_dict()
    return (
        df,
        av,
        curve,
        cohorts,
        filtered,
        churn,
        ue_data,
        ltv_data,
        ca_data,
        ts,
        fallback_notes,
        exclusion_notes,
    )


# Everything from the row-exclusion filter through the analysis compute call
# runs inside one status block, since none of it renders anything useful to
# look at until the tabs below are ready - without this, a large file leaves
# the page looking frozen for however long renaming/currency-conversion/
# compute takes, with no indication anything is happening. st.status (not
# st.spinner) is deliberate: it leaves a visible "Analysis complete" line in
# the page after this block exits, so a cache hit that finishes in a
# fraction of a second still shows confirmation - a plain spinner's element
# is removed the instant the block ends, so a run fast enough to land in a
# single browser frame can render as if nothing happened at all.
with st.status("Running analysis...", state="running") as _analysis_status:
    exclude_rule = st.session_state.get("analysis_exclude_rule")
    if exclude_rule and exclude_rule["column"] in raw.columns:
        _excl_mask = raw[exclude_rule["column"]].astype(str).isin(exclude_rule["values"])
        _n_excluded = int(_excl_mask.sum())
        if _n_excluded:
            raw = raw[~_excl_mask].copy()
            st.caption(
                f"Excluded {_n_excluded:,} row(s) where **{exclude_rule['column']}** was in "
                + ", ".join(repr(v) for v in exclude_rule["values"])
                + "."
            )

    mapping = st.session_state["analysis_mapping"]
    rename_map = {src: tgt for tgt, src in mapping.items() if src}
    # If the raw file already has a column literally named like a template field
    # - either because its header auto-aliased to that name before this form ran
    # (e.g. a "LoanStatus" column becomes "Loan Status" during normalization,
    # regardless of what the user picks below), or because it coincidentally
    # collides with a *different* target the user did map - it needs to be
    # dropped before the rename below. Otherwise a field the user explicitly
    # left "(not provided)" silently survives under its template name anyway
    # (downstream code checking `if "Loan Status" in df.columns` still finds
    # it), and a genuine collision leaves two columns sharing one label, where
    # raw[col] then returns a DataFrame instead of a Series and every
    # .dtype/coercion call below breaks. A column stays only if it's the actual
    # chosen source for some target (a key in rename_map) - that includes a
    # field mapped to itself unchanged.
    _template_fields = {t for t, _ in INPUT_COLUMNS}
    stale_target_cols = [
        col for col in raw.columns if col in _template_fields and col not in rename_map
    ]
    raw = raw.drop(columns=stale_target_cols)
    raw = raw.rename(columns=rename_map)
    _coerce_dates(raw, DATE_FIELDS, active_cfg["dayfirst"])
    # _format_normalize() coerced numeric_fields before this rename, so it only ever
    # caught columns whose raw header already auto-aliased to a template name.
    # Anything the user had to manually map above skipped coercion entirely and
    # stayed as raw text - re-run numeric coercion now that the mapping is final.
    for col in NUMERIC_FIELDS:
        if col in raw.columns and raw[col].dtype != "float64":
            raw[col] = pd.to_numeric(_clean_numeric(raw[col]), errors="coerce")

    portfolio_currency = st.session_state.get("analysis_currency", "USD")
    if portfolio_currency != "USD":
        fx_error, fx_rate = None, None
        try:
            fx_rate = _fetch_usd_fx_rates().get(portfolio_currency)
            if fx_rate is None:
                fx_error = f"No live rate available for {portfolio_currency} right now"
        except Exception as e:
            fx_error = f"Couldn't fetch a live {portfolio_currency}/USD exchange rate ({e})"
        if fx_rate:
            converted = [c for c in CURRENCY_FIELDS if c in raw.columns]
            for col in converted:
                raw[col] = raw[col] / fx_rate
            st.caption(
                f"Converted {', '.join(converted)} from {portfolio_currency} to USD at "
                f"1 USD = {fx_rate:,.4f} {portfolio_currency} (live rate via open.er-api.com)."
            )
        else:
            st.warning(
                f"{fx_error} - figures below are still in {portfolio_currency}, not converted."
            )

    if (
        "Expected Interest" not in raw.columns
        and "Gross Loan Value" in raw.columns
        and "Principal Value" in raw.columns
    ):
        raw["Expected Interest"] = raw["Gross Loan Value"] - raw["Principal Value"]
        st.caption(
            "Derived **Expected Interest** as Gross Loan Value minus Principal Value "
            "(Expected Interest wasn't mapped directly)."
        )

    unmapped = [t for t, _ in INPUT_COLUMNS if t not in raw.columns]
    if unmapped:
        st.warning(
            "Not mapped (optional) - related metrics will be unavailable: " + ", ".join(unmapped)
        )

    mapping_warnings = st.session_state.get("analysis_mapping_warnings") or []
    if mapping_warnings:
        st.warning("Mapping quality warnings:\n\n" + "\n".join(f"- {w}" for w in mapping_warnings))

    if st.session_state.get("dedupe_loan_ids") and active_cfg["id_field"] in raw.columns:
        raw = raw.drop_duplicates(subset=active_cfg["id_field"], keep="first")

    if not is_lending:
        # The workbooks name the asset recovery sale/proceeds column
        # "recovery_value", while the template/calculation field is
        # "recovery_amount". Auto-alias so a workbook-named file still feeds
        # recoveries into the recovery-based metrics without extra mapping.
        if "recovery_amount" not in raw.columns and "recovery_value" in raw.columns:
            raw["recovery_amount"] = raw["recovery_value"]
        raw_statuses = sorted(raw["status"].dropna().unique().tolist(), key=str)
        status_key = _cache_key(raw_statuses)
        cached_status_map = _load_cache(STATUS_CACHE_PATH, status_key) or {}
        seed = [
            {
                "Client status": s,
                "Lendable status": cached_status_map.get(s, DEFAULT_STATUS_MAP.get(s, "")),
            }
            for s in raw_statuses
        ]
        st.subheader("Status mapping")
        st.caption(
            "Map each raw status value in your file to a standardised Lendable status "
            "(active / canceled / paid-off)."
        )
        status_map_df = st.data_editor(
            pd.DataFrame(seed), hide_index=True, width="stretch", key="status_map_editor"
        )
        status_map = dict(zip(status_map_df["Client status"], status_map_df["Lendable status"]))
        unmapped_statuses = [s for s, v in status_map.items() if not v]
        if unmapped_statuses:
            st.error(
                f"Analysis did not run: {len(unmapped_statuses)} status value(s) in your file "
                "aren't mapped to a Lendable status yet. Fill in every row of the table above, "
                "then click 'Run analysis' again. Unmapped: " + ", ".join(unmapped_statuses)
            )
            st.stop()
        _save_cache(STATUS_CACHE_PATH, status_key, status_map)
    else:
        status_map = None

    gi_overrides = dict(st.session_state["analysis_gi_overrides"])
    if is_lending:
        gi = LendingGeneralInputs(raw, **gi_overrides)
        (
            df,
            cohorts,
            filtered,
            ue_data,
            ltv_data,
            lending_chart_data,
            dq_checks,
        ) = _compute_lending_analysis(
            raw, gi.extraction_date, gi.days_after_term, gi.min_loans_per_cohort
        )
        av = curve = churn = ca_data = ts = None
        fallback_notes = exclusion_notes = []
    else:
        gi_overrides["status_map"] = status_map
        gi = GeneralInputs(raw, **gi_overrides)
        (
            df,
            av,
            curve,
            cohorts,
            filtered,
            churn,
            ue_data,
            ltv_data,
            ca_data,
            ts,
            fallback_notes,
            exclusion_notes,
        ) = _compute_rental_analysis(raw, gi.as_calc_dict())
        lending_chart_data = None
        dq_checks = _rental_data_quality_checks(raw, cohorts, RENTAL_CONFIG, gi, filtered, ue_data)

    _analysis_status.update(label="Analysis complete", state="complete")

term_supplied = "Term (days)" in raw.columns
if is_lending:
    st.caption(f"Mapped & computed columns: {', '.join(df.columns)}")
    if "Total Due" not in df.columns:
        st.warning(
            "No 'Total Due' or payment schedule mapped - loss rate proxy and PvD ratio will be unavailable."
        )
else:
    if exclusion_notes:
        st.warning(
            "**Rows excluded from this analysis:**\n\n"
            + "\n\n".join(f"- {note}" for note in exclusion_notes)
        )
    if fallback_notes:
        with st.expander(f"Data quality: {len(fallback_notes)} fallback rule(s) applied"):
            for note in fallback_notes:
                st.write("-", note)
    st.caption(f"Mapped & computed columns: {', '.join(df.columns)}")

if is_lending:
    tab_names = [
        "Summary",
        "Data Checks",
        "Cohorts",
        "LTV Analysis",
        "Unit Economics Analysis",
        "Custom Visualizations",
        "Ask AI",
    ]
else:
    tab_names = [
        "Summary",
        "Data Checks",
        "Asset View",
        "Unit Economics Analysis",
        "Churn Analysis",
        "LTV Analysis",
        "TS Covenants",
        "General Analysis",
        "Custom Visualizations",
        "Ask AI",
    ]
tabs = st.tabs(tab_names)
if is_lending:
    with tabs[0]:
        st.subheader("Summary")
        # Every figure on this tab comes from the shared summary_metrics module
        # so the Streamlit Summary tab and the React app's Summary screen
        # (web_api/main.py serves the same dict) can't drift apart. The Loan
        # Status values that count as "still active" are user-picked in the
        # "Loan status" mapping section (vocabulary isn't standardized across
        # deals - "active" isn't assumed).
        _summary = lending_summary_metrics(
            df,
            gi,
            ue_data,
            ltv_data,
            active_status_values=st.session_state.get("analysis_active_status_values"),
        )
        outstanding_gbv = _summary["outstandingGbv"]
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric(
            "Data up to",
            _summary["dataUpTo"],
            help="As-of date of the loan tape (the most recent disbursement date in the data).",
        )
        c2.metric(
            "Outstanding GBV",
            f"{outstanding_gbv:,.0f}" if outstanding_gbv is not None else "n/a",
            help="Principal + expected interest + expected fees - amount paid, for loans that "
            "haven't yet reached maturity (Reached T+3? = False), further narrowed to the "
            "user-picked 'active/outstanding' Loan Status values (in the 'Loan status' mapping "
            "section) when both a status column and at least one such value are set. Matured "
            "loans' unrecovered shortfall is a credit loss, captured by the Loss Rate metrics "
            "instead - not counted here. n/a when no loans qualify.",
        )
        c3.metric(
            "Loss Rate",
            fmt(ue_data["Average Loss"]),
            help="Average loss rate across matured loans: (owed - paid) / owed.",
        )
        c4.metric(
            "95th %ile Loss Rate",
            fmt(ltv_data["95th Percentile Losses"]),
            help="The 95th percentile of cohort loss rates - a stressed view of how "
            "bad loss could get for the worst cohorts.",
        )
        c5.metric(
            "Product Interest Rate",
            fmt(ue_data["Average Interest %"]),
            help="This is the product interest rate over the term of the loans (i.e. not to be "
            "confused with the APR or an Annualized Interest Rate).",
        )

        pvd_ratio = _summary["pvd"]
        weighted_avg_term_snap = _summary["weightedAvgTermMatured"]
        apr_proxy = _summary["aprProxy"]

        st.markdown("---")
        snapshot_col, chart_col = st.columns([1, 1.3])
        with snapshot_col:
            st.subheader("Portfolio Snapshot")
            date_range = f"{_summary['dateRangeStart']} to {_summary['dateRangeEnd']}"
            snapshot_rows = [
                (
                    "Date Range",
                    date_range,
                    "From the earliest disbursement date to the tape as-of date.",
                ),
                ("Loans", f"{_summary['loans']:,}", "Total number of loan records in the tape."),
                (
                    "Value of Principal Disbursed",
                    f"${_summary['principalDisbursed']:,.0f}",
                    "Total principal amount lent across all loans.",
                ),
                (
                    "Total Collected",
                    f"${_summary['totalCollected']:,.0f}",
                    "Total cash collected from borrowers to date.",
                ),
                (
                    "Expected Interest %",
                    fmt(_summary["expectedInterestPct"]),
                    "Total expected interest as a share of total principal across all loans.",
                ),
                (
                    "Expected Fee %",
                    fmt(_summary["expectedFeePct"]),
                    "Total expected fees as a share of total principal across all loans.",
                ),
                (
                    "Avg Loan Value",
                    f"${_summary['avgLoanValue']:,.0f}",
                    "Average principal amount per loan.",
                ),
                (
                    "Avg Total Revenue",
                    fmt(_summary["avgTotalRevenuePct"]),
                    "Expected interest + fees as a share of total principal.",
                ),
                (
                    "PvD",
                    fmt(pvd_ratio),
                    "Paid-vs-Due ratio (Total Paid / Total Due) across matured loans "
                    "(Reached T+3? = True) in the whole portfolio.",
                ),
                (
                    "Weighted Avg Term (matured)",
                    fmt(weighted_avg_term_snap, "{:,.1f} days"),
                    "Principal-weighted average term (days) across matured loans only - a loan "
                    "missing either Term (days) or Principal Value is excluded from both the "
                    "average and its weight, rather than being counted as a 0-day term.",
                ),
                (
                    "APR Proxy",
                    fmt(apr_proxy),
                    "Avg Annualized Flat Interest Rate: Product Interest Rate divided by the "
                    "Weighted Avg Term (matured), in months, times 12 - a simple flat-rate "
                    "annualization (no compounding, no amortization solve).",
                ),
            ]
            _render_snapshot_table(snapshot_rows)

        with chart_col:
            st.subheader("Cohort Loss Rates")
            summary_cohorts = lending_build_cohorts(
                df, min_matured=gi.min_loans_per_cohort, matured_only=False
            )
            bars = (
                alt.Chart(summary_cohorts)
                .mark_bar(color="#B8BEC6")
                .encode(
                    x=alt.X("Cohort:T", title="Cohort"),
                    y=alt.Y(
                        "Total Principal:Q",
                        title="Principal Disbursed",
                        # "~s" gives short SI-style labels (k/M/G/T); swap D3's "G" (giga)
                        # for the more finance-familiar "B" (billion) - same magnitude, friendlier label.
                        axis=alt.Axis(format="~s", labelExpr="replace(datum.label, 'G', 'B')"),
                    ),
                    tooltip=[
                        alt.Tooltip("Cohort:T"),
                        alt.Tooltip(
                            "Total Principal:Q", format=",.0f", title="Principal Disbursed"
                        ),
                        alt.Tooltip("Loan Count:Q", title="Loans"),
                    ],
                )
            )
            loss_data = summary_cohorts.dropna(subset=["Loss Rate"])
            line = (
                alt.Chart(loss_data)
                .mark_line(point=True, color="#E8632A", strokeWidth=2.5)
                .encode(
                    x=alt.X("Cohort:T"),
                    y=alt.Y("Loss Rate:Q", title="Loss Rate", axis=alt.Axis(format="%")),
                    tooltip=[
                        alt.Tooltip("Cohort:T"),
                        alt.Tooltip("Loss Rate:Q", format=".2%"),
                        alt.Tooltip("Matured Count:Q", title="Matured Loans"),
                    ],
                )
            )
            if not loss_data.empty:

                def _ref_layer(value, label, color, x_anchor):
                    if pd.isna(value):
                        return None
                    ref = pd.DataFrame({"v": [value], "label": [label], "xa": [x_anchor]})
                    rule = (
                        alt.Chart(ref)
                        .mark_rule(color=color, strokeDash=[6, 6])
                        .encode(y=alt.Y("v:Q"))
                    )
                    text = (
                        alt.Chart(ref)
                        .mark_text(
                            color=color, dx=8, dy=-6, align="left", fontSize=11, fontWeight="bold"
                        )
                        .encode(x="xa:T", y=alt.Y("v:Q"), text="label:N")
                    )
                    return rule + text

                for ref in (
                    _ref_layer(
                        ue_data["Average Loss"],
                        f"Avg: {fmt(ue_data['Average Loss'])}",
                        "#6B7280",
                        loss_data["Cohort"].min(),
                    ),
                    _ref_layer(
                        ltv_data["95th Percentile Losses"],
                        f"95th %ile: {fmt(ltv_data['95th Percentile Losses'])}",
                        "#d62728",
                        loss_data["Cohort"].max(),
                    ),
                ):
                    if ref is not None:
                        line = line + ref
            st.altair_chart(
                alt.layer(bars, line)
                .resolve_scale(y="independent")
                .properties(
                    height=340,
                    padding={"right": 95},
                ),
                width="stretch",
            )
            st.caption(
                "Grey bars: principal disbursed per cohort (all loans). Orange line: cohort loss rate, "
                f"shown only for cohorts with at least {gi.min_loans_per_cohort} matured loans."
            )

    with tabs[2]:
        st.subheader("Cohorts")

        matured_only_toggle = st.checkbox(
            "Matured loans only",
            value=True,
            help="When checked (default), every figure below is computed from matured loans only "
            "(Reached T+3? = True) - a cohort with none doesn't appear at all, matching the "
            "Excel template's Cohorts pivot exactly. Uncheck to instead see each cohort's true "
            "origination volume and full-book figures. Loss Rate is always matured-only either "
            "way, since it isn't meaningful otherwise - and the 95th %ile Loss Rate metric below "
            "is never affected by this toggle.",
        )
        display_cohorts = (
            cohorts
            if matured_only_toggle
            else lending_build_cohorts(df, min_matured=gi.min_loans_per_cohort, matured_only=False)
        )
        st.caption(
            f"{len(display_cohorts)} monthly cohorts with at least one matured loan (Reached T+3? = True)"
            if matured_only_toggle
            else f"{len(display_cohorts)} monthly cohorts (all loans, matured or not)"
        )

        min_loans = st.number_input(
            "Minimum loans per cohort",
            min_value=0,
            value=gi.min_loans_per_cohort,
            step=1,
            help="Only show cohorts with at least this many loans (matured-only or all-loans, "
            f"depending on the toggle above). Defaults to the Excel template's threshold "
            f"({gi.min_loans_per_cohort}).",
        )

        cohort_view = display_cohorts[display_cohorts["Loan Count"] >= min_loans]

        lead_cols = [
            c
            for c in ("Cohort", "Loan Count", "Matured Count", "Loss Rate")
            if c in cohort_view.columns
        ]
        cohort_view = cohort_view[
            lead_cols + [c for c in cohort_view.columns if c not in lead_cols]
        ]

        c_metric_1, c_metric_2 = st.columns(2)
        c_metric_1.metric(
            "Total Cohorts available",
            f"{len(display_cohorts)}",
            help="All monthly cohorts in the analysis - not affected by the "
            "minimum-loans filter above.",
        )
        c_metric_2.metric(
            "Cohorts shown",
            f"{len(cohort_view)}",
            help="Cohorts matching the 'Minimum loans per cohort' filter above, out of "
            f"{len(display_cohorts)} monthly cohorts.",
        )

        _render_cohorts_grid(cohort_view)

        st.markdown("#### Explore Cohort Data")
        _render_custom_visualizations_tab(
            {"Cohorts (table above)": cohort_view},
            key_prefix="cohorts_cv",
            header=False,
            simple=True,
            fixed_x="Cohort",
            y_label="Metric",
        )

    with tabs[3]:
        st.subheader("LTV Calculator")

        green_interest = ue_data["Average Interest %"]
        green_term_days = ltv_data["Average Term"]
        green_loss = ltv_data["95th Percentile Losses"]
        if pd.notna(green_term_days):
            green_term_bucket = ltv_calculator_nearest_tenor(green_term_days / 30.4375)
        else:
            green_term_bucket = None

        st.markdown("**Inputs**")
        y1, y2, y3 = st.columns(3)
        with y1:
            calc_data_source = st.selectbox(
                "Data Input Source", LTV_CALC_DATA_SOURCES, key="ltvcalc_data_source"
            )
            calc_country = st.selectbox(
                "Alt Lender Country", LTV_CALC_COUNTRIES, key="ltvcalc_country"
            )
        with y2:
            calc_macro_fx = st.selectbox(
                "Macro FX Risk Rating", LTV_CALC_FX_RISK_RATINGS, key="ltvcalc_macro_fx"
            )
            calc_segmentation = st.selectbox(
                "Segmentation", LTV_CALC_SEGMENTATIONS, key="ltvcalc_segmentation"
            )
        with y3:
            calc_hedge_rate = st.number_input(
                "Hedge Rate (0 if unhedged, else % OTM as a decimal)",
                min_value=0.0,
                max_value=1.0,
                value=0.0,
                step=0.01,
                format="%.4f",
                key="ltvcalc_hedge_rate",
            )
            calc_rolled_hedge = st.selectbox(
                "Rolled hedge?", LTV_CALC_HEDGE_TYPES, key="ltvcalc_rolled_hedge"
            )

        loss_rate_fallback_notes = []
        if pd.isna(green_loss):
            green_loss, floor_notes = ltv_calculator_sector_min_loss_rate(
                calc_segmentation, calc_data_source
            )
            loss_rate_fallback_notes = [
                "No computable Loss Rate for this portfolio (95th %ile Loss is n/a) - using the "
                f"Min Sector Loss Rate for '{calc_segmentation}' ({calc_data_source}) instead: "
                f"{fmt(green_loss)}."
            ] + floor_notes

        result = None
        if pd.isna(green_interest) or pd.isna(green_loss) or green_term_bucket is None:
            st.warning(
                "Can't compute the LTV waterfall - one of the green inputs above is n/a for this portfolio."
            )
        else:
            for note in loss_rate_fallback_notes:
                st.info(note)
            result = ltv_calculator_compute(
                gross_interest=green_interest,
                term_months=green_term_bucket,
                loss_rate=green_loss,
                country=calc_country,
                macro_fx_risk=calc_macro_fx,
                segmentation=calc_segmentation,
                hedge_rate=calc_hedge_rate,
                rolled_hedge=calc_rolled_hedge,
                data_source=calc_data_source,
            )
            for note in result["notes"]:
                st.info(note)

        if result is not None:
            st.markdown("---")
            st.markdown("#### Outputs - Suggested LTV")

            _render_matrix_table(
                row_labels=["FX High", "No FX", "FX Low"],
                col_labels=["Advance on Principal", "Advance on GBV"],
                values=[
                    [fmt(result["ltv_high"]), fmt(result["ltgbv_high"])],
                    [fmt(result["ltv_no_fx"]), fmt(result["ltgbv_no_fx"])],
                    [fmt(result["ltv_low"]), fmt(result["ltgbv_low"])],
                ],
                big_row=0,
            )
            st.caption(
                "FX High is the stressed, headline scenario. Advance on Principal = Advance on "
                "GBV grossed up by (1 + weighted avg. gross interest). Both Advance on GBV and "
                "Advance on Principal limits must be complied with (source: Receivables sheet, "
                "cell C44)."
            )

            st.markdown("---")
            st.markdown("#### Back-Up")

            st.subheader("FX Backup")
            _render_snapshot_table(
                [
                    (
                        "Term",
                        f"{green_term_bucket} months" if green_term_bucket else "n/a",
                        "Weighted average receivable term, snapped to the nearest tenor bucket the "
                        "workbook's dropdown allows.",
                    ),
                    (
                        "Historical Devaluation",
                        fmt(result["historical_fx_deval"]),
                        "99th-percentile historical FX devaluation for the selected country and tenor.",
                    ),
                    (
                        "Utilized Historical FX Deval",
                        fmt(result["utilized_fx_deval"]),
                        "Historical FX devaluation actually used - the 6-month figure when the tenor is 6 "
                        "months or less.",
                    ),
                    (
                        "Stress FX Devaluation",
                        fmt(result["stress_fx_deval"]),
                        f"Max(historical, utilized) FX devaluation, stressed by the FX stress factor ({result['stress_fx_factor']:g}x).",
                    ),
                    (
                        "Base FX Deval",
                        fmt(result["base_fx_deval"]),
                        "Stress FX devaluation, capped at the hedge rate when the deal is hedged.",
                    ),
                    (
                        "FX Slippage Stress",
                        fmt(result["fx_slippage"]),
                        "FX basis-risk slippage for the selected country.",
                    ),
                    (
                        "Selected FX Deval (High)",
                        fmt(result["selected_fx_deval_high"]),
                        "Base FX deval + slippage + high-end roll risk.",
                    ),
                    (
                        "Selected FX Deval (Low)",
                        fmt(result["selected_fx_deval_low"]),
                        "Base FX deval + slippage + low-end roll risk.",
                    ),
                ]
            )
            fx_country_data = pd.DataFrame(
                {
                    "Tenor (months)": LTV_CALC_TENORS,
                    "Historical FX Devaluation": LTV_CALC_FX_DEVAL_TABLE[calc_country],
                }
            )
            st.altair_chart(
                _add_reference_line(
                    alt.Chart(fx_country_data)
                    .mark_line(point=True, color="#1f77b4")
                    .encode(
                        x=alt.X("Tenor (months):O", title="Tenor (months)"),
                        y=alt.Y(
                            "Historical FX Devaluation:Q",
                            title="99th %ile FX Devaluation",
                            axis=alt.Axis(format="%"),
                        ),
                        tooltip=[
                            "Tenor (months):O",
                            alt.Tooltip("Historical FX Devaluation:Q", format=".2%"),
                        ],
                    ),
                    result["historical_fx_deval"],
                    f"Selected tenor ({green_term_bucket}m): {result['historical_fx_deval']:.2%}",
                    color="#d62728",
                ).properties(
                    title=f"99th %ile Historical FX Devaluation - {calc_country}", height=300
                ),
                width="stretch",
            )

            st.subheader("Credit Stress Backup")
            _render_snapshot_table(
                [
                    (
                        "Product Interest Rate",
                        fmt(green_interest),
                        "Weighted average gross interest rate, pulled from this analysis.",
                    ),
                    (
                        "95th %ile Loss",
                        fmt(green_loss),
                        "95th percentile loss rate at T+3, pulled from this analysis - or, when "
                        "that's n/a for this portfolio, the Min Sector Loss Rate used as a "
                        "fallback (see the note above).",
                    ),
                    (
                        "Stress Loss",
                        fmt(result["stress_loss_rate"]),
                        f"Loss rate multiplied by the credit stress factor ({result['credit_stress_factor']:g}x).",
                    ),
                    (
                        "Min Sector Loss Rate",
                        fmt(result["minimum_stress_loss"]),
                        "Sector-minimum stress loss floor for the selected segmentation and data source.",
                    ),
                    (
                        "Selected Stress Loss",
                        fmt(result["selected_stress_loss"]),
                        "Max(stress loss rate, minimum stress loss floor).",
                    ),
                ]
            )
            if not lending_chart_data.empty:
                st.altair_chart(
                    _add_reference_line(
                        alt.Chart(lending_chart_data)
                        .mark_line(point=True)
                        .encode(
                            x=alt.X("Cohort:T", title="Cohort"),
                            y=alt.Y("Loss Rate:Q", title="Loss Rate", axis=alt.Axis(format="%")),
                            tooltip=["Cohort:T", alt.Tooltip("Loss Rate:Q", format=".2%")],
                        ),
                        green_loss,
                        f"95th %ile: {green_loss:.2%}",
                        color="#d62728",
                        x_anchor=lending_chart_data["Cohort"].max(),
                    ).properties(title="Loss per Cohort", height=300),
                    width="stretch",
                )

    with tabs[4]:
        st.subheader("Unit Economics Analysis")
        overview_tab, ue_model_tab = st.tabs(["Overview", "UE Model"])
        with overview_tab:
            row1 = st.columns(3)
            row1[0].metric(
                "Average Expected Term (days)", fmt(ue_data["Average Expected Term"], "{:,.1f}")
            )
            row1[1].metric("Average Loss", fmt(ue_data["Average Loss"]))
            row1[2].metric("Loss Rate Proxy (1-PvD)", fmt(ue_data["Loss Rate Proxy (1-PvD)"]))
            row2 = st.columns(3)
            row2[0].metric(
                "Average Principal Amount", fmt(ue_data["Average Principal Amount"], "{:,.0f}")
            )
            row2[1].metric("Average Fee %", fmt(ue_data["Average Fee %"]))
            row2[2].metric("Average Interest %", fmt(ue_data["Average Interest %"]))
            row3 = st.columns(3)
            row3[0].metric("Sense-check Margin", fmt(ue_data["Sense-check Margin"]))
            if not lending_chart_data.empty:
                st.altair_chart(
                    _add_reference_line(
                        alt.Chart(lending_chart_data)
                        .mark_line(point=True)
                        .encode(
                            x=alt.X("Cohort:T", title="Cohort"),
                            y=alt.Y("Loss Rate:Q", title="Loss Rate", axis=alt.Axis(format="%")),
                            tooltip=["Cohort:T", alt.Tooltip("Loss Rate:Q", format=".2%")],
                        ),
                        ue_data["Average Loss"],
                        f"Avg: {ue_data['Average Loss']:.2%}",
                        color="#2ca02c",
                        x_anchor=lending_chart_data["Cohort"].max(),
                    ).properties(title="Loss per Cohort", height=300),
                    width="stretch",
                )
                st.altair_chart(
                    _add_reference_line(
                        alt.Chart(lending_chart_data)
                        .mark_line(point=True)
                        .encode(
                            x=alt.X("Cohort:T", title="Cohort"),
                            y=alt.Y("Weighted Avg Term:Q", title="Avg Term (days)"),
                            tooltip=["Cohort:T", alt.Tooltip("Weighted Avg Term:Q", format=".1f")],
                        ),
                        ue_data["Average Expected Term"],
                        f"Avg: {ue_data['Average Expected Term']:.1f} days",
                        color="#2ca02c",
                        x_anchor=lending_chart_data["Cohort"].max(),
                    ).properties(title="Average Term per Cohort (days)", height=300),
                    width="stretch",
                )
                st.altair_chart(
                    _add_reference_line(
                        alt.Chart(lending_chart_data)
                        .mark_line(point=True, color="#ff7f0e")
                        .encode(
                            x=alt.X("Cohort:T", title="Cohort"),
                            y=alt.Y(
                                "Interest %:Q", title="% of Principal", axis=alt.Axis(format="%")
                            ),
                            tooltip=["Cohort:T", alt.Tooltip("Interest %:Q", format=".2%")],
                        ),
                        ue_data["Average Interest %"],
                        f"Avg: {ue_data['Average Interest %']:.2%}",
                        color="#ff7f0e",
                        x_anchor=lending_chart_data["Cohort"].max(),
                    ).properties(title="Average Product Interest Percent per Cohort", height=300),
                    width="stretch",
                )
                st.altair_chart(
                    _add_reference_line(
                        alt.Chart(lending_chart_data)
                        .mark_line(point=True, color="#1f77b4")
                        .encode(
                            x=alt.X("Cohort:T", title="Cohort"),
                            y=alt.Y("Fee %:Q", title="% of Principal", axis=alt.Axis(format="%")),
                            tooltip=["Cohort:T", alt.Tooltip("Fee %:Q", format=".2%")],
                        ),
                        ue_data["Average Fee %"],
                        f"Avg: {ue_data['Average Fee %']:.2%}",
                        color="#1f77b4",
                        x_anchor=lending_chart_data["Cohort"].max(),
                    ).properties(title="Average Fee Percent per Cohort", height=300),
                    width="stretch",
                )
        with ue_model_tab:
            ue_model_ai_data = _render_ue_model_ai_tab(df, ue_data)

    with tabs[5]:
        _render_custom_visualizations_tab(
            {
                "Data Input (loan-level)": df,
                "Cohorts": cohorts,
                "Cohorts for X or more loans": filtered,
            }
        )

    with tabs[6]:
        chat_context = _build_lending_chat_context(
            gi,
            df,
            cohorts,
            ue_data,
            ltv_data,
            ue_model_ai_data,
            dq_checks,
            user_context=_ai_context_text(max_chars=60000, per_doc=15000),
        )
        _render_ai_chat_tab(chat_context)

    with tabs[1]:
        st.subheader("Data Checks")
        data_tab, checks_tab = st.tabs(["Data Input", "Checks"])
        with data_tab:
            st.caption(
                f"{len(raw):,} rows x {len(raw.columns):,} columns uploaded. "
                f"Computed columns (Cohort, Term, Reached T+3?) used in downstream sheets."
            )
            _render_data_input_grid(raw, height=400)
        with checks_tab:
            _render_data_quality_checks(
                dq_checks,
                "Loss Rate (%)",
                "Lending",
                "escalate_variance",
                file_name,
            )


else:
    with tabs[0]:
        st.subheader("Summary")
        # Shared with the React app's Summary screen - see summary_metrics.py.
        _summary = rental_summary_metrics(df, av, gi, ue_data, ltv_data, ts)
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Contracts", f"{_summary['contracts']:,}")
        s2.metric("MRR", fmt(_summary["mrr"], "{:,.0f}"))
        s3.metric(
            "Avg MRR per Active Contract",
            f"{_summary['avgMrrPerActiveContract']:,.0f}"
            if _summary["avgMrrPerActiveContract"] is not None
            else "n/a",
            help="MRR divided by currently-open contracts. A quick sanity check on revenue per unit.",
        )
        s4.metric("Average Useful Life (m)", fmt(_summary["avgUsefulLifeM"], "{:.1f}"))
        r1, r2, r3, r4, r5 = st.columns(5)
        r1.metric("95th %ile Churn", fmt(_summary["pctile95Churn"], "{:.2%}"))
        r2.metric("Stressed Churn", fmt(_summary["stressedChurn"], "{:.2%}"))
        r3.metric(
            "Contracts Churned",
            fmt(_summary["contractsChurned"])
            if _summary["contractsChurned"] is not None
            else "n/a",
            help="Share of contracts closed (terminated) rather than paid off or still active.",
        )
        r4.metric("Defaulted contracts > 3mo", fmt(_summary["defaultedGt3m"], "{:.0f}"))
        r5.metric("% recovered", fmt(_summary["pctRecovered"]))
        y1, y2, y3 = st.columns(3)
        y1.metric("Loss (non-recoverability)", fmt(_summary["lossNonRecoverability"]))
        y2.metric("MRR / average cost", fmt(_summary["mrrOverAvgCost"], "{:.2%}"))
        y3.metric("Average Collection Rate", fmt(_summary["avgCollectionRate"]))
        st.markdown("---")
        st.subheader("LTV & UE highlights")
        z1, z2, z3, z4 = st.columns(4)
        z1.metric(
            "Asset LTV (max 95%)",
            fmt(_summary["assetLtv"], "{:.1%}"),
            help="Guide asset-value LTV: (recoverability + subscription support)/(1 + FX), capped at 95%.",
        )
        z2.metric(
            "Net UE margin",
            fmt(_summary["netUeMargin"]),
            help="Gross margin minus Lendable's cost of funding and direct costs over the useful life.",
        )
        z3.metric(
            "Asset DSLP30",
            fmt(_summary["assetDslp30"]),
            help="% of active asset value with no payment in the last 30 days.",
        )
        z4.metric(
            "Receivables Coverage (180d)",
            fmt(_summary["receivablesCoverage180d"]),
            help="180 days of performing receivables over the facility's debt service due over the next 180 days.",
        )
        st.markdown("---")
        st.subheader("Portfolio Snapshot")
        date_range = f"{_summary['dateRangeStart']} to {_summary['dateRangeEnd']}"
        _render_snapshot_table(
            [
                (
                    "Date Range",
                    date_range,
                    "From the earliest contract start date to the tape as-of date.",
                ),
                (
                    "Contracts",
                    f"{_summary['contractRecords']:,}",
                    "Total number of contract records in the tape.",
                ),
                (
                    "Unique Assets",
                    f"{_summary['uniqueAssets']:,}",
                    "Total number of unique assets.",
                ),
                (
                    "Total Collected",
                    f"${_summary['totalCollected']:,.0f}",
                    "Total cash collected from customers to date.",
                ),
                (
                    "Total Cost of Assets",
                    f"${_summary['totalCostOfAssets']:,.0f}",
                    "Total acquisition cost of the assets.",
                ),
                (
                    "Downpayment %",
                    fmt(_summary["downpaymentPct"]),
                    "Total downpayments as a share of total asset cost.",
                ),
                (
                    "Lease Tenor (m)",
                    fmt(_summary["leaseTenorM"], "{:.1f}"),
                    "Cost-weighted average lease term in months.",
                ),
                (
                    "Margin",
                    fmt(_summary["margin"]),
                    "Total scheduled payments over asset cost minus 1.",
                ),
                ("PvD", fmt(_summary["pvd"]), "Total paid over total expected to date."),
                (
                    "Utilisation Rate",
                    fmt(_summary["utilisationRate"]),
                    "Share of assets currently on an open contract.",
                ),
                (
                    "Historical % Collected on Principal",
                    fmt(_summary["historicalPctCollectedOnPrincipal"]),
                    "Collections (incl. recovery) over the initial asset cost for assets that reached term.",
                ),
            ]
        )

    with tabs[1]:
        st.subheader("Data Checks")
        data_tab, checks_tab = st.tabs(["Data Input", "Checks"])
        with data_tab:
            st.caption(
                f"{len(df):,} rows x {len(df.columns):,} columns. Includes all Lendable-derived columns."
            )
            _render_data_input_grid(df, height=400)
        with checks_tab:
            _render_data_quality_checks(
                dq_checks,
                "Churn Rate (%)",
                MODEL_LABELS[model_key],
                "escalate_variance_rental",
                file_name,
            )

    with tabs[2]:
        st.subheader("Asset View")
        st.caption(f"{len(av):,} unique assets")
        st.dataframe(av, width="stretch", height=400)

    with tabs[3]:
        st.subheader("Unit Economics Analysis")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Downpayment %", fmt(ue_data["downpayment_pct"]))
        c2.metric("Lease Tenor (m)", fmt(ue_data["lease_tenor_m"], "{:.1f}"))
        c3.metric("Margin", fmt(ue_data["margin"]))
        c4.metric("Utilisation Rate", fmt(ue_data["utilisation_rate"]))
        c1, c2, c3 = st.columns(3)
        c1.metric("PvD", fmt(ue_data["pvd"]))
        c2.metric(
            "Historical % Collected on Principal",
            fmt(ue_data["historical_pct_collected_on_principal"]),
        )
        c3.metric(
            "Monthly Observed Repayment", fmt(ue_data["monthly_observed_repayment"], "{:.2%}")
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Funding Cost (% p.a.)", fmt(ue_data["funding_cost_pct"], "{:.2%}"))
        c2.metric("Direct Cost (% p.a.)", fmt(ue_data["direct_cost_pct"], "{:.2%}"))
        c3.metric(
            "Cost of Funding over Life", fmt(ue_data["cost_of_funding_over_life_pct"], "{:.2%}")
        )
        c4.metric("Net UE Margin", fmt(ue_data["net_margin"]))
        curve_chart = curve.dropna(subset=["pct_avg_collection"])
        if not curve_chart.empty:
            st.altair_chart(
                alt.Chart(curve_chart)
                .mark_line(point=True)
                .encode(
                    x=alt.X("mob:Q", title="Month on Books"),
                    y=alt.Y(
                        "pct_avg_collection:Q", title="% Avg collection", axis=alt.Axis(format="%")
                    ),
                    tooltip=["mob", alt.Tooltip("pct_avg_collection:Q", format=".2%")],
                )
                .properties(title="High level repayment curve", height=350),
                width="stretch",
            )
        st.dataframe(curve, width="stretch", height=300)

    with tabs[4]:
        st.subheader("Churn Analysis")
        c1, c2, c3 = st.columns(3)
        c1.metric("95th %ile monthly churn", fmt(ca_data["pctile_95"], "{:.2%}"))
        c2.metric("Average monthly churn", fmt(ca_data["avg_churn"], "{:.2%}"))
        c3.metric("Stressed churn (1.7x)", fmt(ca_data["stress_churn"], "{:.2%}"))
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("1-year multiple", fmt(ca_data["multiplier_1y"], "{:.2f}"))
        c2.metric("2-year multiple", fmt(ca_data["multiplier_2y"], "{:.2f}"))
        c3.metric("3-year multiple", fmt(ca_data["multiplier_3y"], "{:.2f}"))
        c4.metric("Total multiple", fmt(ca_data["multiplier_total"], "{:.2f}"))
        residual = ca_data.get("residual_curve")
        if residual is not None and not residual.empty:
            st.altair_chart(
                alt.Chart(residual)
                .mark_line()
                .encode(
                    x=alt.X("month:Q", title="Month"),
                    y=alt.Y("residual_value:Q", title="Residual portfolio"),
                    tooltip=["month", alt.Tooltip("residual_value:Q", format=".2%")],
                )
                .properties(title="Expected residual portfolio by month", height=300),
                width="stretch",
            )
        churn_chart = cohorts.dropna(subset=["churn_rate"])
        if not churn_chart.empty:
            st.altair_chart(
                alt.Chart(churn_chart)
                .mark_line(point=True)
                .encode(
                    x=alt.X("cohort:T", title="Cohort"),
                    y=alt.Y("churn_rate:Q", title="Churn Rate", axis=alt.Axis(format="%")),
                    tooltip=["cohort:T", alt.Tooltip("churn_rate:Q", format=".2%")],
                )
                .properties(title="Churn Rate per Cohort", height=300),
                width="stretch",
            )
        st.markdown("**Lease Cohorts**")
        st.dataframe(cohorts, width="stretch", height=300)
        st.markdown(f"**Cohorts for X or more loans (>= {gi.min_loans_per_cohort} new leases)**")
        st.dataframe(filtered, width="stretch", height=200)

    with tabs[5]:
        st.subheader("LTV Analysis")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Asset Analysis**")
            st.metric("Average Useful Life (m)", fmt(ltv_data["avg_useful_life_m"], "{:.1f}"))
            st.metric("Defaulted contracts > 3mo", fmt(ltv_data["n_defaulted_gt_3m"], "{:.0f}"))
            st.metric("Defaulted last 12m", fmt(ltv_data["n_defaulted_last_12m"], "{:.0f}"))
            st.metric("% recovered", fmt(ltv_data["pct_recovered"]))
            st.metric("Recovery Rate (12m)", fmt(ltv_data["pct_recovered_last_12m"]))
            st.metric("Recoverability (95th %ile)", fmt(ltv_data["recoverability_pctile_95"]))
            st.metric("Loss (non-recoverability)", fmt(ltv_data["loss_non_recoverability"]))
        with col2:
            st.markdown("**Cash Flow Analysis (MRR)**")
            st.metric("MRR", fmt(ltv_data["mrr"], "{:,.0f}"))
            st.metric("MRR (active <= DSLP30)", fmt(ltv_data["mrr_dslp30"], "{:,.0f}"))
            st.metric("Subscription margin", fmt(ltv_data["subscription_margin"], "{:.2%}"))
            st.metric("Subscription support", fmt(ltv_data["subscription_support"], "{:.2%}"))
            st.metric("MRR Multiplier (3y)", fmt(ltv_data["mrr_multiplier"], "{:.2f}"))
            st.metric("MRR / average cost", fmt(ltv_data["mrr_over_avg_cost"], "{:.2%}"))
        st.markdown("---")
        st.subheader("Asset-value LTV waterfall (Guide)")
        w1, w2, w3, w4 = st.columns(4)
        w1.metric("Recoverability (95th %ile)", fmt(ltv_data["recoverability_pctile_95"]))
        w2.metric("Subscription support", fmt(ltv_data["subscription_support"]))
        w3.metric("FX devaluation used", fmt(ltv_data["fx_deval_used"], "{:.2%}"))
        w4.metric("Asset LTV (max 95%)", fmt(ltv_data["ltv_asset_value"], "{:.1%}"))
        st.caption(
            "LTV = min(95%, (recoverability + subscription support) / (1 + FX)). "
            "Recoverability = 95th %ile of per-asset recovery/value on defaulted assets "
            "(falls back to the repossession rate when no recovery values are available)."
        )

    with tabs[6]:
        st.subheader("TS Covenants")
        st.markdown("**Performance Covenants**")
        pc_cols = st.columns(3)
        for col, m in zip(pc_cols, (6, 12, 24)):
            col.metric(
                f"Paid at {m}m over asset cost",
                fmt(ts[f"paid_at_{m}m_over_cost"]),
                help="Cumulative payments over asset acquisition cost at this many months on "
                "book. n/a when the portfolio has no contracts old enough to reach this point.",
            )
        st.metric(
            "Option 2 - Avg Monthly Paid over Avg Cost of Asset",
            fmt(ts["option2_observed"], "{:.2%}"),
        )
        st.markdown("**Recoverability Covenants**")
        st.metric(
            "Recovery Rate (observed)",
            fmt(ts["recovery_rate_observed"]),
            help="Contracts repossessed/insured (proxy: redeployed) over contracts defaulted in the "
            "last 12 months (>= 3 months prior).",
        )
        st.markdown("**Asset-level Covenants**")
        c1, c2 = st.columns(2)
        c1.metric(
            "Asset DSLP30 (observed)",
            fmt(ts["asset_dslp30_observed"]),
            help="% of active asset value with no payment in the last 30 days.",
        )
        c2.metric(
            "Receivables Coverage (180d)",
            fmt(ts["receivables_coverage_observed"]),
            help="180 days of performing receivables over the facility's debt service due over the "
            "next 180 days. n/a when the debt-service input is 0.",
        )

    with tabs[7]:
        st.subheader("General Analysis")
        summary = general_analysis(df)
        st.write(f"**Shape:** {summary['shape'][0]:,} rows x {summary['shape'][1]} columns")
        st.write("**Columns:**", ", ".join(summary["columns"]))
        orig = cohorts[["cohort", "value_of_leases"]].dropna()
        if not orig.empty:
            st.altair_chart(
                alt.Chart(orig)
                .mark_bar()
                .encode(
                    x=alt.X("cohort:T", title="Cohort"),
                    y=alt.Y("value_of_leases:Q", title="Value of Leases"),
                    tooltip=["cohort:T", alt.Tooltip("value_of_leases:Q", format=",.0f")],
                )
                .properties(title="New lease value per month", height=350),
                width="stretch",
            )

    with tabs[8]:
        _render_custom_visualizations_tab(
            {
                "Data Input": df,
                "Asset View": av,
                "Lease Cohorts": cohorts,
                "Cohorts for X or more loans": filtered,
                "Repayment Curve": curve,
                "Churn Residual Curve": ca_data["residual_curve"],
            }
        )

    with tabs[9]:
        chat_context = _build_rental_chat_context(
            gi,
            df,
            av,
            cohorts,
            ue_data,
            ltv_data,
            ca_data,
            ts,
            dq_checks,
            user_context=_ai_context_text(max_chars=60000, per_doc=15000),
        )
        _render_ai_chat_tab(chat_context)
st.markdown("---")


if is_lending:
    buf = _build_lending_export_workbook(
        df,
        cohorts,
        filtered,
        gi.days_after_term,
        gi.min_loans_per_cohort,
        ltv_data,
        ue_data,
        lending_chart_data,
        st.session_state.get("export_charts", []),
        term_supplied,
        mapping=st.session_state.get("analysis_mapping"),
        currency=st.session_state.get("analysis_currency"),
        extraction_date=gi.extraction_date,
        exclude_rule=st.session_state.get("analysis_exclude_rule"),
        active_status_values=st.session_state.get("analysis_active_status_values"),
    )
    file_name = f"SC_Analysis_Lending_{pd.Timestamp.now():%Y-%m-%d}.xlsx"
else:
    buf = _build_rental_export_workbook(
        gi,
        df,
        av,
        curve,
        cohorts,
        filtered,
        ue_data,
        ltv_data,
        ca_data,
        ts,
        st.session_state.get("export_charts", []),
        mapping=st.session_state.get("analysis_mapping"),
        currency=st.session_state.get("analysis_currency"),
        exclude_rule=st.session_state.get("analysis_exclude_rule"),
        status_map=status_map,
    )
    file_name = f"SC_Analysis_Rental_{pd.Timestamp.now():%Y-%m-%d}.xlsx"

chat_history = st.session_state.get("ai_chat_history", [])
include_chat_download = False
if chat_history:
    include_chat_download = st.checkbox(
        "Include the Ask AI chat history as an extra sheet in this download",
        key="include_chat_history_download",
    )
download_bytes = _add_chat_history_sheet(buf, chat_history) if include_chat_download else buf

_dl_col, _share_col = st.columns(2)
with _dl_col:
    st.download_button(
        "Download full workbook as Excel",
        download_bytes,
        file_name=file_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
with _share_col:
    _create_share_clicked = st.button("Create share link", key="create_share_link")

if _create_share_clicked:
    try:
        _share_token = _store_create_share(
            upload_bytes=st.session_state.get("_current_upload_bytes"),
            upload_name=st.session_state.get("_current_upload_name") or "upload",
            mapping=st.session_state.get("analysis_mapping"),
            currency=st.session_state.get("analysis_currency"),
            gi_overrides=st.session_state.get("analysis_gi_overrides"),
            exclude_rule=st.session_state.get("analysis_exclude_rule"),
            active_status_values=st.session_state.get("analysis_active_status_values"),
            analysis_context=st.session_state.get("analysis_context", ""),
            chat_history=st.session_state.get("ai_chat_history", []),
        )
        _base_url = (st.context.url or "").split("?")[0] or "http://localhost:8501"
        st.session_state["last_share_link"] = f"{_base_url}?share={_share_token}"
    except Exception as e:
        st.error(f"Couldn't create a share link: {e}")
if st.session_state.get("last_share_link"):
    st.code(st.session_state["last_share_link"])
    if _auth_mode() == "google":
        st.caption(
            f"Shared read-only with anyone at **{current_user.split('@', 1)[-1]}** who has "
            "this link (never fully public) - revoke any time from your Drive."
        )
    else:
        st.caption("Anyone with this link can open it - there's no access list yet.")
