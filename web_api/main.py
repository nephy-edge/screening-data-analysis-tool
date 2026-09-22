"""FastAPI backend for the SC Analysis React web app.

Wraps the existing, verified Lending (``scripts/template_analysis``) and
Rental & Subscription (``rental_and_subscription/rental_analysis``) engines
and exposes them over HTTP. Nothing here re-implements analysis logic - it
translates JSON requests into the engines' canonical tabular forms and
serialises their results back as JSON.

Run (from the repo root, using the project venv):

    .venv\\Scripts\\uvicorn.exe web_api.main:app --reload --port 8000

The React dev server (web/) proxies ``/api/**`` to this server (port 8000).
"""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import sys
import time
import uuid
from typing import Any, Literal, Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# Make both engines importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rental_and_subscription"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

import config as app_config
from config import UE_MODEL_COST_DEFAULTS
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

# Same .env app.py loads at import (google_oauth.py's _get_google_client_id
# etc. fall back to os.environ when st.secrets isn't set) - lets this
# process reuse credentials already configured for the Streamlit app
# (GOOGLE_CLIENT_ID/SECRET, DEEPINFRA_API_KEY, ...) without duplicating them.
load_dotenv()

# Same raw-upload date coercion app.py itself calls before running the
# Lending engine (see app.py's `_coerce_dates(raw, DATE_FIELDS, dayfirst)`).
from ai_client import (
    _ask_data_chatbot,
    _suggest_derived_column,
    _suggest_escalation_writeup,
    _suggest_mapping,
    _suggest_redshift_filter,
)
from chat_context import _build_rental_chat_context
from data_normalize import _clean_numeric, _coerce_dates as _coerce_lending_dates
from export_workbook import _build_lending_export_workbook, _build_rental_export_workbook
from integrations import (
    _get_redshift_api_key,
    _get_redshift_api_url,
    _redshift_job_status,
    _redshift_list_columns,
    _redshift_list_schemas,
    _redshift_list_tables,
    _redshift_start_table_job,
    _send_slack_feedback,
)
from quality_checks import _lending_data_quality_checks, _rental_data_quality_checks
from summary_metrics import lending_summary_metrics, rental_summary_metrics

# Supporting-document text extraction for the "Additional context for the
# AI" uploader (PDF/Excel/Word/text) - shared with app.py so both apps
# extract identically.
import document_text

# S3-backed save/resume/share persistence (see s3_store.py's module
# docstring) - the same storage backend app.py uses in "cognito" auth mode.
# Used here for every React identity (dummy login and Google sign-in alike)
# rather than adding a local-disk fallback: local disk doesn't survive an
# ECS/container redeploy, and this app has no Cognito login of its own to
# gate an S3-vs-local choice on. identity is whatever LoginScreen.tsx's
# signed-in user resolves to (self-declared string, or the verified Google
# email) - access control is enforced in s3_store itself (per-user prefix),
# not by AWS credentials.
import s3_store

# Google OAuth mechanics (auth URL, code exchange, userinfo) - the same
# helpers app.py's own Google sign-in uses (scripts/login_gates.py's
# _render_google_login_gate). Only the CSRF-state storage and redirect
# target below are new: this app is a normal FastAPI process (no
# st.cache_resource/st.session_state need substituting - see
# scripts/session_cookie.py's docstring for why Streamlit's own version
# can't just be imported as-is), and it redirects back to the React app's
# own callback route, not Streamlit's.
from google_oauth import (
    _exchange_google_code,
    _fetch_google_userinfo,
    _get_google_client_id,
    _get_google_client_secret,
    _google_auth_url,
    _refresh_google_token,
)

# Corporate-root-CA-aware TLS bundle, same lookup app.py's direct Drive/Sheets
# downloads use (falls back to certifi when certs/corporate_root.pem is absent).
from ca_bundle import _ca_bundle_path

# Drive + Sheets import scope - the same scopes app.py's GOOGLE_LOGIN_SCOPE
# requests: read access for browsing the user's Drive (drive.readonly) plus
# write access scoped to files this app itself creates (drive.file, NOT full
# drive access) for Drive-backed save/share. Identity scopes come first so
# the consent screen reads like a normal Google sign-in.
GOOGLE_REACT_APP_SCOPE = (
    "openid email profile "
    "https://www.googleapis.com/auth/drive.readonly "
    "https://www.googleapis.com/auth/drive.file"
)
GOOGLE_REACT_APP_REDIRECT_URI = os.environ.get(
    "GOOGLE_REACT_REDIRECT_URI", "http://localhost:5173/auth/google/callback"
)
_PENDING_GOOGLE_OAUTH_STATES: set[str] = set()

# Persistent "remember me" session cookie for the React app. On a successful
# sign-in (Google or the dummy fallback) the server issues this HttpOnly,
# signed cookie; on every app load the frontend calls /api/auth/me, which
# returns the stored identity when the cookie is valid - so a refresh, a new
# tab, or a browser restart within the lifetime never shows the login screen
# again. The cookie holds only the verified identity (email + display name)
# plus an opaque per-session id (`sid`) that keys the server-side Google
# token store below - the cookie never carries the access/refresh token
# itself (a stolen HttpOnly cookie still can't touch Drive, because the
# token only lives in the server's memory for that sid). It is HMAC-signed
# so a client cannot forge an identity it was never granted. Secret comes
# from SC_REACT_SESSION_SECRET, falls back to GOOGLE_CLIENT_SECRET (already
# a server-only secret), else a per-process random - which is safe but means
# cookies stop validating if this process restarts.
_REACT_SESSION_COOKIE = "sc_react_session"
_REACT_SESSION_MAX_AGE_SECONDS = int(os.environ.get("SC_REACT_SESSION_DAYS", "30")) * 86400
_REACT_SESSION_SECRET = (
    os.environ.get("SC_REACT_SESSION_SECRET")
    or os.environ.get("GOOGLE_CLIENT_SECRET")
    or secrets.token_urlsafe(32)
)

# Server-side Google OAuth token store, keyed by the session id inside the
# signed cookie, so a refresh (or a server restart) auto-restores the Drive
# connection without re-consent. Persisted to a gitignored, owner-only local
# file - a browser only ever reads back the token for its OWN sid (the sid is
# inside an HMAC-signed cookie it cannot forge), so unlike the on-disk cache
# app.py removed (a single shared file that leaked whoever logged in last to
# the next visitor, see .gitignore) there is no cross-user leak. The file is
# never exposed to the browser; the refresh token never leaves the server.
_GOOGLE_TOKENS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", ".google_session_tokens.json"
)
_GOOGLE_TOKENS_MAX = 512


def _load_google_tokens() -> dict[str, dict]:
    try:
        with open(_GOOGLE_TOKENS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        return {}


def _save_google_tokens() -> None:
    """Best-effort, atomic persist of the token store. Never raises - the
    in-memory store keeps working if the disk write fails. Tokens are
    long-lived refresh credentials, so restrict the file to the owner."""
    try:
        os.makedirs(os.path.dirname(_GOOGLE_TOKENS_PATH), exist_ok=True)
        tmp = _GOOGLE_TOKENS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_GOOGLE_TOKENS, f)
        os.replace(tmp, _GOOGLE_TOKENS_PATH)
        try:
            os.chmod(_GOOGLE_TOKENS_PATH, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def _store_google_token(sid: str, token: dict) -> None:
    _GOOGLE_TOKENS[sid] = token
    while len(_GOOGLE_TOKENS) > _GOOGLE_TOKENS_MAX:
        _GOOGLE_TOKENS.pop(next(iter(_GOOGLE_TOKENS)))
    _save_google_tokens()


_GOOGLE_TOKENS: dict[str, dict] = _load_google_tokens()


_react_session_serializer = URLSafeTimedSerializer(_REACT_SESSION_SECRET, salt="sc-react-session")


def _set_react_session_cookie(response: Response, email: str, name: str) -> str:
    """Issue the signed remember-me cookie on a successful sign-in; returns
    the fresh per-session id so the caller can key a Google token to it."""
    sid = uuid.uuid4().hex
    response.set_cookie(
        _REACT_SESSION_COOKIE,
        _react_session_serializer.dumps({"sid": sid, "email": email, "name": name}),
        max_age=_REACT_SESSION_MAX_AGE_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return sid


def _read_react_session(request: Request) -> dict[str, str] | None:
    """Return {"sid", "email", "name"} when the request carries a valid,
    unexpired signed session cookie, else None (missing/invalid/forged/
    expired)."""
    raw = request.cookies.get(_REACT_SESSION_COOKIE)
    if not raw:
        return None
    try:
        return _react_session_serializer.loads(raw, max_age=_REACT_SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


# Google Drive / Sheets import - mirrors app.py's Drive helpers
# (_list_drive_files, _download_drive_file, _download_google_sheet) so the
# React app's "From Google Drive" and "Google Sheets / Drive link" data
# sources behave exactly like the Streamlit app's.
GOOGLE_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_DRIVE_FILE_QUERY = (
    "(mimeType='application/vnd.google-apps.spreadsheet' or "
    "mimeType='text/csv' or "
    "mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' or "
    "mimeType='application/vnd.ms-excel') and trashed=false"
)


def _gdrive_access_token(sid: str) -> str:
    """Return a fresh-enough Drive access token for this session, refreshing
    it (and updating the store) when it's within 60s of expiry and we hold a
    refresh token - same refresh logic as google_oauth._gdrive_access_token,
    minus the Streamlit session_state write."""
    token = _GOOGLE_TOKENS.get(sid)
    if token is None:
        raise HTTPException(
            status_code=403,
            detail="Google Drive isn't connected for this session - sign out and back in with Google.",
        )
    if time.time() >= token.get("expires_at", 0) - 60 and token.get("refresh_token"):
        refreshed = _refresh_google_token(token["refresh_token"])
        refreshed["refresh_token"] = token["refresh_token"]
        _store_google_token(sid, refreshed)
        return refreshed["access_token"]
    return token["access_token"]


def _require_gdrive_session(request: Request) -> str:
    session = _read_react_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return session["sid"]


def _extract_google_id(url: str) -> str:
    """Pull the document ID out of a Google Sheets or Google Drive share link
    (mirror of app.py's _extract_google_id)."""
    for pattern in (
        r"/spreadsheets/d/([a-zA-Z0-9-_]+)",
        r"drive\.google\.com/file/d/([a-zA-Z0-9-_]+)",
    ):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    raise HTTPException(
        status_code=422,
        detail=(
            "That link doesn't look like a Google Sheets or Google Drive document - paste the "
            "full share link (https://docs.google.com/spreadsheets/d/...)."
        ),
    )


def _download_google_sheet(url: str) -> tuple[bytes, str]:
    """Download a Google Sheets / Google Drive document over HTTPS, returning
    (bytes, suggested filename). Mirror of app.py's _download_google_sheet:
    the document must be shared with 'Anyone with the link can view' - no
    Google API key or token is needed for public documents. A Sheets link is
    exported as a full Excel workbook (every tab, not just whichever was open
    in the URL) so the sheet picker can offer all of them, same as a local
    upload; a generic Drive file link is fetched as-is and sniffed for CSV vs
    Excel."""
    url = url.strip()
    doc_id = _extract_google_id(url)
    if "spreadsheets" in url:
        export = f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=xlsx"
        resp = requests.get(export, timeout=30, verify=_ca_bundle_path())
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Google returned HTTP {resp.status_code} - the sheet must be shared with "
                    "'Anyone with the link can view' (File > Share > General access)."
                ),
            )
        return resp.content, "google_sheet.xlsx"
    resp = requests.get(
        f"https://drive.google.com/uc?export=download&id={doc_id}",
        timeout=30,
        verify=_ca_bundle_path(),
    )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Google Drive returned HTTP {resp.status_code} - the file must be shared with "
                "'Anyone with the link can view'."
            ),
        )
    data = resp.content
    name = "google_file.xlsx" if data[:4] == b"PK\x03\x04" else "google_file.csv"
    return data, name


def _list_drive_files(access_token: str) -> list[dict[str, Any]]:
    """List the user's spreadsheets/CSV/Excel files from Drive, newest first."""
    resp = requests.get(
        GOOGLE_DRIVE_FILES_URL,
        params={
            "q": _DRIVE_FILE_QUERY,
            "pageSize": 200,
            "fields": "files(id,name,mimeType,modifiedTime)",
            "orderBy": "modifiedTime desc",
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
        verify=_ca_bundle_path(),
    )
    if resp.status_code == 401:
        raise HTTPException(
            status_code=401,
            detail="Google Drive access expired - sign out and back in to reconnect.",
        )
    resp.raise_for_status()
    return resp.json().get("files", [])


def _gdrive_file_meta(access_token: str, file_id: str) -> dict[str, Any]:
    resp = requests.get(
        f"{GOOGLE_DRIVE_FILES_URL}/{file_id}",
        params={"fields": "id,name,mimeType"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
        verify=_ca_bundle_path(),
    )
    if resp.status_code == 401:
        raise HTTPException(
            status_code=401,
            detail="Google Drive access expired - sign out and back in to reconnect.",
        )
    resp.raise_for_status()
    return resp.json()


def _download_drive_file(access_token: str, drive_file: dict[str, Any]) -> tuple[bytes, str]:
    """Download a Drive file (or export a Google Sheet as a full Excel
    workbook - every tab, not just its first - so the sheet picker can offer
    all of them), returning (bytes, suggested filename)."""
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
    if resp.status_code == 401:
        raise HTTPException(
            status_code=401,
            detail="Google Drive access expired - sign out and back in to reconnect.",
        )
    resp.raise_for_status()
    data = resp.content
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data, name


# Lending engine
from template_analysis.cohorts import build_cohorts
from template_analysis.cohorts_for_x_or_more_loans import filter_cohorts
from template_analysis.data_input import process_data_input
from template_analysis.general_inputs import GeneralInputs
from template_analysis.ltv_analysis import LtvAnalysis
from template_analysis.ltv_calculator import (
    COUNTRIES as LTV_CALC_COUNTRIES,
    DATA_INPUT_SOURCES as LTV_CALC_DATA_SOURCES,
    FX_DEVAL_TABLE as LTV_CALC_FX_DEVAL_TABLE,
    FX_RISK_RATINGS as LTV_CALC_FX_RISK_RATINGS,
    HEDGE_TYPES as LTV_CALC_HEDGE_TYPES,
    SEGMENTATIONS as LTV_CALC_SEGMENTATIONS,
    TENORS as LTV_CALC_TENORS,
    compute_ltv as ltv_calculator_compute,
    nearest_tenor_bucket as ltv_calculator_nearest_tenor,
    sector_min_loss_rate as ltv_calculator_sector_min_loss_rate,
)
from template_analysis.ue_analysis import UeAnalysis

# Rental & Subscription engine
from rental_analysis.asset_view import build_asset_view
from rental_analysis.churn_analysis import ChurnAnalysis
from rental_analysis.cohorts_for_x_or_more_loans import filter_cohorts as rental_filter_cohorts
from rental_analysis.data_input import OPTIONAL_COLUMNS as RENTAL_OPTIONAL_COLUMNS
from rental_analysis.data_input import REQUIRED_COLUMNS as RENTAL_REQUIRED_COLUMNS_LIST
from rental_analysis.data_input import (
    apply_fallbacks as rental_apply_fallbacks,
)
from rental_analysis.data_input import process_data_input as rental_process_data_input
from rental_analysis.general_analysis import describe as rental_general_analysis
from rental_analysis.general_inputs import GeneralInputs as RentalGeneralInputs
from rental_analysis.lease_cohorts import build_cohorts as rental_build_cohorts
from rental_analysis.ltv_analysis import LtvAnalysis as RentalLtvAnalysis
from rental_analysis.repayment_curve import build_repayment_curve
from rental_analysis.ue_analysis import UeAnalysis as RentalUeAnalysis
from rental_analysis.ts_covenants import build_ts_covenants

app = FastAPI(
    title="SC Analysis Engine API",
    version="0.1.0",
    description="Lending + Rental & Subscription analysis engines for the React app.",
)


# Canonical template fields the Lending engine requires (see LENDING_CONFIG).
REQUIRED_FIELDS = [
    "Loan ID",
    "Disbursement Date",
    "Expected Completion Date",
    "Principal Value",
    "Expected Interest",
    "Total Paid",
]


class GeneralInputsBody(BaseModel):
    extractionDate: Optional[str] = None
    daysAfterTerm: int = 90
    minLoansPerCohort: int = 10


class DerivedColumn(BaseModel):
    name: str = Field(..., description="New column label (used as a mapping source).")
    formula: str = Field(
        ...,
        description="Safe arithmetic formula over source columns, e.g. 'Gross Loan Value - Principal Value'.",
    )


class LendingAnalyseIngest(BaseModel):
    ingestId: str = Field(..., description="Id returned by POST /api/ingest.")
    mapping: dict[str, str] = Field(
        ..., description="Template field key -> raw source column name."
    )
    derivedColumns: list[DerivedColumn] = Field(
        default_factory=list,
        description="Columns to derive server-side (name + formula) and add to the source columns the mapping can reference.",
    )


class ChartSpecBody(BaseModel):
    """One Custom Visualizations chart card, already aggregated client-side
    (web/src/lib/chartAggregate.ts) - only used by the /api/export/*
    endpoints, which turn these into scripts/export_workbook.py's
    _write_custom_charts_sheet input format. Ignored by the analyse
    endpoints (charts render straight from the analyse response client-side)."""

    title: str
    kind: str = Field(..., description="'Line', 'Bar', 'Area', or 'Scatter'.")
    xCol: str
    yCol: str
    colorCol: Optional[str] = None
    data: list[dict[str, Any]] = Field(
        default_factory=list, description="Aggregated rows keyed by xCol/yCol[/colorCol]."
    )


class LendingAnalyseBody(BaseModel):
    rows: list[dict[str, Any]] = Field(
        default_factory=list, description="Mapped rows keyed by canonical template field names."
    )
    generalInputs: GeneralInputsBody = Field(default_factory=GeneralInputsBody)
    ingest: Optional[LendingAnalyseIngest] = None
    exportCharts: list[ChartSpecBody] = Field(default_factory=list)
    dedupeById: bool = False
    selectedCohorts: Optional[list[str]] = Field(
        default=None,
        description="Cohort month labels (e.g. '2025-06-01') to restrict the analysis to - "
        "the Cohorts tab's selection. None/empty means every cohort, same as today.",
    )


class RentalGeneralInputsBody(BaseModel):
    extractionDate: Optional[str] = None
    daysAfterTerm: int = 0
    monthsSinceDefault: int = 3
    minLoansPerCohort: int = 20
    usefulLifeYears: float = 3.0
    churnStressMultiplier: float = 1.7
    fundingCostPct: float = 0.13
    directCostPct: float = 0.02
    fxDevalPct: Optional[float] = None
    fxDiscount50pct: bool = True
    debtService180d: Optional[float] = None


class RentalAnalyseIngest(BaseModel):
    ingestId: str = Field(..., description="Id returned by POST /api/ingest.")
    mapping: dict[str, str] = Field(
        ..., description="Template field key -> raw source column name."
    )
    derivedColumns: list[DerivedColumn] = Field(default_factory=list)


class RentalAnalyseBody(BaseModel):
    rows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Mapped rows keyed by canonical rental template field names.",
    )
    generalInputs: RentalGeneralInputsBody = Field(default_factory=RentalGeneralInputsBody)
    ingest: Optional[RentalAnalyseIngest] = None
    exportCharts: list[ChartSpecBody] = Field(default_factory=list)
    dedupeById: bool = False


RENTAL_REQUIRED_COLUMNS = [
    "contract_id",
    "asset_id",
    "start_date",
    "status",
    "downpayment",
    "monthly_expected_payment",
    "total_paid",
    "cost_of_asset",
]

# Minimal mirrors of app.py's LENDING_CONFIG/RENTAL_CONFIG, holding only the
# 3 keys quality_checks.py's _step2_data_quality_checks actually reads
# (id_field, input_columns, primary_date_field). app.py itself can't be
# imported here - it's a top-level Streamlit script that renders UI on
# import - so these are kept in sync by hand with app.py's definitions.
LENDING_DQ_CONFIG = {
    "id_field": "Loan ID",
    "input_columns": [
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
    "primary_date_field": "Disbursement Date",
}
RENTAL_DQ_CONFIG = {
    "id_field": "contract_id",
    "input_columns": [(c, True) for c in RENTAL_REQUIRED_COLUMNS_LIST]
    + [(c, False) for c in RENTAL_OPTIONAL_COLUMNS],
    "primary_date_field": "start_date",
}

# In-memory registry of ingested uploads. A real file upload stores its parsed
# DataFrame here keyed by a short-lived id; the analyse endpoints then look it
# up, apply the user's column mapping, and run the engine. Kept tiny so we do
# not linger on the user's data longer than the interaction needs it.
INGESTS: dict[str, pd.DataFrame] = {}
# Parallel registry of each ingest's original bytes/filename - kept
# alongside the parsed DataFrame only so a save/share snapshot can persist
# and later re-parse the exact original upload (s3_store.py stores the
# raw file, matching app.py's own save/resume/share model). bytes is None
# for an ingest with no original file (a merge or warehouse-fetch result) -
# see _ingest_raw_bytes, which fills it in lazily on first actual need.
INGEST_RAW: dict[str, tuple[Optional[bytes], str]] = {}


def _default_sheet(sheets: list[str]) -> Optional[str]:
    """Prefer "Data Input" when present (e.g. re-uploading this app's own
    export) rather than blindly taking whichever sheet is first."""
    if not sheets:
        return None
    return "Data Input" if "Data Input" in sheets else sheets[0]


def _parse_upload(
    file_bytes: bytes, name: str, sheet: Optional[str] = None
) -> tuple[pd.DataFrame, list[str]]:
    """Parse an uploaded CSV or XLSX the same way the Streamlit app does
    (see app.py `_parse_uploaded` / `_read_tabular_file`): CSV straight via
    read_csv; XLSX reads `sheet` (defaulting to "Data Input" when present,
    else the first non-"_App Session" sheet - "_App Session" is this app's
    own hidden bookkeeping sheet in its exports, never loan-level data), and
    falls back to a lower header row when the parse leaves a majority of
    "Unnamed: N" columns (i.e. a title/banner row above the real headers).
    Returns (df, pickable_sheets) - pickable_sheets is always [] for a CSV.

    `pd.ExcelFile()` reads the whole workbook's shared-strings table and
    sheet index up front, not just the one sheet asked for - on a large,
    many-sheet workbook that open can cost far more than parsing the sheet
    itself (measured ~11s open vs ~6s parse on a 23MB, 12-sheet file). This
    used to run twice per request (once here, once in a separate sheet-list
    helper called right before/after it) for no benefit; returning the sheet
    list from this same open means every caller pays that cost once.

    engine="calamine" (python-calamine, a Rust reader) vs pandas' openpyxl
    default: ~16x faster end-to-end on a real 23.8MB/39k-row/27-col upload
    (49.7s -> 3.0s; the open alone was 38.7s under openpyxl), with output
    verified byte-identical (df.equals() True across every row/column) on
    that same file - openpyxl's slowness scales badly with workbook size
    since it eagerly builds an in-memory object model of the whole file."""
    if name.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(file_bytes)), []
    xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="calamine")
    pickable = [s for s in xls.sheet_names if s != "_App Session"]
    if sheet is not None and sheet not in pickable:
        raise ValueError(
            f"'{sheet}' isn't a sheet in this file. Available: {', '.join(pickable) or 'none'}"
        )
    if sheet is None:
        sheet = _default_sheet(pickable)
    df = xls.parse(sheet)
    unnamed_frac = sum(str(c).startswith("Unnamed:") for c in df.columns) / max(len(df.columns), 1)
    if unnamed_frac >= 0.5:
        preview = xls.parse(sheet, header=None, nrows=10)
        for i in range(1, len(preview)):
            row = preview.iloc[i]
            if (
                row.notna().mean() > 0.7
                and row.dropna().map(lambda v: isinstance(v, str)).mean() > 0.7
            ):
                df = xls.parse(sheet, header=i)
                break
    return df, pickable


def _frame_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Serialize a DataFrame to JSON-safe records.

    Converts pandas scalars (Timestamp, numpy numbers, NaN) into plain Python
    values so the result is valid JSON for the React client.
    """
    out: list[dict[str, Any]] = []
    for rec in df.where(pd.notna(df), None).to_dict(orient="records"):
        cleaned: dict[str, Any] = {}
        for k, v in rec.items():
            col = str(k)
            if v is None:
                cleaned[col] = None
            elif isinstance(v, pd.Timestamp):
                cleaned[col] = v.isoformat()
            elif hasattr(v, "item"):
                try:
                    cleaned[col] = v.item()
                except (ValueError, OverflowError):
                    cleaned[col] = v
            else:
                cleaned[col] = v
        out.append(cleaned)
    return out


def _json_safe(value: Any) -> Any:
    """Recursively convert numpy/pandas objects to JSON-safe Python values."""
    if isinstance(value, pd.DataFrame):
        return _frame_to_records(value)
    if isinstance(value, pd.Series):
        return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, np.ndarray):
        return [_json_safe(v) for v in value.tolist()]
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        try:
            return value.item()
        except (ValueError, OverflowError):
            return None
    if isinstance(value, (str, int, float, bool)):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "engine": "template_analysis"}


def _register_ingest(
    file_bytes: Optional[bytes],
    name: str,
    sheet: Optional[str] = None,
    ingest_id: Optional[str] = None,
    df: Optional[pd.DataFrame] = None,
) -> dict[str, Any]:
    """Parse+register a raw upload (new, re-parsed at a different sheet, or
    restored from a save/share snapshot) and return the ingest response
    payload. Shared by /api/ingest, the sheet picker and the profile/share
    restore endpoints so all of them produce a real, independently-analysable
    ingestId the same way. Passing `ingest_id` re-uses that id (the sheet
    picker re-parsing the same upload) instead of minting a new one.

    Passing `df` skips parsing `file_bytes` and registers that DataFrame
    directly - for merge_ingest/redshift_table, which already have the
    result DataFrame in memory and would otherwise pay to serialize it to
    CSV just to have "file bytes" to store, and (before `df` existed at all)
    immediately re-parse those same bytes right back into a DataFrame for no
    benefit. `file_bytes` may be None in this case - see _ingest_raw_bytes,
    which computes and caches it lazily the first time something (Save/
    Share) actually needs real bytes, instead of paying to_csv()'s cost
    (measured ~190s for a 2M-row fetch) on every such ingest whether or not
    it's ever saved/shared."""
    if df is None:
        try:
            df, sheets = _parse_upload(file_bytes, name, sheet)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc
    else:
        sheets = []
    if df.empty:
        raise HTTPException(status_code=422, detail="File parsed with no rows.")
    df.columns = [str(c) for c in df.columns]
    ingest_id = ingest_id or uuid.uuid4().hex[:12]
    INGESTS[ingest_id] = df
    INGEST_RAW[ingest_id] = (file_bytes, name)
    return {
        "ingestId": ingest_id,
        "name": name,
        "columns": list(df.columns),
        "rowCount": int(len(df)),
        "preview": _frame_preview(df),
        # Which sheets the user may switch between, and which one produced
        # the columns/preview above. Both empty/None for a CSV.
        "sheets": sheets,
        "sheet": sheet if (sheet and sheet in sheets) else _default_sheet(sheets),
    }


def _ingest_raw_bytes(ingest_id: str) -> tuple[bytes, str]:
    """Return (file_bytes, name) for a registered ingest, only used by
    Save/Share (and the sheet picker, for a real upload). A normal file
    upload already has real bytes from the moment it's registered; a merge
    or warehouse-fetch result doesn't (see _register_ingest), so this
    computes and caches its CSV serialization here, the first time it's
    actually needed, rather than eagerly for every such ingest regardless of
    whether anyone ever saves or shares it."""
    raw = INGEST_RAW.get(ingest_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="ingestId not found (upload may have expired).")
    file_bytes, name = raw
    if file_bytes is not None:
        return file_bytes, name
    df = INGESTS.get(ingest_id)
    if df is None:
        raise HTTPException(status_code=404, detail="ingestId not found (upload may have expired).")
    file_bytes = df.to_csv(index=False).encode("utf-8")
    INGEST_RAW[ingest_id] = (file_bytes, name)
    return file_bytes, name


@app.post("/api/ingest")
async def ingest_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """Accept a real uploaded CSV/XLSX, parse it server-side (the same pandas
    path the Streamlit app uses), and return its columns/preview plus an
    ``ingestId`` that the analyse endpoints use to run the engine on the
    actual uploaded data. Nothing here re-implements parsing logic - it wraps
    the shared pandas read helpers."""
    name = file.filename or "upload"
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file.")
    if not name.lower().endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(
            status_code=400, detail="Only CSV or Excel (.xlsx/.xls) files are supported."
        )
    return _json_safe(_register_ingest(file_bytes, name))


class SelectSheetBody(BaseModel):
    sheet: str = Field(..., description="Sheet name to re-parse this upload at.")


@app.post("/api/ingest/{ingest_id}/sheet")
def select_ingest_sheet(ingest_id: str, body: SelectSheetBody) -> dict[str, Any]:
    """Re-parse an already-uploaded workbook at a different sheet, for the
    "this file has multiple sheets - which one has your loan-level data?"
    picker (app.py's `_read_tabular_file` shows the same choice). Re-uses the
    stored original bytes rather than making the browser upload the file
    again, and keeps the same ingestId."""
    file_bytes, name = _ingest_raw_bytes(ingest_id)
    # An invalid sheet name surfaces as a 422 from _parse_upload's own check
    # (via _register_ingest's existing try/except) rather than a separate
    # pre-check here - that used to mean opening the workbook (expensive on
    # a large one, see _parse_upload's docstring) an extra time just to
    # validate before opening it again to actually parse.
    return _json_safe(_register_ingest(file_bytes, name, sheet=body.sheet, ingest_id=ingest_id))


class MergeIngestBody(BaseModel):
    primaryIngestId: str
    secondaryIngestId: str
    mode: Literal["join", "stack"] = Field(
        ...,
        description=(
            "'join': add the secondary file's columns onto the primary file's rows, matched by a "
            "key column in each (e.g. a loan tape + a separate payments file) - row count stays "
            "the primary file's row count. 'stack': append the secondary file's rows onto the "
            "primary file's rows as more rows (e.g. two months of the same loan-tape export) - "
            "row count is primary + secondary; mismatched columns are unioned, blank where a file "
            "doesn't have that column."
        ),
    )
    primaryKey: Optional[str] = Field(
        None, description="Join-key column in the primary file (mode='join')."
    )
    secondaryKey: Optional[str] = Field(
        None, description="Join-key column in the secondary file (mode='join')."
    )
    aggregation: Literal["sum", "last"] = Field(
        "sum",
        description=(
            "mode='join' only: how to collapse the secondary file to one row per key before "
            "joining, when it has more than one row per key (e.g. a raw payments/transactions "
            "file): 'sum' adds up numeric columns and takes the first non-key value of the rest "
            "(a payments file -> one 'total paid'-style row per loan); 'last' just keeps each "
            "key's last row as-is (a snapshot/status file where only the latest row per key matters)."
        ),
    )


@app.post("/api/ingest/merge")
def merge_ingest(body: MergeIngestBody) -> dict[str, Any]:
    """Combine two already-ingested files into one dataset, either 'stack'ed
    as more rows (two exports of the same shape) or 'join'ed as more columns
    matched by a key (the template engines want one row per loan with fields
    like Total Paid already aggregated - see config.py's field_aliases - so a
    raw second file like payment transactions can't just sit side by side
    with the loan tape; it has to be collapsed to one row per join key
    first, then left-joined on). Produces a normal CSV ingest (new
    ingestId) from the result, so nothing downstream - column mapping,
    save/resume/share, analysis - needs any special handling for a merged
    dataset; it's indistinguishable from a single-file upload from here."""
    df1 = INGESTS.get(body.primaryIngestId)
    df2 = INGESTS.get(body.secondaryIngestId)
    if df1 is None or df2 is None:
        raise HTTPException(status_code=404, detail="ingestId not found (upload may have expired).")

    if body.mode == "stack":
        merged = pd.concat([df1, df2], ignore_index=True)
    else:
        if not body.primaryKey or not body.secondaryKey:
            raise HTTPException(
                status_code=422, detail="primaryKey and secondaryKey are required to join."
            )
        if body.primaryKey not in df1.columns:
            raise HTTPException(
                status_code=422, detail=f"'{body.primaryKey}' isn't a column in the primary file."
            )
        if body.secondaryKey not in df2.columns:
            raise HTTPException(
                status_code=422, detail=f"'{body.secondaryKey}' isn't a column in the second file."
            )

        other_cols = [c for c in df2.columns if c != body.secondaryKey]
        if body.aggregation == "sum":
            agg_map = {
                c: ("sum" if pd.api.types.is_numeric_dtype(df2[c]) else "first") for c in other_cols
            }
        else:
            agg_map = {c: "last" for c in other_cols}
        agg_df2 = (
            df2.groupby(body.secondaryKey, as_index=False).agg(agg_map)
            if agg_map
            else df2[[body.secondaryKey]].drop_duplicates()
        )

        merged = df1.merge(
            agg_df2,
            how="left",
            left_on=body.primaryKey,
            right_on=body.secondaryKey,
            suffixes=("", "_2"),
        )
        if body.secondaryKey != body.primaryKey and body.secondaryKey in merged.columns:
            merged = merged.drop(columns=[body.secondaryKey])

    primary_name = INGEST_RAW[body.primaryIngestId][1]
    merged_name = (
        primary_name
        if primary_name.lower().endswith(".csv")
        else f"{primary_name.rsplit('.', 1)[0]}_merged.csv"
    )
    return _json_safe(_register_ingest(None, merged_name, df=merged))


# Snowflake-backed tables, via the same warehouse Query API app.py's
# "Redshift (Warehouse)" data source already talks to (see
# scripts/integrations.py and handover_snowflake_api_link.md) - kept named
# "redshift" throughout for the same backward-compatibility reason that doc
# gives (matches the API's own deployed env vars/secrets).
@app.get("/api/redshift/config")
def redshift_config() -> dict[str, bool]:
    return {"available": bool(_get_redshift_api_url() and _get_redshift_api_key())}


def _redshift_creds() -> tuple[str, str]:
    url, key = _get_redshift_api_url(), _get_redshift_api_key()
    if not url or not key:
        raise HTTPException(
            status_code=503, detail="The warehouse API isn't configured on this server."
        )
    return url, key


_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_]+")


@app.get("/api/redshift/schemas")
def redshift_schemas(database: str = "MAESTRO_SRC") -> list[str]:
    """Every schema under `database`, for the "Other table" mode's schema
    dropdown - unlike /borrowers (schemas that happen to own a LOANS table),
    this covers e.g. SALMON, which has no LOANS table."""
    url, key = _redshift_creds()
    if not _IDENTIFIER_RE.fullmatch(database):
        raise HTTPException(status_code=422, detail="Database must be a plain identifier.")
    try:
        return _redshift_list_schemas(url, key, database)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502, detail=f"Couldn't list schemas from the warehouse: {exc}"
        ) from exc


@app.get("/api/redshift/tables")
def redshift_tables(schema: str, database: str = "MAESTRO_SRC") -> list[str]:
    """Every table in `database.schema`, for the "Other table" mode's table
    dropdown, once a schema is picked."""
    url, key = _redshift_creds()
    if not (_IDENTIFIER_RE.fullmatch(database) and _IDENTIFIER_RE.fullmatch(schema)):
        raise HTTPException(status_code=422, detail="Database/schema must be plain identifiers.")
    try:
        return _redshift_list_tables(url, key, schema, database)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502, detail=f"Couldn't list tables from the warehouse: {exc}"
        ) from exc


class RedshiftFilterBody(BaseModel):
    schemaName: str = Field(..., description="Schema (e.g. 'SALMON').")
    table: str
    database: str = "MAESTRO_SRC"
    request: str = Field(
        ...,
        description="Natural-language filter description, e.g. 'active loans from 2024 onwards'.",
    )


@app.post("/api/redshift/filter")
def redshift_filter(body: RedshiftFilterBody) -> dict[str, Any]:
    """AI-assisted filter translation (ai_client._suggest_redshift_filter):
    turn a natural-language description into a SQL WHERE condition, grounded
    in the target table's real column names (via /api/redshift/columns's
    underlying helper). Shown to the user to review/edit before it's ever
    run against the warehouse - see redshift_table's `where` param. Returns
    {"where": None} (not an error) when no DEEPINFRA_API_KEY is configured
    or the model declined as too vague, mirroring /api/ai/derive-column."""
    url, key = _redshift_creds()
    if not (
        _IDENTIFIER_RE.fullmatch(body.database)
        and _IDENTIFIER_RE.fullmatch(body.schemaName)
        and _IDENTIFIER_RE.fullmatch(body.table)
    ):
        raise HTTPException(
            status_code=422, detail="Database/schema/table must be plain identifiers."
        )
    try:
        columns = _redshift_list_columns(url, key, body.schemaName, body.table, body.database)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502, detail=f"Couldn't list columns from the warehouse: {exc}"
        ) from exc
    try:
        result = _suggest_redshift_filter(
            f"{body.database}.{body.schemaName}.{body.table}", columns, body.request
        )
    except requests.RequestException as exc:
        raise _ai_error(exc) from exc
    return {"where": result.get("where") if result else None}


class RedshiftTableStartBody(BaseModel):
    schemaName: str = Field(..., description="Schema (e.g. 'SALMON').")
    table: str
    database: str = "MAESTRO_SRC"
    limit: int = 100_000
    where: Optional[str] = None


def _validate_redshift_identifiers(database: str, schema: str, table: str) -> None:
    if not (
        _IDENTIFIER_RE.fullmatch(database)
        and _IDENTIFIER_RE.fullmatch(schema)
        and _IDENTIFIER_RE.fullmatch(table)
    ):
        raise HTTPException(
            status_code=422,
            detail="Database/schema/table must be plain identifiers (letters, digits, underscores only).",
        )


@app.post("/api/redshift/table/start")
def redshift_table_start(body: RedshiftTableStartBody) -> dict[str, str]:
    """Start a table fetch as a background job on the warehouse API and
    return its job id, so the client can poll /api/redshift/table/status for
    real progress (partitions fetched so far) instead of one request that
    blocks silently for however long a big table takes - see
    integrations._redshift_start_table_job."""
    url, key = _redshift_creds()
    _validate_redshift_identifiers(body.database, body.schemaName, body.table)
    try:
        job_id = _redshift_start_table_job(
            url, key, body.database, body.schemaName, body.table, body.limit, body.where
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't start the fetch: {exc}") from exc
    return {"jobId": job_id}


@app.get("/api/redshift/table/status")
def redshift_table_status(jobId: str, schema: str, table: str) -> dict[str, Any]:
    """Poll one job started by /api/redshift/table/start. While running,
    returns partition progress only; once done, also registers the result as
    a normal ingest (same as the synchronous /api/redshift/table) and
    returns that payload alongside the final progress counts."""
    url, key = _redshift_creds()
    try:
        status_payload = _redshift_job_status(url, key, jobId)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502, detail=f"Couldn't check fetch progress: {exc}"
        ) from exc

    result: dict[str, Any] = {
        "status": status_payload["status"],
        "partitionsDone": status_payload.get("partitionsDone", 0),
        "partitionsTotal": status_payload.get("partitionsTotal", 1),
    }
    if status_payload["status"] == "error":
        result["error"] = status_payload.get("error") or "The fetch failed."
    elif status_payload["status"] == "done":
        df = pd.DataFrame(status_payload["rows"], columns=status_payload["columns"])
        if df.empty:
            result["error"] = f"{schema}.{table} came back empty."
            result["status"] = "error"
        else:
            result.update(_json_safe(_register_ingest(None, f"{schema}_{table}.csv", df=df)))
    return result


class ContextDocument(BaseModel):
    """One supporting document's extracted text, as returned by
    /api/context-docs and round-tripped back to the AI endpoints below."""

    name: str
    text: str = ""
    error: Optional[str] = None


@app.post("/api/context-docs")
async def extract_context_docs(files: list[UploadFile] = File(...)) -> list[dict[str, Any]]:
    """Extract readable text from supporting documents (PDF/Excel/Word/text)
    uploaded for "Additional context for the AI" - the same parsers app.py's
    uploader uses (scripts/document_text.py), so an unsupported file type or
    a parse failure comes back as a per-file error instead of failing the
    whole request."""
    out = []
    for f in files:
        data = await f.read()
        out.append(document_text.extract_document_text(f.filename or "document", data))
    return out


def _frame_preview(df: pd.DataFrame) -> list[list[Any]]:
    """First ~6 rows as lists, JSON-safe (used for the client's preview grid).

    Slices to 6 rows *before* computing the null mask - `pd.notna(df)` scans
    every cell of whatever's passed in, so doing it on the full frame first
    (as this used to) meant a 2M-row Snowflake fetch paid for a 2M-row null
    scan just to preview 6 rows of it."""
    head = df.head(6)
    rows: list[list[Any]] = []
    for rec in head.where(pd.notna(head), None).to_dict(orient="records"):
        row = []
        for v in rec.values():
            if v is None:
                row.append(None)
            elif isinstance(v, pd.Timestamp):
                row.append(v.isoformat())
            elif hasattr(v, "item"):
                try:
                    row.append(v.item())
                except (ValueError, OverflowError):
                    row.append(v)
            else:
                row.append(v)
        rows.append(list(row))
    return rows


def _canonical_rows_from_mapping(
    raw: pd.DataFrame,
    mapping: dict[str, str],
    derived_columns: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Build canonical (template-field-keyed) rows from a raw uploaded DataFrame
    and the user's field->source-column mapping. Mirrors app.py's mapping form:
    each template field pulls its source column's values into a canonical key.
    Missing columns / unmapped required fields surface as validation errors.
    This only selects/reorders columns - no analysis math lives here. Optional
    ``derived_columns`` ([{name, formula}]) are computed deterministically onto
    the raw frame first, so a derived name becomes a first-class source column
    the mapping can reference (see derive_columns.py)."""
    if derived_columns:
        from .derive_columns import FormulaError, apply_derived_columns

        try:
            raw = apply_derived_columns(raw, derived_columns)
        except FormulaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not mapping:
        raise HTTPException(status_code=422, detail="No column mapping provided.")
    reverse: dict[str, list[str]] = {}
    for field, src in mapping.items():
        if not src:
            continue
        reverse.setdefault(src, []).append(field)
    missing_srcs = [src for src in reverse if src not in raw.columns]
    if missing_srcs:
        raise HTTPException(
            status_code=422,
            detail=f"Mapping references missing source columns: {', '.join(missing_srcs)}",
        )
    data: dict[str, list[Any]] = {}
    for src, fields in reverse.items():
        for f in fields:
            data[f] = raw[src].tolist()
    rows = [dict(zip(data, vals)) for vals in zip(*data.values())] if data else []
    return rows


def _derived_dicts(derived: Optional[list]) -> list[dict[str, Any]]:
    """Normalise a list of DerivedColumn pydantic models (or plain dicts) into
    [{name, formula}] for apply_derived_columns."""
    if not derived:
        return []
    out = []
    for d in derived:
        name = getattr(d, "name", None) or (d.get("name") if isinstance(d, dict) else None)
        formula = getattr(d, "formula", None) or (d.get("formula") if isinstance(d, dict) else None)
        if name and formula:
            out.append({"name": name, "formula": formula})
    return out


# Cap on rows returned to the Data-Checks "Data Input" grid - the React
# Table isn't virtualised like Streamlit's ag-grid, so a huge tape is shown
# in full only up to this many rows (the true total is still reported).
_DATA_GRID_LIMIT = 5000


class DataGridBody(BaseModel):
    ingestId: str = Field(..., description="Id returned by POST /api/ingest.")
    mapping: dict[str, str] = Field(
        ..., description="Template field key -> raw source column name."
    )
    derivedColumns: list[DerivedColumn] = Field(default_factory=list)


@app.post("/api/ingest-data")
def ingest_data(body: DataGridBody) -> dict[str, Any]:
    """Return the mapped canonical rows for the current ingest + mapping, so
    the Data-Checks "Data Input" nested tab can render the uploaded data grid
    (mirrors app.py's `_render_data_input_grid(raw)`)."""
    raw = INGESTS.get(body.ingestId)
    if raw is None:
        raise HTTPException(status_code=404, detail="ingestId not found (upload may have expired).")
    rows = _canonical_rows_from_mapping(raw, body.mapping, _derived_dicts(body.derivedColumns))
    row_count = len(rows)
    truncated = row_count > _DATA_GRID_LIMIT
    return _json_safe(
        {
            "columns": list(rows[0].keys()) if rows else [],
            "rows": rows[:_DATA_GRID_LIMIT],
            "rowCount": row_count,
            "truncated": truncated,
        }
    )


LENDING_DATE_FIELDS = {"Disbursement Date", "Expected Completion Date"}

# Canonical money/numeric fields the engine does arithmetic on directly (UE
# analysis divides these; process_data_input only coerces a couple itself).
# Mirrors app.py's LENDING_CONFIG["numeric_fields"] - and the Streamlit path
# coerces with the same _clean_numeric()/_coerce_dates() helpers, so this keeps
# the two entry points consistent when a source file formats money as quoted,
# comma-separated strings like "85,000" instead of plain numbers.
LENDING_NUMERIC_FIELDS = {
    "Principal Value",
    "Expected Interest",
    "Gross Loan Value",
    "Expected Fee",
    "Total Paid",
    "Total Due",
    "Days Late",
    "Term (days)",
}


def _resolve_lending_df(body: LendingAnalyseBody) -> pd.DataFrame:
    """Return the canonical template-field-keyed DataFrame to analyse, from
    either a direct ``rows`` payload or an ``ingest`` (raw upload + mapping).

    Date columns are coerced here, before returning - not left to
    process_data_input's own internal coercion - because GeneralInputs'
    extraction_date default (df["Disbursement Date"].max(), used whenever
    the caller omits extractionDate) reads the date column before
    process_data_input ever runs. A raw string column there breaks with a
    plain "str - Timedelta" TypeError deep in date arithmetic; mirrors
    app.py's own _coerce_dates(raw, DATE_FIELDS, dayfirst) call, which runs
    on the raw upload for the same reason."""
    if body.ingest:
        raw = INGESTS.get(body.ingest.ingestId)
        if raw is None:
            raise HTTPException(
                status_code=404, detail="ingestId not found (upload may have expired)."
            )
        mapped_rows = _canonical_rows_from_mapping(
            raw, body.ingest.mapping, _derived_dicts(body.ingest.derivedColumns)
        )
        missing = [f for f in REQUIRED_FIELDS if not any(f in (r or {}) for r in mapped_rows)]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"Missing required template fields in your mapping: {', '.join(missing)}",
            )
        df = pd.DataFrame(mapped_rows)
    else:
        if not body.rows:
            raise HTTPException(status_code=400, detail="No rows provided.")
        missing = [f for f in REQUIRED_FIELDS if not any(f in (r or {}) for r in body.rows)]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"Missing required template fields in payload rows: {', '.join(missing)}",
            )
        data = {f: [r.get(f) for r in body.rows] for f in REQUIRED_FIELDS}
        # Include any optional canonical columns present in the payload.
        for f in body.rows[0]:
            if f not in data:
                data[f] = [r.get(f) for r in body.rows]
        df = pd.DataFrame(data)
    # Data-Checks "Deduplicate" action (mirrors app.py's
    # raw.drop_duplicates(subset=id_field, keep="first") when the
    # duplicate_loan_id checkbox is checked) - applied to the canonical ID
    # field before dates are coerced, exactly like Streamlit.
    if body.dedupeById and "Loan ID" in df.columns:
        df = df.drop_duplicates(subset=["Loan ID"], keep="first")
    # Coerce the canonical money/numeric fields, same as app.py's Streamlit path
    # (app.py:2981-2983): a source file that quotes money with commas/thousands
    # separators ("85,000") parses as text, and the UE analysis divides these
    # columns bare - leaving them as strings blows up with a 500.
    for col in LENDING_NUMERIC_FIELDS:
        if col not in df.columns or df[col].dtype == "float64":
            continue
        if df[col].dtype.kind in "iuf":
            # Already numeric (read_csv infers int/float) - coerce without the
            # astype(str)+regex detour in _clean_numeric, which dominated wall
            # time on multi-million-row tapes. Dtype matches the old string
            # round-trip exactly: integer columns stay int64, float16/32 upcast
            # to float64 (pd.to_numeric on the raw numeric column would leave a
            # float32 alone, unlike the old str->to_numeric path).
            if df[col].dtype.kind == "f":
                df[col] = df[col].astype("float64")
            else:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            # Money-with-commas/percent text ("85,000", "$1,200"): strip the
            # currency junk, then parse - same as the previous behaviour.
            df[col] = pd.to_numeric(_clean_numeric(df[col]), errors="coerce")
    return _coerce_lending_dates(df, LENDING_DATE_FIELDS, dayfirst=True)


def _compute_lending(
    df: pd.DataFrame,
    gi_body: GeneralInputsBody,
    selected_cohorts: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Run the lending engine pipeline once and return every raw
    intermediate object (real DataFrames/GeneralInputs, not yet JSON-safe) -
    shared by both /api/analyse/lending (serializes to JSON) and
    /api/export/lending (feeds export_workbook.py's real Excel builder), so
    the pipeline itself runs in exactly one place."""
    gi_kwargs: dict[str, Any] = {
        "days_after_term": gi_body.daysAfterTerm,
        "min_loans_per_cohort": gi_body.minLoansPerCohort,
    }
    if gi_body.extractionDate:
        gi_kwargs["extraction_date"] = pd.Timestamp(gi_body.extractionDate)

    gi = GeneralInputs(df, **gi_kwargs)
    # A real per-loan "Term (days)" column supplied by the source file changes
    # which columns export_workbook.py writes as formulas vs raw values (see
    # its term_supplied param) - decided from the pre-process_data_input frame,
    # since process_data_input always adds a derived "Term (days)" itself.
    term_supplied = "Term (days)" in df.columns

    try:
        processed = process_data_input(df, gi.extraction_date, gi.days_after_term)
        # Cohorts-tab selection: restricts every downstream figure (cohorts
        # table itself, UE, LTV, Summary, checks, chat context) to just the
        # selected cohort-months, by filtering here - before build_cohorts/
        # UeAnalysis/LtvAnalysis ever see the rest. extraction_date above is
        # deliberately computed from the FULL, unfiltered df first (an
        # "as of" date for the whole portfolio, not scoped to the selection).
        if selected_cohorts:
            processed = processed[processed["Cohort"].isin(pd.to_datetime(selected_cohorts))]
        cohorts = build_cohorts(processed, min_matured=gi.min_loans_per_cohort)
        filtered = filter_cohorts(cohorts, gi.min_loans_per_cohort)
        ue = UeAnalysis(processed)
        ltv = LtvAnalysis(processed, filtered)
    except Exception as exc:  # engine failures -> 422 so the UI can surface them
        raise HTTPException(status_code=422, detail=f"Analysis failed: {exc}") from exc

    chart_data = cohorts.dropna(subset=["Loss Rate"]).copy()
    if not chart_data.empty:
        chart_data["Fee %"] = chart_data["Total Fee"] / chart_data["Total Principal"]
        chart_data["Interest %"] = chart_data["Total Interest"] / chart_data["Total Principal"]

    # The Summary tab's chart plots principal disbursed per cohort across ALL
    # loans (true origination volume) with the matured-only loss rate on top -
    # a different view from `cohorts` above, which is the Excel-faithful
    # matured-only pivot. Same call app.py's Summary tab makes.
    summary_cohorts = build_cohorts(
        processed, min_matured=gi.min_loans_per_cohort, matured_only=False
    )

    return {
        "gi": gi,
        "processed": processed,
        "cohorts": cohorts,
        "filtered": filtered,
        "ue": ue,
        "ltv": ltv,
        "chart_data": chart_data,
        "summary_cohorts": summary_cohorts,
        "term_supplied": term_supplied,
    }


def _ue_model_payload(processed: pd.DataFrame, ue_data: dict) -> dict[str, Any]:
    """Served to the React UE Model sub-tab. Mirrors app.py's
    _render_ue_model_ai_tab's data-derived inputs so the browser can recompute
    every dependent figure on the fly when the user edits the manual cost /
    loss-rate assumptions (the IRR and derived tables all derive from these
    raw matured-loan aggregates + the UE metric set)."""
    principal = float(processed["Principal Value"].sum())
    revenue = float(processed["Expected Interest"].sum())
    origination_income = float(processed["Expected Fee"].sum())

    matured = processed[processed["Reached T+3?"]]
    matured_principal = float(matured["Principal Value"].sum())
    matured_revenue = float(matured["Expected Interest"].sum())
    matured_origination_income = float(matured["Expected Fee"].sum())
    matured_owed = matured_principal + matured_revenue + matured_origination_income
    matured_paid = float(matured["Total Paid"].sum())

    n_matured = int(len(matured))

    def _nan_safe(v: float) -> float | None:
        return None if (v is None or (isinstance(v, float) and np.isnan(v))) else v

    actual_loss_rate = (
        (matured_owed - matured_paid) / matured_owed if matured_owed else float("nan")
    )
    matured_term_m = (
        (matured["Term (days)"] * matured["Principal Value"]).sum() / matured_principal / 30.4375
        if matured_principal
        else float("nan")
    )

    return {
        "averageInterestPct": ue_data["Average Interest %"],
        "averageFeePct": ue_data["Average Fee %"],
        "averageExpectedTerm": ue_data["Average Expected Term"],
        "lossRateProxy": ue_data["Loss Rate Proxy (1-PvD)"],
        "principal": _nan_safe(principal),
        "revenue": _nan_safe(revenue),
        "originationIncome": _nan_safe(origination_income),
        "actualLossRate": _nan_safe(actual_loss_rate),
        "maturedPrincipal": _nan_safe(matured_principal),
        "maturedRevenue": _nan_safe(matured_revenue),
        "maturedOriginationIncome": _nan_safe(matured_origination_income),
        "maturedOwed": _nan_safe(matured_owed),
        "maturedPaid": _nan_safe(matured_paid),
        "maturedTermM": _nan_safe(matured_term_m),
        "nMatured": n_matured,
        "nTotal": int(len(processed)),
        "maturedGbvPct": (_nan_safe(matured_principal / principal) if principal else None),
        "costDefaults": {k: float(v) for k, v in UE_MODEL_COST_DEFAULTS.items()},
    }


def _run_lending_engine(
    df: pd.DataFrame,
    gi_body: GeneralInputsBody,
    selected_cohorts: Optional[list[str]] = None,
) -> dict[str, Any]:
    c = _compute_lending(df, gi_body, selected_cohorts)
    cohorts, filtered, chart_data = c["cohorts"], c["filtered"], c["chart_data"]
    ue_data, ltv_data = c["ue"].as_dict(), c["ltv"].as_dict()
    checks = _lending_data_quality_checks(df, cohorts, LENDING_DQ_CONFIG, c["gi"], filtered)

    return _json_safe(
        {
            "summary": {
                "rows": int(len(c["processed"])),
                "cohorts": int(len(filtered)),
                "lossRate": round(float(cohorts["Loss Rate"].mean()), 4)
                if "Loss Rate" in cohorts and len(cohorts)
                else None,
            },
            "ue": ue_data,
            "ltv": ltv_data,
            # The Cohorts tab's own table - every matured-only monthly cohort,
            # NOT pre-filtered by min_loans_per_cohort (that's `filtered`, used
            # only for the LTV/UE calc above and the quality checks below); the
            # tab applies its own adjustable "Minimum loans per cohort" filter
            # client-side, same as app.py's `cohort_view` does.
            # build_cohorts() already returns a plain RangeIndex frame with
            # "Cohort" as a real column (`.reset_index(drop=True)` internally) -
            # calling .reset_index() again here would turn that clean RangeIndex
            # into a spurious extra "index" column, since _frame_to_records's
            # to_dict("records") never serializes the index itself.
            "cohorts": _frame_to_records(cohorts),
            # The same table with matured_only=False - app.py's Cohorts tab shows
            # this instead when "Matured loans only" is unchecked.
            "cohortsAllLoans": _frame_to_records(c["summary_cohorts"]),
            "chart": _frame_to_records(chart_data),
            "checks": checks,
            # The UE Model sub-tab's raw inputs (matured-loan aggregates + cost
            # defaults) - the browser recomputes the derived figures/IRR from
            # these when the user edits the manual assumptions, mirroring app.py's
            # interactive _render_ue_model_ai_tab.
            "ueModel": _ue_model_payload(c["processed"], ue_data),
            # The processed frame, not the raw resolved one - app.py's Summary tab
            # reads the same post-process_data_input columns (Reached T+3?, Term).
            "summaryMetrics": lending_summary_metrics(c["processed"], c["gi"], ue_data, ltv_data),
            "summaryChart": _frame_to_records(
                c["summary_cohorts"][
                    ["Cohort", "Total Principal", "Loan Count", "Matured Count", "Loss Rate"]
                ]
            ),
            "generalInputs": c["gi"].as_dict(),
        }
    )


@app.post("/api/analyse/lending")
def analyse_lending(body: LendingAnalyseBody) -> dict[str, Any]:
    df = _resolve_lending_df(body)
    return _run_lending_engine(df, body.generalInputs, body.selectedCohorts)


def _export_charts_payload(specs: list[ChartSpecBody]) -> list[dict[str, Any]]:
    """Convert the request's chart-card specs into the
    {data, x_col, y_col, color_col, kind, title, y_title} shape
    scripts/export_workbook.py's _write_custom_charts_sheet expects, with
    "data" turned into a real DataFrame - no chart-embedding logic
    re-implemented here."""
    return [
        {
            "data": pd.DataFrame(spec.data),
            "x_col": spec.xCol,
            "y_col": spec.yCol,
            "color_col": spec.colorCol,
            "kind": spec.kind,
            "title": spec.title,
            "y_title": spec.yCol,
        }
        for spec in specs
        if spec.data
    ]


@app.post("/api/export/lending")
def export_lending(body: LendingAnalyseBody) -> Response:
    """Same request shape as /api/analyse/lending (rows or ingest+mapping),
    but returns the real downloadable .xlsx from export_workbook.py's
    _build_lending_export_workbook instead of a JSON summary - no export
    logic re-implemented here, just wiring the same real pipeline's raw
    outputs into the existing (Streamlit-independent, verified working
    outside a script run) builder."""
    df = _resolve_lending_df(body)
    c = _compute_lending(df, body.generalInputs, body.selectedCohorts)
    ue_data, ltv_data = c["ue"].as_dict(), c["ltv"].as_dict()
    mapping = body.ingest.mapping if body.ingest else None
    xlsx_bytes = _build_lending_export_workbook(
        c["processed"],
        c["cohorts"],
        c["filtered"],
        body.generalInputs.daysAfterTerm,
        body.generalInputs.minLoansPerCohort,
        ltv_data,
        ue_data,
        c["chart_data"],
        _export_charts_payload(body.exportCharts),
        term_supplied=c["term_supplied"],
        mapping=mapping,
        extraction_date=c["gi"].extraction_date,
    )
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="SC_Analysis_Lending.xlsx"'},
    )


def _resolve_rental_df(body: RentalAnalyseBody) -> pd.DataFrame:
    """Return the canonical rental DataFrame from either ``ingest`` (raw
    upload + mapping) or direct ``rows``, with date columns coerced to
    datetime and absent optional date columns pre-declared as NaT so the
    engine's date arithmetic works under pandas 3.0."""
    if body.ingest:
        raw = INGESTS.get(body.ingest.ingestId)
        if raw is None:
            raise HTTPException(
                status_code=404, detail="ingestId not found (upload may have expired)."
            )
        mapped_rows = _canonical_rows_from_mapping(
            raw, body.ingest.mapping, _derived_dicts(body.ingest.derivedColumns)
        )
        missing = [
            c for c in RENTAL_REQUIRED_COLUMNS if not any(c in (r or {}) for r in mapped_rows)
        ]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"Missing required rental fields in your mapping: {', '.join(missing)}",
            )
        df = pd.DataFrame(mapped_rows)
    else:
        if not body.rows:
            raise HTTPException(status_code=400, detail="No rows provided.")
        missing = [c for c in RENTAL_REQUIRED_COLUMNS if not any(c in (r or {}) for r in body.rows)]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"Missing required rental fields in payload rows: {', '.join(missing)}",
            )
        data = {c: [r.get(c) for r in body.rows] for c in RENTAL_REQUIRED_COLUMNS}
        for c in body.rows[0]:
            if c not in data:
                data[c] = [r.get(c) for r in body.rows]
        df = pd.DataFrame(data)

    # Data-Checks "Deduplicate" action (mirrors app.py's
    # raw.drop_duplicates(subset=id_field, keep="first") when the duplicate
    # contract checkbox is checked) - applied to the canonical ID field.
    if body.dedupeById and "contract_id" in df.columns:
        df = df.drop_duplicates(subset=["contract_id"], keep="first")

    # The rental engine expects date columns as datetime (the Streamlit app
    # normalises them during ingestion); coerce here the same way, and ensure
    # absent optional date columns are real datetime64 (NaT) so the engine's
    # date arithmetic and .fillna paths work under pandas 3.0.
    for c in df.columns:
        if c in {
            "start_date",
            "expected_end_date",
            "closed_date",
            "asset_recovery_date",
            "recovery_date",
            "last_payment_date",
        }:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in {
        "expected_end_date",
        "closed_date",
        "asset_recovery_date",
        "recovery_date",
        "last_payment_date",
    }:
        if c not in df.columns:
            df[c] = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    return df


def _compute_rental(df: pd.DataFrame, gi_body: RentalGeneralInputsBody) -> dict[str, Any]:
    """Run the rental engine pipeline once and return every raw intermediate
    object - shared by /api/analyse/rental (serializes to JSON) and
    /api/export/rental (feeds export_workbook.py's real Excel builder), so
    the pipeline itself runs in exactly one place (same pattern as
    _compute_lending)."""
    gi = RentalGeneralInputs(
        df,
        extraction_date=(pd.Timestamp(gi_body.extractionDate) if gi_body.extractionDate else None),
        days_after_term=gi_body.daysAfterTerm,
        months_since_default=gi_body.monthsSinceDefault,
        min_loans_per_cohort=gi_body.minLoansPerCohort,
        useful_life_years=gi_body.usefulLifeYears,
        churn_stress_multiplier=gi_body.churnStressMultiplier,
        funding_cost_pct=gi_body.fundingCostPct,
        direct_cost_pct=gi_body.directCostPct,
        fx_deval_pct=gi_body.fxDevalPct,
        fx_discount_50pct=gi_body.fxDiscount50pct,
        debt_service_180d=gi_body.debtService180d,
    )
    gi_calc = gi.as_calc_dict()

    try:
        fallback_df, _fallback_notes = rental_apply_fallbacks(
            df, gi_calc["useful_life_years"], gi_calc["extraction_date"]
        )
        processed = rental_process_data_input(fallback_df, gi_calc)
        av = build_asset_view(processed, gi_calc)
        curve = build_repayment_curve(av, gi_calc)
        cohorts = rental_build_cohorts(processed, gi_calc)
        filtered = rental_filter_cohorts(cohorts, gi_calc["min_loans_per_cohort"])
        churn = ChurnAnalysis(cohorts, gi_calc)
        ue = RentalUeAnalysis(processed, av, curve, gi_calc, churn)
        ltv = RentalLtvAnalysis(processed, churn, gi_calc, av)
        ts = build_ts_covenants(
            ue.as_dict(), ltv.as_dict(), curve, gi_calc.get("debt_service_180d")
        )
        general_data = rental_general_analysis(processed)
    except Exception as exc:  # engine failures -> 422 so the UI can surface them
        raise HTTPException(status_code=422, detail=f"Analysis failed: {exc}") from exc

    return {
        "gi": gi,
        "gi_calc": gi_calc,
        "processed": processed,
        "av": av,
        "curve": curve,
        "cohorts": cohorts,
        "filtered": filtered,
        "ue": ue,
        "ltv": ltv,
        "churn": churn,
        "ts": ts,
        "general_data": general_data,
    }


@app.post("/api/analyse/rental")
def analyse_rental(body: RentalAnalyseBody) -> dict[str, Any]:
    df = _resolve_rental_df(body)
    c = _compute_rental(df, body.generalInputs)
    ue_data, ltv_data, churn_data = c["ue"].as_dict(), c["ltv"].as_dict(), c["churn"].as_dict()
    checks = _rental_data_quality_checks(
        df, c["cohorts"], RENTAL_DQ_CONFIG, c["gi"], c["filtered"], ue_data
    )

    return _json_safe(
        {
            "summary": {
                "rows": int(len(c["processed"])),
                "cohorts": int(len(c["filtered"])),
            },
            "ue": ue_data,
            "ltv": ltv_data,
            "churn": churn_data,
            "ts": c["ts"],
            "general": c["general_data"],
            "assetView": _frame_to_records(c["av"].reset_index()),
            "cohortRows": _frame_to_records(c["filtered"].reset_index()),
            # High-level repayment curve (mob vs % avg collection) - the rental UE
            # tab's "High level repayment curve" chart + table, mirroring app.py's
            # tabs[3] (Unit Economics Analysis).
            "curveRows": _frame_to_records(c["curve"]),
            "checks": checks,
            "summaryMetrics": rental_summary_metrics(
                c["processed"], c["av"], c["gi"], ue_data, ltv_data, c["ts"]
            ),
            "generalInputs": c["gi_calc"],
        }
    )


class EscalateVarianceBody(BaseModel):
    modelKey: str = Field(
        "lending", description="'lending' or 'rental' (labels the Slack message)."
    )
    modelName: str = Field(
        "Lending", description="Human label, e.g. 'Lending' or 'Rental & Subscription'."
    )
    metricLabel: str = Field(
        "Loss Rate (%)",
        description="Column in `detail` holding the swung metric, e.g. 'Loss Rate (%)'.",
    )
    filename: str = Field("", description="Uploaded file name, for the AI borrower guess.")
    detail: list[dict[str, Any]] = Field(
        default_factory=list,
        description="The unexplained_variance check's detail rows: Cohort, <metricLabel>, Swing vs Prior Cohort (pp), Likely Contributing Factor(s).",
    )


@app.post("/api/analyse/escalate")
def escalate_variance(body: EscalateVarianceBody) -> dict[str, Any]:
    """Draft and send the "escalate flagged cohorts to analytics" write-up for
    the Data-Checks unexplained_variance finding - mirrors app.py's
    _render_variance_escalation: format the flagged cohort facts, ask DeepSeek
    for a borrower-guessing Slack write-up (falling back to a plain summary
    when the AI call fails or no key is set), then POST to the configured
    Slack webhook. Returns the drafted message whether or not the send
    succeeded, plus `sent` and any send/AI errors so the UI can mirror
    Streamlit's "Sent to analytics." / "Could not escalate: ..." states."""
    lines = []
    for r in body.detail:
        cohort = str(r.get("Cohort", "")).strip()
        if not cohort:
            continue
        metric = r.get(body.metricLabel)
        swing = r.get("Swing vs Prior Cohort (pp)")
        factor = r.get("Likely Contributing Factor(s)") or "n/a"
        metric_s = "" if metric is None else f"{metric}%"
        swing_s = "" if swing is None else f"({float(swing):+.1f}pp swing)"
        lines.append(f"- {cohort}: {metric_s} {swing_s} - {factor}".replace("  ", " ").strip())

    if not lines:
        # No flagged cohorts to draft from - refuse rather than let the AI
        # fabricate facts out of nothing.
        return {
            "borrowerName": None,
            "message": "",
            "aiWarning": None,
            "sent": False,
            "error": "No flagged cohorts to escalate.",
        }

    borrower_name, message, ai_warning = None, None, None
    try:
        ai = _suggest_escalation_writeup(body.filename, lines, body.modelName)
        borrower_name = ai.get("borrower_name")
        message = ai.get("message")
    except Exception as exc:
        ai_warning = str(exc)
    if not message:
        message = (
            f"Unexplained variance flagged in {body.modelName} screening analysis:\n"
            + "\n".join(lines)
        )

    ok, err = _send_slack_feedback(message, borrower_name or body.modelName)
    return {
        "borrowerName": borrower_name,
        "message": message,
        "aiWarning": ai_warning,
        "sent": bool(ok),
        "error": None if ok else err,
    }


def _ltv_calc_options() -> dict[str, Any]:
    return {
        "dataInputSources": LTV_CALC_DATA_SOURCES,
        "countries": LTV_CALC_COUNTRIES,
        "fxRiskRatings": LTV_CALC_FX_RISK_RATINGS,
        "segmentations": LTV_CALC_SEGMENTATIONS,
        "hedgeTypes": LTV_CALC_HEDGE_TYPES,
        "tenors": LTV_CALC_TENORS,
    }


class LtvCalcBody(BaseModel):
    dataSource: str = Field("Observed", description="'Observed' or 'Self-reported'.")
    country: str = Field(
        "Ghana",
        description="Alt Lender Country (default = first option, as Streamlit's selectbox).",
    )
    macroFxRisk: str = Field("Green", description="Macro FX Risk Rating: Green/Orange/Red.")
    segmentation: str = Field("Asset Lending", description="Segmentation.")
    hedgeRate: float = Field(
        0.0, ge=0.0, le=1.0, description="Hedge rate (0 if unhedged, else % OTM as a decimal)."
    )
    rolledHedge: str = Field("Roll", description="'Roll' or 'Term'.")
    averageInterest: Optional[float] = Field(
        None, description="Weighted avg gross interest (decimal), from the analysis."
    )
    termDays: Optional[float] = Field(
        None, description="Weighted avg receivable term (days), from the analysis."
    )
    lossRate: Optional[float] = Field(
        None, description="95th %ile loss rate (decimal) at T+3, from the analysis."
    )


@app.post("/api/ltv-calculator")
def ltv_calculator(body: LtvCalcBody) -> dict[str, Any]:
    """Mirror app.py's lending "LTV Analysis" tab (the LTV Calculator): snap
    the portfolio's weighted-avg term to a tenor bucket, pick the 95th %ile
    loss (falling back to the sector-minimum floor when it's n/a), then run
    the Receivables-sheet waterfall via template_analysis.ltv_calculator and
    return every output plus the dropdown options and the FX-by-tenor table
    so the React tab can render the waterfall matrix, backup tables and
    charts exactly like Streamlit."""
    green_interest = body.averageInterest
    green_term_days = body.termDays
    green_loss = body.lossRate
    green_term_bucket = None
    if green_term_days is not None and not pd.isna(green_term_days):
        green_term_bucket = ltv_calculator_nearest_tenor(green_term_days / 30.4375)

    loss_rate_fallback_notes: list[str] = []
    if green_loss is None or pd.isna(green_loss):
        green_loss, floor_notes = ltv_calculator_sector_min_loss_rate(
            body.segmentation, body.dataSource
        )
        loss_rate_fallback_notes = [
            "No computable Loss Rate for this portfolio (95th %ile Loss is n/a) - using the "
            f"Min Sector Loss Rate for '{body.segmentation}' ({body.dataSource}) instead: "
            f"{green_loss:.1%}."
        ] + floor_notes

    if (
        green_interest is None
        or pd.isna(green_interest)
        or green_loss is None
        or pd.isna(green_loss)
        or green_term_bucket is None
    ):
        return {
            "options": _ltv_calc_options(),
            "ok": False,
            "warning": "Can't compute the LTV waterfall - one of the green inputs above is n/a for this portfolio.",
            "greenInterest": green_interest,
            "greenTermBucket": green_term_bucket,
            "greenLoss": green_loss,
            "lossRateFallbackNotes": loss_rate_fallback_notes,
            "result": None,
            "fxCountryData": None,
        }

    result = ltv_calculator_compute(
        gross_interest=green_interest,
        term_months=green_term_bucket,
        loss_rate=green_loss,
        country=body.country,
        macro_fx_risk=body.macroFxRisk,
        segmentation=body.segmentation,
        hedge_rate=body.hedgeRate,
        rolled_hedge=body.rolledHedge,
        data_source=body.dataSource,
    )
    return {
        "options": _ltv_calc_options(),
        "ok": True,
        "warning": None,
        "greenInterest": green_interest,
        "greenTermBucket": green_term_bucket,
        "greenLoss": green_loss,
        "lossRateFallbackNotes": loss_rate_fallback_notes,
        "result": result,
        "fxCountryData": {
            "tenors": LTV_CALC_TENORS,
            "values": LTV_CALC_FX_DEVAL_TABLE[body.country],
        },
    }


@app.post("/api/export/rental")
def export_rental(body: RentalAnalyseBody) -> Response:
    """Same request shape as /api/analyse/rental (rows or ingest+mapping), but
    returns the real downloadable .xlsx from export_workbook.py's
    _build_rental_export_workbook instead of a JSON summary - no export logic
    re-implemented here (mirrors /api/export/lending)."""
    df = _resolve_rental_df(body)
    c = _compute_rental(df, body.generalInputs)
    ue_data, ltv_data, churn_data = c["ue"].as_dict(), c["ltv"].as_dict(), c["churn"].as_dict()
    mapping = body.ingest.mapping if body.ingest else None
    xlsx_bytes = _build_rental_export_workbook(
        c["gi"],
        c["processed"],
        c["av"],
        c["curve"],
        c["cohorts"],
        c["filtered"],
        ue_data,
        ltv_data,
        churn_data,
        c["ts"],
        _export_charts_payload(body.exportCharts),
        mapping=mapping,
    )
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="SC_Analysis_Rental.xlsx"'},
    )


class SuggestMappingBody(BaseModel):
    modelKey: str = Field(..., description="'lending' or 'rental'.")
    columns: list[str] = Field(..., description="Raw uploaded file's column headers.")
    context: str = ""
    documents: list[ContextDocument] = Field(default_factory=list)
    knownLoanStatusColumn: Optional[str] = None


@app.post("/api/ai/suggest-mapping")
def suggest_mapping(body: SuggestMappingBody) -> dict[str, Any]:
    """AI-assisted column-mapping auto-fill (ai_client._suggest_mapping), the
    same DeepSeek V4 Flash call app.py's "Auto-fill with AI" button makes -
    no mapping logic re-implemented here. Returns an empty mapping (not an
    error) when no DEEPINFRA_API_KEY is configured, exactly like the
    Streamlit app; the frontend falls back to its own name-matching
    heuristic in that case."""
    config = LENDING_DQ_CONFIG if body.modelKey == "lending" else RENTAL_DQ_CONFIG
    # Same 20000/6000 truncation app.py's mapping-suggestion call uses
    # (_ai_context_text(max_chars=20000, per_doc=6000)) - keeps this cheaper,
    # more frequent call's tokens bounded tighter than the chat endpoints below.
    context_text = document_text.combine_context_text(
        body.context, [d.model_dump() for d in body.documents], max_chars=20000, per_doc=6000
    )
    mapping, exclude_suggestion, active_status_values = _suggest_mapping(
        body.columns, config["input_columns"], context_text, body.knownLoanStatusColumn
    )
    return {
        "mapping": mapping,
        "excludeSuggestion": exclude_suggestion,
        "activeStatusValues": active_status_values,
    }


class DeriveColumnBody(BaseModel):
    modelKey: str = Field(..., description="'lending' or 'rental'.")
    columns: list[str] = Field(
        ...,
        description="Raw uploaded file's column headers (the formula may only reference these).",
    )
    request: str = Field(
        ...,
        description="User's natural-language request, e.g. 'Expected Interest = Gross Loan Value - Principal Value'.",
    )


@app.post("/api/ai/derive-column")
def derive_column(body: DeriveColumnBody) -> dict[str, Any]:
    """AI-assisted column derivation (ai_client._suggest_derived_column): turn
    the user's natural-language request into a safe arithmetic formula over the
    file's real columns, e.g. {name: "Expected Interest", formula: "Gross Loan
    Value - Principal Value"}. The LLM only writes the formula - the value is
    computed deterministically server-side by web_api/derive_columns.py when the
    mapping is applied. Returns nulls (not an error) when no DEEPINFRA_API_KEY
    is configured, mirroring /api/ai/suggest-mapping; the frontend tells the
    user to type a formula manually in that case."""
    try:
        result = _suggest_derived_column(body.columns, body.request)
    except requests.RequestException as exc:
        raise _ai_error(exc) from exc
    return {
        "name": result.get("name") if result else None,
        "formula": result.get("formula") if result else None,
    }


class ChatMessage(BaseModel):
    role: str
    content: str


class LendingChatBody(LendingAnalyseBody):
    question: str
    history: list[ChatMessage] = Field(default_factory=list)
    userContext: str = ""
    documents: list[ContextDocument] = Field(default_factory=list)


class RentalChatBody(RentalAnalyseBody):
    question: str
    history: list[ChatMessage] = Field(default_factory=list)
    userContext: str = ""
    documents: list[ContextDocument] = Field(default_factory=list)


def _ai_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=502, detail=f"AI request failed: {exc}")


def _build_lending_chat_context_lite(
    gi,
    df: pd.DataFrame,
    cohorts: pd.DataFrame,
    ue_data: dict,
    ltv_data: dict,
    checks: list,
    user_context: str = "",
) -> str:
    """A reduced version of scripts/chat_context.py's
    _build_lending_chat_context: same portfolio/cohort/UE/LTV/checks
    sections, minus the "UE Model (AI)" section, which app.py computes
    inside a Streamlit-rendered tab (_render_ue_model_ai_tab) that isn't
    exposed by this API yet - that tab isn't built in the React app either.
    Chat answers here are correspondingly missing that section's figures
    (matured-only income/margin, implied APR) until it is."""
    lines = [
        "## Lending portfolio - summary statistics",
        f"Loans: {len(df):,}. Mapped/computed columns: {', '.join(map(str, df.columns))}.",
        f"Data extraction date: {gi.extraction_date.date()}. Days after term (T+3 cutoff): {gi.days_after_term}.",
        f"Minimum loans per cohort (template threshold): {gi.min_loans_per_cohort}.",
        f"Value of Principal Disbursed: ${df['Principal Value'].sum():,.0f}.",
        f"Total Collected: ${df['Total Paid'].sum():,.0f}.",
        "",
        "## Overview metrics",
        f"Average Loss (owed vs paid, matured loans): {ue_data.get('Average Loss')}.",
        f"Average Principal Amount per loan: ${ue_data.get('Average Principal Amount', 0):,.0f}.",
        f"Average Fee %: {ue_data.get('Average Fee %')}. Average Interest %: {ue_data.get('Average Interest %')}.",
        f"95th Percentile Loss Rate across cohorts: {ltv_data.get('95th Percentile Losses')}.",
        f"Average Total Revenue % (interest+fees / principal): {ltv_data.get('Average Total Revenue %')}.",
        "",
        "## Cohorts",
        f"{len(cohorts)} monthly cohorts by Disbursement Date with at least one matured loan. "
        f"Columns: {', '.join(cohorts.columns)}.",
        cohorts.to_json(orient="records", date_format="iso"),
    ]
    if user_context.strip():
        lines += ["", "## User-provided context about this dataset", user_context.strip()]
    if checks:
        lines += ["", "## Data quality checks flagged"]
        lines += [f"- ({c.get('level', 'info')}) {c.get('message', '')}" for c in checks]
    return "\n".join(lines)


@app.post("/api/ai/chat/lending")
def ai_chat_lending(body: LendingChatBody) -> dict[str, Any]:
    """Ask AI, Lending model. Builds the same kind of aggregate-only text
    context app.py's chat tab sends (never raw loan rows - see
    _build_lending_chat_context_lite's docstring for the one section it's
    still missing) and forwards the question to ai_client._ask_data_chatbot."""
    df = _resolve_lending_df(body)
    c = _compute_lending(df, body.generalInputs, body.selectedCohorts)
    ue_data, ltv_data = c["ue"].as_dict(), c["ltv"].as_dict()
    checks = _lending_data_quality_checks(
        df, c["cohorts"], LENDING_DQ_CONFIG, c["gi"], c["filtered"]
    )
    # Same 60000/15000 truncation app.py's chat tab uses
    # (_ai_context_text(max_chars=60000, per_doc=15000)).
    user_context = document_text.combine_context_text(
        body.userContext, [d.model_dump() for d in body.documents], max_chars=60000, per_doc=15000
    )
    context = _build_lending_chat_context_lite(
        c["gi"], c["processed"], c["cohorts"], ue_data, ltv_data, checks, user_context
    )
    try:
        answer = _ask_data_chatbot(body.question, context, [m.model_dump() for m in body.history])
    except (RuntimeError, requests.RequestException) as exc:
        raise _ai_error(exc) from exc
    return {"answer": answer}


@app.post("/api/ai/chat/rental")
def ai_chat_rental(body: RentalChatBody) -> dict[str, Any]:
    """Ask AI, Rental & Subscription model. Unlike the lending endpoint, this
    calls the real, unmodified scripts/chat_context.py builder directly -
    every object it needs (gi/df/av/cohorts/ue/ltv/churn/ts) already comes
    out of _compute_rental, no reduced version required."""
    df = _resolve_rental_df(body)
    c = _compute_rental(df, body.generalInputs)
    ue_data, ltv_data, churn_data = c["ue"].as_dict(), c["ltv"].as_dict(), c["churn"].as_dict()
    checks = _rental_data_quality_checks(
        df, c["cohorts"], RENTAL_DQ_CONFIG, c["gi"], c["filtered"], ue_data
    )
    user_context = document_text.combine_context_text(
        body.userContext, [d.model_dump() for d in body.documents], max_chars=60000, per_doc=15000
    )
    context = _build_rental_chat_context(
        c["gi"],
        c["processed"],
        c["av"],
        c["cohorts"],
        ue_data,
        ltv_data,
        churn_data,
        c["ts"],
        checks,
        user_context,
    )
    try:
        answer = _ask_data_chatbot(body.question, context, [m.model_dump() for m in body.history])
    except (RuntimeError, requests.RequestException) as exc:
        raise _ai_error(exc) from exc
    return {"answer": answer}


def _infer_model_key(mapping: dict[str, Any]) -> str:
    """A saved profile/share doesn't record which model it was mapped for -
    infer it from which template fields the mapping uses (Rental fields are
    a disjoint set from Lending's, see RENTAL_REQUIRED_COLUMNS/REQUIRED_FIELDS)
    rather than adding a new field to s3_store.py's shared schema."""
    if any(f in mapping for f in RENTAL_REQUIRED_COLUMNS):
        return "rental"
    return "lending"


def _restore_payload(profile: dict[str, Any]) -> dict[str, Any]:
    """Re-ingest a saved/shared upload and build the payload the frontend
    needs to jump straight back into the mapping/analysis screens with the
    same file, mapping, general inputs and chat history."""
    ingest = _register_ingest(profile["upload_bytes"], profile.get("upload_name") or "upload")
    mapping = profile.get("mapping") or {}
    return _json_safe(
        {
            **ingest,
            "modelKey": _infer_model_key(mapping),
            "mapping": mapping,
            "generalInputs": profile.get("gi_overrides") or {},
            "chatHistory": profile.get("chat_history") or [],
            "label": profile.get("label"),
            "analysisContext": profile.get("analysis_context") or "",
            # When present, the frontend renders this straight away instead of
            # re-running the engine - see save_profile's analysisResult.
            "analysisResult": profile.get("analysis_result"),
        }
    )


class SaveProfileBody(BaseModel):
    identity: str = Field(
        ..., description="Self-declared, unverified identity string (see LoginScreen.tsx)."
    )
    sessionId: str = Field(
        ..., description="Client-generated id identifying this deal across saves."
    )
    label: str
    ingestId: str
    mapping: dict[str, str] = Field(default_factory=dict)
    generalInputs: dict[str, Any] = Field(default_factory=dict)
    chatHistory: list[ChatMessage] = Field(default_factory=list)
    analysisContext: str = ""
    analysisResult: dict[str, Any] | None = None


@app.post("/api/profiles/save")
def save_profile(body: SaveProfileBody) -> dict[str, bool]:
    """Save this deal's current upload + mapping/inputs/chat history under
    the caller's identity, for the "Resume my saved session" picker -
    scripts/s3_store.py does the actual persistence (unmodified)."""
    upload_bytes, upload_name = _ingest_raw_bytes(body.ingestId)
    s3_store.save_profile(
        body.identity,
        body.sessionId,
        upload_bytes=upload_bytes,
        upload_name=upload_name,
        label=body.label or upload_name,
        mapping=body.mapping,
        currency=None,
        gi_overrides=body.generalInputs,
        chat_history=[m.model_dump() for m in body.chatHistory],
        analysis_context=body.analysisContext,
        analysis_result=body.analysisResult,
    )
    _PROFILES_CACHE.pop(body.identity, None)
    return {"ok": True}


# Each S3 round trip from this process costs roughly a second (network RTT,
# not something a warm boto3 client avoids - see s3_store.py), and listing
# profiles now also feeds the sidebar's "Runs" list, which is opened far
# more often than the old resume-picker was. app.py hits the exact same cost
# and solves it the same way: a short TTL cache, invalidated on save so a
# just-saved run shows up immediately rather than after the TTL - see its
# _cached_list_profiles/_store_list_profiles.
_PROFILES_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_PROFILES_CACHE_TTL = 30.0


@app.get("/api/profiles")
def list_profiles(identity: str) -> list[dict[str, Any]]:
    cached = _PROFILES_CACHE.get(identity)
    if cached and time.time() - cached[0] < _PROFILES_CACHE_TTL:
        return cached[1]
    result = s3_store.list_profiles(identity)
    _PROFILES_CACHE[identity] = (time.time(), result)
    return result


@app.get("/api/profiles/{session_id}")
def load_profile(session_id: str, identity: str) -> dict[str, Any]:
    profile = s3_store.load_profile(identity, session_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No saved session found under that id.")
    return _restore_payload(profile)


class CreateShareBody(BaseModel):
    identity: str = Field(..., description="Self-declared identity of the person sharing.")
    ingestId: str
    mapping: dict[str, str] = Field(default_factory=dict)
    generalInputs: dict[str, Any] = Field(default_factory=dict)
    chatHistory: list[ChatMessage] = Field(default_factory=list)
    analysisContext: str = ""
    analysisResult: dict[str, Any] | None = None


@app.post("/api/share")
def create_share(body: CreateShareBody) -> dict[str, str]:
    """Snapshot this deal under a new random token anyone with the link can
    load (no allow-list - matches s3_store.create_share's documented
    behaviour). Returns the token; the frontend turns it into a link."""
    upload_bytes, upload_name = _ingest_raw_bytes(body.ingestId)
    token = s3_store.create_share(
        body.identity,
        upload_bytes=upload_bytes,
        upload_name=upload_name,
        mapping=body.mapping,
        currency=None,
        gi_overrides=body.generalInputs,
        chat_history=[m.model_dump() for m in body.chatHistory],
        analysis_context=body.analysisContext,
        analysis_result=body.analysisResult,
    )
    return {"token": token}


@app.get("/api/share/{token}")
def load_share(token: str) -> dict[str, Any]:
    profile = s3_store.load_share(token)
    if profile is None:
        raise HTTPException(status_code=404, detail="This share link is invalid or has expired.")
    return _restore_payload(profile)


@app.get("/api/auth/config")
def auth_config() -> dict[str, bool]:
    """Tells the frontend whether real Google sign-in is available, so
    LoginScreen can show the "Sign in with Google" button only when a
    client id/secret are actually configured - otherwise it falls back to
    the self-declared identity box (same fallback logic as
    scripts/login_gates.py's _render_login_gate)."""
    return {"googleEnabled": bool(_get_google_client_id() and _get_google_client_secret())}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    """Restore a remembered sign-in: returns the identity stored in the
    signed session cookie (issued on a successful sign-in) so the frontend
    can skip the login screen on load/refresh, plus whether this session has
    a live Google Drive connection (the frontend gates the "From Google
    Drive" import on it). 401 when there's no valid cookie - the caller
    falls back to the normal login screen."""
    session = _read_react_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return {
        "email": session["email"],
        "name": session["name"],
        "gdrive": session["sid"] in _GOOGLE_TOKENS,
    }


@app.post("/api/auth/logout")
def auth_logout(response: Response) -> dict[str, bool]:
    """Clear the signed session cookie (mirrors app.py's "Log out" button in
    its non-Cognito branches, which clears its own session cookie the same
    way - see _clear_session_cookie_and_reload). The Google token cached
    server-side under this session's sid is left in place: it's keyed by an
    unguessable sid the browser can no longer present, and dropping it here
    would just force a fresh OAuth consent on the next sign-in for no
    benefit."""
    response.delete_cookie(_REACT_SESSION_COOKIE, path="/")
    return {"ok": True}


class DummySignInBody(BaseModel):
    email: str


@app.post("/api/auth/dummy")
def dummy_sign_in(body: DummySignInBody, response: Response) -> dict[str, str]:
    """Set the remember-me cookie for the self-declared "temporary sign-in"
    fallback (shown when Google isn't configured - see LoginScreen.tsx).
    Identity here is unverified by design, so the cookie just remembers
    whatever email the user typed - no worse than the existing client-only
    behaviour, but it survives a refresh."""
    email = (body.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required.")
    _set_react_session_cookie(response, email, email)
    return {"email": email, "name": email}


@app.get("/api/auth/google/login-url")
def google_login_url() -> dict[str, str]:
    if not (_get_google_client_id() and _get_google_client_secret()):
        raise HTTPException(status_code=503, detail="Google sign-in is not configured.")
    state = secrets.token_urlsafe(16)
    _PENDING_GOOGLE_OAUTH_STATES.add(state)
    url = _google_auth_url(
        state, scope=GOOGLE_REACT_APP_SCOPE, redirect_uri=GOOGLE_REACT_APP_REDIRECT_URI
    )
    return {"url": url}


class GoogleCallbackBody(BaseModel):
    code: str
    state: str


@app.post("/api/auth/google/callback")
def google_callback(body: GoogleCallbackBody, response: Response) -> dict[str, Any]:
    """Exchange the authorization code Google's redirect handed back to the
    React app's /auth/google/callback route for a real identity - the
    server-side half of the flow, so the client secret never reaches the
    browser. The OAuth token (Drive-scoped, since GOOGLE_REACT_APP_SCOPE
    includes drive.readonly/drive.file) is kept server-side keyed by the
    session id, so the React app's "From Google Drive" import can list and
    download the user's files without the token ever touching the browser.
    Also issues the signed remember-me cookie, so later loads skip the
    login screen."""
    if body.state not in _PENDING_GOOGLE_OAUTH_STATES:
        raise HTTPException(
            status_code=400, detail="Invalid or expired sign-in attempt - please try again."
        )
    _PENDING_GOOGLE_OAUTH_STATES.discard(body.state)
    try:
        token = _exchange_google_code(body.code, redirect_uri=GOOGLE_REACT_APP_REDIRECT_URI)
        userinfo = _fetch_google_userinfo(token["access_token"])
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Google sign-in failed: {exc}") from exc
    email = (userinfo.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=502, detail="Google did not return an email address.")
    name = userinfo.get("name") or email
    sid = _set_react_session_cookie(response, email, name)
    _store_google_token(sid, token)
    return {"email": email, "name": name, "gdrive": True}


@app.get("/api/auth/gdrive/files")
def gdrive_files_list(request: Request) -> list[dict[str, Any]]:
    """List the signed-in user's Drive files that can be analysed
    (spreadsheets, CSV, Excel), newest first - backs the React app's
    "From Google Drive" picker (app.py's _list_drive_files)."""
    sid = _require_gdrive_session(request)
    return _list_drive_files(_gdrive_access_token(sid))


class GDriveImportBody(BaseModel):
    fileId: str = Field(..., description="Drive file id from /api/auth/gdrive/files.")


@app.post("/api/auth/gdrive/import")
def gdrive_import(body: GDriveImportBody, request: Request) -> dict[str, Any]:
    """Download a chosen Drive file (exporting a Google Sheet as a full
    workbook) and register it as a normal ingest, returning the same
    ingest payload as a local upload - app.py's "My Google Drive" source."""
    sid = _require_gdrive_session(request)
    token = _gdrive_access_token(sid)
    meta = _gdrive_file_meta(token, body.fileId)
    data, name = _download_drive_file(token, meta)
    return _json_safe(_register_ingest(data, name))


class GDriveLinkImportBody(BaseModel):
    url: str = Field(..., description="Google Sheets or Drive share link.")


@app.post("/api/gdrive-link/import")
def gdrive_link_import(body: GDriveLinkImportBody) -> dict[str, Any]:
    """Import a public Google Sheets / Google Drive share link (must be
    shared with 'Anyone with the link can view' - no token needed) and
    register it as a normal ingest - app.py's "Google Sheets / Drive link"
    source."""
    data, name = _download_google_sheet(body.url)
    return _json_safe(_register_ingest(data, name))


# --- Production static serving of the React SPA ---------------------------
# In a real deploy the built frontend (web/dist, produced by `npm run build`)
# is served by this same FastAPI process, so the SPA and every /api route share
# one origin: the React app calls fetch("/api/...") with same-origin cookies,
# and Google's OAuth redirect returns to a browser route (/auth/google/callback)
# on the same host. Nothing is mounted when web/dist is absent, so local dev is
# unaffected - there the Vite dev server (web/, port 5173) serves the app and
# proxies /api here. This block is intentionally at the END of the file so the
# catch-all below is registered after every real /api route and never shadows it.
_WEB_DIST_DIR = os.environ.get(
    "SC_REACT_DIST_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "dist"),
)
if os.path.isdir(_WEB_DIST_DIR):
    from starlette.staticfiles import StaticFiles

    # Vite emits hashed assets under /assets (no html=True: a missing asset
    # should 404, not silently fall back to index.html).
    app.mount(
        "/assets",
        StaticFiles(
            directory=os.path.join(_WEB_DIST_DIR, "assets"),
            check_dir=os.path.isdir(os.path.join(_WEB_DIST_DIR, "assets")),
        ),
        name="assets",
    )

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str, request: Request) -> Response:
        # Serve the built index.html for any non-API GET - react-router deep
        # links and the Google OAuth callback browser route included.
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found.")
        index_path = os.path.join(_WEB_DIST_DIR, "index.html")
        if not os.path.isfile(index_path):
            raise HTTPException(status_code=404, detail="Not found.")
        # Bare static files copied from public/ (e.g. favicon.svg, manifest).
        if "/" not in path and os.path.isfile(os.path.join(_WEB_DIST_DIR, path)):
            return Response(
                content=open(os.path.join(_WEB_DIST_DIR, path), "rb").read(),
                media_type="image/svg+xml" if path.endswith(".svg") else "application/octet-stream",
            )
        return Response(content=open(index_path, "rb").read(), media_type="text/html")
