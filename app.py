"""Streamlit SC Analysis tool for the Lending model.

Reproduces the SC Analysis for LTV and UE workbook's generic lending
template (port of the Lending/ project in this monorepo, itself derived
from github.com/nephy-edge/screening-data-analysis-tool).

The Rental & Subscription (asset-lease) model was removed from the app and
moved to the rental_and_subscription/ folder - see git history for the
previously unified two-model version of this file.
"""

import io
import json
import os
import re
import secrets
import sys
import tempfile
import time
from datetime import datetime as _dt
from urllib.parse import quote

import altair as alt
import certifi
import numpy_financial as npf
import pandas as pd
import requests
import streamlit as st
from openpyxl import load_workbook
from openpyxl.chart import AreaChart, BarChart, LineChart, Reference, ScatterChart, Series
from openpyxl.utils import get_column_letter
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

from dotenv import load_dotenv

load_dotenv()

from theme import inject_style, render_cover, render_masthead

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))

from template_analysis.general_inputs import GeneralInputs as LendingGeneralInputs
from template_analysis.data_questionnaire import QUESTIONS as LENDING_QUESTIONS
from template_analysis.data_input import process_data_input as lending_process_data_input
from template_analysis.cohorts import build_cohorts as lending_build_cohorts
from template_analysis.cohorts_for_x_or_more_loans import filter_cohorts as lending_filter_cohorts
from template_analysis.ltv_analysis import LtvAnalysis as LendingLtvAnalysis
from template_analysis.ue_analysis import UeAnalysis as LendingUeAnalysis
from template_analysis.general_analysis import describe as lending_general_analysis
from template_analysis.apr import principal_weighted_average_rates
from template_analysis.date_detection import (
    SLASHED_DATE_RE as _SLASHED_DATE_RE,
    infer_dayfirst as _infer_dayfirst,
    detect_date as _detect_date,
    mixed_parsed_and_text_warning as _mixed_parsed_and_text_warning,
)
from template_analysis.ltv_calculator import (
    COUNTRIES as LTV_CALC_COUNTRIES, DATA_INPUT_SOURCES as LTV_CALC_DATA_SOURCES,
    FX_RISK_RATINGS as LTV_CALC_FX_RISK_RATINGS, HEDGE_TYPES as LTV_CALC_HEDGE_TYPES,
    SEGMENTATIONS as LTV_CALC_SEGMENTATIONS, compute_ltv as ltv_calculator_compute,
    nearest_tenor_bucket as ltv_calculator_nearest_tenor,
    TENORS as LTV_CALC_TENORS, FX_DEVAL_TABLE as LTV_CALC_FX_DEVAL_TABLE,
)

import config as app_config

DEEPINFRA_MODEL = app_config.AI["model"]
DEEPINFRA_CHAT_URL = app_config.AI["chat_url"]
_EXTRA_CA_PEM = os.path.join(os.path.dirname(__file__), "certs", "corporate_root.pem")


@st.cache_resource
def _ca_bundle_path() -> str:
    """Certifi's trust store plus this machine's corporate proxy root CA (if
    bundled), so HTTPS calls work on networks with TLS-inspecting proxies
    (e.g. Zscaler, Cisco Umbrella) without an env var set before launch."""
    if not os.path.exists(_EXTRA_CA_PEM):
        return certifi.where()
    with open(certifi.where(), "r", encoding="utf-8") as f:
        bundle = f.read()
    with open(_EXTRA_CA_PEM, "r", encoding="utf-8") as f:
        bundle += "\n" + f.read()
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False, encoding="utf-8")
    tmp.write(bundle)
    tmp.close()
    return tmp.name


# ISO 4217 currencies covered by the open.er-api.com free FX endpoint - kept
# in sync with that API's own currency set (checked via its /latest/USD
# response) so every option in the picker below is actually convertible.
CURRENCY_NAMES = {
    "AED": "UAE Dirham", "AFN": "Afghan Afghani", "ALL": "Albanian Lek",
    "AMD": "Armenian Dram", "ANG": "Netherlands Antillean Guilder", "AOA": "Angolan Kwanza",
    "ARS": "Argentine Peso", "AUD": "Australian Dollar", "AWG": "Aruban Florin",
    "AZN": "Azerbaijani Manat", "BAM": "Bosnia-Herzegovina Convertible Mark", "BBD": "Barbadian Dollar",
    "BDT": "Bangladeshi Taka", "BGN": "Bulgarian Lev", "BHD": "Bahraini Dinar",
    "BIF": "Burundian Franc", "BMD": "Bermudan Dollar", "BND": "Brunei Dollar",
    "BOB": "Bolivian Boliviano", "BRL": "Brazilian Real", "BSD": "Bahamian Dollar",
    "BTN": "Bhutanese Ngultrum", "BWP": "Botswanan Pula", "BYN": "Belarusian Ruble",
    "BZD": "Belize Dollar", "CAD": "Canadian Dollar", "CDF": "Congolese Franc",
    "CHF": "Swiss Franc", "CLF": "Chilean Unit of Account (UF)", "CLP": "Chilean Peso",
    "CNH": "Chinese Yuan (Offshore)", "CNY": "Chinese Yuan", "COP": "Colombian Peso",
    "CRC": "Costa Rican Colon", "CUP": "Cuban Peso", "CVE": "Cape Verdean Escudo",
    "CZK": "Czech Koruna", "DJF": "Djiboutian Franc", "DKK": "Danish Krone",
    "DOP": "Dominican Peso", "DZD": "Algerian Dinar", "EGP": "Egyptian Pound",
    "ERN": "Eritrean Nakfa", "ETB": "Ethiopian Birr", "EUR": "Euro",
    "FJD": "Fijian Dollar", "FKP": "Falkland Islands Pound", "FOK": "Faroese Krona",
    "GBP": "British Pound", "GEL": "Georgian Lari", "GGP": "Guernsey Pound",
    "GHS": "Ghanaian Cedi", "GIP": "Gibraltar Pound", "GMD": "Gambian Dalasi",
    "GNF": "Guinean Franc", "GTQ": "Guatemalan Quetzal", "GYD": "Guyanaese Dollar",
    "HKD": "Hong Kong Dollar", "HNL": "Honduran Lempira", "HRK": "Croatian Kuna",
    "HTG": "Haitian Gourde", "HUF": "Hungarian Forint", "IDR": "Indonesian Rupiah",
    "ILS": "Israeli New Shekel", "IMP": "Isle of Man Pound", "INR": "Indian Rupee",
    "IQD": "Iraqi Dinar", "IRR": "Iranian Rial", "ISK": "Icelandic Krona",
    "JEP": "Jersey Pound", "JMD": "Jamaican Dollar", "JOD": "Jordanian Dinar",
    "JPY": "Japanese Yen", "KES": "Kenyan Shilling", "KGS": "Kyrgystani Som",
    "KHR": "Cambodian Riel", "KID": "Kiribati Dollar", "KMF": "Comorian Franc",
    "KRW": "South Korean Won", "KWD": "Kuwaiti Dinar", "KYD": "Cayman Islands Dollar",
    "KZT": "Kazakhstani Tenge", "LAK": "Laotian Kip", "LBP": "Lebanese Pound",
    "LKR": "Sri Lankan Rupee", "LRD": "Liberian Dollar", "LSL": "Lesotho Loti",
    "LYD": "Libyan Dinar", "MAD": "Moroccan Dirham", "MDL": "Moldovan Leu",
    "MGA": "Malagasy Ariary", "MKD": "Macedonian Denar", "MMK": "Myanmar Kyat",
    "MNT": "Mongolian Tugrik", "MOP": "Macanese Pataca", "MRU": "Mauritanian Ouguiya",
    "MUR": "Mauritian Rupee", "MVR": "Maldivian Rufiyaa", "MWK": "Malawian Kwacha",
    "MXN": "Mexican Peso", "MYR": "Malaysian Ringgit", "MZN": "Mozambican Metical",
    "NAD": "Namibian Dollar", "NGN": "Nigerian Naira", "NIO": "Nicaraguan Cordoba",
    "NOK": "Norwegian Krone", "NPR": "Nepalese Rupee", "NZD": "New Zealand Dollar",
    "OMR": "Omani Rial", "PAB": "Panamanian Balboa", "PEN": "Peruvian Sol",
    "PGK": "Papua New Guinean Kina", "PHP": "Philippine Peso", "PKR": "Pakistani Rupee",
    "PLN": "Polish Zloty", "PYG": "Paraguayan Guarani", "QAR": "Qatari Riyal",
    "RON": "Romanian Leu", "RSD": "Serbian Dinar", "RUB": "Russian Ruble",
    "RWF": "Rwandan Franc", "SAR": "Saudi Riyal", "SBD": "Solomon Islands Dollar",
    "SCR": "Seychellois Rupee", "SDG": "Sudanese Pound", "SEK": "Swedish Krona",
    "SGD": "Singapore Dollar", "SHP": "Saint Helena Pound", "SLE": "Sierra Leonean Leone",
    "SLL": "Sierra Leonean Leone (old)", "SOS": "Somali Shilling", "SRD": "Surinamese Dollar",
    "SSP": "South Sudanese Pound", "STN": "Sao Tome & Principe Dobra", "SYP": "Syrian Pound",
    "SZL": "Swazi Lilangeni", "THB": "Thai Baht", "TJS": "Tajikistani Somoni",
    "TMT": "Turkmenistani Manat", "TND": "Tunisian Dinar", "TOP": "Tongan Pa'anga",
    "TRY": "Turkish Lira", "TTD": "Trinidad & Tobago Dollar", "TVD": "Tuvaluan Dollar",
    "TWD": "New Taiwan Dollar", "TZS": "Tanzanian Shilling", "UAH": "Ukrainian Hryvnia",
    "UGX": "Ugandan Shilling", "USD": "US Dollar", "UYU": "Uruguayan Peso",
    "UZS": "Uzbekistani Som", "VES": "Venezuelan Bolivar", "VND": "Vietnamese Dong",
    "VUV": "Vanuatu Vatu", "WST": "Samoan Tala", "XAF": "Central African CFA Franc",
    "XCD": "East Caribbean Dollar", "XCG": "Caribbean Guilder", "XDR": "IMF Special Drawing Rights",
    "XOF": "West African CFA Franc", "XPF": "CFP Franc", "YER": "Yemeni Rial",
    "ZAR": "South African Rand", "ZMW": "Zambian Kwacha", "ZWG": "Zimbabwe Gold",
    "ZWL": "Zimbabwean Dollar",
}
# The subset of NUMERIC_FIELDS that are actually money (as opposed to day
# counts) - only these get rescaled when converting to USD.
CURRENCY_FIELDS = {"Principal Value", "Expected Interest", "Expected Fee", "Total Paid", "Total Due"}
_FX_API_URL = app_config.FX["api_url"]


@st.cache_data(ttl=app_config.FX["cache_ttl_seconds"])
def _fetch_usd_fx_rates() -> dict:
    """Live USD exchange rates from a free, keyless, open API (open.er-api.com).
    Returns {currency_code: units of that currency per 1 USD}; cached for an
    hour so repeated reruns/uploads don't hammer the endpoint."""
    resp = requests.get(_FX_API_URL, timeout=app_config.FX["request_timeout_seconds"], verify=_ca_bundle_path())
    resp.raise_for_status()
    data = resp.json()
    if data.get("result") != "success":
        raise ValueError(f"FX API returned {data.get('result', 'an error')}")
    return data["rates"]


def _get_slack_webhook_url():
    try:
        return st.secrets["SLACK_WEBHOOK_URL"]
    except Exception:
        return os.environ.get("SLACK_WEBHOOK_URL")


def _send_slack_feedback(message: str, model: str, user: str = "") -> tuple[bool, str]:
    """POST a feedback submission to the configured Slack Incoming Webhook."""
    webhook = _get_slack_webhook_url()
    if not webhook:
        return False, "SLACK_WEBHOOK_URL not configured."
    who = f" — {user.strip()}" if user.strip() else ""
    payload = {
        "text": (
            f"*Feedback* ({model}){who}\n{message.strip()}\n"
            f"_Submitted {_dt.now().strftime('%Y-%m-%d %H:%M')}_"
        )
    }
    try:
        resp = requests.post(
            webhook,
            json=payload,
            timeout=15,
            verify=_ca_bundle_path(),
        )
        resp.raise_for_status()
        return True, ""
    except Exception as e:
        return False, str(e)


# The app is Lending-only; the Rental & Subscription model was removed and
# moved to the rental_and_subscription/ folder (see git history).
is_lending = True

st.set_page_config(
    page_title="SC Analysis - Lending",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_style()
render_masthead("SC Analysis - Lending")
render_cover(
    "Structured Credit Analysis - LTV & Unit Economics",
    "Upload loan-level portfolio data, map your columns to the Data Input "
    "template, and the analysis is computed automatically across all sheets.",
)

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
                ok, err = _send_slack_feedback(
                    fb_text, "Lending", fb_user
                )
                if ok:
                    st.session_state["fb_form_version"] = fb_form_version + 1
                    st.session_state["fb_just_sent"] = True
                    st.rerun()
                else:
                    st.error(f"Could not send feedback: {err}")

LENDING_CONFIG = dict(
    id_field="Loan ID",
    input_columns=[
        ("Loan ID", True), ("Disbursement Date", True), ("Expected Completion Date", True),
        ("Principal Value", True), ("Expected Interest", True), ("Expected Fee", False),
        ("Total Paid", True), ("Total Due", False),
        ("Loan Status", False), ("Days Late", False), ("Term (days)", False),
    ],
    date_fields={"Disbursement Date", "Expected Completion Date"},
    numeric_fields={
        "Principal Value", "Expected Interest", "Expected Fee", "Total Paid", "Total Due",
        "Days Late", "Term (days)",
    },
    dayfirst=True,
    primary_date_field="Disbursement Date",
    mapping_cache_path=os.path.join(os.path.expanduser("~"), ".sc_analysis_column_mappings.json"),
    needs_status_map=False,
    domain_hint="loan-portfolio spreadsheet",
    derived_hint="If your file provides Total GBV and Principal Value but not Expected Interest, "
                 "or separate Principal / Interest / Fee columns but not Total Due, combine them "
                 "here; the result becomes selectable in the mapping below.",
    derived_placeholder="For example, Expected Interest, calculated as Total GBV minus Principal Value",
)
active_cfg = LENDING_CONFIG
INPUT_COLUMNS = active_cfg["input_columns"]
DATE_FIELDS = active_cfg["date_fields"]
NUMERIC_FIELDS = active_cfg["numeric_fields"]
REQUIRED_FIELDS = {t for t, required in INPUT_COLUMNS if required}
MAPPING_CACHE_PATH = active_cfg["mapping_cache_path"]


def _cache_key(values) -> str:
    return "|".join(sorted(str(v) for v in values))


def _invalidate_ai_mapping_guesses() -> None:
    """Drop cached AI mapping auto-fill results so they are recomputed with the
    current user-provided context on the next rerun (called when the context
    text area changes)."""
    for k in list(st.session_state):
        if k.startswith("ai_mapping_guess_"):
            st.session_state.pop(k, None)


def _read_uploaded_bytes(uploaded) -> bytes:
    try:
        return uploaded.getvalue()
    except AttributeError:
        uploaded.seek(0)
        return uploaded.read()


def _extract_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("PDF support requires the 'pypdf' package (pip install pypdf).")
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)
    return "\n".join(parts)


def _extract_excel_text(data: bytes) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines = []
    try:
        for ws in wb.worksheets:
            lines.append(f"=== Sheet: {ws.title} ===")
            for n, row in enumerate(ws.iter_rows(values_only=True), 1):
                vals = [str(v) for v in row if v is not None]
                if vals:
                    lines.append(" | ".join(vals))
                if n >= 2000:
                    lines.append("...[sheet truncated at 2000 rows]")
                    break
    finally:
        wb.close()
    return "\n".join(lines)


def _extract_docx_text(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError:
        raise RuntimeError("Word support requires the 'python-docx' package (pip install python-docx).")
    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def _extract_text_file(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_document_text(uploaded) -> dict:
    """Extract readable text from an uploaded document so it can be included in
    AI context. Returns {"name", "text", "error"}; a missing/unsupported parser
    or a parse failure surfaces as an "error" note instead of breaking upload."""
    name = uploaded.name or "document"
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    data = _read_uploaded_bytes(uploaded)
    try:
        if suffix == "pdf":
            text = _extract_pdf_text(data)
        elif suffix in ("xlsx", "xlsm"):
            text = _extract_excel_text(data)
        elif suffix == "docx":
            text = _extract_docx_text(data)
        elif suffix in ("txt", "csv", "md", "json", "log"):
            text = _extract_text_file(data)
        else:
            return {"name": name, "text": "", "error": f"Unsupported file type '.{suffix}' - use PDF, Excel, Word (.docx), or text files."}
    except Exception as e:
        return {"name": name, "text": "", "error": str(e)}
    return {"name": name, "text": text, "error": None}


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated, {len(text) - limit:,} chars omitted]"


def _ai_context_text(max_chars: int = 60000, per_doc: int = 15000) -> str:
    """Combine the user's free-text notes with text extracted from uploaded
    documents into one prompt block, truncated to keep AI calls bounded."""
    parts = []
    notes = st.session_state.get("analysis_context", "").strip()
    if notes:
        parts.append("## Additional context notes\n" + notes)
    for doc in st.session_state.get("context_documents", []):
        if doc.get("error"):
            parts.append(f"## Uploaded document: {doc['name']}\n[Could not be used: {doc['error']}]")
        else:
            parts.append(
                f"## Uploaded document: {doc['name']}\n" + _truncate_text(doc["text"], per_doc)
            )
    text = "\n\n".join(parts)
    return _truncate_text(text, max_chars) if len(text) > max_chars else text


def _load_cache(path, key) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return cache.get(key)


def _save_cache(path, key, value) -> None:
    cache = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            cache = {}
    cache[key] = value
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except OSError:
        pass


def _validate_mapping(
    raw: pd.DataFrame, mapping: dict, required_fields, date_fields, numeric_fields, dayfirst=False
) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    for target, source in mapping.items():
        if not source:
            continue
        series = raw[source]
        non_null = series.notna()
        if non_null.sum() == 0:
            msg = f"**{target}** -> column '{source}' is entirely empty."
            (errors if target in required_fields else warnings).append(msg)
            continue
        if target in date_fields:
            parsed = _detect_date(series)
            fail_rate = 1 - (parsed.notna().sum() / non_null.sum())
            if fail_rate > 0.5:
                msg = f"**{target}** -> column '{source}' doesn't look like dates ({fail_rate:.0%} unparseable)."
                (errors if target in required_fields else warnings).append(msg)
            mixed_msg = _mixed_parsed_and_text_warning(series, target)
            if mixed_msg:
                warnings.append(mixed_msg)
        elif target in numeric_fields:
            parsed = pd.to_numeric(_clean_numeric(series), errors="coerce")
            fail_rate = 1 - (parsed.notna().sum() / non_null.sum())
            if fail_rate > 0.5:
                msg = f"**{target}** -> column '{source}' doesn't look numeric ({fail_rate:.0%} unparseable)."
                (errors if target in required_fields else warnings).append(msg)
    return errors, warnings


def _step2_data_quality_checks(raw: pd.DataFrame, config: dict) -> list[dict]:
    """Step 2 checks from the screening briefing: duplicate IDs, field
    completion below 80%, portfolio size above 5,000 rows, and history
    below 12 months. Driven entirely by the LENDING_CONFIG dict (id_field,
    input_columns, primary_date_field). Each check is a dict with "level"
    ("warning"/"info"), "message", an optional "detail" DataFrame, and an
    optional "detail_expander" title (if absent but "detail" is set, the
    table is shown directly, not collapsed)."""
    checks: list[dict] = []
    id_field = config["id_field"]

    if id_field in raw.columns:
        dup_mask = raw[id_field].notna() & raw[id_field].duplicated(keep=False)
        if dup_mask.any():
            checks.append({
                "level": "warning",
                "check_id": "duplicate_loan_id",
                "message": (
                    f"**Duplicate {id_field}s** - {raw.loc[dup_mask, id_field].nunique()} "
                    f"{id_field}(s) appear more than once ({int(dup_mask.sum())} rows). "
                    "Confirm with the borrower whether these are genuine duplicates."
                ),
                # key=str: id_field can be a mix of str/int/float (e.g. an Excel
                # column read with some cells numeric, others text) - sort_values
                # compares values pairwise and raises TypeError on a mixed-type
                # column, so sort by each value's string form instead.
                "detail": raw.loc[dup_mask].sort_values(id_field, key=lambda s: s.astype(str)),
                "detail_expander": f"View {int(dup_mask.sum())} duplicate row(s)",
            })

    low_completion = [
        f"{target} ({raw[target].notna().mean():.0%})"
        for target, _ in config["input_columns"]
        if target in raw.columns and raw[target].notna().mean() < 0.80
    ]
    if low_completion:
        checks.append({
            "level": "warning",
            "message": (
                "**Field completion below 80%** - " + ", ".join(low_completion) + ". "
                "Records missing these fields may distort downstream metrics."
            ),
        })

    if len(raw) > 5000:
        checks.append({
            "level": "info",
            "message": (
                f"**Large portfolio** - {len(raw):,} rows exceeds the 5,000-row "
                "screening threshold. The tool itself scales well past this "
                "(tested sub-second at ~419,000 rows); flagged in case screening "
                "policy calls for a sampled or ad-hoc review at this size regardless."
            ),
        })

    primary_date_field = config["primary_date_field"]
    if primary_date_field in raw.columns and raw[primary_date_field].notna().any():
        span_days = (
            raw[primary_date_field].max() - raw[primary_date_field].min()
        ).days
        if span_days < 365:
            checks.append({
                "level": "warning",
                "message": (
                    f"**Limited history** - dates span only "
                    f"{span_days / 30:.1f} months, below the 12-month screening "
                    "threshold; loss/churn-rate percentiles may be unreliable."
                ),
            })

    return checks


def _lending_variance_check(cohorts: pd.DataFrame) -> list[dict]:
    """Step 4: flags cohort-to-cohort loss-rate swings above 5 percentage
    points, with likely contributing factors diagnosed from signals already
    in the cohort table."""
    checks: list[dict] = []
    if cohorts is not None and "Loss Rate" in cohorts.columns:
        by_month = cohorts.dropna(subset=["Loss Rate"]).sort_values("Cohort")
        deltas = by_month["Loss Rate"].diff()
        flagged = by_month.loc[deltas.abs() > 0.05]
        if not flagged.empty:
            flagged_deltas = deltas.loc[flagged.index]
            loan_count_pct = by_month["Loan Count"].pct_change().loc[flagged.index]
            term_delta = by_month["Weighted Avg Term"].diff().loc[flagged.index]
            prior_term = by_month["Weighted Avg Term"].shift(1).loc[flagged.index]
            avg_loan_size = by_month["Total Principal"] / by_month["Loan Count"]
            avg_loan_size_pct = avg_loan_size.pct_change().loc[flagged.index]
            pvd_delta = by_month["PvD Ratio"].diff().loc[flagged.index] if "PvD Ratio" in by_month.columns else None

            def _diagnose(idx):
                causes = []
                matured = flagged.loc[idx, "Matured Count"]
                if pd.notna(matured) and matured < 10:
                    causes.append(f"small matured sample ({int(matured)} loans) - swing may be noise")
                lc_pct = loan_count_pct.loc[idx]
                if pd.notna(lc_pct) and abs(lc_pct) > 0.5:
                    causes.append(
                        f"loan count {'dropped' if lc_pct < 0 else 'spiked'} "
                        f"{abs(lc_pct):.0%} vs prior cohort - check for missing loads"
                    )
                td, pt = term_delta.loc[idx], prior_term.loc[idx]
                if pd.notna(td) and pd.notna(pt) and pt > 0 and abs(td) / pt > 0.25:
                    causes.append(
                        f"weighted avg term shifted {td:+.0f} days vs prior cohort "
                        "- possible mix/definition change"
                    )
                als_pct = avg_loan_size_pct.loc[idx]
                if pd.notna(als_pct) and abs(als_pct) > 0.5:
                    causes.append(
                        f"average loan size {'dropped' if als_pct < 0 else 'grew'} "
                        f"{abs(als_pct):.0%} vs prior cohort - possible portfolio mix change"
                    )
                if pvd_delta is not None:
                    pvd = pvd_delta.loc[idx]
                    if pd.notna(pvd) and abs(pvd) > 0.10:
                        causes.append(
                            f"PvD ratio shifted {pvd * 100:+.0f}pp vs prior cohort "
                            "- possible collections/servicing change"
                        )
                return "; ".join(causes) if causes else (
                    "No obvious data-driven cause - likely a genuine portfolio "
                    "event, escalate to Borrower"
                )

            detail = pd.DataFrame({
                "Cohort": flagged["Cohort"].dt.strftime("%b %Y"),
                "Loss Rate (%)": (flagged["Loss Rate"] * 100).round(1).values,
                "Swing vs Prior Cohort (pp)": (flagged_deltas * 100).round(1).values,
                "Likely Contributing Factor(s)": [_diagnose(i) for i in flagged.index],
            })
            checks.append({
                "level": "warning",
                "check_id": "unexplained_variance",
                "message": (
                    f"**Unexplained variance** - {len(detail)} cohort(s) show a "
                    "loss-rate swing of more than 5 percentage points versus the "
                    "prior cohort. Likely contributing factors are diagnosed below "
                    "from loan-count, sample-size, and term-mix signals already in "
                    "the data - verify before relying on these cohorts."
                ),
                "detail": detail,
            })

    return checks


def _negative_loss_rate_check(cohorts: pd.DataFrame) -> list[dict]:
    """Flags any cohort whose Loss Rate came out negative - i.e. Total Paid
    exceeded Principal+Interest+Fee for that cohort's matured loans. This can
    be legitimate (late fees/penalty interest collected but never added to
    the "owed" side) or a real data issue (refinanced/rolled-over principal
    double-counted in Total Paid, a scale/currency mismatch, a sign error
    upstream) - the detail table below shows the exact owed/paid components
    behind the number so either can be traced by hand, the same way the
    Loss Rate is actually computed in cohorts.py, rather than just asserting
    something looks wrong."""
    checks: list[dict] = []
    if cohorts is None or cohorts.empty or "Loss Rate" not in cohorts.columns:
        return checks
    flagged = cohorts[cohorts["Loss Rate"] < 0].sort_values("Cohort")
    if flagged.empty:
        return checks

    owed = flagged["Total Principal"] + flagged["Total Interest"] + flagged["Total Fee"]
    detail = pd.DataFrame({
        "Cohort": flagged["Cohort"].dt.strftime("%b %Y"),
        "Matured Count": flagged["Matured Count"].values,
        "Loss Rate (%)": (flagged["Loss Rate"] * 100).round(2).values,
        "Owed = Principal+Interest+Fee": owed.round(2).values,
        "Total Paid": flagged["Total Paid"].round(2).values,
        "Excess Collected (Paid - Owed)": (flagged["Total Paid"] - owed).round(2).values,
    })
    checks.append({
        "level": "warning",
        "check_id": "negative_loss_rate",
        "message": (
            f"**Negative Loss Rate** - {len(detail)} cohort(s) show Total Paid exceeding "
            "Principal+Interest+Fee for their matured loans, i.e. a negative loss. This can "
            "be legitimate (late fees or penalty interest collected but never added to the "
            "\"owed\" side of the formula) or a real data issue (refinanced/rolled-over loans "
            "double-counting principal in Total Paid, a scale or currency mismatch, or a sign "
            "error upstream) - the owed/paid components below are the exact figures the Loss "
            "Rate is computed from, so the cause can be traced directly rather than guessed at."
        ),
        "detail": detail,
        "detail_expander": f"View {len(detail)} negative-loss cohort(s)",
    })
    return checks


def _cohort_coverage_note(cohorts: pd.DataFrame) -> list[dict]:
    """Surfaces the exact date range and month count behind the cohort-level
    metrics, so a mismatch against a previously-downloaded Excel/Google
    Sheets export can be self-diagnosed rather than mistaken for an app bug.
    This tool always computes cohorts live from the uploaded Data Input rows,
    never from any cached pivot in a source file - but a Google Sheets export
    can itself go stale (its cached "Cohorts"/"Cohorts for X or more loans"
    pivots keep showing an old, narrower date range after the live sheet
    grows), which was the real explanation behind two deals this tool was
    checked against (Pintek, Teclogi Logtech) that appeared to "disagree"
    with their own Excel file."""
    checks: list[dict] = []
    if cohorts is not None and not cohorts.empty and "Cohort" in cohorts.columns:
        months = cohorts["Cohort"].dropna()
        if not months.empty:
            checks.append({
                "level": "info",
                "check_id": "cohort_coverage",
                "message": (
                    f"**Cohort coverage** - this analysis covers "
                    f"{months.nunique()} disbursement cohort(s), "
                    f"{months.min():%b %Y} to {months.max():%b %Y}, computed "
                    "live from the uploaded Data Input rows. If comparing "
                    "against a previously downloaded Excel/Google Sheets "
                    "export, check that its own Cohorts sheet covers the "
                    "same range - Google Sheets pivot caches can go stale "
                    "and keep showing an older, narrower range after the "
                    "live sheet has grown."
                ),
            })
    return checks


def _cohort_threshold_check(gi, cohorts: pd.DataFrame, filtered: pd.DataFrame) -> list[dict]:
    """Flags when 'Minimum loans per cohort' excludes most of this file's own
    cohorts. That threshold is a per-deal assumption baked into each Excel
    copy of the template (confirmed to vary: GoCab/Subbyx use 20, Ennoo/SME
    Go Rental use 1) - the app has no way to read the "right" value for a
    given file from its data alone, so a mismatch here is silent: cohorts
    keep rendering, just built from far fewer loans than the real template
    would have used. This can't fix that, only make the mismatch visible."""
    checks: list[dict] = []
    if cohorts is None or cohorts.empty:
        return checks
    total = len(cohorts)
    kept = len(filtered) if filtered is not None else 0
    if total >= 3 and kept / total < 0.5:
        checks.append({
            "level": "warning",
            "check_id": "min_loans_per_cohort_too_high",
            "message": (
                f"**Minimum loans per cohort ({gi.min_loans_per_cohort}) may not fit this file** - "
                f"only {kept} of {total} cohorts meet it. This threshold is a per-deal assumption "
                "that varies between real files (seen as low as 1, as high as 20) and isn't "
                "derivable from the uploaded data - if this file's real threshold is lower, "
                "loss/churn-rate percentiles are being computed from a small, filtered slice of "
                "the portfolio. Check the file's own General Inputs value if you have it, or "
                "lower the number in the form above and re-run."
            ),
        })
    return checks


def _lending_data_quality_checks(
    raw: pd.DataFrame, cohorts: pd.DataFrame, gi=None, filtered: pd.DataFrame = None
) -> list[dict]:
    """Lending: Step 2 checks (shared) + Step 4 loss-rate variance diagnosis
    + negative-loss-rate flag + cohort-coverage note + General Inputs
    sanity check (min loans/cohort vs this file's own cohort count)."""
    checks = (
        _step2_data_quality_checks(raw, LENDING_CONFIG)
        + _lending_variance_check(cohorts)
        + _negative_loss_rate_check(cohorts)
        + _cohort_coverage_note(cohorts)
    )
    if gi is not None and filtered is not None:
        checks += _cohort_threshold_check(gi, cohorts, filtered)
    return checks


def fmt(value, spec="{:.1%}"):
    return spec.format(value) if pd.notna(value) else "n/a"


def _esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_snapshot_table(rows: list) -> None:
    """A small metric/value/tooltip table, styled like the Summary tab's Portfolio Snapshot."""
    html = (
        """
        <style>
        div.snapshot-table table{border-collapse:collapse;font-family:"Inter",sans-serif;font-size:.82rem;}
        div.snapshot-table th{padding:8px 12px;text-align:left;border-bottom:1px solid #D8DEE5;font-weight:600;color:#16312E;}
        div.snapshot-table td{padding:7px 12px;border-bottom:1px solid #D8DEE5;color:#525252;}
        div.snapshot-table td.val{color:#16312E;}
        div.snapshot-table .q{display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;
            margin-left:6px;border-radius:50%;background:#D8DEE5;color:#16312E;font-size:.7rem;font-weight:700;
            cursor:help;position:relative;vertical-align:middle;}
        div.snapshot-table .tip{display:none;position:absolute;left:0;top:100%;width:230px;margin-top:6px;padding:8px 10px;
            background:#16312E;color:#EDF3F1;font-size:.72rem;font-weight:400;font-family:"Inter",sans-serif;
            border-radius:6px;z-index:50;line-height:1.4;}
        div.snapshot-table .q:hover .tip{display:block;}
        </style>
        <div class="snapshot-table">
        <table>
          <thead><tr><th>Metric</th><th>Value</th></tr></thead>
          <tbody>
        """
        + "".join(
            f'<tr><td>{_esc(name)}<span class="q">?<span class="tip">{_esc(desc)}</span></span></td>'
            f'<td class="val">{_esc(value)}</td></tr>'
            for name, value, desc in rows
        )
        + "</tbody></table></div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def _render_big_number_card(label: str, value: float, value_fmt: str = "{:.1%}", help_text: str = None) -> None:
    """A metric-style card matching stMetric's card look (see theme.py), but
    with the big number colored green/red by sign - stMetric itself only
    colors its delta, not the primary value, so a plain st.metric can't do
    this."""
    if pd.isna(value):
        display, color = "n/a", "#16312E"
    else:
        display = value_fmt.format(value)
        color = "#2ca02c" if value >= 0 else "#d62728"
    tip = f'<span class="q">?<span class="tip">{_esc(help_text)}</span></span>' if help_text else ""
    st.markdown(
        f"""
        <style>
        div.big-number-card{{background:#fff;border:1px solid #D8DEE5;border-radius:7px;padding:18px 22px;}}
        div.big-number-card .lbl{{font-size:.58rem;text-transform:uppercase;letter-spacing:.11em;color:#666666;
            font-family:"Inter",sans-serif;font-weight:600;}}
        div.big-number-card .val{{font-size:1.9rem;font-family:"Inter",sans-serif;font-weight:700;margin-top:2px;}}
        div.big-number-card .q{{display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;
            margin-left:6px;border-radius:50%;background:#D8DEE5;color:#16312E;font-size:.7rem;font-weight:700;
            cursor:help;position:relative;vertical-align:middle;}}
        div.big-number-card .tip{{display:none;position:absolute;left:0;top:100%;width:230px;margin-top:6px;padding:8px 10px;
            background:#16312E;color:#EDF3F1;font-size:.72rem;font-weight:400;font-family:"Inter",sans-serif;
            text-transform:none;letter-spacing:normal;border-radius:6px;z-index:50;line-height:1.4;}}
        div.big-number-card .q:hover .tip{{display:block;}}
        </style>
        <div class="big-number-card">
            <div class="lbl">{_esc(label)}{tip}</div>
            <div class="val" style="color:{color};">{_esc(display)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _ue_model_badge(text: str, bg: str, fg: str) -> None:
    st.markdown(
        f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:4px;'
        f'font-size:.8rem;font-weight:600;">{_esc(text)}</span>',
        unsafe_allow_html=True,
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
    this app doesn't build. 'Net Unit Economics (simplified)' below is a single-period proxy for
    that figure, not a replacement for it.
    """
    principal = df["Principal Value"].sum()
    revenue = df["Expected Interest"].sum()
    origination_income = df["Expected Fee"].sum()

    matured = df[df["Reached T+3?"] == True]
    matured_principal = matured["Principal Value"].sum()
    matured_revenue = matured["Expected Interest"].sum()
    matured_origination_income = matured["Expected Fee"].sum()
    matured_owed = matured_principal + matured_revenue + matured_origination_income
    losses_dollar = matured_owed - matured["Total Paid"].sum() if matured_owed else float("nan")
    losses_pct_of_gbv = losses_dollar / principal if principal and pd.notna(losses_dollar) else float("nan")
    matured_term_m = (
        (matured["Term (days)"] * matured["Principal Value"]).sum() / matured_principal / 30.4375
        if matured_principal else float("nan")
    )

    rates = principal_weighted_average_rates(df)
    implied_interest = rates["APR"]
    term_m = ue_data["Average Expected Term"] / 30.4375

    _ue_model_badge("Manual inputs (not in a loan tape)", "#f5d90a", "#16312E")
    st.caption(
        "The master workbook needs these to complete the Unit Economics picture, but they're cost "
        "and financing assumptions, not something a loan tape records - enter cost % against "
        "matured-loan GBV (matching the Derived section below), financing % as per annum; 0 if not "
        "applicable."
    )
    _cost_defaults = app_config.UE_MODEL_COST_DEFAULTS
    m1, m2 = st.columns(2)
    upfront_cost_pct = m1.number_input(
        "Upfront variable costs (% of matured-loan GBV)", min_value=0.0,
        value=_cost_defaults["upfront_cost_pct"], step=0.1,
        format="%.2f", key="ue_ai_upfront_cost",
    ) / 100
    ongoing_cost_pct = m2.number_input(
        "Ongoing variable costs (% of matured-loan GBV)", min_value=0.0,
        value=_cost_defaults["ongoing_cost_pct"], step=0.1,
        format="%.2f", key="ue_ai_ongoing_cost",
    ) / 100
    m4, m5, m6 = st.columns(3)
    financing_cost_pct = m4.number_input(
        "Financing cost (% p.a.)", min_value=0.0,
        value=_cost_defaults["financing_cost_pct"], step=0.1,
        format="%.2f", key="ue_ai_financing_cost",
    ) / 100
    facility_fee_pct = m5.number_input(
        "Facility fee - annualised (% p.a.)", min_value=0.0,
        value=_cost_defaults["facility_fee_pct"], step=0.1,
        format="%.2f", key="ue_ai_facility_fee",
    ) / 100
    hedge_cost_pct = m6.number_input(
        "Hedge cost - annualised (% p.a.)", min_value=0.0,
        value=_cost_defaults["hedge_cost_pct"], step=0.1,
        format="%.2f", key="ue_ai_hedge_cost",
    ) / 100

    n_matured = len(matured)
    avg_principal_matured = matured_principal / n_matured if n_matured else float("nan")
    avg_fee_matured = matured_origination_income / n_matured if n_matured else float("nan")
    avg_gbv_matured = matured_owed / n_matured if n_matured and matured_owed else float("nan")
    n_periods = int(round(matured_term_m)) + 3 if pd.notna(matured_term_m) and matured_term_m > 0 else None

    gross_irr = float("nan")
    if n_periods and pd.notna(avg_principal_matured) and pd.notna(avg_gbv_matured) and pd.notna(losses_dollar) \
            and matured_owed:
        loss_rate = losses_dollar / matured_owed
        cf0 = -avg_principal_matured + avg_fee_matured - (upfront_cost_pct * avg_principal_matured)
        monthly_cf = (
            (avg_gbv_matured / n_periods) * (1 - loss_rate)
            - (ongoing_cost_pct * avg_principal_matured / n_periods)
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
        net_ue_simple = product_margin - cost_of_finance_over_term
    else:
        income_net_losses = income_net_losses_margin = float("nan")
        variable_costs_dollar = float("nan")
        product_contribution = product_margin = float("nan")
        net_ue_simple = float("nan")

    st.markdown("")
    _ue_model_badge("Auto-calculated from your data", "#2ca02c", "#fff")
    _render_snapshot_table([
        ("Product Interest Rate", fmt(ue_data["Average Interest %"]),
         "Total expected interest income as a % of total principal disbursed, across all loans - "
         "the stated interest on the product, not the solved-for APR below."),
        ("Origination Income %", fmt(ue_data["Average Fee %"]),
         "Total expected fee income as a % of total principal disbursed, across all loans."),
        ("Implied Interest (APR) %", fmt(implied_interest),
         "Nominal APR: principal-weighted average implied rate, same figure as the Cohorts Stats tab."),
        ("Implied Monthly Interest %", fmt(implied_interest / 12), "Implied interest divided by 12."),
        ("Loss %", fmt(losses_pct_of_gbv),
         "Losses in dollars divided by total Principal Value disbursed across the whole book - a "
         "literal % of GBV (not the Summary tab's Loss Rate, which divides by matured owed instead "
         "and is scoped to a much smaller base)."),
        ("Term", fmt(term_m, "{:,.1f} months"), "Principal-weighted average term."),
    ])
    st.caption(
        f"{len(matured):,} of {len(df):,} loans have matured (Reached T+3? = True), "
        f"{matured_principal / principal:.1%} of GBV" if principal else "No loans in view."
    )

    st.markdown("")
    _ue_model_badge("Derived - matured loans only (auto-calculated + manual inputs)", "#2ca02c", "#fff")
    st.caption(
        "Revenue, Origination Income and GBV below are re-summed over matured loans only, to match "
        "Losses' scope - not the full-book totals shown above."
    )
    _render_snapshot_table([
        ("Income (net losses)", fmt(income_net_losses_margin),
         "Matured-only Revenue + Origination Income - Losses, as a % of matured-loan GBV."),
        ("Variable costs", f"${variable_costs_dollar:,.0f}" if pd.notna(variable_costs_dollar) else "n/a",
         "(Upfront + ongoing costs %) x matured-loan GBV."),
        ("Variable costs (% of GBV)", fmt(variable_costs_pct), "Sum of the two cost %s entered above."),
        ("Product contribution", fmt(product_margin),
         "Income (net losses) - Variable costs, as a % of matured-loan GBV - the workbook's "
         "'simple' (non-time-value-of-money) unit economics basis, computed only on the subset "
         "of the book that's actually matured."),
        ("Cost of Finance (% p.a.)", fmt(cost_of_finance_pct),
         "Financing cost + Facility fee + Hedge cost, as entered (per annum)."),
        ("Cost of Finance (over loan life)", fmt(cost_of_finance_over_term),
         f"Cost of Finance (% p.a.) x matured loans' own weighted term ({fmt(matured_term_m, '{:,.1f}')} "
         "months) / 12 - not the full-book Term (m) above, which covers a different, longer-duration "
         "population. Grossed up so it's comparable to Product contribution before being netted below."),
        ("Net Unit Economics (simplified)", fmt(net_ue_simple),
         "Product contribution - Cost of Finance (over loan life). A single-period proxy for the "
         "workbook's cash-flow-basis Net Unit Economics - see the caption below."),
    ])
    st.caption(
        "Not replicated: 'IRR of cash flow' and 'Net Unit Economics (Cash Flow)'. Both need a full "
        "monthly repayment + loss + cost cash-flow schedule (XIRR/XNPV at WACC), which this app "
        "doesn't build - 'Net Unit Economics (simplified)' above is a proxy, not that figure, and "
        "'Net IRR' at the top of this tab is a straight-line approximation, not that schedule either."
    )
    return {
        "revenue": revenue, "origination_income": origination_income,
        "implied_interest": implied_interest, "losses_dollar": losses_dollar,
        "losses_pct_of_gbv": losses_pct_of_gbv, "term_m": term_m,
        "n_matured": len(matured), "n_total": len(df),
        "matured_gbv_pct": matured_principal / principal if principal else float("nan"),
        "income_net_losses": income_net_losses, "income_net_losses_margin": income_net_losses_margin,
        "product_margin": product_margin, "cost_of_finance_pct": cost_of_finance_pct,
        "net_ue_simple": net_ue_simple, "gross_irr": gross_irr, "net_irr": net_irr,
    }


def _coerce_dates(raw: pd.DataFrame, date_fields, dayfirst=False) -> pd.DataFrame:
    for col in date_fields:
        if col in raw.columns:
            raw[col] = _detect_date(raw[col])
    return raw


# Canonical field -> acceptable header aliases, used to auto-normalise a file
# whose column names differ from the template's (strip spaces/punctuation/case
# before matching), so a "clean but differently-worded" upload still maps.
_FIELD_ALIASES = {
    "Loan ID": ["loan id", "loan_id", "account id", "account_id", "id", "loan no", "loan number", "contract no", "contract number"],
    "Disbursement Date": ["disbursement date", "disbursal date", "disbursed date", "funded date", "funding date", "start date", "origination date", "loan date", "issue date"],
    "Expected Completion Date": ["expected completion date", "maturity date", "expected end date", "end date", "expected maturity", "due date"],
    "Principal Value": ["principal value", "principal", "loan amount", "disbursement amount", "principal amount"],
    "Expected Interest": ["expected interest", "interest", "interest amount", "expected interest amount"],
    "Expected Fee": ["expected fee", "fee", "fee amount", "upfront fee", "expected fees"],
    "Total Paid": ["total paid", "amount paid", "total repayments", "payments", "total amount paid"],
    "Total Due": ["total due", "total dues calculated", "total due calculated", "pos", "outstanding", "balance", "amount due"],
    "Loan Status": ["loan status", "status", "loan status label", "account status"],
    "Days Late": ["days late", "days past due", "dpd", "days overdue", "days in arrears"],
    "Term (days)": ["term (days)", "term", "tenor", "tenor (days)", "loan term", "term in days", "loan tenor"],
    "Payment per Period": ["payment per period", "payment", "installment", "instalment", "monthly payment", "periodic payment"],
    "Payment Frequency": ["payment frequency", "frequency", "payment terms", "repayment frequency"],
}

_LENDING_EXTRA_FIELDS = {
    "Begin Date", "Total Dues Calculated", "Delinquent Amount", "Write-off amount",
}


def _norm_key(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _clean_numeric(series) -> pd.Series:
    s = series.astype(str).str.strip()
    s = s.str.replace(r"[\$,£€\s%]", "", regex=True)
    s = s.str.replace(r"\(([^)]*)\)", r"-\1", regex=True)
    s = s.str.replace(",", "", regex=False)
    return s


def _normalize_columns(raw: pd.DataFrame, fields) -> pd.DataFrame:
    reverse = {}
    for canon in fields:
        reverse[_norm_key(canon)] = canon
        for alias in _FIELD_ALIASES.get(canon, []):
            reverse[_norm_key(alias)] = canon
    rename = {}
    claimed = set()
    for col in raw.columns:
        k = _norm_key(col)
        if k in reverse:
            canon = reverse[k]
            # Only the first raw column matching a given canonical field claims
            # it - a second alias for the same field (e.g. "Principal" and
            # "Loan Amount" both meaning "Principal Value") is left renamed,
            # otherwise both would collide into one duplicate-labeled column.
            if canon not in raw.columns and canon not in claimed:
                rename[col] = canon
                claimed.add(canon)
    return raw.rename(columns=rename)


def _format_normalize(raw: pd.DataFrame, date_fields, numeric_fields, model_fields=()) -> pd.DataFrame:
    # model_fields is scoped to the Lending canonical names so aliases that
    # overlap between fields resolve deterministically rather than by set
    # iteration order.
    raw = _normalize_columns(raw, set(date_fields) | set(numeric_fields) | set(model_fields))
    for col in date_fields:
        if col in raw.columns and raw[col].dtype != "datetime64[ns]":
            raw[col] = _detect_date(raw[col])
    for col in numeric_fields:
        if col in raw.columns:
            cleaned = pd.to_numeric(_clean_numeric(raw[col]), errors="coerce")
            if cleaned.notna().sum() >= raw[col].notna().sum() * 0.5:
                raw[col] = cleaned
    # Value-level normalization for any remaining (unmapped/passthrough) columns:
    # coerce to numeric when most non-null values parse as numbers, so columns
    # like "Total EMI" (with $, commas, trailing %) become proper numerics.
    for col in raw.columns:
        if col in date_fields or col in numeric_fields:
            continue
        if pd.api.types.is_numeric_dtype(raw[col]):
            continue
        non_null = raw[col].notna().sum()
        if non_null == 0:
            continue
        cleaned = pd.to_numeric(_clean_numeric(raw[col]), errors="coerce")
        if cleaned.notna().sum() >= non_null * 0.5:
            raw[col] = cleaned
    return raw


DERIVED_OPS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "×": lambda a, b: a * b,
    "÷": lambda a, b: a.divide(b).replace([float("inf"), float("-inf")], pd.NA),
}


def _apply_derived_columns(raw: pd.DataFrame, defs: list) -> pd.DataFrame:
    for d in defs:
        col_a = pd.to_numeric(raw[d["col_a"]], errors="coerce")
        col_b = pd.to_numeric(raw[d["col_b"]], errors="coerce")
        raw[d["name"]] = DERIVED_OPS[d["op"]](col_a, col_b)
    return raw


def _get_deepinfra_api_key():
    try:
        return st.secrets["DEEPINFRA_API_KEY"]
    except Exception:
        return os.environ.get("DEEPINFRA_API_KEY")


def _suggest_derived_column(
    user_request: str, columns: list, domain_hint: str, context: str = ""
) -> dict:
    """Ask DeepSeek V4 Flash (via DeepInfra) to turn a plain-English request into
    a two-column formula using only the columns actually present in the file.
    `context` is optional user-provided documentation about the dataset that the
    model should prefer when interpreting column meaning."""
    api_key = _get_deepinfra_api_key()
    if not api_key:
        raise RuntimeError("No DEEPINFRA_API_KEY found. Add it to Streamlit secrets or the environment.")

    ops = list(DERIVED_OPS.keys())
    system_prompt = app_config.render_prompt(
        app_config.AI_PROMPTS["derived_column_suggestion"],
        domain_hint=domain_hint, ops=", ".join(ops), columns=", ".join(columns),
    ).strip()
    if context.strip():
        system_prompt += app_config.AI_PROMPTS["derived_column_context_suffix"] + context.strip()

    resp = requests.post(
        DEEPINFRA_CHAT_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": DEEPINFRA_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_request},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": app_config.AI["derived_column_max_tokens"],
        },
        timeout=app_config.AI["request_timeout_seconds"],
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    suggestion = json.loads(content)

    missing = [k for k in ("name", "col_a", "op", "col_b", "explanation") if k not in suggestion]
    if missing:
        raise ValueError(f"Model response missing fields: {', '.join(missing)}")
    if suggestion["col_a"] not in columns or suggestion["col_b"] not in columns:
        raise ValueError("Model suggested a column that isn't in your file.")
    if suggestion["op"] not in DERIVED_OPS:
        raise ValueError(f"Model suggested an unsupported operator: {suggestion['op']}")
    return suggestion


# Short human-readable meaning for each Data Input template field, used to prompt
# the auto-fill model so it maps raw file columns to the right template fields.
FIELD_DESCRIPTIONS = {
    # Lending
    "Loan ID": "unique loan/account identifier",
    "Disbursement Date": "date the loan was disbursed/funded",
    "Expected Completion Date": "expected maturity or completion date",
    "Principal Value": "principal amount lent",
    "Expected Interest": "expected interest amount",
    "Expected Fee": "expected fee amount",
    "Total Paid": "total amount the borrower has paid",
    "Total Due": "total amount owed (principal + interest + fees)",
    "Loan Status": "loan/account status label (e.g. active, closed, paid off)",
    "Days Late": "number of days the loan is past due",
}


def _suggest_mapping(columns: list, input_columns: list, context: str = "") -> dict:
    """Ask DeepSeek V4 Flash (via DeepInfra) to guess the best mapping of the
    uploaded file's raw columns to each Data Input template field for the active
    model. Returns {target_field: source_column} using only columns actually
    present; fields with no sensible source are left unmapped (None).
    `context` is optional user-provided documentation about the dataset that the
    model should prefer when a column's meaning isn't obvious."""
    api_key = _get_deepinfra_api_key()
    if not api_key:
        return {}

    fields = "\n".join(
        f"- {target} ({'required' if required else 'optional'}): {FIELD_DESCRIPTIONS.get(target, '')}"
        for target, required in input_columns
    )
    system_prompt = app_config.render_prompt(
        app_config.AI_PROMPTS["mapping_suggestion"],
        columns=", ".join(columns), fields=fields,
    ).strip()
    if context.strip():
        system_prompt += app_config.AI_PROMPTS["mapping_suggestion_context_suffix"] + context.strip()

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
    return mapping


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
        model_name=model_name, filename=filename, facts="\n".join(facts),
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


def _render_variance_escalation(
    detail: "pd.DataFrame", metric_label: str, model_name: str, button_key: str,
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
            st.warning(
                f"AI write-up unavailable ({ai_warning}); sent a plain summary instead."
            )
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
    checks: list, variance_metric_label: str, model_name: str, button_key: str,
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
                    detail, variance_metric_label, model_name, button_key, uploaded_name,
                )


def _add_reference_line(chart, value, label, color="#d62728", x_anchor=None):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return chart
    ref = pd.DataFrame({"v": [value], "label": [label]})
    rule = alt.Chart(ref).mark_rule(color=color, strokeDash=[6, 6]).encode(y=alt.Y("v:Q"))
    if x_anchor is not None:
        ref["xa"] = [x_anchor]
        text = alt.Chart(ref).mark_text(
            color=color, dx=8, dy=-6, align="left", fontSize=11, fontWeight="bold"
        ).encode(x="xa:T", y=alt.Y("v:Q"), text="label:N")
    else:
        text = alt.Chart(ref).mark_text(
            color=color, dy=-7, dx=6, align="left", fontSize=11, fontWeight="bold"
        ).encode(x=alt.value(70), y=alt.Y("v:Q"), text="label:N")
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
            f"Mean: {s.mean():,.2f}\n"
            f"Median: {s.median():,.2f}\n"
            f"Std Dev: {s.std():,.2f}\n"
            f"Min: {s.min():,.2f}\n"
            f"Max: {s.max():,.2f}"
        )
    return (
        f"Count: {non_null.count():,}\n"
        f"Nulls: {nulls:,}\n"
        f"Distinct: {non_null.nunique():,}"
    )


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
_FMT_PCT1_JS = JsCode(
    "params => params.value == null ? '' : (params.value * 100).toFixed(1) + '%'"
)


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
    money_cols = {"Total Principal", "Total Interest", "Total Fee", "Total Due", "Total Paid"}
    term_cols = {"Avg Term (days)", "Weighted Avg Term"}
    pct_cols = {"PvD Ratio", "Loss Rate"}
    column_formatters = {
        **{c: _FMT_MONEY_JS for c in money_cols},
        **{c: _FMT_INT_JS for c in term_cols},
        **{c: _FMT_PCT1_JS for c in pct_cols},
    }
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(
        resizable=True, sortable=True, filter=True, floatingFilter=True,
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
            if series.nunique() > 1 else []
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
        df, gridOptions=grid_options, height=height,
        allow_unsafe_jscode=True, theme="streamlit",
        show_toolbar=False, show_search=False, show_download_button=False,
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
    data_sources: dict, key_prefix: str = "cv", header: bool = True, simple: bool = False,
):
    """Generic ad-hoc chart builder shared by both models. `data_sources` maps
    a display name to the DataFrame it plots from for the active model - the
    first entry is the data source pre-selected when a chart card is first
    added. `key_prefix` namespaces all widget/session-state keys so this can
    be embedded in more than one place (e.g. the Cohorts tab) on the same
    page run without colliding with another instance's state. `simple=True`
    drops the multi-card/Excel-export machinery (per-card header, "add to
    export"/"remove card"/"add another chart" buttons, the export queue) for
    a single-chart embedding like the Cohorts tab's."""
    if header:
        st.subheader("Custom Visualizations")
        st.caption("Build your own charts from the mapped and computed data. Add as many chart cards as you like.")

    cards_state_key = f"{key_prefix}_chart_cards"
    next_id_state_key = f"{key_prefix}_chart_next_id"
    if cards_state_key not in st.session_state:
        st.session_state[cards_state_key] = [0]
        st.session_state[next_id_state_key] = 1

    def _render_chart_card(card_id):
        k = lambda name: f"{key_prefix}_{name}_{card_id}"
        cv1, cv2 = st.columns([1, 1])
        with cv1:
            source_name = st.selectbox("Data source", options=list(data_sources.keys()), key=k("source"))
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
            datetime_cols, ["cohort", "disbursement", "origination", "start"],
            datetime_cols[0] if datetime_cols else all_cols[0],
        )
        y_options = numeric_cols if numeric_cols else all_cols
        default_y = _pick(numeric_cols, ["principal"], y_options[0])
        chart_options = ["Line", "Bar", "Scatter", "Area"]
        default_kind = "Bar" if default_x in datetime_cols and default_y in numeric_cols else "Line"

        with cv2:
            chart_kind = st.selectbox("Chart type", options=chart_options, index=chart_options.index(default_kind), key=k("kind"))

        cv3, cv4, cv5, cv6 = st.columns(4)
        with cv3:
            x_col = st.selectbox("X axis", options=all_cols, index=all_cols.index(default_x), key=k("x"))
        with cv4:
            y_col = st.selectbox("Y axis", options=y_options, index=y_options.index(default_y), key=k("y"))
        with cv5:
            color_col = st.selectbox("Group / color by (optional)", options=["(none)"] + [c for c in categorical_cols if c != x_col], key=k("color"))
        with cv6:
            # A secondary metric line (e.g. Loss Rate alongside a Principal
            # bar chart) reads its own axis on the right - more useful here
            # than a Sum/Mean/etc. picker, which was a no-op on data that's
            # already one row per X (like the Cohorts table: aggregating a
            # single value any way you slice it just returns that value).
            line_options = ["(none)"] + [c for c in numeric_cols if c not in (x_col, y_col)]
            default_line = "Loss Rate" if "Loss Rate" in line_options else "(none)"
            line_choice = st.selectbox(
                "Add a line (optional)", options=line_options,
                index=line_options.index(default_line), key=k("line"),
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
            line_chart = alt.Chart(line_df).mark_line(point=True, color="#E8632A", strokeWidth=2.5).encode(
                x=alt.X(f"{x_col}:{x_type}", axis=x_axis),
                y=alt.Y(f"{line_col}:Q", title=line_col, axis=line_axis),
                tooltip=[x_col, alt.Tooltip(f"{line_col}:Q", title=line_col, format=line_tooltip_format)],
            )
            custom_chart = alt.layer(primary_chart, line_chart).resolve_scale(y="independent").properties(
                title=chart_title, height=400,
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
                export_charts.append({
                    "title": chart_title, "kind": chart_kind, "x_col": x_col, "y_col": y_col,
                    "color_col": None if color_col == "(none)" else color_col, "y_title": y_col,
                    "data": plot_df[[c for c in {x_col, y_col, color_col} if c in plot_df.columns]].copy(),
                })
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


def _ask_data_chatbot(question: str, context: str, history: list) -> str:
    """Ask DeepSeek V4 Flash (via DeepInfra) a question about the current
    analysis. `context` is a text summary of already-computed portfolio/cohort
    statistics and calculation methodology - never the raw loan-level rows, so
    no per-loan data leaves the app for this feature."""
    api_key = _get_deepinfra_api_key()
    if not api_key:
        raise RuntimeError("No DEEPINFRA_API_KEY found. Add it to Streamlit secrets or the environment.")

    system_prompt = app_config.AI_PROMPTS["chat_assistant"].strip() + "\n\n" + context
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": question})

    resp = requests.post(
        DEEPINFRA_CHAT_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": DEEPINFRA_MODEL, "messages": messages, "max_tokens": app_config.AI["chat_assistant_max_tokens"]},
        timeout=app_config.AI["request_timeout_seconds"],
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _build_lending_chat_context(
    gi, df: pd.DataFrame, cohorts: pd.DataFrame, ue_data: dict, ltv_data: dict,
    ue_model_ai_data: dict, dq_checks: list, user_context: str = "",
) -> str:
    lines = [
        "## Lending portfolio - summary statistics",
        f"Loans: {len(df):,}. Mapped/computed columns: {', '.join(map(str, df.columns))}.",
        f"Data extraction date: {gi.extraction_date.date()}. Days after term (T+3 cutoff): {gi.days_after_term}.",
        f"Minimum loans per cohort (template threshold): {gi.min_loans_per_cohort}.",
        f"Value of Principal Disbursed: ${df['Principal Value'].sum():,.0f}.",
        f"Total Collected: ${df['Total Paid'].sum():,.0f}.",
        "",
        "## Overview metrics",
        f"Average Loss (owed vs paid, matured loans, Reached T+3?=True): {fmt(ue_data['Average Loss'])}.",
        f"Loss Rate Proxy (1-PvD, needs Total Due mapped): {fmt(ue_data['Loss Rate Proxy (1-PvD)'])}.",
        f"Average Principal Amount per loan: ${ue_data['Average Principal Amount']:,.0f}.",
        f"Average Fee %: {fmt(ue_data['Average Fee %'])}. Average Interest %: {fmt(ue_data['Average Interest %'])}.",
        f"Average Expected Term: {fmt(ue_data['Average Expected Term'], '{:,.1f}')} days.",
        f"Sense-check Margin (Interest%+Fee%, netted against Average Loss - mixes full-book revenue "
        "with matured-only losses, a known approximation): " + fmt(ue_data["Sense-check Margin"]),
        f"95th Percentile Loss Rate across cohorts (Loan Count >= {gi.min_loans_per_cohort}): "
        + fmt(ltv_data["95th Percentile Losses"]),
        f"Average Total Revenue % (interest+fees / principal): {fmt(ltv_data['Average Total Revenue %'])}.",
        "",
        "## Cohorts",
        f"{len(cohorts)} monthly cohorts by Disbursement Date with at least one matured loan "
        "(Reached T+3? = True) - a cohort with none doesn't appear at all, matching the Excel "
        "template's Cohorts pivot, which has the same Reached T+3? filter. Loan Count and Matured "
        f"Count are therefore the same figure. Columns: {', '.join(cohorts.columns)}.",
        "Loss Rate per cohort is NaN when its Matured Count is below the minimum-loans threshold "
        "(too few matured loans to be statistically meaningful).",
        cohorts.to_csv(index=False),
        "",
        "## UE Model (AI) tab - matured-loans-only unit economics",
        "This tab replicates the master UE Model workbook's 'For AI' sheet. Figures below with "
        "'(full book)' cover all loans; figures under 'Derived' are recomputed on ONLY the matured "
        "subset (Reached T+3?=True) for revenue, origination income, losses AND the GBV denominator, "
        "so margins reflect a complete, at-maturity outcome rather than mixing unrealized revenue "
        "against a small realized-loss sample.",
        f"Revenue (interest, full book): ${ue_model_ai_data['revenue']:,.0f}. "
        f"Origination Income (full book): ${ue_model_ai_data['origination_income']:,.0f}.",
        f"Implied Interest (annualized nominal APR, amortization-solved, full book): "
        + fmt(ue_model_ai_data["implied_interest"]),
        f"Losses (matured-only $, owed-paid): ${ue_model_ai_data['losses_dollar']:,.0f}. "
        f"Losses as a literal % of total book GBV: {fmt(ue_model_ai_data['losses_pct_of_gbv'])}.",
        f"Term (m, full-book weighted average): {fmt(ue_model_ai_data['term_m'], '{:,.1f}')}.",
        f"Matured coverage: {ue_model_ai_data['n_matured']:,} of {ue_model_ai_data['n_total']:,} loans "
        f"({fmt(ue_model_ai_data['matured_gbv_pct'])} of GBV) have matured.",
        f"Derived, matured-only: Income (net losses) = ${ue_model_ai_data['income_net_losses']:,.0f} "
        f"({fmt(ue_model_ai_data['income_net_losses_margin'])} of matured-loan GBV).",
        f"Derived, matured-only: Product Margin (after any user-entered variable costs) = "
        + fmt(ue_model_ai_data["product_margin"]),
        f"User-entered Cost of Finance (% p.a., 0 unless the user filled it in): "
        + fmt(ue_model_ai_data["cost_of_finance_pct"]),
        f"Net Unit Economics (simplified proxy, NOT a true XIRR/XNPV cash-flow figure): "
        + fmt(ue_model_ai_data["net_ue_simple"]),
        "'IRR of cash flow' and the workbook's true cash-flow-basis 'Net Unit Economics' are NOT "
        "computed by this app - they'd need a full monthly repayment/loss/cost schedule.",
    ]
    if user_context.strip():
        lines += ["", "## User-provided context about this dataset", user_context.strip()]
    if dq_checks:
        lines += ["", "## Data quality checks flagged"]
        lines += [f"- ({c.get('level', 'info')}) {c.get('message', '')}" for c in dq_checks]
    return "\n".join(lines)


def _render_ai_chat_tab(context: str) -> None:
    st.subheader("Ask AI")
    st.caption(
        "Ask about the metrics and calculations on the other tabs. The assistant only sees the "
        "aggregated summary statistics below (cohort tables and portfolio-level figures) - never "
        "individual loan rows - so it can't answer questions about a specific loan ID."
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
                    answer = _ask_data_chatbot(question, context, st.session_state["ai_chat_history"][:-1])
            except Exception as e:
                answer = f"Unable to get a response: {e}"
            st.markdown(answer)
        st.session_state["ai_chat_history"].append({"role": "assistant", "content": answer})


def _write_custom_charts_sheet(writer, export_charts):
    """Shared by both export functions - identical chart-embedding logic,
    independent of which model produced the queued chart definitions."""
    if not export_charts:
        return
    cc_ws = writer.book.create_sheet("Custom Charts")
    row = 1
    for ec in export_charts:
        if ec["color_col"]:
            wide = ec["data"].pivot_table(index=ec["x_col"], columns=ec["color_col"], values=ec["y_col"], aggfunc="first").reset_index()
        else:
            wide = ec["data"][[ec["x_col"], ec["y_col"]]]
        header_row = row
        wide.to_excel(writer, sheet_name="Custom Charts", startrow=row - 1, index=False)
        data_start = header_row + 1
        data_end = header_row + len(wide)
        n_series_cols = len(wide.columns) - 1
        chart_map = {"Line": LineChart, "Bar": BarChart, "Area": AreaChart}
        if ec["kind"] in chart_map:
            c = chart_map[ec["kind"]]()
            c.title = ec["title"]
            c.height, c.width = 10, 20
            data = Reference(cc_ws, min_col=2, min_row=header_row, max_col=1 + n_series_cols, max_row=data_end)
            cats = Reference(cc_ws, min_col=1, min_row=data_start, max_row=data_end)
            c.add_data(data, titles_from_data=True)
            c.set_categories(cats)
            cc_ws.add_chart(c, f"{get_column_letter(n_series_cols + 4)}{header_row}")
        else:
            sc = ScatterChart()
            sc.title = ec["title"]
            sc.height, sc.width = 10, 20
            sc.x_axis.title = ec["x_col"]
            sc.y_axis.title = ec["y_title"]
            xvalues = Reference(cc_ws, min_col=1, min_row=data_start, max_row=data_end)
            for col_idx in range(2, 2 + n_series_cols):
                yvalues = Reference(cc_ws, min_col=col_idx, min_row=header_row, max_row=data_end)
                series = Series(yvalues, xvalues, title_from_data=True)
                series.marker.symbol = "circle"
                series.graphicalProperties.line.noFill = True
                sc.series.append(series)
            cc_ws.add_chart(sc, f"{get_column_letter(n_series_cols + 4)}{header_row}")
        row = data_end + 3


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
    Sheets link exports CSV (honouring a #gid= tab in the URL); a generic Drive
    file link is fetched as-is and sniffed for CSV vs Excel."""
    url = url.strip()
    doc_id = _extract_google_id(url)
    if "spreadsheets" in url:
        export = f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=csv"
        gid = re.search(r"[#&]gid=(\d+)", url)
        if gid:
            export += f"&gid={gid.group(1)}"
        resp = requests.get(export, timeout=30, verify=_ca_bundle_path())
        if resp.status_code != 200:
            raise RuntimeError(
                f"Google returned HTTP {resp.status_code} - the sheet must be shared with "
                "'Anyone with the link can view' (File > Share > General access)."
            )
        data = resp.content
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        return data, "google_sheet.csv"
    resp = requests.get(
        f"https://drive.google.com/uc?export=download&id={doc_id}",
        timeout=30, verify=_ca_bundle_path(),
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Google Drive returned HTTP {resp.status_code} - the file must be shared with "
            "'Anyone with the link can view'."
        )
    data = resp.content
    name = "google_file.xlsx" if data[:4] == b"PK\x03\x04" else "google_file.csv"
    return data, name


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
# OAuth callback lands back on the app itself (the redirect URI registered in the
# Google Cloud console) with ?code=...&state=... in the query string. For local
# runs that's http://localhost:8501; override via GOOGLE_REDIRECT_URI for cloud.
GDRIVE_TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".google_drive_token.json")


def _get_google_client_id():
    try:
        return st.secrets.get("GOOGLE_CLIENT_ID")
    except Exception:
        return os.environ.get("GOOGLE_CLIENT_ID")


def _get_google_client_secret():
    try:
        return st.secrets.get("GOOGLE_CLIENT_SECRET")
    except Exception:
        return os.environ.get("GOOGLE_CLIENT_SECRET")


def _google_redirect_uri() -> str:
    uri = None
    try:
        uri = st.secrets.get("GOOGLE_REDIRECT_URI")
    except Exception:
        pass
    uri = uri or os.environ.get("GOOGLE_REDIRECT_URI")
    return uri or "http://localhost:8501"


def _load_gdrive_token_file() -> dict | None:
    try:
        with open(GDRIVE_TOKEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_gdrive_token_file(token: dict) -> None:
    try:
        with open(GDRIVE_TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(token, f, indent=2)
    except OSError:
        pass


def _google_auth_url(state: str) -> str:
    return (
        f"{GOOGLE_AUTH_URL}?client_id={quote(_get_google_client_id())}"
        f"&redirect_uri={quote(_google_redirect_uri())}"
        f"&response_type=code&scope={quote(GOOGLE_DRIVE_SCOPE)}"
        f"&access_type=offline&prompt=consent&state={quote(state)}"
    )


def _exchange_google_code(code: str) -> dict:
    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": _get_google_client_id(),
            "client_secret": _get_google_client_secret(),
            "redirect_uri": _google_redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=30,
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    token = resp.json()
    token["expires_at"] = time.time() + int(token.get("expires_in", 3600))
    return token


def _refresh_google_token(refresh_token: str) -> dict:
    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": _get_google_client_id(),
            "client_secret": _get_google_client_secret(),
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    token = resp.json()
    token["expires_at"] = time.time() + int(token.get("expires_in", 3600))
    return token


def _gdrive_token() -> dict | None:
    token = st.session_state.get("gdrive_token")
    if not token:
        token = _load_gdrive_token_file()
        if token:
            st.session_state["gdrive_token"] = token
    return token


def _gdrive_access_token(token: dict) -> str:
    if (
        time.time() >= token.get("expires_at", 0) - 60
        and token.get("refresh_token")
    ):
        refreshed = _refresh_google_token(token["refresh_token"])
        refreshed["refresh_token"] = token["refresh_token"]
        st.session_state["gdrive_token"] = refreshed
        _save_gdrive_token_file(refreshed)
        return refreshed["access_token"]
    return token["access_token"]


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
    """Download a Drive file (or export a Google Sheet as CSV), returning
    (bytes, suggested filename)."""
    mid = drive_file["mimeType"]
    name = drive_file["name"]
    if mid == "application/vnd.google-apps.spreadsheet":
        url = f"{GOOGLE_DRIVE_FILES_URL}/{drive_file['id']}/export?mimeType=text/csv"
        if not name.lower().endswith(".csv"):
            name += ".csv"
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
        code = st.query_params.get("code")
        state = st.query_params.get("state")
        if code and state and state == st.session_state.get("gdrive_state"):
            try:
                with st.spinner("Connecting to Google Drive..."):
                    token = _exchange_google_code(code)
            except Exception as e:
                st.error(f"Couldn't complete Google sign-in: {e}")
                st.query_params.clear()
                st.stop()
                return None
            st.query_params.clear()
            if token.get("refresh_token"):
                _save_gdrive_token_file(token)
            st.session_state["gdrive_token"] = token
            st.rerun()
            return None
        state_token = secrets.token_urlsafe(16)
        st.session_state["gdrive_state"] = state_token
        st.markdown("**Pick a file from your Google Drive**")
        st.caption(
            "Connect your Google account to browse and load files directly from your "
            "Drive. Only read access is requested; your files stay in your account."
        )
        st.link_button("Connect Google Drive", _google_auth_url(state_token))
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


def _read_tabular_file(uploaded) -> pd.DataFrame:
    """Read an uploaded CSV/XLSX, robust to a workbook with more than one
    sheet (defaults to reading the first, but lets the user pick) and to a
    header row that isn't row 0 - e.g. a title/banner row above the real
    column headers, which would otherwise silently produce "Unnamed: N"
    columns instead of an error."""
    if uploaded.name.endswith(".csv"):
        with st.spinner("Reading file..."):
            return pd.read_csv(uploaded)

    with st.spinner("Reading file..."):
        xls = pd.ExcelFile(uploaded)
        sheet_name = xls.sheet_names[0]
    if len(xls.sheet_names) > 1:
        sheet_name = st.selectbox(
            "This file has multiple sheets - which one has your loan-level data?",
            options=xls.sheet_names, key="upload_sheet_name",
        )

    with st.spinner("Reading file..."):
        df = xls.parse(sheet_name)
        unnamed_frac = sum(str(c).startswith("Unnamed:") for c in df.columns) / max(len(df.columns), 1)
        if unnamed_frac >= 0.5:
            preview = xls.parse(sheet_name, header=None, nrows=10)
            for i in range(1, len(preview)):
                row = preview.iloc[i]
                if row.notna().mean() > 0.7 and row.dropna().map(lambda v: isinstance(v, str)).mean() > 0.7:
                    df = xls.parse(sheet_name, header=i)
                    break
    return df


def _detect_restored_session(file_bytes: bytes, name: str) -> dict | None:
    """If `file_bytes` is a workbook previously produced by 'Download full
    workbook as Excel' (i.e. it carries the hidden '_App Session' sheet
    written by _build_lending_export_workbook), return its embedded mapping/
    currency/GI-overrides/derived-columns config so the upload flow can
    pre-fill the mapping form to reproduce that run. Returns None for any
    other file, or if the sheet is present but unreadable - this must never
    block a normal upload."""
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


data_source = st.radio(
    "Data source",
    ["Local file", "Google Sheets / Drive link", "My Google Drive"],
    key="data_source",
    horizontal=True,
)

uploaded = None
file_id = None
file_name = None
gdrive_pick = None
if data_source == "Local file":
    uploaded = st.file_uploader(
        "Choose a CSV or Excel file", type=["csv", "xlsx"], key="uploader"
    )
    if not uploaded:
        st.info("Upload a contract-level file to begin.")
        st.stop()
    file_id = uploaded.file_id
    file_name = uploaded.name
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
else:
    gdrive_pick = _render_gdrive_picker()
    if gdrive_pick is None:
        st.stop()
    file_id = gdrive_pick["file_id"]
    file_name = gdrive_pick["file_name"]

if st.session_state.get("uploaded_file_id") != file_id:
    st.session_state["uploaded_file_id"] = file_id
    st.session_state["analysis_ran"] = False
    st.session_state.pop("analysis_mapping", None)
    st.session_state.pop("analysis_gi_overrides", None)
    st.session_state.pop("analysis_context", None)
    st.session_state.pop("context_files", None)
    st.session_state.pop("context_documents", None)
    st.session_state.pop("context_doc_ids", None)
    st.session_state["derived_columns"] = []
    for target, _ in INPUT_COLUMNS:
        st.session_state.pop(f"map_{target}", None)

try:
    if data_source == "Local file":
        raw = _read_tabular_file(uploaded)
    elif data_source == "Google Sheets / Drive link":
        if st.session_state.get("gs_download_id") != file_id:
            with st.spinner("Loading Google Sheet..."):
                gs_bytes, gs_name = _download_google_sheet(gs_url)
            st.session_state["gs_download_id"] = file_id
            st.session_state["gs_bytes"] = gs_bytes
            st.session_state["gs_name"] = gs_name
        raw = _read_tabular_file(_NamedBytes(st.session_state["gs_bytes"], st.session_state["gs_name"]))
    else:
        raw = _read_tabular_file(_NamedBytes(gdrive_pick["data"], gdrive_pick["name"]))
except Exception as e:
    st.error(f"Couldn't read '{file_name}': {e}. Check that the file is a valid, non-empty CSV or Excel.")
    st.stop()
if raw.empty or not len(raw.columns):
    st.error(f"'{file_name}' has no data to read.")
    st.stop()

if st.session_state.get("restored_session_checked_id") != file_id:
    st.session_state["restored_session_checked_id"] = file_id
    if data_source == "Local file":
        _source_bytes = _read_uploaded_bytes(uploaded)
    elif data_source == "Google Sheets / Drive link":
        _source_bytes = st.session_state.get("gs_bytes")
    else:
        _source_bytes = gdrive_pick["data"] if gdrive_pick else None
    restored = _detect_restored_session(_source_bytes, file_name) if _source_bytes else None
    st.session_state["restored_session"] = restored
    if restored and restored.get("derived_columns"):
        st.session_state["derived_columns"] = restored["derived_columns"]

_active_model_fields = {c for c, _ in INPUT_COLUMNS} | _LENDING_EXTRA_FIELDS
raw = _format_normalize(raw, DATE_FIELDS, NUMERIC_FIELDS, _active_model_fields)

st.success(f"Loaded {len(raw):,} rows - {len(raw.columns)} columns.")
st.dataframe(raw.head(10), width="stretch", height=300)
st.caption("Columns in your file: " + ", ".join(map(str, raw.columns)))

st.subheader("Additional context for the AI")
st.caption(
    "Optional documentation about this dataset. The AI uses it to answer questions about "
    "the analysis, to auto-fill the column mapping, and to suggest derived columns - for "
    "example the loan product and currency, what each field means, or how interest and "
    "fees are charged. You can type notes and/or upload supporting documents (PDF, Excel, "
    "Word, text). Editing either re-runs the AI column-mapping suggestion."
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

if "derived_columns" not in st.session_state:
    st.session_state["derived_columns"] = []

with st.expander("Derive a missing column from existing fields"):
    st.caption(active_cfg["derived_hint"])
    st.markdown("**Describe the calculation and let AI suggest the formula.**")
    if st.session_state.pop("_clear_dc_ai_request", False):
        st.session_state["dc_ai_request"] = ""
    ai1, ai2 = st.columns([4, 1])
    with ai1:
        ai_request = st.text_area(
            "Describe the calculation",
            placeholder=active_cfg["derived_placeholder"],
            key="dc_ai_request", height=70,
        )
    with ai2:
        st.write("")
        ask_ai = st.button("Suggest formula", key="dc_ai_ask")

    if ask_ai:
        if not ai_request.strip():
            st.warning("Describe the calculation before requesting a suggestion.")
        else:
            try:
                with st.spinner("Asking DeepSeek..."):
                    st.session_state["dc_ai_suggestion"] = _suggest_derived_column(
                        ai_request, list(raw.columns), active_cfg["domain_hint"],
                        _ai_context_text(max_chars=20000, per_doc=6000),
                    )
            except Exception as e:
                st.session_state["dc_ai_suggestion"] = None
                st.error(f"Unable to generate a suggestion: {e}")

    suggestion = st.session_state.get("dc_ai_suggestion")
    if suggestion:
        st.info(
            f"**Suggested:** {suggestion['name']} = {suggestion['col_a']} "
            f"{suggestion['op']} {suggestion['col_b']}\n\n{suggestion['explanation']}"
        )
        if st.button("Use this suggestion", key="dc_ai_use"):
            name = suggestion["name"].strip()
            existing_names = {d["name"] for d in st.session_state["derived_columns"]}
            if name in raw.columns or name in existing_names:
                base, i = name, 2
                while f"{base} ({i})" in raw.columns or f"{base} ({i})" in existing_names:
                    i += 1
                name = f"{base} ({i})"
            st.session_state["derived_columns"].append({
                "name": name, "col_a": suggestion["col_a"], "op": suggestion["op"], "col_b": suggestion["col_b"],
            })
            st.session_state["dc_ai_suggestion"] = None
            st.session_state["_clear_dc_ai_request"] = True
            st.rerun()

    st.markdown("**Or build it manually:**")
    dc1, dc2, dc3, dc4 = st.columns([2, 2, 1, 2])
    with dc1:
        new_name = st.text_input("New column name", key="dc_name")
    with dc2:
        col_a = st.selectbox("Column A", options=list(raw.columns), key="dc_col_a")
    with dc3:
        op = st.selectbox("Operator", options=list(DERIVED_OPS.keys()), key="dc_op")
    with dc4:
        col_b = st.selectbox("Column B", options=list(raw.columns), key="dc_col_b")

    if st.button("Add derived column", key="dc_add"):
        existing_names = {d["name"] for d in st.session_state["derived_columns"]}
        if not new_name.strip():
            st.error("Give the derived column a name.")
        elif new_name in raw.columns or new_name in existing_names:
            st.error(f"'{new_name}' already exists - choose a different name.")
        else:
            st.session_state["derived_columns"].append({"name": new_name.strip(), "col_a": col_a, "op": op, "col_b": col_b})
            st.rerun()

    if st.session_state["derived_columns"]:
        st.markdown("**Derived columns:**")
        for i, d in enumerate(st.session_state["derived_columns"]):
            rc1, rc2 = st.columns([5, 1])
            rc1.write(f"`{d['name']}` = {d['col_a']} {d['op']} {d['col_b']}")
            if rc2.button("Remove", key=f"dc_remove_{i}"):
                st.session_state["derived_columns"].pop(i)
                st.rerun()

raw = _apply_derived_columns(raw, st.session_state["derived_columns"])
if st.session_state["derived_columns"]:
    st.dataframe(raw[[d["name"] for d in st.session_state["derived_columns"]]].head(10), width="stretch", height=150)

cached_mapping = _load_cache(MAPPING_CACHE_PATH, _cache_key(raw.columns)) or {}
if cached_mapping:
    st.caption("A saved mapping was found for a file with these same column headers - pre-filled below.")

# AI auto-fill: guess the best column mapping for this file's headers (per model).
guess_key = f"ai_mapping_guess_{_cache_key(raw.columns)}"
if guess_key not in st.session_state:
    if _get_deepinfra_api_key():
        try:
            with st.spinner("Asking the AI to auto-fill the column mapping..."):
                st.session_state[guess_key] = _suggest_mapping(
                    list(raw.columns), INPUT_COLUMNS,
                    _ai_context_text(max_chars=20000, per_doc=6000),
                )
        except Exception as e:
            st.session_state[guess_key] = {}
            st.warning(f"AI auto-fill wasn't available ({e}). You can still map columns manually.")
    else:
        st.session_state[guess_key] = {}
ai_guess = st.session_state.get(guess_key) or {}
restored_session = st.session_state.get("restored_session")
restored_gi = (restored_session or {}).get("gi_overrides", {})
if restored_session:
    st.info(
        "This file was exported from a previous session - mapping and inputs below have "
        "been pre-filled to match it. Review and click 'Run analysis' to reproduce it."
    )

with st.form("column_mapping"):
    st.subheader("Map your columns to the Data Input template")
    st.caption("For each template field below, select the matching column in your file. Required fields must be mapped to run the analysis.")
    used = set()
    mapping = {}
    fillna_zero = {}
    for target, required in INPUT_COLUMNS:
        options = ["(not provided)"] + [c for c in raw.columns if c not in used]
        # Precedence: restored session (re-uploaded export) > saved mapping > AI guess > "(not provided)".
        default_choice = (
            (restored_session or {}).get("mapping", {}).get(target)
            or cached_mapping.get(target) or ai_guess.get(target)
        )
        default_index = options.index(default_choice) if default_choice in options else 0
        chosen = st.selectbox(
            f"Map to **{target}** ({'required' if required else 'optional'})",
            options=options, index=default_index, key=f"map_{target}",
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
                key=f"fillna0_{target}", value=False,
            )
            if fillna_zero[target] and n_missing:
                st.caption(f"{n_missing:,} row(s) with a blank '{chosen}' will be treated as 0.")

    for target, checked in fillna_zero.items():
        if checked and mapping.get(target):
            raw[mapping[target]] = raw[mapping[target]].fillna(0)

    st.subheader("Currency")
    _currency_options = ["USD"] + sorted(c for c in CURRENCY_NAMES if c != "USD")
    _restored_currency = (restored_session or {}).get("currency")
    _currency_index = _currency_options.index(_restored_currency) if _restored_currency in _currency_options else 0
    portfolio_currency = st.selectbox(
        "Portfolio currency",
        options=_currency_options,
        format_func=lambda c: f"{c} - {CURRENCY_NAMES[c]}",
        index=_currency_index,
        help="The currency the mapped monetary columns (Principal Value, Expected Interest, "
             "Expected Fee, Total Paid, Total Due) are denominated in. If not USD, every one of "
             "those columns is converted to USD using a live exchange rate before analysis runs.",
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
            "Date of extraction", value=default_extraction.date(),
            help="The max date in the loan tape. Used to identify which loans have reached their maturity.",
        )
    with gc2:
        days_after_term = st.number_input(
            "Days after term",
            value=restored_gi.get("days_after_term", app_config.GENERAL_INPUTS_DEFAULTS["days_after_term"]),
            min_value=0,
            help="Days after term used to compute loss rate (default 90 = Term + 3 months). "
                 "Modify only if the company has significant repayments after 3 months from term.",
        )
    with gc3:
        min_loans_per_cohort = st.number_input(
            "Minimum loans per cohort",
            value=restored_gi.get("min_loans_per_cohort", app_config.GENERAL_INPUTS_DEFAULTS["min_loans_per_cohort"]),
            min_value=0,
            help="Affects the cohort stressed loss rate: requires a minimum number of observations to "
                 "include a cohort in the cohort loss rate distribution (default 10).",
        )

    submitted = st.form_submit_button("Run analysis")

if submitted:
    missing_required = [t for t, required in INPUT_COLUMNS if required and not mapping[t]]
    if missing_required:
        st.error("Map these required fields before running: " + ", ".join(missing_required))
        st.stop()
    mapping_errors, mapping_warnings = _validate_mapping(
        raw, mapping, REQUIRED_FIELDS, DATE_FIELDS, NUMERIC_FIELDS, active_cfg["dayfirst"]
    )
    if mapping_errors:
        st.error("Fix these mappings before running:\n\n" + "\n".join(f"- {e}" for e in mapping_errors))
        st.stop()
    st.session_state["analysis_mapping"] = mapping
    st.session_state["analysis_mapping_warnings"] = mapping_warnings
    st.session_state["analysis_currency"] = portfolio_currency
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
    st.session_state["analysis_gi_overrides"] = overrides
    st.session_state["analysis_ran"] = True
    _save_cache(MAPPING_CACHE_PATH, _cache_key(raw.columns), mapping)

if not st.session_state.get("analysis_ran"):
    st.info("Map your file's columns above, then click 'Run analysis' to compute all sheets.")
    st.stop()

mapping = st.session_state["analysis_mapping"]
rename_map = {src: tgt for tgt, src in mapping.items() if src}
# If the raw file already has a column literally named like a mapping target
# (e.g. a leftover "Total Due" column that isn't the one the user actually
# mapped), renaming the chosen source into that name would leave two columns
# sharing the same label - raw[col] then returns a DataFrame instead of a
# Series and every .dtype/coercion call below breaks. Drop the stale column
# first so the mapped source always wins.
stale_target_cols = [tgt for tgt in rename_map.values() if tgt in raw.columns and tgt not in rename_map]
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
        st.warning(f"{fx_error} - figures below are still in {portfolio_currency}, not converted.")

unmapped = [t for t, _ in INPUT_COLUMNS if t not in raw.columns]
if unmapped:
    st.warning("Not mapped (optional) - related metrics will be unavailable: " + ", ".join(unmapped))

mapping_warnings = st.session_state.get("analysis_mapping_warnings") or []
if mapping_warnings:
    st.warning("Mapping quality warnings:\n\n" + "\n".join(f"- {w}" for w in mapping_warnings))

if st.session_state.get("dedupe_loan_ids") and active_cfg["id_field"] in raw.columns:
    raw = raw.drop_duplicates(subset=active_cfg["id_field"], keep="first")

gi_overrides = dict(st.session_state["analysis_gi_overrides"])
gi = LendingGeneralInputs(raw, **gi_overrides)

with st.spinner("Running analysis..."):
    term_supplied = "Term (days)" in raw.columns
    df = lending_process_data_input(raw, gi.extraction_date, gi.days_after_term)
    cohorts = lending_build_cohorts(df, min_matured=gi.min_loans_per_cohort)
    filtered = lending_filter_cohorts(cohorts, gi.min_loans_per_cohort)
    st.caption(f"Mapped & computed columns: {', '.join(df.columns)}")
    if "Total Due" not in df.columns:
        st.warning("No 'Total Due' or payment schedule mapped - loss rate proxy and PvD ratio will be unavailable.")
    ltv = LendingLtvAnalysis(df, filtered)
    ue = LendingUeAnalysis(df)
    ue_data, ltv_data = ue.as_dict(), ltv.as_dict()
    lending_chart_data = cohorts.dropna(subset=["Loss Rate"]).copy()
    if not lending_chart_data.empty:
        lending_chart_data["Fee %"] = lending_chart_data["Total Fee"] / lending_chart_data["Total Principal"]
        lending_chart_data["Interest %"] = lending_chart_data["Total Interest"] / lending_chart_data["Total Principal"]

dq_checks = _lending_data_quality_checks(raw, cohorts, gi, filtered)

tab_names = [
    "Summary", "Data Checks",
    "Cohorts", "LTV Analysis",
    "Unit Economics Analysis",
    "Custom Visualizations", "Ask AI",
]
tabs = st.tabs(tab_names)

with tabs[0]:
    st.subheader("Summary")
    if "Loan Status" in df.columns:
        status = df["Loan Status"].astype(str).str.strip().str.lower()
        gbv_df = df[status == "active"]
    else:
        gbv_df = df
    if gbv_df.empty:
        outstanding_gbv = None
    else:
        principal = gbv_df["Principal Value"].sum()
        interest = gbv_df["Expected Interest"].sum()
        fees = gbv_df["Expected Fee"].sum()
        paid = gbv_df["Total Paid"].sum()
        outstanding_gbv = principal + interest + fees - paid
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "Data up to", str(gi.extraction_date.date()),
        help="As-of date of the loan tape (the most recent disbursement date in the data).",
    )
    c2.metric(
        "Outstanding GBV",
        f"{outstanding_gbv:,.0f}" if outstanding_gbv is not None else "n/a",
        help="Principal + expected interest + expected fees - amount paid, "
             "for loans with status 'active' only. n/a when no active loans.",
    )
    c3.metric(
        "Loss Rate", fmt(ue_data["Average Loss"]),
        help="Average loss rate across matured loans: (owed - paid) / owed.",
    )
    c4.metric(
        "95th %ile Loss Rate", fmt(ltv_data["95th Percentile Losses"]),
        help="The 95th percentile of cohort loss rates - a stressed view of how "
             "bad loss could get for the worst cohorts.",
    )
    c5.metric(
        "Interest Rate", fmt(ue_data["Average Interest %"]),
        help="Total expected interest as a share of total principal across all loans.",
    )

    st.markdown("---")
    snapshot_col, chart_col = st.columns([1, 1.3])
    with snapshot_col:
        st.subheader("Portfolio Snapshot")
        date_range = (
            f"{df['Disbursement Date'].min().date()} to {gi.extraction_date.date()}"
        )
        snapshot_rows = [
            ("Date Range", date_range, "From the earliest disbursement date to the tape as-of date."),
            ("Loans", f"{len(df):,}", "Total number of loan records in the tape."),
            ("Value of Principal Disbursed", f"${df['Principal Value'].sum():,.0f}",
             "Total principal amount lent across all loans."),
            ("Total Collected", f"${df['Total Paid'].sum():,.0f}",
             "Total cash collected from borrowers to date."),
            ("Avg Fee", fmt(ue_data["Average Fee %"]),
             "Total expected fees as a share of total principal."),
            ("Avg Loan Value", f"${ue_data['Average Principal Amount']:,.0f}",
             "Average principal amount per loan."),
            ("Avg Loan Term", fmt(ue_data["Average Expected Term"], "{:,.1f} days"),
             "Principal-weighted average loan term (days)."),
            ("Avg Total Revenue", fmt(ltv_data["Average Total Revenue %"]),
             "Expected interest + fees as a share of total principal."),
        ]
        _render_snapshot_table(snapshot_rows)

    with chart_col:
        st.subheader("Cohort Loss Rates")
        summary_cohorts = lending_build_cohorts(
            df, min_matured=gi.min_loans_per_cohort, matured_only=False
        )
        bars = alt.Chart(summary_cohorts).mark_bar(color="#B8BEC6").encode(
            x=alt.X("Cohort:T", title="Cohort"),
            y=alt.Y(
                "Total Principal:Q", title="Principal Disbursed",
                # "~s" gives short SI-style labels (k/M/G/T); swap D3's "G" (giga)
                # for the more finance-familiar "B" (billion) - same magnitude, friendlier label.
                axis=alt.Axis(format="~s", labelExpr="replace(datum.label, 'G', 'B')"),
            ),
            tooltip=[
                alt.Tooltip("Cohort:T"),
                alt.Tooltip("Total Principal:Q", format=",.0f", title="Principal Disbursed"),
                alt.Tooltip("Loan Count:Q", title="Loans"),
            ],
        )
        loss_data = summary_cohorts.dropna(subset=["Loss Rate"])
        line = alt.Chart(loss_data).mark_line(point=True, color="#E8632A", strokeWidth=2.5).encode(
            x=alt.X("Cohort:T"),
            y=alt.Y("Loss Rate:Q", title="Loss Rate", axis=alt.Axis(format="%")),
            tooltip=[
                alt.Tooltip("Cohort:T"),
                alt.Tooltip("Loss Rate:Q", format=".2%"),
                alt.Tooltip("Matured Count:Q", title="Matured Loans"),
            ],
        )
        if not loss_data.empty:
            def _ref_layer(value, label, color, x_anchor):
                if pd.isna(value):
                    return None
                ref = pd.DataFrame({"v": [value], "label": [label], "xa": [x_anchor]})
                rule = alt.Chart(ref).mark_rule(color=color, strokeDash=[6, 6]).encode(y=alt.Y("v:Q"))
                text = alt.Chart(ref).mark_text(
                    color=color, dx=8, dy=-6, align="left", fontSize=11, fontWeight="bold"
                ).encode(x="xa:T", y=alt.Y("v:Q"), text="label:N")
                return rule + text

            for ref in (
                _ref_layer(
                    ue_data["Average Loss"], f"Avg: {fmt(ue_data['Average Loss'])}",
                    "#6B7280", loss_data["Cohort"].min(),
                ),
                _ref_layer(
                    ltv_data["95th Percentile Losses"], f"95th %ile: {fmt(ltv_data['95th Percentile Losses'])}",
                    "#d62728", loss_data["Cohort"].max(),
                ),
            ):
                if ref is not None:
                    line = line + ref
        st.altair_chart(
            alt.layer(bars, line).resolve_scale(y="independent").properties(
                height=340, padding={"right": 95},
            ),
            width="stretch",
        )
        st.caption(
            "Grey bars: principal disbursed per cohort (all loans). Orange line: cohort loss rate, "
            f"shown only for cohorts with at least {gi.min_loans_per_cohort} matured loans."
        )

if is_lending:
    with tabs[2]:
        st.subheader("Cohorts")

        matured_only_toggle = st.checkbox(
            "Matured loans only", value=True,
            help="When checked (default), every figure below is computed from matured loans only "
                 "(Reached T+3? = True) - a cohort with none doesn't appear at all, matching the "
                 "Excel template's Cohorts pivot exactly. Uncheck to instead see each cohort's true "
                 "origination volume and full-book figures. Loss Rate is always matured-only either "
                 "way, since it isn't meaningful otherwise - and the 95th %ile Loss Rate metric below "
                 "is never affected by this toggle.",
        )
        display_cohorts = cohorts if matured_only_toggle else lending_build_cohorts(
            df, min_matured=gi.min_loans_per_cohort, matured_only=False
        )
        st.caption(
            f"{len(display_cohorts)} monthly cohorts with at least one matured loan (Reached T+3? = True)"
            if matured_only_toggle else
            f"{len(display_cohorts)} monthly cohorts (all loans, matured or not)"
        )

        min_loans = st.number_input(
            "Minimum loans per cohort",
            min_value=0, value=gi.min_loans_per_cohort, step=1,
            help="Only show cohorts with at least this many loans (matured-only or all-loans, "
                 f"depending on the toggle above). Defaults to the Excel template's threshold "
                 f"({gi.min_loans_per_cohort}).",
        )

        cohort_view = display_cohorts[display_cohorts["Loan Count"] >= min_loans]

        lead_cols = [c for c in ("Cohort", "Loan Count", "Matured Count", "Loss Rate") if c in cohort_view.columns]
        cohort_view = cohort_view[lead_cols + [c for c in cohort_view.columns if c not in lead_cols]]

        c_metric_1, c_metric_2 = st.columns(2)
        c_metric_1.metric(
            "Total Cohorts available",
            f"{len(display_cohorts)}",
            help="All monthly cohorts in the analysis - not affected by the "
                 "minimum-loans filter above.",
        )
        c_metric_2.metric(
            "Cohorts shown", f"{len(cohort_view)}",
            help="Cohorts matching the 'Minimum loans per cohort' filter above, out of "
                 f"{len(display_cohorts)} monthly cohorts.",
        )

        table_tab, stats_tab = st.tabs(["Table", "Stats"])
        with table_tab:
            _render_cohorts_grid(cohort_view)

            st.markdown("#### Principal by Cohorts")
            _render_custom_visualizations_tab(
                {
                    "Cohorts (table above)": cohort_view,
                    "Cohorts (all, unfiltered)": display_cohorts,
                    "Data Input (loan-level)": df,
                },
                key_prefix="cohorts_cv",
                header=False,
                simple=True,
            )
        with stats_tab:
            # Compare normalized calendar periods, not raw Cohort timestamps -
            # .isin() on datetime columns is fragile to subtle dtype mismatches
            # (precision/backend) between the two sides, which can silently
            # match nothing instead of erroring.
            qualifying_periods = pd.to_datetime(cohort_view["Cohort"]).dt.to_period("M")
            cohort_loans = df[pd.to_datetime(df["Cohort"]).dt.to_period("M").isin(qualifying_periods)]
            avg_rates = principal_weighted_average_rates(cohort_loans)
            _render_snapshot_table([
                ("PvD", fmt(cohort_view["PvD Ratio"].mean()),
                 "Average Paid-vs-Due ratio (Total Paid / Total Due) across the cohorts shown in "
                 "the table."),
                ("Weighted Avg Term", fmt(cohort_view["Weighted Avg Term"].mean(), "{:,.1f} days"),
                 "Average, across the cohorts shown in the table, of each cohort's own "
                 "principal-weighted average term (days)."),
                ("APR", fmt(avg_rates["APR"]),
                 "Nominal APR: principal-weighted average, across loans in the cohorts shown, of "
                 "the periodic rate that amortizes Principal to the full amount owed - using the "
                 "loan's real installment (Payment per Period) where available, else a synthetic "
                 "owed/term estimate - annualized as rate x periods per year."),
                ("EAR", fmt(avg_rates["EAR"]),
                 "Effective Annual Rate: the same implied periodic rate as APR, annualized by "
                 "compounding - (1 + rate)^periods per year - 1 - instead of a simple multiply."),
            ])
            solved_pct = (
                f"{avg_rates['n_solved']}/{avg_rates['n_total']}" if avg_rates["n_total"] else "0/0"
            )
            st.caption(
                f"APR/EAR solved for {solved_pct} loans in view "
                f"({avg_rates['n_valid_inputs']} had usable inputs, "
                f"{avg_rates['n_converged']} of those converged). "
                "Amount owed is always Principal + Interest + Fee (never Total Due, which is "
                "often a to-date collections figure rather than the full lifetime amount owed). "
                f"Real Payment per Period available for {avg_rates['n_real_pmt']} loan(s) "
                "(others use a synthetic owed/term estimate)."
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
            calc_data_source = st.selectbox("Data Input Source", LTV_CALC_DATA_SOURCES, key="ltvcalc_data_source")
            calc_country = st.selectbox("Alt Lender Country", LTV_CALC_COUNTRIES, key="ltvcalc_country")
        with y2:
            calc_macro_fx = st.selectbox("Macro FX Risk Rating", LTV_CALC_FX_RISK_RATINGS, key="ltvcalc_macro_fx")
            calc_segmentation = st.selectbox("Segmentation", LTV_CALC_SEGMENTATIONS, key="ltvcalc_segmentation")
        with y3:
            calc_hedge_rate = st.number_input(
                "Hedge Rate (0 if unhedged, else % OTM as a decimal)",
                min_value=0.0, max_value=1.0, value=0.0, step=0.01, format="%.4f", key="ltvcalc_hedge_rate",
            )
            calc_rolled_hedge = st.selectbox("Rolled hedge?", LTV_CALC_HEDGE_TYPES, key="ltvcalc_rolled_hedge")

        result = None
        if pd.isna(green_interest) or pd.isna(green_loss) or green_term_bucket is None:
            st.warning(
                "Can't compute the LTV waterfall - one of the green inputs above is n/a for this portfolio."
            )
        else:
            result = ltv_calculator_compute(
                gross_interest=green_interest, term_months=green_term_bucket, loss_rate=green_loss,
                country=calc_country, macro_fx_risk=calc_macro_fx, segmentation=calc_segmentation,
                hedge_rate=calc_hedge_rate, rolled_hedge=calc_rolled_hedge, data_source=calc_data_source,
            )
            for note in result["notes"]:
                st.info(note)

        if result is not None:
            st.markdown("---")
            st.markdown("#### Outputs - Suggested LTV")

            st.subheader("LTGBV")
            _render_snapshot_table([
                ("No FX Adjustment", fmt(result["ltgbv_no_fx"]),
                 "LTGBV before any FX adjustment: 1 minus the selected stress loss."),
                ("With FX Adjustment (High)", fmt(result["ltgbv_high"]),
                 "LTGBV (no FX) divided by (1 + selected FX devaluation, high end)."),
                ("With FX Adjustment (Low)", fmt(result["ltgbv_low"]),
                 "LTGBV (no FX) divided by (1 + selected FX devaluation, low end)."),
            ])

            st.subheader("Advance on Principal (LTV)")
            _render_snapshot_table([
                ("No FX Adjustment", fmt(result["ltv_no_fx"]),
                 "LTGBV (no FX) grossed up by (1 + weighted avg. gross interest)."),
                ("With FX Adjustment (High)", fmt(result["ltv_high"]),
                 "LTGBV (FX high) grossed up by (1 + weighted avg. gross interest)."),
                ("With FX Adjustment (Low)", fmt(result["ltv_low"]),
                 "LTGBV (FX low) grossed up by (1 + weighted avg. gross interest)."),
            ])
            st.caption("Both LTGBV and LTV limits must be complied with (source: Receivables sheet, cell C44).")

            st.markdown("---")
            st.markdown("#### Back-Up")

            st.subheader("FX Backup")
            _render_snapshot_table([
                ("Term", f"{green_term_bucket} months" if green_term_bucket else "n/a",
                 "Weighted average receivable term, snapped to the nearest tenor bucket the "
                 "workbook's dropdown allows."),
                ("Historical Devaluation", fmt(result["historical_fx_deval"]),
                 "99th-percentile historical FX devaluation for the selected country and tenor."),
                ("Utilized Historical FX Deval", fmt(result["utilized_fx_deval"]),
                 "Historical FX devaluation actually used - the 6-month figure when the tenor is 6 "
                 "months or less."),
                ("Stress FX Devaluation", fmt(result["stress_fx_deval"]),
                 f"Max(historical, utilized) FX devaluation, stressed by the FX stress factor ({result['stress_fx_factor']:g}x)."),
                ("Base FX Deval", fmt(result["base_fx_deval"]),
                 "Stress FX devaluation, capped at the hedge rate when the deal is hedged."),
                ("FX Slippage Stress", fmt(result["fx_slippage"]),
                 "FX basis-risk slippage for the selected country."),
                ("Selected FX Deval (High)", fmt(result["selected_fx_deval_high"]),
                 "Base FX deval + slippage + high-end roll risk."),
                ("Selected FX Deval (Low)", fmt(result["selected_fx_deval_low"]),
                 "Base FX deval + slippage + low-end roll risk."),
            ])
            fx_country_data = pd.DataFrame({
                "Tenor (months)": LTV_CALC_TENORS,
                "Historical FX Devaluation": LTV_CALC_FX_DEVAL_TABLE[calc_country],
            })
            st.altair_chart(
                _add_reference_line(
                    alt.Chart(fx_country_data).mark_line(point=True, color="#1f77b4").encode(
                        x=alt.X("Tenor (months):O", title="Tenor (months)"),
                        y=alt.Y("Historical FX Devaluation:Q", title="99th %ile FX Devaluation",
                                axis=alt.Axis(format="%")),
                        tooltip=["Tenor (months):O", alt.Tooltip("Historical FX Devaluation:Q", format=".2%")],
                    ),
                    result["historical_fx_deval"],
                    f"Selected tenor ({green_term_bucket}m): {result['historical_fx_deval']:.2%}",
                    color="#d62728",
                ).properties(title=f"99th %ile Historical FX Devaluation - {calc_country}", height=300),
                width="stretch",
            )

            st.subheader("Credit Stress Backup")
            _render_snapshot_table([
                ("Product Interest Rate", fmt(green_interest),
                 "Weighted average gross interest rate, pulled from this analysis."),
                ("95th %ile Loss", fmt(green_loss),
                 "95th percentile loss rate at T+3, pulled from this analysis."),
                ("Stress Loss", fmt(result["stress_loss_rate"]),
                 f"Loss rate multiplied by the credit stress factor ({result['credit_stress_factor']:g}x)."),
                ("Minimum Loss Rate", fmt(result["minimum_stress_loss"]),
                 "Sector-minimum stress loss floor for the selected segmentation and data source."),
                ("Selected Stress Loss", fmt(result["selected_stress_loss"]),
                 "Max(stress loss rate, minimum stress loss floor)."),
            ])
            if not lending_chart_data.empty:
                st.altair_chart(
                    _add_reference_line(
                        alt.Chart(lending_chart_data).mark_line(point=True).encode(
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
            row1[0].metric("Average Expected Term (days)", fmt(ue_data["Average Expected Term"], "{:,.1f}"))
            row1[1].metric("Average Loss", fmt(ue_data["Average Loss"]))
            row1[2].metric("Loss Rate Proxy (1-PvD)", fmt(ue_data["Loss Rate Proxy (1-PvD)"]))
            row2 = st.columns(3)
            row2[0].metric("Average Principal Amount", fmt(ue_data["Average Principal Amount"], "{:,.0f}"))
            row2[1].metric("Average Fee %", fmt(ue_data["Average Fee %"]))
            row2[2].metric("Average Interest %", fmt(ue_data["Average Interest %"]))
            row3 = st.columns(3)
            row3[0].metric("Sense-check Margin", fmt(ue_data["Sense-check Margin"]))
            if not lending_chart_data.empty:
                st.altair_chart(
                    _add_reference_line(
                        alt.Chart(lending_chart_data).mark_line(point=True).encode(
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
                        alt.Chart(lending_chart_data).mark_line(point=True).encode(
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
                        alt.Chart(lending_chart_data).mark_line(point=True, color="#ff7f0e").encode(
                            x=alt.X("Cohort:T", title="Cohort"),
                            y=alt.Y("Interest %:Q", title="% of Principal", axis=alt.Axis(format="%")),
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
                        alt.Chart(lending_chart_data).mark_line(point=True, color="#1f77b4").encode(
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
        _render_custom_visualizations_tab({
            "Data Input (loan-level)": df, "Cohorts": cohorts, "Cohorts for X or more loans": filtered,
        })

    with tabs[6]:
        chat_context = _build_lending_chat_context(
            gi, df, cohorts, ue_data, ltv_data, ue_model_ai_data, dq_checks,
            user_context=_ai_context_text(max_chars=60000, per_doc=15000),
        )
        _render_ai_chat_tab(chat_context)

with tabs[1]:
    st.subheader("Data Checks")
    checks_tab, data_tab = st.tabs(["Checks", "Data Input"])
    with checks_tab:
        _render_data_quality_checks(
            dq_checks, "Loss Rate (%)", "Lending",
            "escalate_variance", file_name,
        )
    with data_tab:
        st.caption(
            f"{len(raw):,} rows x {len(raw.columns):,} columns uploaded. "
            f"Computed columns (Cohort, Term, Reached T+3?) used in downstream sheets."
        )
        st.dataframe(raw, width="stretch", height=400)

st.markdown("---")


@st.cache_data(show_spinner="Building the Excel export — this can take a while for large portfolios...")
def _build_lending_export_workbook(
    df, cohorts, filtered, days_after_term, min_loans_per_cohort,
    ltv_data, ue_data, lending_chart_data, export_charts, term_supplied=False,
    mapping=None, currency=None, derived_columns=None, extraction_date=None,
):
    """Cached on its inputs so it's only rebuilt when the analysis or queued
    custom charts actually change, rather than on every Streamlit rerun (e.g.
    toggling an unrelated checkbox) -- at scale (hundreds of thousands of
    rows) rebuilding this from scratch every rerun took 45-60+ seconds."""
    raw_cols = ["Loan ID", "Disbursement Date", "Expected Completion Date",
                "Principal Value", "Expected Interest", "Expected Fee",
                "Total Due", "Total Paid"]
    if term_supplied:
        raw_cols.append("Term (days)")
    data_frame = pd.DataFrame({col: df.get(col, pd.Series([None] * len(df))) for col in raw_cols})
    num_data = len(df)
    orig_source = cohorts[["Cohort", "Total Principal"]].dropna()

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        gs = writer.book.create_sheet("General Inputs", 0)
        for r, label, val, note in [
            (2, "Inputs", None, None),
            (3, "Date of extraction", "=MAX('Data Input'!B:B)", "Defaults to most recent disbursement date"),
            (4, "Days after term", days_after_term, "Ignore any loans for loss rates that are less than this number of days after term"),
            (5, "Minimum loans per cohort", min_loans_per_cohort, "Ignore any cohorts for loss rates that are less than this number of loans"),
        ]:
            if label:
                gs.cell(row=r, column=2, value=label)
            if val is not None:
                gs.cell(row=r, column=3, value=val)
            if note:
                gs.cell(row=r, column=4, value=note)

        q_df = pd.DataFrame([(i, q) for i, q in enumerate(LENDING_QUESTIONS, 1)], columns=["#", "Question"])
        q_df.to_excel(writer, sheet_name="Data Questionnaire", index=False)

        data_frame.to_excel(writer, sheet_name="Data Input", index=False, header=True)
        dws = writer.sheets["Data Input"]
        # "Term (days)" is column 9 (real supplied values, already written by
        # to_excel above) when term_supplied, otherwise it's derived here as a
        # formula in column 11 same as the other computed columns - shift
        # Reached T+3?/Cohort's column to match.
        matured_col, cohort_col = (10, 11) if term_supplied else (9, 10)
        dws.cell(row=1, column=matured_col, value="Reached T+3?")
        dws.cell(row=1, column=cohort_col, value="Cohort")
        if not term_supplied:
            dws.cell(row=1, column=11, value="Term (days)")
        for r in range(2, max(num_data, 1) + 2):
            dws.cell(row=r, column=matured_col).value = f'=IF(ISBLANK(C{r}),"",(C{r}+\'General Inputs\'!$C$4)<=\'General Inputs\'!$C$3)'
            dws.cell(row=r, column=cohort_col).value = f'=IF(ISBLANK(B{r}),"",DATE(YEAR(B{r}),MONTH(B{r}),1))'
            if not term_supplied:
                dws.cell(row=r, column=11).value = f'=IF(OR(ISBLANK(B{r}),ISBLANK(C{r})),"",C{r}-B{r})'

        ltv_df = pd.DataFrame(list(ltv_data.items()), columns=["Metric", "Value"])
        ltv_df.to_excel(writer, sheet_name="LTV Analysis", index=False)
        ltv_ws = writer.sheets["LTV Analysis"]
        if not lending_chart_data.empty:
            lending_chart_data[["Cohort", "Loss Rate"]].to_excel(writer, sheet_name="LTV Analysis", startrow=6, index=False)
            c = LineChart()
            c.title = "Loss per Cohort"
            c.y_axis.numFmt = "0.00%"
            c.height, c.width = 14, 24
            data = Reference(ltv_ws, min_col=1, min_row=7, max_col=2, max_row=7 + len(lending_chart_data))
            cats = Reference(ltv_ws, min_col=1, min_row=8, max_row=7 + len(lending_chart_data))
            c.add_data(data, titles_from_data=True)
            c.set_categories(cats)
            ltv_ws.add_chart(c, "B8")

        ue_df = pd.DataFrame(list(ue_data.items()), columns=["Metric", "Value"])
        ue_df.to_excel(writer, sheet_name="Unit Economics Analysis", index=False)
        ue_ws = writer.sheets["Unit Economics Analysis"]
        if not lending_chart_data.empty:
            lending_chart_data[["Cohort", "Loss Rate"]].to_excel(writer, sheet_name="Unit Economics Analysis", startrow=10, index=False)
            lending_chart_data[["Cohort", "Weighted Avg Term"]].to_excel(writer, sheet_name="Unit Economics Analysis", startrow=10, startcol=4, index=False)
            lending_chart_data[["Cohort", "Fee %"]].to_excel(writer, sheet_name="Unit Economics Analysis", startrow=10, startcol=8, index=False)
            lending_chart_data[["Cohort", "Interest %"]].to_excel(writer, sheet_name="Unit Economics Analysis", startrow=10, startcol=11, index=False)
            sr = 11
            lr = sr + len(lending_chart_data) - 1
            for title, col, anchor in [
                ("Loss per Cohort", (1, 2), "B29"),
                ("Average Term per Cohort (days)", (5, 6), "B48"),
                ("Average Fee Percent per Cohort", (9, 10), "B67"),
                ("Average Product Interest Percent per Cohort", (12, 13), "B86"),
            ]:
                c = LineChart()
                c.title = title
                c.height, c.width = 14, 24
                if col[1] - col[0] == 1:
                    c.y_axis.numFmt = "0.00%" if "Loss" in title or "Percent" in title else "0"
                data = Reference(ue_ws, min_col=col[0], min_row=sr - 1, max_col=col[1], max_row=lr)
                cats = Reference(ue_ws, min_col=col[0], min_row=sr, max_row=lr)
                c.add_data(data, titles_from_data=True)
                c.set_categories(cats)
                ue_ws.add_chart(c, anchor)

        ga_summary = lending_general_analysis(df)
        ga_df = pd.DataFrame([
            ("Shape", f"{ga_summary['shape'][0]:,} rows x {ga_summary['shape'][1]} cols"),
            ("Columns", ", ".join(ga_summary["columns"])),
        ], columns=["Property", "Value"])
        ga_df.to_excel(writer, sheet_name="General Analysis", index=False)
        ga_ws = writer.sheets["General Analysis"]
        if not orig_source.empty:
            orig_source.to_excel(writer, sheet_name="General Analysis", startrow=4, index=False)
            c = BarChart()
            c.title = "Originations per month"
            c.height, c.width = 14, 24
            data = Reference(ga_ws, min_col=1, min_row=5, max_col=2, max_row=4 + len(orig_source))
            cats = Reference(ga_ws, min_col=1, min_row=6, max_row=4 + len(orig_source))
            c.add_data(data, titles_from_data=True)
            c.set_categories(cats)
            ga_ws.add_chart(c, "A6")

        _write_custom_charts_sheet(writer, export_charts)

        cohorts.to_excel(writer, sheet_name="Cohorts", index=False)
        filtered.to_excel(writer, sheet_name="Cohorts for X or more loans", index=False)

        # Embed the session config (mapping/currency/GI overrides/derived
        # columns) as a hidden sheet so re-uploading this same file lets the
        # app detect it and pre-fill the mapping form to reproduce this exact
        # run, instead of the recipient re-mapping/re-tuning from scratch.
        if mapping is not None:
            session_cfg = {
                "schema": 1,
                "mapping": mapping,
                "currency": currency,
                "gi_overrides": {
                    "extraction_date": extraction_date.isoformat() if extraction_date is not None else None,
                    "days_after_term": days_after_term,
                    "min_loans_per_cohort": min_loans_per_cohort,
                },
                "derived_columns": derived_columns or [],
            }
            cfg_ws = writer.book.create_sheet("_App Session")
            cfg_ws["A1"] = json.dumps(session_cfg)
            cfg_ws.sheet_state = "hidden"

    buf.seek(0)
    return buf.getvalue()


if is_lending:
    buf = _build_lending_export_workbook(
        df, cohorts, filtered, gi.days_after_term, gi.min_loans_per_cohort,
        ltv_data, ue_data, lending_chart_data, st.session_state.get("export_charts", []),
        term_supplied,
        mapping=st.session_state.get("analysis_mapping"),
        currency=st.session_state.get("analysis_currency"),
        derived_columns=st.session_state.get("derived_columns"),
        extraction_date=gi.extraction_date,
    )
    file_name = f"SC_Analysis_Lending_{pd.Timestamp.now():%Y-%m-%d}.xlsx"

st.download_button(
    "Download full workbook as Excel",
    buf,
    file_name=file_name,
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
st.caption(
    "Sharing this with a colleague? They can open it directly in Excel, or upload it back "
    "into this app - column mapping and General Inputs will be pre-filled to match this run."
)
