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
import itertools
import json
import math
import os
import re
import secrets
import sys
import threading
import time
import typing
import uuid
from pathlib import Path
from typing import Any, Literal

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# Make both engines importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rental_and_subscription"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# ...and this package's own directory, so modules here are importable by the
# bare name the engine code uses. scripts/ai_client.py's
# _suggest_derived_column does `from derive_columns import ...` to validate a
# drafted formula against the file's real columns; derive_columns lives here,
# not in scripts/, so without this every /api/ai/derive-column call died with
# ModuleNotFoundError -> 500. The failure was invisible in tests because
# nothing imports ai_client's derive path outside the running server.
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

from config import FIELD_ALIASES, FX, REVOLVING_DEFAULTS, UE_MODEL_COST_DEFAULTS
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, PrivateAttr, ValidationError

# Same .env app.py loads at import (google_oauth.py's _get_google_client_id
# etc. fall back to os.environ when st.secrets isn't set) - lets this
# process reuse credentials already configured for the Streamlit app
# (GOOGLE_CLIENT_ID/SECRET, DEEPINFRA_API_KEY, ...) without duplicating them.
load_dotenv()

# Same raw-upload date coercion app.py itself calls before running the
# Lending engine (see app.py's `_coerce_dates(raw, DATE_FIELDS, dayfirst)`).
from ai_client import (
    _ai_status,
    _ask_data_chatbot,
    _extract_whatif,
    _get_deepinfra_api_key,
    _suggest_derived_column,
    _suggest_escalation_writeup,
    _suggest_mapping,
    _suggest_redshift_filter,
)
from chat_context import _build_rental_chat_context
from data_normalize import _clean_numeric, _coerce_dates as _coerce_lending_dates
from export_workbook import (
    _add_chat_history_sheet,
    _build_lending_export_workbook,
    _build_rental_export_workbook,
    _build_revolving_export_workbook,
)
from share_html import build_share_html
import warehouse
from integrations import (
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
import usage_log

# Google OAuth mechanics (auth URL, code exchange, userinfo) - the same
# helpers app.py's own Google sign-in uses (scripts/login_gates.py's
# _render_google_login_gate). Only the CSRF-state storage and redirect
# target below are new: this app is a normal FastAPI process (no
# st.cache_resource/st.session_state need substituting - see
# scripts/session_cookie.py's docstring for why Streamlit's own version
# can't just be imported as-is), and it redirects back to the React app's
# own callback route, not Streamlit's.
from google_oauth import (
    _delegated_drive_token,
    _exchange_google_code,
    _fetch_google_userinfo,
    _get_google_client_id,
    _get_google_client_secret,
    _google_auth_url,
    _refresh_google_token,
    _service_account_info,
)

# Corporate-root-CA-aware TLS bundle, same lookup app.py's direct Drive/Sheets
# downloads use (falls back to certifi when certs/corporate_root.pem is absent).
from ca_bundle import _ca_bundle_path

# Cognito OIDC sign-in for the deployed app (see web_api/cognito.py). When it
# is configured it is the ONLY way to get a session: the Google and temporary
# sign-in routes below refuse to issue one.
import cognito
from starlette.middleware.sessions import SessionMiddleware

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
# state -> issue time. login-url is public, so entries expire and the dict is
# capped (insertion-ordered: the oldest go first) instead of growing forever.
_PENDING_GOOGLE_OAUTH_STATES: dict[str, float] = {}
_OAUTH_STATE_TTL_SECONDS = 600
_OAUTH_STATE_MAX = 1000
_OAUTH_STATE_LOCK = threading.Lock()

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
# from SC_REACT_SESSION_SECRET, then APP_SESSION_SECRET (what the ECS Express
# module injects), falls back to GOOGLE_CLIENT_SECRET (already a server-only
# secret), else a per-process random - which is safe but means cookies stop
# validating if this process restarts.
_REACT_SESSION_COOKIE = "sc_react_session"
_REACT_SESSION_MAX_AGE_SECONDS = int(os.environ.get("SC_REACT_SESSION_DAYS", "30")) * 86400
_REACT_SESSION_SECRET = (
    os.environ.get("SC_REACT_SESSION_SECRET")
    or os.environ.get("APP_SESSION_SECRET")
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
# The service account's own Drive token. In memory only: a 1-hour access
# token re-mintable from the key at any time, so nothing worth persisting.
_SA_TOKEN: dict[str, dict] = {}


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


def _service_account_access_token() -> str:
    token = _SA_TOKEN.get("token")
    if token is None or time.time() >= token["expires_at"] - 60:
        try:
            token = _delegated_drive_token(None)
        except requests.RequestException as exc:
            raise HTTPException(
                status_code=502, detail=f"Google Drive service account sign-in failed: {exc}"
            ) from exc
        _SA_TOKEN["token"] = token
    return token["access_token"]


def _gdrive_access_token(session: dict[str, str]) -> str:
    """Return a fresh-enough Drive access token for this session.

    With a service account configured (GOOGLE_SERVICE_ACCOUNT_JSON/_FILE),
    every signed-in user shares the service account's own token - it sees
    the Shared Drives and files shared with it, not anyone's personal Drive,
    and needs no per-user OAuth consent. Otherwise falls back to the per-sid
    OAuth token, refreshing it when within 60s of expiry - same refresh
    logic as google_oauth._gdrive_access_token, minus the Streamlit
    session_state write."""
    if _service_account_info():
        return _service_account_access_token()
    sid = session["sid"]
    token = _GOOGLE_TOKENS.get(sid)
    if token is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "Google Drive isn't connected for this session - sign out and back in with Google."
            ),
        )
    if time.time() >= token.get("expires_at", 0) - 60 and token.get("refresh_token"):
        refreshed = _refresh_google_token(token["refresh_token"])
        refreshed["refresh_token"] = token["refresh_token"]
        _store_google_token(sid, refreshed)
        return refreshed["access_token"]
    return token["access_token"]


def _require_gdrive_session(request: Request) -> dict[str, str]:
    session = _read_react_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return session


def _require_identity(request: Request) -> str:
    """The caller's VERIFIED identity, from the HMAC-signed session cookie.

    Every endpoint that reads or writes per-user storage must key off this
    rather than an `identity` the client sends. s3_store enforces access
    control in application code - a user's data is private only because
    nothing reads outside their own users/<safe_id>/ prefix - so letting the
    client name the prefix let anyone list and load anyone else's saved deal
    by changing an email in a query string.

    Note this is only as strong as the sign-in that issued the cookie: under
    the "temporary sign-in" fallback (/api/auth/dummy, used when Google isn't
    configured) the email is self-declared and unverified by design. With
    Google configured the identity is verified, and the cookie is signed
    either way, so a user can no longer read a DIFFERENT identity's data
    without having signed in as them.
    """
    session = _read_react_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return session["email"]


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
            # Include Shared Drives, not just My Drive.
            "corpora": "allDrives",
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
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
        params={"fields": "id,name,mimeType", "supportsAllDrives": "true"},
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
        url = f"{GOOGLE_DRIVE_FILES_URL}/{drive_file['id']}?alt=media&supportsAllDrives=true"
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
from template_analysis.date_detection import detect_date
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
from rental_analysis.general_inputs import DEFAULT_STATUS_MAP as RENTAL_DEFAULT_STATUS_MAP
from rental_analysis.general_inputs import GeneralInputs as RentalGeneralInputs
from rental_analysis.lease_cohorts import build_cohorts as rental_build_cohorts
from rental_analysis.ltv_analysis import LtvAnalysis as RentalLtvAnalysis
from rental_analysis.repayment_curve import build_repayment_curve
from rental_analysis.ue_analysis import UeAnalysis as RentalUeAnalysis
from rental_analysis.ue_analysis import ue_by_cohort as rental_ue_by_cohort
from rental_analysis.ts_covenants import build_ts_covenants

# Revolving Lending (RCL) engine
from revolving_analysis.engine import RevolvingInputs
from revolving_analysis.engine import run as run_revolving
from revolving_analysis.monthly_input import OPTIONAL_COLUMNS as REVOLVING_OPTIONAL_COLUMNS
from revolving_analysis.monthly_input import REQUIRED_COLUMNS as REVOLVING_REQUIRED_COLUMNS
from revolving_analysis.metrics import BY_KEY as REVOLVING_METRICS
from revolving_analysis.facility import EXTRA_METRICS as _REVOLVING_EXTRA_METRICS
from revolving_analysis.settings import WINDOW_LENGTHS as REVOLVING_WINDOWS

# Every metric a covenant can test: the catalogue plus the ECL / net-yield figures.
REVOLVING_COVENANT_METRICS = {
    **{k: (m.label, m.kind) for k, m in REVOLVING_METRICS.items()},
    **_REVOLVING_EXTRA_METRICS,
}

app = FastAPI(
    title="SC Analysis Engine API",
    version="0.1.0",
    description="Lending + Rental & Subscription analysis engines for the React app.",
)

# Fail closed: a deployment that sets COGNITO_CLIENT_ID but forgets the rest
# must refuse to start, not serve with sign-in quietly broken.
if cognito.configuration_error():
    raise RuntimeError(f"Refusing to start: {cognito.configuration_error()}")

# Holds only Authlib's short-lived OAuth state/nonce between /api/auth/login
# and /oauth2callback - the identity lives in _REACT_SESSION_COOKIE. lax, not
# strict: the callback is a cross-site top-level redirect back from Cognito,
# and a strict cookie would not be sent on it.
app.add_middleware(
    SessionMiddleware,
    secret_key=_REACT_SESSION_SECRET,
    session_cookie="sc_oauth_state",
    max_age=_OAUTH_STATE_TTL_SECONDS,
    same_site="lax",
    https_only=True,
)


# Paths under /api that are reachable without a session, because requiring one
# would make signing in impossible (or, for a share link, defeat the point).
# Everything else under /api requires a valid signed cookie - see
# _require_session_middleware. This is an allow-list on purpose: a new route is
# protected by default, and only becomes public by being named here.
_PUBLIC_API_PREFIXES = (
    "/api/health",
    "/api/auth/",  # login, login-url, callback, dummy, me, logout, config
)
# /api/share/{token} is deliberately NOT public: each load registers an
# ingest, so anonymous loads could evict every signed-in user's working
# upload (the registry is capped). The React Shell only resumes a share after
# sign-in, and an anonymous viewer couldn't analyse it anyway.


@app.middleware("http")
async def _require_session_middleware(request: Request, call_next):
    """Reject unauthenticated calls to the data and AI routes.

    Guarding these in one place rather than per-route: the endpoints that
    ingest tapes, run the engine, export workbooks and call the LLM had no
    auth at all, so anyone who could reach the URL could upload data, run
    analyses and spend the AI budget. A middleware means a route added later
    is protected without anyone remembering to decorate it.

    Set SC_ALLOW_ANON_API=1 to disable this (local development against a
    dev server with no OAuth client configured). It must never be set on a
    deployed service, which is why the default is to require a session.
    """
    path = request.url.path
    if (
        path.startswith("/api/")
        and not path.startswith(_PUBLIC_API_PREFIXES)
        and os.environ.get("SC_ALLOW_ANON_API") != "1"
        and _read_react_session(request) is None
    ):
        return JSONResponse({"detail": "Not signed in."}, status_code=401)
    started = time.monotonic()
    response = await call_next(request)
    session = _read_react_session(request)
    usage_log.log_call(
        path,
        request.method,
        session["email"] if session else None,
        response.status_code,
        time.monotonic() - started,
    )
    return response


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
    extractionDate: str | None = None
    daysAfterTerm: int = 90
    minLoansPerCohort: int = 10
    # Optional "YYYY-MM" cohort range for the LTV Calculator's interest and
    # 95th %ile loss inputs only (UeAnalysis.avg_interest_pct,
    # LtvAnalysis.percentile_95_losses) - the cohorts table and every other
    # tab still use the whole book. Blank = no limit on that end.
    ltvCohortFrom: str | None = None
    ltvCohortTo: str | None = None
    # ISO 4217 code the mapped monetary columns are denominated in. Non-USD
    # values trigger one live FX lookup and a rescale to USD before the engine
    # runs - mirrors app.py's portfolio_currency selectbox. "USD"/None is a
    # no-op, so the common case never touches the network.
    currency: str | None = None


class DerivedColumn(BaseModel):
    name: str = Field(..., description="New column label (used as a mapping source).")
    formula: str = Field(
        ...,
        description=(
            "Safe arithmetic formula over source columns, "
            "e.g. 'Gross Loan Value - Principal Value'."
        ),
    )


class LendingAnalyseIngest(BaseModel):
    ingestId: str = Field(..., description="Id returned by POST /api/ingest.")
    mapping: dict[str, str] = Field(
        ..., description="Template field key -> raw source column name."
    )
    derivedColumns: list[DerivedColumn] = Field(
        default_factory=list,
        description=(
            "Columns to derive server-side (name + formula) and add to the source "
            "columns the mapping can reference."
        ),
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
    colorCol: str | None = None
    data: list[dict[str, Any]] = Field(
        default_factory=list, description="Aggregated rows keyed by xCol/yCol[/colorCol]."
    )


class LendingAnalyseBody(BaseModel):
    rows: list[dict[str, Any]] = Field(
        default_factory=list, description="Mapped rows keyed by canonical template field names."
    )
    generalInputs: GeneralInputsBody = Field(default_factory=GeneralInputsBody)
    ingest: LendingAnalyseIngest | None = None
    exportCharts: list[ChartSpecBody] = Field(default_factory=list)
    dedupeById: bool = False
    selectedCohorts: list[str] | None = Field(
        default=None,
        description="Cohort month labels (e.g. '2025-06-01') to restrict the analysis to - "
        "the Cohorts tab's selection. None/empty means every cohort, same as today.",
    )
    # Optional [{role, content}] chat turns, so /api/export/lending can include
    # the Ask AI transcript as an extra sheet (mirrors app.py's opt-in
    # "Include the Ask AI chat history" download). Ignored by /api/analyse.
    chatHistory: list[dict[str, Any]] = Field(default_factory=list)


class RentalGeneralInputsBody(BaseModel):
    extractionDate: str | None = None
    daysAfterTerm: int = 0
    monthsSinceDefault: int = 3
    minLoansPerCohort: int = 20
    usefulLifeYears: float = 3.0
    churnStressMultiplier: float = 1.7
    fundingCostPct: float = 0.13
    directCostPct: float = 0.02
    fxDevalPct: float | None = None
    fxDiscount50pct: bool = True
    debtService180d: float | None = None
    # Portfolio currency for the mapped monetary columns - see
    # GeneralInputsBody.currency. Rescaled to USD before the engine runs.
    currency: str | None = None
    # Client status value -> Lendable status (Open / Closed / Paid-off), from
    # the General Inputs status table (app.py's "Status mapping" editor).
    # None falls back to the engine's DEFAULT_STATUS_MAP.
    statusMap: dict[str, str] | None = None


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
    ingest: RentalAnalyseIngest | None = None
    exportCharts: list[ChartSpecBody] = Field(default_factory=list)
    dedupeById: bool = False
    # Optional Ask AI transcript for /api/export/rental - see LendingAnalyseBody.
    chatHistory: list[dict[str, Any]] = Field(default_factory=list)


class RevolvingScenarioBody(BaseModel):
    name: str
    rollRateMultiplier: float = Field(..., gt=0)
    lgdMultiplier: float = Field(..., gt=0)


class RevolvingCovenantBody(BaseModel):
    metric: str = Field(..., description="A revolving metric key, e.g. 'par91_total'.")
    operator: Literal["<=", ">="]
    limit: float


class RevolvingGeneralInputsBody(BaseModel):
    """Revolving model tunables; defaults from config.toml [revolving_defaults]."""

    asOfMonth: str | None = Field(None, description="'YYYY-MM'; None = last month in the data.")
    lgd: float = REVOLVING_DEFAULTS["lgd"]
    rollRateWindow: str = Field(
        REVOLVING_DEFAULTS["roll_rate_window"],
        description="A window name from config.toml [revolving_defaults.roll_rate_windows].",
    )
    ratioWindowMonths: int = Field(REVOLVING_DEFAULTS["ratio_window_months"], ge=1)
    writeoffWindowMonths: int = Field(REVOLVING_DEFAULTS["writeoff_window_months"], ge=1)
    minOutstandingInSample: float = REVOLVING_DEFAULTS["min_outstanding_in_sample"]
    minOutstandingPctOfPeak: float = Field(
        REVOLVING_DEFAULTS["min_outstanding_in_sample_pct_of_peak"], ge=0, le=1
    )
    ltvWindowMonths: int = Field(REVOLVING_DEFAULTS["ltv_window_months"], ge=1)
    stressPercentile: float = Field(REVOLVING_DEFAULTS["stress_percentile"], ge=0, le=1)
    denominatorLagMonths: int = Field(REVOLVING_DEFAULTS["denominator_lag_months"], ge=0)
    laggedBlendWeight: float = Field(REVOLVING_DEFAULTS["lagged_blend_weight"], ge=0, le=1)
    lossRateBasis: Literal["Naive", "Blended", "Lagged"] = REVOLVING_DEFAULTS["loss_rate_basis"]
    lgdBasis: Literal["Input", "Recoveries"] = REVOLVING_DEFAULTS["lgd_basis"]
    costOfFundsPct: float = Field(REVOLVING_DEFAULTS["cost_of_funds_pct"], ge=0)
    opexPct: float = Field(REVOLVING_DEFAULTS["opex_pct"], ge=0)
    advanceHaircut: float = Field(REVOLVING_DEFAULTS["advance_haircut"], ge=0, le=1)
    backtestMinHistoryMonths: int = Field(REVOLVING_DEFAULTS["backtest_min_history_months"], ge=1)
    earlyWarningLookbackMonths: int = Field(
        REVOLVING_DEFAULTS["early_warning_lookback_months"], ge=1
    )
    earlyWarningThreshold: float = Field(REVOLVING_DEFAULTS["early_warning_threshold"], ge=0)
    scenarios: list[RevolvingScenarioBody] = Field(
        default_factory=lambda: [
            RevolvingScenarioBody(
                name=s["name"],
                rollRateMultiplier=s["roll_rate_multiplier"],
                lgdMultiplier=s["lgd_multiplier"],
            )
            for s in REVOLVING_DEFAULTS["scenarios"]
        ]
    )
    covenants: list[RevolvingCovenantBody] = Field(
        default_factory=lambda: [
            RevolvingCovenantBody(**c) for c in REVOLVING_DEFAULTS.get("covenants", [])
        ]
    )


class RevolvingAnalyseBody(BaseModel):
    rows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Monthly rows keyed by the Outputs field names (report_month, ...).",
    )
    generalInputs: RevolvingGeneralInputsBody = Field(default_factory=RevolvingGeneralInputsBody)
    ingest: LendingAnalyseIngest | None = None


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
# Only input_columns is read for the revolving model (by /api/ai/suggest-mapping).
REVOLVING_DQ_CONFIG = {
    "input_columns": [(c, True) for c in REVOLVING_REQUIRED_COLUMNS]
    + [(c, False) for c in REVOLVING_OPTIONAL_COLUMNS],
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
INGEST_RAW: dict[str, tuple[bytes | None, str]] = {}
# Each ingest's _register_ingest payload (columns/rowCount/preview/sheet),
# stored with a save/share so restoring it needn't download the data.
INGEST_INFO: dict[str, dict[str, Any]] = {}
# Restored saved runs whose data hasn't been downloaded yet:
# ingestId -> (load_bytes, info, lock). _get_ingest moves one into INGESTS on
# first read; the lock stops two concurrent first reads loading it twice.
_PENDING_INGESTS: dict[str, tuple[Any, dict[str, Any], threading.Lock]] = {}

# Both registries are bounded, because nothing else ever removes from them:
# an ingest has no "session ended" signal (the browser can close mid-deal, and
# a save/share may re-register the same id much later), so without a cap a
# long-running task accumulates every loan tape and every original upload it
# has ever seen until it is OOM-killed. That also contradicts the intent
# above - "we do not linger on the user's data longer than the interaction
# needs it" - which was a comment, not behaviour.
#
# Eviction is oldest-first by last use, and additionally drops anything idle
# past the TTL, so a busy hour cannot push out an ingest someone is still
# working on while an abandoned one survives. Both registries are keyed
# identically and evicted together, so _ingest_raw_bytes can never find raw
# bytes for a DataFrame that is gone (or vice versa).
# Largest upload accepted, before parsing. The parse is fully in memory and a
# DataFrame is several times its source file, so without a ceiling one large
# upload can OOM the task regardless of how few are retained.
_MAX_UPLOAD_BYTES = int(os.environ.get("SC_MAX_UPLOAD_MB", "200")) * 1024 * 1024
_INGEST_MAX = int(os.environ.get("SC_INGEST_MAX", "32"))
_INGEST_TTL_SECONDS = int(os.environ.get("SC_INGEST_TTL_HOURS", "8")) * 3600
# Two clocks on purpose. LRU order uses a monotonic counter, not the wall
# clock: time.time() has ~15ms resolution on Windows, so several ingests
# registered in the same tick share a timestamp and min() then evicts an
# arbitrary one - including the one the user is actively analysing. The wall
# clock is kept only for the TTL, where coarse resolution is irrelevant.
_INGEST_LAST_USED: dict[str, float] = {}
_INGEST_USE_SEQ: dict[str, int] = {}
_ingest_use_counter = itertools.count()
# Sync routes run on a threadpool, so eviction's walk over these dicts can race
# another request's insert ("dictionary changed size during iteration").
# Reentrant because _register_ingest holds it while calling touch/evict.
_INGEST_LOCK = threading.RLock()
# ingestId -> (settings key, raw frame it was computed from, (df, fx_warning, c)).
# Only the LAST engine run per upload is kept, so memory is bounded by the
# ingest cap: analyse -> chat -> export on the same settings runs the engine
# once instead of per request. Dropped with its ingest (see _drop_ingest).
_ENGINE_CACHE: dict[str, tuple[str, pd.DataFrame, tuple[Any, ...]]] = {}
# The body fields the engine result depends on; export charts, chat history
# and the chat-only fields don't change it, so they stay out of the key.
_ENGINE_KEY_FIELDS = {"ingest", "generalInputs", "dedupeById", "selectedCohorts"}


def _cached_pipeline(model: str, body: BaseModel, run) -> tuple[Any, ...]:
    """(df, fx_warning, c) for this ingest + settings, computing via `run()`
    only on a miss. Consumers only read these objects (verified: nothing
    downstream mutates them in place), so sharing one result is safe."""
    ingest = getattr(body, "ingest", None)
    # Direct `rows` payload (nothing stable to key on), or an Ask AI what-if.
    if ingest is None or getattr(body, "_skip_cache", False):
        return run()
    key = model + json.dumps(
        body.model_dump(include=_ENGINE_KEY_FIELDS, mode="json"), sort_keys=True, default=str
    )
    raw = _get_ingest(ingest.ingestId)
    hit = _ENGINE_CACHE.get(ingest.ingestId)
    # Identity check on the raw frame: a sheet re-parse re-registers the same id.
    if hit is not None and hit[0] == key and raw is not None and hit[1] is raw:
        _touch_ingest(ingest.ingestId)
        return hit[2]
    result = run()
    with _INGEST_LOCK:
        # Only if the upload wasn't evicted/replaced while the engine ran.
        if raw is not None and INGESTS.get(ingest.ingestId) is raw:
            _ENGINE_CACHE[ingest.ingestId] = (key, raw, result)
    return result


def _touch_ingest(ingest_id: str) -> None:
    """Mark an ingest as in-use so eviction treats it as live."""
    with _INGEST_LOCK:
        _INGEST_LAST_USED[ingest_id] = time.time()
        _INGEST_USE_SEQ[ingest_id] = next(_ingest_use_counter)


def _evict_ingests() -> None:
    """Drop expired ingests, then the least-recently-used until under cap."""
    with _INGEST_LOCK:
        now = time.time()
        for key in [k for k, t in _INGEST_LAST_USED.items() if now - t > _INGEST_TTL_SECONDS]:
            _drop_ingest(key)
        while len(INGESTS.keys() | _PENDING_INGESTS.keys()) > _INGEST_MAX:
            oldest = min(_INGEST_USE_SEQ, key=_INGEST_USE_SEQ.get, default=None)
            if oldest is None:
                break
            _drop_ingest(oldest)


def _drop_ingest(ingest_id: str) -> None:
    with _INGEST_LOCK:
        INGESTS.pop(ingest_id, None)
        INGEST_RAW.pop(ingest_id, None)
        INGEST_INFO.pop(ingest_id, None)
        _PENDING_INGESTS.pop(ingest_id, None)
        _ENGINE_CACHE.pop(ingest_id, None)
        _INGEST_LAST_USED.pop(ingest_id, None)
        _INGEST_USE_SEQ.pop(ingest_id, None)


def _get_ingest(ingest_id: str) -> pd.DataFrame | None:
    """INGESTS.get + keep-alive, so an ingest being actively analysed is not
    evicted out from under the user by other uploads. Downloads and parses a
    restored run's data here on first read (see _register_pending_ingest)."""
    pending = _PENDING_INGESTS.get(ingest_id)
    if pending is not None:
        load_bytes, info, lock = pending
        with lock:
            if ingest_id in _PENDING_INGESTS:
                try:
                    file_bytes = load_bytes()
                except Exception as exc:
                    # Stays pending, so a retry can succeed after a blip.
                    raise HTTPException(
                        status_code=502, detail=f"Could not download this saved run's data: {exc}"
                    ) from exc
                _register_ingest(
                    file_bytes,
                    info["name"],
                    sheet=info.get("sheet"),
                    ingest_id=ingest_id,
                    enforce_size_cap=False,
                )
                _PENDING_INGESTS.pop(ingest_id, None)
    df = INGESTS.get(ingest_id)
    if df is not None:
        _touch_ingest(ingest_id)
    return df


def _default_sheet(sheets: list[str]) -> str | None:
    """Prefer "Data Input" when present (e.g. re-uploading this app's own
    export) rather than blindly taking whichever sheet is first."""
    if not sheets:
        return None
    return "Data Input" if "Data Input" in sheets else sheets[0]


def _parse_upload(
    file_bytes: bytes, name: str, sheet: str | None = None
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
            # A datetime column can't hold None, so the .where above leaves NaT,
            # which the response serializer rejects (500).
            if v is None or v is pd.NaT:
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
            value = value.item()
        except (ValueError, OverflowError):
            return None
    # math.isnan explicitly, before the plain-scalar passthrough below: a
    # Python float('nan') - from scripts/summary_metrics.py's own division
    # guards (native floats, not numpy/pandas scalars) or from unwrapping
    # np.generic just above - would otherwise serialize as the bare `NaN`
    # token, which Python's json module accepts but JS JSON.parse rejects
    # outright, breaking every fetch() call that hits this field.
    if isinstance(value, float) and math.isnan(value):
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


@app.get("/api/field-aliases/{model_key}")
def field_aliases(model_key: str) -> dict[str, list[str]]:
    """Known header aliases per template field (config.toml [field_aliases]),
    so the mapping screen can pre-fill obvious columns without an AI call."""
    return FIELD_ALIASES.get(model_key, {})


@app.get("/api/ai/status")
def ai_status(identity: str = Depends(_require_identity)) -> dict[str, Any]:
    """Can Ask AI work on this server? Open in the browser to diagnose a
    deployment without log access; reports key presence/shape and a 1-token
    test call, never the key."""
    return _ai_status()


def _register_ingest(
    file_bytes: bytes | None,
    name: str,
    sheet: str | None = None,
    ingest_id: str | None = None,
    df: pd.DataFrame | None = None,
    enforce_size_cap: bool = True,
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
    it's ever saved/shared.

    `enforce_size_cap=False` is for save/share restores only: those bytes
    were already accepted once (a capped upload, or a warehouse/merge frame
    serialized at save time, which can be far past the cap)."""
    if df is None:
        # Every byte-based source (upload, Drive, link, save/share restore)
        # lands here, so the cap is checked once, before the in-memory parse.
        # ponytail: Drive/link downloads are still buffered whole before this
        # check; stream with a byte limit if remote files that big turn up.
        if enforce_size_cap and len(file_bytes) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File is too large ({len(file_bytes) / 1024 / 1024:.0f}MB). "
                    f"The limit is {_MAX_UPLOAD_BYTES // 1024 // 1024}MB."
                ),
            )
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
    info = _json_safe(
        {
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
    )
    with _INGEST_LOCK:
        INGESTS[ingest_id] = df
        INGEST_RAW[ingest_id] = (file_bytes, name)
        INGEST_INFO[ingest_id] = info
        _ENGINE_CACHE.pop(ingest_id, None)
        _touch_ingest(ingest_id)
        _evict_ingests()
    return info


def _register_pending_ingest(info: dict[str, Any], load_bytes: Any) -> dict[str, Any]:
    """Register a saved run's ingest without its data: `info` (saved at save
    time) is enough to show the mapping/analysis screens, and `load_bytes`
    is only called by _get_ingest the first time something reads the data."""
    ingest_id = uuid.uuid4().hex[:12]
    info = {**info, "ingestId": ingest_id}
    with _INGEST_LOCK:
        _PENDING_INGESTS[ingest_id] = (load_bytes, info, threading.Lock())
        INGEST_INFO[ingest_id] = info
        _touch_ingest(ingest_id)
        _evict_ingests()
    return info


def _ingest_raw_bytes(ingest_id: str) -> tuple[bytes, str]:
    """Return (file_bytes, name) for a registered ingest, only used by
    Save/Share (and the sheet picker, for a real upload). A normal file
    upload already has real bytes from the moment it's registered; a merge
    or warehouse-fetch result doesn't (see _register_ingest), so this
    computes and caches its CSV serialization here, the first time it's
    actually needed, rather than eagerly for every such ingest regardless of
    whether anyone ever saves or shares it."""
    if ingest_id in _PENDING_INGESTS:
        _get_ingest(ingest_id)
    raw = INGEST_RAW.get(ingest_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="ingestId not found (upload may have expired).")
    _touch_ingest(ingest_id)
    file_bytes, name = raw
    if file_bytes is not None:
        return file_bytes, name
    df = _get_ingest(ingest_id)
    if df is None:
        raise HTTPException(status_code=404, detail="ingestId not found (upload may have expired).")
    file_bytes = df.to_csv(index=False).encode("utf-8")
    with _INGEST_LOCK:
        if ingest_id in INGESTS:  # not evicted while to_csv ran
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
    primaryKey: str | None = Field(
        None, description="Join-key column in the primary file (mode='join')."
    )
    secondaryKey: str | None = Field(
        None, description="Join-key column in the secondary file (mode='join')."
    )
    aggregation: Literal["sum", "last"] = Field(
        "sum",
        description=(
            "mode='join' only: how to collapse the secondary file to one row per key before "
            "joining, when it has more than one row per key (e.g. a raw payments/transactions "
            "file): 'sum' adds up numeric columns and takes the first non-key value of the rest "
            "(a payments file -> one 'total paid'-style row per loan); 'last' just keeps each "
            "key's last row as-is (a snapshot/status file where only the latest "
            "row per key matters)."
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
    df1 = _get_ingest(body.primaryIngestId)
    df2 = _get_ingest(body.secondaryIngestId)
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

    primary_name = INGEST_RAW.get(body.primaryIngestId, (None, "upload.csv"))[1]
    merged_name = (
        primary_name
        if primary_name.lower().endswith(".csv")
        else f"{primary_name.rsplit('.', 1)[0]}_merged.csv"
    )
    return _json_safe(_register_ingest(None, merged_name, df=merged))


# Snowflake-backed tables, queried directly via the SQL Statement API (see
# scripts/warehouse.py). These routes used to proxy to a local redshift-api
# service; the routes keep the "redshift" name so the React client's URLs
# did not have to change when that hop was removed.
@app.get("/api/redshift/config")
def redshift_config() -> dict[str, bool]:
    return {"available": warehouse.is_configured()}


def _require_warehouse() -> None:
    if not warehouse.is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Snowflake isn't configured on this server "
                "(set SNOWFLAKE_ACCOUNT and SNOWFLAKE_PAT)."
            ),
        )


_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_]+")


@app.get("/api/redshift/schemas")
def redshift_schemas(database: str = "MAESTRO_SRC") -> list[str]:
    """Every schema under `database`, for the "Other table" mode's schema
    dropdown - unlike /borrowers (schemas that happen to own a LOANS table),
    this covers e.g. SALMON, which has no LOANS table."""
    _require_warehouse()
    if not _IDENTIFIER_RE.fullmatch(database):
        raise HTTPException(status_code=422, detail="Database must be a plain identifier.")
    try:
        return _redshift_list_schemas(database)
    except (requests.RequestException, warehouse.WarehouseError) as exc:
        raise HTTPException(
            status_code=502, detail=f"Couldn't list schemas from the warehouse: {exc}"
        ) from exc


@app.get("/api/redshift/tables")
def redshift_tables(schema: str, database: str = "MAESTRO_SRC") -> list[str]:
    """Every table in `database.schema`, for the "Other table" mode's table
    dropdown, once a schema is picked."""
    _require_warehouse()
    if not (_IDENTIFIER_RE.fullmatch(database) and _IDENTIFIER_RE.fullmatch(schema)):
        raise HTTPException(status_code=422, detail="Database/schema must be plain identifiers.")
    try:
        return _redshift_list_tables(schema, database)
    except (requests.RequestException, warehouse.WarehouseError) as exc:
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
    _require_warehouse()
    if not (
        _IDENTIFIER_RE.fullmatch(body.database)
        and _IDENTIFIER_RE.fullmatch(body.schemaName)
        and _IDENTIFIER_RE.fullmatch(body.table)
    ):
        raise HTTPException(
            status_code=422, detail="Database/schema/table must be plain identifiers."
        )
    try:
        columns = _redshift_list_columns(body.schemaName, body.table, body.database)
    except (requests.RequestException, warehouse.WarehouseError) as exc:
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
    where: str | None = None


def _validate_redshift_identifiers(database: str, schema: str, table: str) -> None:
    if not (
        _IDENTIFIER_RE.fullmatch(database)
        and _IDENTIFIER_RE.fullmatch(schema)
        and _IDENTIFIER_RE.fullmatch(table)
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Database/schema/table must be plain identifiers "
                "(letters, digits, underscores only)."
            ),
        )


@app.post("/api/redshift/table/start")
def redshift_table_start(body: RedshiftTableStartBody) -> dict[str, str]:
    """Start a table fetch as a background job on the warehouse API and
    return its job id, so the client can poll /api/redshift/table/status for
    real progress (partitions fetched so far) instead of one request that
    blocks silently for however long a big table takes - see
    integrations._redshift_start_table_job."""
    _require_warehouse()
    _validate_redshift_identifiers(body.database, body.schemaName, body.table)
    try:
        job_id = _redshift_start_table_job(
            body.database, body.schemaName, body.table, body.limit, body.where
        )
    except (requests.RequestException, warehouse.WarehouseError) as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't start the fetch: {exc}") from exc
    return {"jobId": job_id}


@app.get("/api/redshift/table/status")
def redshift_table_status(jobId: str, schema: str, table: str) -> dict[str, Any]:
    """Poll one job started by /api/redshift/table/start. While running,
    returns partition progress only; once done, also registers the result as
    a normal ingest (same as the synchronous /api/redshift/table) and
    returns that payload alongside the final progress counts."""
    _require_warehouse()
    try:
        status_payload = _redshift_job_status(jobId)
    except (requests.RequestException, warehouse.WarehouseError) as exc:
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
    error: str | None = None


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
    derived_columns: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Build the canonical (template-field-keyed) DataFrame from a raw uploaded
    DataFrame and the user's field->source-column mapping. Mirrors app.py's
    mapping form: each template field pulls its source column's values into a
    canonical key. Missing columns / unmapped required fields surface as
    validation errors. This only selects/reorders columns - no analysis math
    lives here. Optional ``derived_columns`` ([{name, formula}]) are computed
    deterministically onto the raw frame first, so a derived name becomes a
    first-class source column the mapping can reference (see derive_columns.py)."""
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
    # Build the canonical frame by selecting/renaming whole columns - never by
    # materialising Python lists of 3M+ values (the old `raw[src].tolist()` +
    # `[dict(zip(...))...]` + `pd.DataFrame(rows)` round-trip took ~20s and a
    # ~1GB dict spike on a 3M-row tape for exactly the same result). Column
    # order here matches the mapping's insertion order, as before.
    if not reverse:
        return pd.DataFrame()
    return pd.DataFrame({f: raw[src] for src, fields in reverse.items() for f in fields})


def _derived_dicts(derived: list | None) -> list[dict[str, Any]]:
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
    raw = _get_ingest(body.ingestId)
    if raw is None:
        raise HTTPException(status_code=404, detail="ingestId not found (upload may have expired).")
    rows = _canonical_rows_from_mapping(raw, body.mapping, _derived_dicts(body.derivedColumns))
    row_count = len(rows)
    truncated = row_count > _DATA_GRID_LIMIT
    return _json_safe(
        {
            "columns": list(rows.columns) if not rows.empty else [],
            "rows": rows.head(_DATA_GRID_LIMIT).to_dict("records") if not rows.empty else [],
            "rowCount": row_count,
            "truncated": truncated,
        }
    )


@app.post("/api/data-grid/{model}")
def data_grid(model: str, body: dict[str, Any]) -> dict[str, Any]:
    """Data Checks > Data Input after a run: the engine's per-row table (the
    template's Data Input sheet), i.e. the mapped inputs plus every column the
    engine computes. Same payload as /api/analyse/{model}, so it reuses the
    cached run. `calculated` names the engine's columns; `derived` maps each
    field fed by an AI-derived column to its formula."""
    runs = {
        "lending": (LendingAnalyseBody, _lending_pipeline, "processed"),
        "rental": (RentalAnalyseBody, _rental_pipeline, "processed"),
        "revolving": (RevolvingAnalyseBody, _revolving_pipeline, "monthly"),
    }
    if model not in runs:
        raise HTTPException(status_code=404, detail="Unknown model.")
    cls, pipeline, key = runs[model]
    parsed = cls.model_validate(body)
    frame = pipeline(parsed)[2][key]
    mapping = parsed.ingest.mapping if parsed.ingest else {}
    formulas = {d.name: d.formula for d in parsed.ingest.derivedColumns} if parsed.ingest else {}
    return _json_safe(
        {
            "columns": list(frame.columns),
            "rows": frame.head(_DATA_GRID_LIMIT).to_dict("records"),
            "rowCount": len(frame),
            "truncated": len(frame) > _DATA_GRID_LIMIT,
            "calculated": [c for c in frame.columns if c not in mapping],
            "derived": {f: formulas[s] for f, s in mapping.items() if s in formulas},
        }
    )


LENDING_DATE_FIELDS = {"Disbursement Date", "Expected Completion Date"}

# Monetary canonical fields rescaled when a non-USD portfolio currency is
# selected - mirrors app.py's CURRENCY_FIELDS. Covers both models; only
# whichever columns are present in the loaded file are touched.
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

# Process-level FX cache, keyed by the API URL. A plain dict+timestamp rather
# than Streamlit's @st.cache_data (integrations.py is imported by app.py and
# pulls in streamlit at module load, so it can't be used here). One entry for
# the one configured endpoint; TTL from config.FX["cache_ttl_seconds"].
_FX_CACHE: dict[str, tuple[float, dict]] = {}


def _fetch_usd_fx_rates() -> dict:
    """Live USD rates from the same free, keyless endpoint app.py uses
    (config.FX["api_url"]). Returns {currency_code: units per 1 USD}, cached
    for config.FX["cache_ttl_seconds"] so repeated runs don't re-hit it."""
    url = FX["api_url"]
    now = time.time()
    cached = _FX_CACHE.get(url)
    if cached and now - cached[0] < FX["cache_ttl_seconds"]:
        return cached[1]
    resp = requests.get(url, timeout=FX["request_timeout_seconds"], verify=_ca_bundle_path())
    resp.raise_for_status()
    data = resp.json()
    if data.get("result") != "success":
        raise ValueError(f"FX API returned {data.get('result', 'an error')}")
    rates = data["rates"]
    _FX_CACHE[url] = (now, rates)
    return rates


def _convert_to_usd(df: pd.DataFrame, currency: str | None) -> tuple[pd.DataFrame, str | None]:
    """Rescale the monetary columns of `df` from `currency` to USD in place,
    returning (df, warning). USD/None/blank is a no-op.

    Matches app.py: the rate is units of `currency` per 1 USD, so each money
    column is divided by it. A missing rate or a failed lookup does NOT abort
    the run - the figures stay in the original currency and a warning is
    returned for the UI, exactly as Streamlit shows a st.warning there."""
    code = (currency or "").strip().upper()
    if not code or code == "USD":
        return df, None
    try:
        rate = _fetch_usd_fx_rates().get(code)
    except (requests.RequestException, ValueError) as exc:
        return df, (
            f"Couldn't fetch a live {code}/USD exchange rate ({exc}) - "
            f"figures are in {code}, not converted."
        )
    if not rate:
        return df, (
            f"No live rate available for {code} right now - figures are in {code}, not converted."
        )
    converted = [c for c in CURRENCY_FIELDS if c in df.columns]
    for col in converted:
        df[col] = df[col] / rate
    return df, None


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


def _resolve_lending_df(body: LendingAnalyseBody) -> tuple[pd.DataFrame, str | None]:
    """Return the canonical template-field-keyed DataFrame to analyse, from
    either a direct ``rows`` payload or an ``ingest`` (raw upload + mapping),
    plus any non-fatal FX-conversion warning (None when converted/not needed).

    Date columns are coerced here, before returning - not left to
    process_data_input's own internal coercion - because GeneralInputs'
    extraction_date default (df["Disbursement Date"].max(), used whenever
    the caller omits extractionDate) reads the date column before
    process_data_input ever runs. A raw string column there breaks with a
    plain "str - Timedelta" TypeError deep in date arithmetic; mirrors
    app.py's own _coerce_dates(raw, DATE_FIELDS, dayfirst) call, which runs
    on the raw upload for the same reason."""
    if body.ingest:
        raw = _get_ingest(body.ingest.ingestId)
        if raw is None:
            raise HTTPException(
                status_code=404, detail="ingestId not found (upload may have expired)."
            )
        mapped = _canonical_rows_from_mapping(
            raw, body.ingest.mapping, _derived_dicts(body.ingest.derivedColumns)
        )
        missing = [f for f in REQUIRED_FIELDS if f not in mapped.columns]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"Missing required template fields in your mapping: {', '.join(missing)}",
            )
        df = mapped
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
    df, fx_warning = _convert_to_usd(df, body.generalInputs.currency)
    return _coerce_lending_dates(df, LENDING_DATE_FIELDS, dayfirst=True), fx_warning


def _compute_lending(
    df: pd.DataFrame,
    gi_body: GeneralInputsBody,
    selected_cohorts: list[str] | None = None,
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
        ltv_range = (gi_body.ltvCohortFrom or None, gi_body.ltvCohortTo or None)
        ltv_interest = ue.avg_interest_pct(*ltv_range)
        ltv_loss = ltv.percentile_95_losses(*ltv_range)
        # The LTV tab's "include unfinished loans" option (Salmon-sheet basis).
        all_loans_losses = ltv.all_loans_cohort_losses(gi.min_loans_per_cohort)
        ltv_loss_all_loans = ltv.percentile_95_losses_all_loans(gi.min_loans_per_cohort, *ltv_range)
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
        "ltv_interest": ltv_interest,
        "ltv_loss": ltv_loss,
        "ltv_loss_all_loans": ltv_loss_all_loans,
        "all_loans_losses": all_loans_losses,
        "gi_body": gi_body,
        "chart_data": chart_data,
        "summary_cohorts": summary_cohorts,
        "term_supplied": term_supplied,
    }


def _lending_pipeline(body: LendingAnalyseBody) -> tuple[pd.DataFrame, str | None, dict]:
    def run():
        df, fx_warning = _resolve_lending_df(body)
        return df, fx_warning, _compute_lending(df, body.generalInputs, body.selectedCohorts)

    return _cached_pipeline("lending", body, run)


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


def _irr(cashflows: list[float]) -> float | None:
    """Newton-Raphson IRR, ported 1:1 from the React UE Model tab's `irr()`
    (AnalysisSession.tsx) so a figure the analyst reads off the workbook cell
    matches the same figure on screen. Returns None when it can't converge,
    same as the client and numpy_financial's npf.irr."""
    if not cashflows:
        return None
    rate = 0.1
    for _ in range(200):
        f = 0.0
        fd = 0.0
        try:
            for i, cf in enumerate(cashflows):
                f += cf / (1 + rate) ** i
                fd -= (i * cf) / (1 + rate) ** (i + 1)
        except (OverflowError, ZeroDivisionError):
            # JS yields Infinity/NaN here and the client returns null; match it.
            return None
        if abs(f) < 1e-9:
            return rate
        if not math.isfinite(fd) or fd == 0:
            return None
        rate = rate - f / fd
        if not math.isfinite(rate):
            return None
    return None


class UeModelInputsBody(BaseModel):
    """The UE Model sub-tab's manual assumptions as the tab holds them (costs
    as decimals), so Ask AI quotes the figures on screen, not the defaults."""

    upfrontCostPct: float = Field(..., ge=0)
    ongoingCostPct: float = Field(..., ge=0)
    financingCostPct: float = Field(..., ge=0)
    facilityFeePct: float = Field(..., ge=0)
    hedgeCostPct: float = Field(..., ge=0)
    lossChoice: Literal["Actual", "Proxy", "Input"] = "Actual"
    lossInputPct: float | None = Field(
        None, ge=0, description="Loss Rate (%) typed in when lossChoice is 'Input'."
    )


def _ue_model_metrics(
    ue_model: dict[str, Any], inputs: UeModelInputsBody | None = None
) -> dict[str, Any]:
    """The lending UE Model sub-tab's derived figures (Gross/Net IRR, Estimated
    APR, Implied Monthly Interest %, Loss %, Income/cost breakdown), ported 1:1
    from AnalysisSession.tsx's LendingUeModelTab so the export's UE Model sheet
    carries the same numbers the app shows at the default assumptions.

    Mirrors the client: loss rate = Actual, costs = the engine's UE_MODEL_COST_
    DEFAULTS. The app lets the user override these interactively; the export
    records the defaults the analysis ran with (same values the sheet's own
    input cells carry, so a reader can see the basis). `inputs` carries the
    user's overrides instead (Ask AI), with the same Actual/Proxy/Input loss
    choice the tab offers."""
    m = ue_model
    loss_choice = "Actual"
    if inputs is None:
        defaults = m.get("costDefaults") or {}
        upfront = defaults.get("upfront_cost_pct", 0) / 100
        ongoing = defaults.get("ongoing_cost_pct", 0) / 100
        financing = defaults.get("financing_cost_pct", 0) / 100
        facility = defaults.get("facility_fee_pct", 0) / 100
        hedge = defaults.get("hedge_cost_pct", 0) / 100
    else:
        upfront, ongoing = inputs.upfrontCostPct, inputs.ongoingCostPct
        financing, facility, hedge = (
            inputs.financingCostPct,
            inputs.facilityFeePct,
            inputs.hedgeCostPct,
        )
        loss_choice = inputs.lossChoice

    if loss_choice == "Proxy":
        loss_rate = m.get("lossRateProxy")
    elif loss_choice == "Input" and inputs.lossInputPct is not None:
        loss_rate = inputs.lossInputPct / 100
    else:
        loss_rate = m.get("actualLossRate")
    principal = m.get("principal")
    matured_owed = m.get("maturedOwed")
    matured_principal = m.get("maturedPrincipal")
    nMatured = m.get("nMatured")
    matured_term_m = m.get("maturedTermM")
    avg_interest = m.get("averageInterestPct")
    avg_term_days = m.get("averageExpectedTerm")

    def _f(v):
        return v if isinstance(v, (int, float)) and math.isfinite(v) else None

    losses_dollar = (
        loss_rate * matured_owed
        if _f(loss_rate) is not None and _f(matured_owed) is not None
        else None
    )
    losses_pct_of_gbv = (
        losses_dollar / principal
        if losses_dollar is not None and _f(principal) and principal
        else None
    )
    term_m = _f(avg_term_days) / 30.4375 if _f(avg_term_days) is not None else None
    implied_interest = (
        (_f(avg_interest) / term_m) * 12 * 2 if _f(avg_interest) is not None and term_m else None
    )
    implied_monthly = implied_interest / 12 if implied_interest is not None else None

    avg_principal_matured = matured_principal / nMatured if nMatured else None
    avg_fee_matured = m.get("maturedOriginationIncome") / nMatured if nMatured else None
    avg_gbv_matured = matured_owed / nMatured if nMatured and _f(matured_owed) is not None else None
    n_periods = (
        round(matured_term_m) + 3 if _f(matured_term_m) is not None and matured_term_m > 0 else None
    )

    gross_irr = None
    if (
        n_periods is not None
        and _f(avg_principal_matured) is not None
        and _f(avg_gbv_matured) is not None
        and _f(loss_rate) is not None
        and _f(matured_owed) is not None
    ):
        cf0 = -avg_principal_matured + avg_fee_matured - upfront * avg_principal_matured
        monthly_cf = (avg_gbv_matured / n_periods) * (1 - loss_rate) - (
            ongoing * avg_principal_matured
        ) / n_periods
        monthly_irr = _irr([cf0] + [monthly_cf] * n_periods)
        if monthly_irr is not None:
            gross_irr = (1 + monthly_irr) ** 12 - 1

    cost_of_finance = financing + facility + hedge
    net_irr = gross_irr - cost_of_finance if gross_irr is not None else None
    variable_costs = upfront + ongoing
    cost_of_finance_over_term = (
        cost_of_finance * (matured_term_m / 12) if _f(matured_term_m) is not None else None
    )

    matured_revenue = m.get("maturedRevenue")
    matured_origination_income = m.get("maturedOriginationIncome")
    income_net_losses = (
        matured_revenue + matured_origination_income - losses_dollar
        if _f(matured_revenue) is not None
        and _f(matured_origination_income) is not None
        and losses_dollar is not None
        else None
    )
    income_net_losses_margin = (
        income_net_losses / matured_principal
        if income_net_losses is not None and _f(matured_principal) and matured_principal
        else None
    )
    variable_costs_dollar = (
        variable_costs * matured_principal if _f(matured_principal) is not None else None
    )
    product_contribution = (
        income_net_losses - variable_costs_dollar
        if income_net_losses is not None and variable_costs_dollar is not None
        else None
    )
    product_margin = (
        product_contribution / matured_principal
        if product_contribution is not None and _f(matured_principal) and matured_principal
        else None
    )

    return {
        f"Loss Rate used ({loss_choice})": loss_rate,
        "Loss % of GBV": losses_pct_of_gbv,
        "Estimated APR": implied_interest,
        "Implied Monthly Interest %": implied_monthly,
        "Gross IRR": gross_irr,
        "Cost of Finance (% p.a.)": cost_of_finance,
        "Net IRR": net_irr,
        "Cost of Finance (over loan life)": cost_of_finance_over_term,
        "Income (net of losses)": income_net_losses,
        "Income (net of losses) margin": income_net_losses_margin,
        "Variable costs": variable_costs_dollar,
        "Product contribution": product_contribution,
        "Product contribution margin": product_margin,
        "Upfront cost (% of principal)": upfront,
        "Ongoing cost (% of principal)": ongoing,
        "Financing cost (% p.a.)": financing,
        "Facility fee (% p.a.)": facility,
        "Hedge cost (% p.a.)": hedge,
    }


def _run_lending_engine(
    df: pd.DataFrame, c: dict[str, Any], fx_warning: str | None = None
) -> dict[str, Any]:
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
            # The LTV Calculator's interest and loss inputs: Average Interest %
            # and 95th %ile loss limited to the General Inputs cohort range
            # (equal to the whole-book figures when unset).
            "ltvInterest": c["ltv_interest"],
            "ltvLoss": c["ltv_loss"],
            # The same 95th %ile with every loan counted, finished or not, and
            # the per-cohort losses behind it - the LTV tab's checkbox.
            "ltvLossAllLoans": c["ltv_loss_all_loans"],
            "cohortLossesAllLoans": _frame_to_records(c["all_loans_losses"]),
            # The range those figures were computed over, echoed back so the UI
            # labels them with what was run, not whatever is in the inputs now.
            "ltvCohortRange": {
                "from": c["gi_body"].ltvCohortFrom or None,
                "to": c["gi_body"].ltvCohortTo or None,
            },
            # Non-fatal FX notice (selected currency had no live rate) - the
            # React Summary tab shows it; None when USD or conversion succeeded.
            "fxWarning": fx_warning,
        }
    )


@app.post("/api/analyse/lending")
def analyse_lending(body: LendingAnalyseBody) -> dict[str, Any]:
    df, fx_warning, c = _lending_pipeline(body)
    return _run_lending_engine(df, c, fx_warning)


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
    df, _, c = _lending_pipeline(body)
    ue_data, ltv_data = c["ue"].as_dict(), c["ltv"].as_dict()
    mapping = body.ingest.mapping if body.ingest else None
    ue_model = _ue_model_payload(c["processed"], ue_data)
    # The app's LTV Calculator tab runs /api/ltv-calculator with the analysis'
    # green inputs; reproduce the same call here (at its default dropdown
    # choices) so the workbook carries the suggested-LTV matrix + backups.
    ltv_calculator = _ltv_calculator_payload(
        LtvCalcBody(
            averageInterest=c["ltv_interest"],
            termDays=ltv_data.get("Average Term"),
            lossRate=c["ltv_loss"],
        )
    )
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
        currency=body.generalInputs.currency,
        extraction_date=c["gi"].extraction_date,
        # Same Summary figures the React Summary tab shows (summary_metrics.py),
        # so the workbook's Summary sheet can never drift from the app.
        summary_metrics=lending_summary_metrics(c["processed"], c["gi"], ue_data, ltv_data),
        ltv_calculator=ltv_calculator,
        ue_model=_ue_model_metrics(ue_model),
        checks=_lending_data_quality_checks(
            c["processed"], c["cohorts"], LENDING_DQ_CONFIG, c["gi"], c["filtered"]
        ),
    )
    if body.chatHistory:
        xlsx_bytes = _add_chat_history_sheet(xlsx_bytes, body.chatHistory)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="SC_Analysis_Lending.xlsx"'},
    )


def _resolve_rental_df(body: RentalAnalyseBody) -> tuple[pd.DataFrame, str | None]:
    """Return the canonical rental DataFrame from either ``ingest`` (raw
    upload + mapping) or direct ``rows``, with date columns coerced to
    datetime and absent optional date columns pre-declared as NaT so the
    engine's date arithmetic works under pandas 3.0, plus any non-fatal
    FX-conversion warning (None when converted/not needed)."""
    if body.ingest:
        raw = _get_ingest(body.ingest.ingestId)
        if raw is None:
            raise HTTPException(
                status_code=404, detail="ingestId not found (upload may have expired)."
            )
        mapped = _canonical_rows_from_mapping(
            raw, body.ingest.mapping, _derived_dicts(body.ingest.derivedColumns)
        )
        missing = [c for c in RENTAL_REQUIRED_COLUMNS if c not in mapped.columns]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"Missing required rental fields in your mapping: {', '.join(missing)}",
            )
        df = mapped
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
    # date arithmetic and .fillna paths work under pandas 3.0. detect_date
    # (shared with app.py) handles a column mixing formats, e.g. "11/28/2024"
    # beside "2024-08-11 00:00:00" - a bare to_datetime locks onto one format
    # and turns the rest into NaT.
    for c in df.columns:
        if c in {
            "start_date",
            "expected_end_date",
            "closed_date",
            "asset_recovery_date",
            "recovery_date",
            "last_payment_date",
        }:
            df[c] = pd.to_datetime(detect_date(df[c]), errors="coerce")
    for c in {
        "expected_end_date",
        "closed_date",
        "asset_recovery_date",
        "recovery_date",
        "last_payment_date",
    }:
        if c not in df.columns:
            df[c] = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    df, fx_warning = _convert_to_usd(df, body.generalInputs.currency)
    return df, fx_warning


def _status_text(series: pd.Series) -> pd.Series:
    """Status values as text (blanks stay NaN), so they match the string keys
    of a JSON statusMap - a numeric status column (1/0) would otherwise never
    match. Shared by the status gate and /api/ingest/status-values."""
    return series.map(lambda v: v if pd.isna(v) else str(v).strip()).replace("", np.nan)


def _compute_rental(df: pd.DataFrame, gi_body: RentalGeneralInputsBody) -> dict[str, Any]:
    """Run the rental engine pipeline once and return every raw intermediate
    object - shared by /api/analyse/rental (serializes to JSON) and
    /api/export/rental (feeds export_workbook.py's real Excel builder), so
    the pipeline itself runs in exactly one place (same pattern as
    _compute_lending)."""
    status_map = gi_body.statusMap or RENTAL_DEFAULT_STATUS_MAP
    # Same gate as app.py's status editor: an unmapped status would reach the
    # engine as NaN and silently zero MRR / utilisation / loss, not fail.
    if "status" in df.columns:
        df = df.assign(status=_status_text(df["status"]))
        present = df["status"].dropna().unique().tolist()
        unmapped = sorted(s for s in present if not status_map.get(s))
        if unmapped:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{len(unmapped)} status value(s) aren't mapped to a Lendable status yet. "
                    "Map them in General Inputs, then run again. Unmapped: " + ", ".join(unmapped)
                ),
            )
    gi = RentalGeneralInputs(
        df,
        status_map=status_map,
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


def _rental_pipeline(body: RentalAnalyseBody) -> tuple[pd.DataFrame, str | None, dict]:
    def run():
        df, fx_warning = _resolve_rental_df(body)
        return df, fx_warning, _compute_rental(df, body.generalInputs)

    return _cached_pipeline("rental", body, run)


class StatusValuesBody(BaseModel):
    ingest: RentalAnalyseIngest


# A status column with more distinct values than this is almost certainly the
# wrong column (e.g. a free-text note), not something to map row by row.
_STATUS_VALUES_CAP = 200


@app.post("/api/ingest/status-values")
def ingest_status_values(body: StatusValuesBody) -> dict[str, Any]:
    """Every distinct value (with count) of the column mapped to ``status``,
    for the rental General Inputs status table - it needs all raw statuses,
    not the few preview rows the ingest response carries. ``suggested`` is
    DEFAULT_STATUS_MAP matched ignoring case (as the workbook's MATCH does),
    or "" when the value has no default and the user must pick."""
    raw = _get_ingest(body.ingest.ingestId)
    if raw is None:
        raise HTTPException(status_code=404, detail="ingestId not found (upload may have expired).")
    src = body.ingest.mapping.get("status")
    if not src:
        raise HTTPException(status_code=422, detail="No column is mapped to status.")
    mapped = _canonical_rows_from_mapping(
        raw, {"status": src}, _derived_dicts(body.ingest.derivedColumns)
    )
    counts = _status_text(mapped["status"]).value_counts()
    defaults = {k.casefold(): v for k, v in RENTAL_DEFAULT_STATUS_MAP.items()}
    return {
        "values": [
            {"value": v, "count": int(n), "suggested": defaults.get(v.casefold(), "")}
            for v, n in counts.head(_STATUS_VALUES_CAP).items()
        ],
        "truncated": len(counts) > _STATUS_VALUES_CAP,
    }


@app.post("/api/analyse/rental")
def analyse_rental(body: RentalAnalyseBody) -> dict[str, Any]:
    df, fx_warning, c = _rental_pipeline(body)
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
            # Margin / lease tenor per cohort for the UE Overview charts, on the
            # same cohorts as cohortRows.
            "ueCohortRows": _frame_to_records(
                rental_ue_by_cohort(c["processed"], c["gi_calc"]).pipe(
                    lambda u: u[u["cohort"].isin(c["filtered"]["cohort"])]
                )
            ),
            # High-level repayment curve (mob vs % avg collection) - the rental UE
            # tab's "High level repayment curve" chart + table, mirroring app.py's
            # tabs[3] (Unit Economics Analysis).
            "curveRows": _frame_to_records(c["curve"]),
            "checks": checks,
            "summaryMetrics": rental_summary_metrics(
                c["processed"], c["av"], c["gi"], ue_data, ltv_data, c["ts"]
            ),
            "generalInputs": c["gi_calc"],
            # Non-fatal FX notice - see _run_lending_engine's fxWarning.
            "fxWarning": fx_warning,
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
        description=(
            "The unexplained_variance check's detail rows: Cohort, <metricLabel>, "
            "Swing vs Prior Cohort (pp), Likely Contributing Factor(s)."
        ),
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
    averageInterest: float | None = Field(
        None, description="Weighted avg gross interest (decimal), from the analysis."
    )
    termDays: float | None = Field(
        None, description="Weighted avg receivable term (days), from the analysis."
    )
    lossRate: float | None = Field(
        None, description="95th %ile loss rate (decimal) at T+3, from the analysis."
    )
    includeUnfinishedLoans: bool = Field(
        False,
        description="The LTV tab's checkbox: count every loan in a cohort for the loss, "
        "finished or not. Only read by Ask AI, which recomputes the loss from the analysis.",
    )


def _ltv_calculator_payload(body: LtvCalcBody) -> dict[str, Any]:
    """Mirror app.py's lending "LTV Analysis" tab (the LTV Calculator): snap
    the portfolio's weighted-avg term to a tenor bucket, pick the 95th %ile
    loss (falling back to the sector-minimum floor when it's n/a), then run
    the Receivables-sheet waterfall via template_analysis.ltv_calculator and
    return every output plus the dropdown options and the FX-by-tenor table
    so the React tab can render the waterfall matrix, backup tables and
    charts exactly like Streamlit.

    Shared by the /api/ltv-calculator route and /api/export/lending (which
    records the same matrix in the workbook), so the two can't drift."""
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
            "warning": (
                "Can't compute the LTV waterfall - one of the green inputs above "
                "is n/a for this portfolio."
            ),
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
        # Echo the choices the run used, so the export sheet can record them
        # (the React tab keeps them in local state and doesn't need this, but
        # the workbook has no other record of which scenario was computed).
        "dataSource": body.dataSource,
        "country": body.country,
        "macroFxRisk": body.macroFxRisk,
        "segmentation": body.segmentation,
        "hedgeRate": body.hedgeRate,
        "rolledHedge": body.rolledHedge,
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


@app.post("/api/ltv-calculator")
def ltv_calculator(body: LtvCalcBody) -> dict[str, Any]:
    """Route wrapper - the maths lives in _ltv_calculator_payload so the export
    path reuses it (see that function)."""
    return _ltv_calculator_payload(body)


@app.post("/api/export/rental")
def export_rental(body: RentalAnalyseBody) -> Response:
    """Same request shape as /api/analyse/rental (rows or ingest+mapping), but
    returns the real downloadable .xlsx from export_workbook.py's
    _build_rental_export_workbook instead of a JSON summary - no export logic
    re-implemented here (mirrors /api/export/lending)."""
    df, _, c = _rental_pipeline(body)
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
        currency=body.generalInputs.currency,
        # Same Summary figures the React Summary tab shows (summary_metrics.py).
        summary_metrics=rental_summary_metrics(
            c["processed"], c["av"], c["gi"], ue_data, ltv_data, c["ts"]
        ),
        checks=_rental_data_quality_checks(
            c["processed"], c["cohorts"], RENTAL_DQ_CONFIG, c["gi"], c["filtered"], ue_data
        ),
    )
    if body.chatHistory:
        xlsx_bytes = _add_chat_history_sheet(xlsx_bytes, body.chatHistory)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="SC_Analysis_Rental.xlsx"'},
    )


def _resolve_revolving_df(body: RevolvingAnalyseBody) -> pd.DataFrame:
    """The canonical monthly DataFrame from ``ingest`` (upload + mapping) or
    direct ``rows``. No FX rescale: the loss-rate sample threshold is in the
    source currency, and every Outputs ratio is currency-free."""
    if body.ingest:
        raw = _get_ingest(body.ingest.ingestId)
        if raw is None:
            raise HTTPException(
                status_code=404, detail="ingestId not found (upload may have expired)."
            )
        df = _canonical_rows_from_mapping(
            raw, body.ingest.mapping, _derived_dicts(body.ingest.derivedColumns)
        )
    elif body.rows:
        df = pd.DataFrame(body.rows)
    else:
        raise HTTPException(status_code=400, detail="No rows provided.")
    missing = [c for c in REVOLVING_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required revolving fields in your mapping: {', '.join(missing)}",
        )
    # Drop rows blank in every mapped field (trailing summary rows) before the
    # text cleaning below turns their blanks into "nan" strings.
    df = df.replace(r"^\s*$", np.nan, regex=True).dropna(how="all").reset_index(drop=True)
    df["report_month"] = pd.to_datetime(detect_date(df["report_month"]), errors="coerce")
    for c in df.columns:
        if c != "report_month":
            df[c] = _clean_numeric(df[c])
    return df


def _revolving_pipeline(body: RevolvingAnalyseBody) -> tuple[pd.DataFrame, None, dict]:
    def run():
        df = _resolve_revolving_df(body)
        gi = body.generalInputs
        if gi.rollRateWindow not in REVOLVING_WINDOWS:
            raise HTTPException(
                status_code=422,
                detail=f"Roll-rate window must be one of {', '.join(REVOLVING_WINDOWS)}",
            )
        unknown = [c.metric for c in gi.covenants if c.metric not in REVOLVING_COVENANT_METRICS]
        if unknown:
            raise HTTPException(
                status_code=422, detail=f"Unknown covenant metric(s): {', '.join(unknown)}"
            )
        inputs = RevolvingInputs(
            as_of_month=gi.asOfMonth or None,
            lgd=gi.lgd,
            roll_rate_window=gi.rollRateWindow,
            ratio_window_months=gi.ratioWindowMonths,
            writeoff_window_months=gi.writeoffWindowMonths,
            min_outstanding_in_sample=gi.minOutstandingInSample,
            min_outstanding_pct_of_peak=gi.minOutstandingPctOfPeak,
            ltv_window_months=gi.ltvWindowMonths,
            stress_percentile=gi.stressPercentile,
            denominator_lag_months=gi.denominatorLagMonths,
            lagged_blend_weight=gi.laggedBlendWeight,
            loss_rate_basis=gi.lossRateBasis,
            balance_tolerance=REVOLVING_DEFAULTS["balance_tolerance"],
            unexplained_warning_pct=REVOLVING_DEFAULTS["unexplained_warning_pct"],
            lgd_basis=gi.lgdBasis,
            cost_of_funds_pct=gi.costOfFundsPct,
            opex_pct=gi.opexPct,
            advance_haircut=gi.advanceHaircut,
            backtest_min_history_months=gi.backtestMinHistoryMonths,
            early_warning_lookback_months=gi.earlyWarningLookbackMonths,
            early_warning_threshold=gi.earlyWarningThreshold,
            scenarios=[sc.model_dump() for sc in gi.scenarios],
            covenants=[cv.model_dump() for cv in gi.covenants],
            stress_roll_rate_cap=REVOLVING_DEFAULTS["stress_roll_rate_cap"],
        )
        try:
            return df, None, run_revolving(df, inputs)
        except ValueError as exc:  # bad input shape -> 422 so the UI can surface it
            raise HTTPException(status_code=422, detail=f"Analysis failed: {exc}") from exc

    return _cached_pipeline("revolving", body, run)


@app.get("/api/revolving/defaults")
def revolving_defaults() -> dict[str, Any]:
    """The revolving General Inputs defaults (config.toml), so the UI shows the
    values a run will use without keeping its own copy of them."""
    return RevolvingGeneralInputsBody().model_dump()


@app.get("/api/revolving/options")
def revolving_options() -> dict[str, Any]:
    """The General Inputs pickers' choices, from config / the metric
    catalogue: roll-rate window names and the metrics a covenant can test."""
    return {
        "rollRateWindows": list(REVOLVING_WINDOWS),
        "covenantMetrics": [
            {"key": k, "label": label} for k, (label, _) in REVOLVING_COVENANT_METRICS.items()
        ],
    }


@app.post("/api/analyse/revolving")
def analyse_revolving(body: RevolvingAnalyseBody) -> dict[str, Any]:
    _, _, c = _revolving_pipeline(body)

    def records(frame: pd.DataFrame) -> list[dict[str, Any]]:
        return _frame_to_records(frame.reset_index())

    return _json_safe(
        {
            "summary": {"rows": int(len(c["monthly"])), "cohorts": 0},
            "availableMonths": c["availableMonths"],
            "facts": _frame_to_records(c["facts"]),
            "ratios": c["ratios"],
            "warnings": c["warnings"],
            "qualityChecks": c["qualityChecks"],
            "balances": records(c["balances"]),
            "rollRates": records(c["rollRates"]),
            "lossFactors": records(c["lossFactors"]),
            "ecl": _frame_to_records(c["ecl"]),
            "eclSummary": c["eclSummary"],
            "checks": records(c["checks"]),
            "lossRates": _frame_to_records(c["pit"]),
            "lossRateDistribution": records(c["distribution"]),
            "lgdUsed": c["lgdUsed"],
            "lgdBasis": c["lgdBasis"],
            "standardMetrics": c["standardMetrics"],
            "overTime": _revolving_over_time_records(c["overTime"]),
            "unitEconomics": c["unitEconomics"],
            "performance": c["performance"],
            "ltvInterest": c["ltvInterest"],
            "tenorInputs": c["tenorInputs"],
            "ageingMix": c["ageingMix"],
            "largestCustomerShare": c["largestCustomerShare"],
            "earlyWarnings": c["earlyWarnings"],
            "backtest": _frame_to_records(c["backtest"]),
            "backtestSummary": c["backtestSummary"],
            "stress": c["stress"],
            "advance": c["advance"],
            "covenants": c["covenants"],
            "generalInputs": body.generalInputs.model_dump(),
        }
    )


def _revolving_over_time_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Metrics-by-month rows, each labelled and typed for display."""
    labelled = frame.copy()
    labelled.insert(0, "kind", [REVOLVING_METRICS[k].kind for k in labelled.index])
    labelled.insert(0, "Metric", [REVOLVING_METRICS[k].label for k in labelled.index])
    return _frame_to_records(labelled.reset_index(names="key"))


@app.post("/api/export/revolving")
def export_revolving(body: RevolvingAnalyseBody) -> Response:
    """Same request shape as /api/analyse/revolving; returns the .xlsx with the
    Outputs and Transition Matrices sheets."""
    _, _, c = _revolving_pipeline(body)
    xlsx_bytes = _build_revolving_export_workbook(c, body.generalInputs.model_dump())
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="SC_Analysis_Revolving.xlsx"'},
    )


# Standalone read-only HTML report (what Share downloads until the app is
# deployed). Each route reruns the model's own (cached) analyse endpoint and
# hands that exact JSON to scripts/share_html.py - no figures recomputed. The
# LTV choices are the user's LTV-tab dropdowns; the green inputs are filled in
# here from the analysis, the same way each LTV tab does.
def _html_response(
    model: str, result: dict, inputs: dict | None, ltv_calc: dict | None = None
) -> Response:
    ltv_calc = _json_safe(ltv_calc) if ltv_calc else None
    return Response(
        content=build_share_html(model, _json_safe(result), inputs, ltv_calc),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="SC_Analysis_{model}.html"'},
    )


@app.post("/api/export/html/lending")
def export_html_lending(body: LendingAnalyseBody, ltv: LtvCalcBody | None = None) -> Response:
    result = analyse_lending(body)
    ue, ltv_data = result.get("ue") or {}, result.get("ltv") or {}
    use_all = bool(ltv and ltv.includeUnfinishedLoans)
    ltv_calc = _ltv_calculator_payload(
        (ltv or LtvCalcBody()).model_copy(
            update={
                "averageInterest": result.get("ltvInterest", ue.get("Average Interest %")),
                "termDays": ltv_data.get("Average Term"),
                "lossRate": result.get("ltvLossAllLoans") if use_all else result.get("ltvLoss"),
            }
        )
    )
    return _html_response("lending", result, body.generalInputs.model_dump(), ltv_calc)


@app.post("/api/export/html/rental")
def export_html_rental(body: RentalAnalyseBody) -> Response:
    return _html_response("rental", analyse_rental(body), body.generalInputs.model_dump())


@app.post("/api/export/html/revolving")
def export_html_revolving(body: RevolvingAnalyseBody, ltv: LtvCalcBody | None = None) -> Response:
    result = analyse_revolving(body)
    perf = result.get("performance") or {}
    avg_term = perf.get("avgTerm")
    ltv_calc = _ltv_calculator_payload(
        (ltv or LtvCalcBody()).model_copy(
            update={
                "averageInterest": result.get("ltvInterest"),
                "termDays": avg_term * 30.4375 if avg_term is not None else None,
                "lossRate": perf.get("lossRate95"),
            }
        )
    )
    return _html_response("revolving", result, body.generalInputs.model_dump(), ltv_calc)


class SuggestMappingBody(BaseModel):
    modelKey: str = Field(..., description="'lending' or 'rental'.")
    columns: list[str] = Field(..., description="Raw uploaded file's column headers.")
    context: str = ""
    documents: list[ContextDocument] = Field(default_factory=list)
    knownLoanStatusColumn: str | None = None


@app.post("/api/ai/suggest-mapping")
def suggest_mapping(body: SuggestMappingBody) -> dict[str, Any]:
    """AI-assisted column-mapping auto-fill (ai_client._suggest_mapping), the
    same DeepSeek V4 Flash call app.py's "Auto-fill with AI" button makes -
    no mapping logic re-implemented here. Returns an empty mapping (not an
    error) when no DEEPINFRA_API_KEY is configured, exactly like the
    Streamlit app; the frontend falls back to its own name-matching
    heuristic in that case."""
    config = {
        "lending": LENDING_DQ_CONFIG,
        "revolving": REVOLVING_DQ_CONFIG,
    }.get(body.modelKey, RENTAL_DQ_CONFIG)
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
        description=(
            "User's natural-language request, e.g. "
            "'Expected Interest = Gross Loan Value - Principal Value'."
        ),
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
    user to type a formula manually in that case.

    `configured` distinguishes the two ways this returns no formula: no API
    key at all, versus a key that works but a request the model could not
    turn into one. Both used to come back as bare nulls, so the UI reported
    "AI isn't configured" even when it was - sending people to check server
    config when the fix was to rephrase the request."""
    try:
        result = _suggest_derived_column(body.columns, body.request)
    except requests.RequestException as exc:
        raise _ai_error(exc) from exc
    return {
        "name": result.get("name") if result else None,
        "formula": result.get("formula") if result else None,
        "configured": bool(_get_deepinfra_api_key()),
    }


class ChatMessage(BaseModel):
    role: str
    content: str


class LendingChatBody(LendingAnalyseBody):
    question: str
    # Set on Ask AI what-if copies so they never replace the cached real run.
    _skip_cache: bool = PrivateAttr(default=False)
    history: list[ChatMessage] = Field(default_factory=list)
    userContext: str = ""
    documents: list[ContextDocument] = Field(default_factory=list)
    # The LTV Calculator dropdowns and UE Model assumptions currently on
    # screen. Both are user choices the analysis itself doesn't carry, so
    # without them the chat can't quote those tabs' figures. The green inputs
    # in ltvCalculator are ignored and recomputed from the analysis.
    ltvCalculator: LtvCalcBody | None = None
    ueModelInputs: UeModelInputsBody | None = None


class RentalChatBody(RentalAnalyseBody):
    question: str
    # Set on Ask AI what-if copies so they never replace the cached real run.
    _skip_cache: bool = PrivateAttr(default=False)
    history: list[ChatMessage] = Field(default_factory=list)
    userContext: str = ""
    documents: list[ContextDocument] = Field(default_factory=list)


class RevolvingChatBody(RevolvingAnalyseBody):
    question: str
    # Set on Ask AI what-if copies so they never replace the cached real run.
    _skip_cache: bool = PrivateAttr(default=False)
    history: list[ChatMessage] = Field(default_factory=list)
    userContext: str = ""
    documents: list[ContextDocument] = Field(default_factory=list)


# Ask AI what-ifs: the inputs a question may override, per model, as
# (body attribute, its model). Only numeric fields are offered, and the
# engine re-runs with them - the AI never supplies a figure.
_WHATIF_SECTIONS: dict[str, list[tuple[str, type[BaseModel]]]] = {
    "lending": [
        ("generalInputs", GeneralInputsBody),
        ("ueModelInputs", UeModelInputsBody),
        ("ltvCalculator", LtvCalcBody),
    ],
    "rental": [("generalInputs", RentalGeneralInputsBody)],
    "revolving": [("generalInputs", RevolvingGeneralInputsBody)],
}


def _default_ue_model_inputs() -> UeModelInputsBody:
    d = UE_MODEL_COST_DEFAULTS
    return UeModelInputsBody(
        upfrontCostPct=d["upfront_cost_pct"] / 100,
        ongoingCostPct=d["ongoing_cost_pct"] / 100,
        financingCostPct=d["financing_cost_pct"] / 100,
        facilityFeePct=d["facility_fee_pct"] / 100,
        hedgeCostPct=d["hedge_cost_pct"] / 100,
    )


def _whatif_section(body: BaseModel, attr: str, cls: type[BaseModel]) -> BaseModel:
    """The section as the user has it, or its defaults when untouched."""
    current = getattr(body, attr)
    if current is not None:
        return current
    return _default_ue_model_inputs() if cls is UeModelInputsBody else cls()


# Numeric, but only read when another (non-numeric) choice is set, so a
# what-if on it alone would change nothing.
_WHATIF_EXCLUDED = {"ueModelInputs.lossInputPct"}


def _is_numeric_field(annotation: Any) -> bool:
    args = typing.get_args(annotation) or (annotation,)
    return any(a in (int, float) for a in args)


def _whatif_fields(body: BaseModel, model: str) -> dict[str, Any]:
    """'section.field' -> current value, for every numeric input."""
    out: dict[str, Any] = {}
    for attr, cls in _WHATIF_SECTIONS[model]:
        section = _whatif_section(body, attr, cls)
        for name, field in cls.model_fields.items():
            key = f"{attr}.{name}"
            if _is_numeric_field(field.annotation) and key not in _WHATIF_EXCLUDED:
                out[key] = getattr(section, name)
    return out


def _apply_whatif(
    body: BaseModel, model: str, overrides: dict[str, Any]
) -> tuple[BaseModel, dict[str, Any], list[str]]:
    """A copy of `body` with the overrides that pass the input's own
    validation (ranges, types); returns (copy, applied, rejected names)."""
    allowed = _whatif_fields(body, model)
    applied: dict[str, Any] = {}
    rejected: list[str] = []
    update: dict[str, BaseModel] = {}
    for attr, cls in _WHATIF_SECTIONS[model]:
        data = _whatif_section(body, attr, cls).model_dump()
        for key, value in overrides.items():
            section, _, name = key.partition(".")
            if section != attr:
                continue
            ok = key in allowed and isinstance(value, (int, float)) and not isinstance(value, bool)
            if ok:
                try:
                    cls.model_validate({**data, name: value})
                except ValidationError:
                    ok = False
            if ok:
                data[name] = value
                applied[key] = value
            else:
                rejected.append(key)
        if any(k.startswith(f"{attr}.") for k in applied):
            update[attr] = cls.model_validate(data)
    rejected += [k for k in overrides if k not in applied and k not in rejected]
    new = body.model_copy(update=update)
    new._skip_cache = True
    return new, applied, rejected


def _chat_answer(body: BaseModel, model: str, build_context) -> dict[str, Any]:
    """Answer an Ask AI question. When it asks for a what-if on an input, the
    engine re-runs with that input changed and the AI is given both results."""
    history = [msg.model_dump() for msg in body.history]
    context = build_context(body)
    fields = _whatif_fields(body, model)
    overrides = _extract_whatif(body.question, history, fields)
    whatif = None
    if overrides:
        alt, applied, rejected = _apply_whatif(body, model, overrides)
        lines = [
            "",
            "## What-if scenario (hypothetical - recomputed by the app with the inputs below "
            "changed; the actual portfolio figures are the sections above)",
        ]
        lines += [f"Changed: {k}: {fields[k]} -> {v}" for k, v in applied.items()]
        if rejected:
            lines.append(
                "Could not apply (not an input, or outside its allowed range): "
                + ", ".join(rejected)
            )
        if applied:
            lines.append(build_context(alt, with_docs=False))
        context += "\n".join(lines)
        whatif = {
            "changes": [
                {
                    "input": _whatif_label(k),
                    "from": _whatif_value_text(k, fields[k]),
                    "to": _whatif_value_text(k, v),
                }
                for k, v in applied.items()
            ],
            "rejected": [_whatif_label(k) for k in rejected],
        }
    try:
        answer = _ask_data_chatbot(body.question, context, history)
    except (RuntimeError, requests.RequestException) as exc:
        raise _ai_error(exc) from exc
    # `whatif` drives the Ask AI tab's banner: which inputs the answer changed.
    return {"answer": answer, "whatif": whatif}


_WHATIF_SECTION_LABELS = {
    "generalInputs": "General Inputs",
    "ueModelInputs": "UE Model",
    "ltvCalculator": "LTV Calculator",
}


def _whatif_label(key: str) -> str:
    """'generalInputs.fundingCostPct' -> 'General Inputs: Funding Cost %'."""
    section, _, name = key.partition(".")
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("Pct", "%")
    words = re.sub(r"\b(lgd|ltv|ue|fx)\b", lambda w: w.group(0).upper(), words, flags=re.I)
    return f"{_WHATIF_SECTION_LABELS.get(section, section)}: {words[:1].upper()}{words[1:]}"


def _whatif_value_text(key: str, value: Any) -> str:
    """Rates as percentages (they are held as decimals), everything else as is."""
    if value is None:
        return "blank"
    name = key.partition(".")[2]
    if re.search(r"Pct|Rate|Percentile|Haircut|Weight|^lgd$", name):
        return f"{value * 100:g}%"
    return f"{value:g}"


def _ai_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=502, detail=f"AI request failed: {exc}")


# How each Lending figure is calculated, written from the engine code
# (template_analysis/*, summary_metrics.py, _ue_model_metrics,
# ltv_calculator.compute_ltv). The chat prompt tells the model to explain
# "why"/"how" questions from these notes only, so keep them in step with the
# code they describe.
_LENDING_CHAT_METHODOLOGY = """\
All rates below are decimals (0.1986 = 19.86%). Sums run over loans; "pooled" means the
figure is computed once over the summed loans, not averaged across cohorts.
- Cohort: the Disbursement Date's calendar month.
- Matured ("Reached T+3?"): maturity date <= extraction date - Days after term. Maturity date
  is Disbursement Date + Term (days) when a Term column is mapped, else Expected Completion
  Date. A loan with no maturity date is never matured.
- Term (days): the mapped Term column, else Expected Completion Date - Disbursement Date.
- Loss Rate (Summary tab) = Average Loss (Unit Economics tab) = 1 - sum(Total Paid) /
  sum(Principal Value + Expected Interest + Expected Fee), matured loans only, pooled. This is
  a GBV basis (unpaid principal AND unpaid interest/fees count as loss), not principal-only.
  Penalties count only if they are included in the column mapped to Expected Fee.
- Cohort Loss Rate: the same formula over one cohort's matured loans; blank when that cohort
  has fewer matured loans than Minimum loans per cohort.
- 95th %ile Loss Rate (95th Percentile Losses): 95th percentile, linearly interpolated (as
  Excel PERCENTILE), of the cohort Loss Rates of cohorts with at least Minimum loans per
  cohort matured loans.
- Loss Rate Proxy (1-PvD): 1 - sum(Total Paid) / sum(Total Due) over ALL loans.
- PvD (Summary tab): sum(Total Paid) / sum(Total Due), matured loans only. Cohort PvD Ratio:
  the same per cohort.
- Outstanding GBV: over loans NOT yet matured, sum(Principal + Expected Interest + Expected
  Fee) - sum(Total Paid).
- Expected Interest % = Average Interest % = Product Interest Rate: sum(Expected Interest) /
  sum(Principal Value), all loans. Expected Fee % = Average Fee %: sum(Expected Fee) /
  sum(Principal Value), all loans. Average Total Revenue %: (sum(Expected Interest) +
  sum(Expected Fee)) / sum(Principal Value), all loans.
- Average Term (LTV) = Average Expected Term (UE), days: sum(Term (days) x Principal) /
  sum(Principal), ALL loans. Weighted Avg Term (matured), Summary tab: the same, matured loans
  only. Cohort Weighted Avg Term: the same per cohort.
- APR Proxy: Average Interest % / (Weighted Avg Term (matured) / 30.4375) x 12.
- Avg Loan Value = Average Principal Amount: mean Principal Value per loan.
- Sense-check Margin: (Interest % + Fee %) - Average Loss x (1 + Interest % + Fee %).
- Cohort columns: Avg Loan Principal Size = Total Principal / Loan Count; Avg Loan Fee % per
  cohort = Total Fee / Total Principal; Avg Annualized Flat Interest Rate = (Total Interest /
  Total Principal) / (Weighted Avg Term / 30.4375) x 12.
- The matured-only cohort table is the Excel "Cohorts" pivot (Loan Count = matured loans).
  The all-loans cohort table aggregates every loan; its Loss Rate is still matured-only.
- UE Model sub-tab (matured loans): Losses = Loss Rate used x matured owed; Loss % of GBV =
  Losses / total principal; Estimated APR = (Average Interest % / (Average Expected Term /
  30.4375)) x 12 x 2; Implied Monthly Interest % = Estimated APR / 12. Gross IRR: monthly IRR
  of [-avg principal + avg fee - upfront cost x avg principal, then (matured term in months,
  rounded, + 3) equal monthly flows of avg matured owed / periods x (1 - Loss Rate used) -
  ongoing cost x avg principal / periods], annualised as (1 + r)^12 - 1. Cost of Finance
  (% p.a.) = financing + facility fee + hedge; Net IRR = Gross IRR - Cost of Finance.
  Income (net of losses) = matured interest + matured fees - Losses; Product contribution =
  that - (upfront + ongoing cost) x matured principal; margins divide by matured principal.
- LTV Calculator: term bucket = the tenor nearest Average Term / 30.4375 months. Stress Loss =
  95th %ile Loss x credit stress factor (the Min Sector Loss Rate replaces the 95th %ile when
  it is n/a). Selected Stress Loss = max(Stress Loss, Min Sector Loss Rate). Advance on GBV
  (No FX) = 1 - Selected Stress Loss; Advance on GBV (FX High/Low) = No FX / (1 + Selected FX
  Deval High/Low). Selected FX Deval = Base FX Deval + FX Slippage + roll risk (0 for a Term
  hedge). Advance on Principal = Advance on GBV x (1 + Product Interest Rate).
"""

_SUMMARY_METRIC_LABELS = {
    "dataUpTo": "Data up to (latest disbursement date)",
    "outstandingGbv": "Outstanding GBV",
    "lossRate": "Loss Rate",
    "pctile95LossRate": "95th %ile Loss Rate",
    "productInterestRate": "Product Interest Rate",
    "dateRangeStart": "Date Range start",
    "dateRangeEnd": "Date Range end (extraction date)",
    "loans": "Loans",
    "principalDisbursed": "Value of Principal Disbursed",
    "totalCollected": "Total Collected",
    "expectedInterestPct": "Expected Interest %",
    "expectedFeePct": "Expected Fee %",
    "avgLoanValue": "Avg Loan Value",
    "avgTotalRevenuePct": "Avg Total Revenue",
    "pvd": "PvD",
    "weightedAvgTermMatured": "Weighted Avg Term (matured), days",
    "aprProxy": "APR Proxy",
}

_LTV_RESULT_LABELS = {
    "tenor_bucket": "Term bucket (months)",
    "historical_fx_deval": "Historical Devaluation",
    "utilized_fx_deval": "Utilized Historical FX Deval",
    "stress_fx_factor": "FX stress factor",
    "stress_fx_deval": "Stress FX Devaluation",
    "base_fx_deval": "Base FX Deval",
    "fx_slippage": "FX Slippage Stress",
    "roll_risk_high": "Roll risk (High)",
    "roll_risk_low": "Roll risk (Low)",
    "selected_fx_deval_high": "Selected FX Deval (High)",
    "selected_fx_deval_low": "Selected FX Deval (Low)",
    "credit_stress_factor": "Credit stress factor",
    "stress_loss_rate": "Stress Loss",
    "minimum_stress_loss": "Min Sector Loss Rate",
    "selected_stress_loss": "Selected Stress Loss",
    "ltgbv_no_fx": "Advance on GBV - No FX",
    "ltgbv_high": "Advance on GBV - FX High",
    "ltgbv_low": "Advance on GBV - FX Low",
    "ltv_no_fx": "Advance on Principal - No FX",
    "ltv_high": "Advance on Principal - FX High",
    "ltv_low": "Advance on Principal - FX Low",
}


def _chat_value(v: Any) -> str:
    """One figure as chat-context text: n/a for missing, money with commas."""
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "n/a"
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    return f"{v:,.2f}" if abs(v) >= 1000 else f"{v:.6g}"


def _chat_items(values: dict[str, Any], labels: dict[str, str] | None = None) -> list[str]:
    labels = labels or {}
    return [f"- {labels.get(k, k)}: {_chat_value(v)}" for k, v in values.items()]


def _lending_chat_ltv_lines(
    c: dict[str, Any], ue_data: dict, ltv_data: dict, ltv_calc: LtvCalcBody | None
) -> list[str]:
    """The LTV Calculator exactly as the LTV Analysis tab runs it: the user's
    dropdowns, with the green inputs taken from the analysis the same way
    LtvTabs.tsx does (interest and loss limited to the General Inputs cohort
    range; a range with no data gives n/a, which the calculator handles)."""
    body = (ltv_calc or LtvCalcBody()).model_copy(
        update={
            "averageInterest": c["ltv_interest"],
            "termDays": ltv_data.get("Average Term"),
            "lossRate": c["ltv_loss_all_loans"]
            if ltv_calc is not None and ltv_calc.includeUnfinishedLoans
            else c["ltv_loss"],
        }
    )
    p = _ltv_calculator_payload(body)
    gi_body = c["gi_body"]
    ltv_range = (
        f" (cohorts {gi_body.ltvCohortFrom or 'first'} to {gi_body.ltvCohortTo or 'latest'} "
        "only, per General Inputs)"
        if gi_body.ltvCohortFrom or gi_body.ltvCohortTo
        else ""
    )
    lines = [
        "## LTV Analysis tab - LTV Calculator (at the dropdown choices currently on screen)",
        f"Choices: Data source {body.dataSource}; Alt Lender Country {body.country}; "
        f"Macro FX Risk {body.macroFxRisk}; Segmentation {body.segmentation}; "
        f"Hedge rate {_chat_value(body.hedgeRate)}; Rolled/Term hedge {body.rolledHedge}.",
        f"Green inputs: Product Interest Rate {_chat_value(p['greenInterest'])}{ltv_range}; "
        f"Average Term {_chat_value(body.termDays)} days -> term bucket "
        f"{_chat_value(p['greenTermBucket'])} months; 95th %ile Loss "
        f"{_chat_value(p['greenLoss'])}{ltv_range}"
        + (
            " - counting every loan, finished or not (LTV tab checkbox)"
            if body.includeUnfinishedLoans
            else " - matured loans only (T+3)"
        )
        + ".",
    ]
    lines += [f"Note: {n}" for n in p["lossRateFallbackNotes"]]
    if not p["ok"]:
        return lines + [f"Not computed: {p['warning']}"]
    result = dict(p["result"])
    notes = result.pop("notes", [])
    return lines + _chat_items(result, _LTV_RESULT_LABELS) + [f"Note: {n}" for n in notes]


def _build_lending_chat_context_lite(
    c: dict[str, Any],
    ue_data: dict,
    ltv_data: dict,
    checks: list,
    user_context: str = "",
    fx_warning: str | None = None,
    ltv_calc: LtvCalcBody | None = None,
    ue_model_inputs: UeModelInputsBody | None = None,
) -> str:
    """Every figure the React Lending screens compute, as text: Summary,
    Cohorts (matured-only and all-loans tables), Unit Economics Overview and
    UE Model, LTV Analysis and its Calculator, Data Checks - plus how each is
    calculated, which the chat prompt requires for "why" questions.

    Aggregates only, never loan rows: a check's detail table goes in only
    when it is per cohort (the duplicate-ID and negative-fee checks list
    individual loans and are left as their one-line message)."""
    gi, df, cohorts = c["gi"], c["processed"], c["cohorts"]
    ue_model = _ue_model_payload(df, ue_data)
    ue_basis = (
        "the assumptions currently entered on screen"
        if ue_model_inputs
        else "the default assumptions"
    )
    lines = [
        "## Lending portfolio - summary statistics",
        f"Loans: {len(df):,}. Mapped/computed columns: {', '.join(map(str, df.columns))}.",
        f"Data extraction date: {gi.extraction_date.date()}. "
        f"Days after term (T+3 cutoff): {gi.days_after_term}.",
        f"Minimum loans per cohort (template threshold): {gi.min_loans_per_cohort}.",
        f"Currency: {c['gi_body'].currency or 'as uploaded'}."
        + (f" FX warning: {fx_warning}" if fx_warning else ""),
        "",
        "## Summary tab",
        *_chat_items(lending_summary_metrics(df, gi, ue_data, ltv_data), _SUMMARY_METRIC_LABELS),
        "",
        "## Unit Economics tab - Overview",
        *_chat_items(ue_data),
        "",
        "## LTV Analysis tab - LTV Inputs",
        *_chat_items(ltv_data),
        "",
        *_lending_chat_ltv_lines(c, ue_data, ltv_data, ltv_calc),
        "",
        f"## Unit Economics tab - UE Model sub-tab (matured loans, at {ue_basis})",
        *_chat_items({k: v for k, v in ue_model.items() if k != "costDefaults"}),
        *_chat_items(_ue_model_metrics(ue_model, ue_model_inputs)),
        "",
        "## Cohorts tab - matured loans only (the Excel Cohorts pivot)",
        f"{len(cohorts)} monthly cohorts by Disbursement Date with at least one matured loan. "
        f"Columns: {', '.join(cohorts.columns)}.",
        cohorts.to_json(orient="records", date_format="iso"),
        "",
        "## Cohorts tab - all loans (shown when 'Matured loans only' is unticked)",
        c["summary_cohorts"].to_json(orient="records", date_format="iso"),
    ]
    chart = c["chart_data"]
    if not chart.empty:
        lines += [
            "",
            "## Unit Economics tab - per-cohort Interest % and Fee % (Overview charts)",
            chart[["Cohort", "Interest %", "Fee %"]].to_json(orient="records", date_format="iso"),
        ]
    if checks:
        lines += ["", "## Data Checks tab - checks flagged"]
        for chk in checks:
            lines.append(f"- ({chk.get('level', 'info')}) {chk.get('message', '')}")
            detail = chk.get("detail")
            if (
                isinstance(detail, pd.DataFrame)
                and not detail.empty
                and "Cohort" in detail.columns
                and LENDING_DQ_CONFIG["id_field"] not in detail.columns
            ):
                lines.append("  Detail: " + detail.to_json(orient="records", date_format="iso"))
    lines += ["", "## How each figure is calculated", _LENDING_CHAT_METHODOLOGY]
    if user_context.strip():
        lines += ["", "## User-provided context about this dataset", user_context.strip()]
    return "\n".join(lines)


def _chat_user_context(body: BaseModel, with_docs: bool) -> str:
    # Same 60000/15000 truncation app.py's chat tab uses
    # (_ai_context_text(max_chars=60000, per_doc=15000)). A what-if copy
    # skips it: the documents are already in the real run's context.
    if not with_docs:
        return ""
    return document_text.combine_context_text(
        body.userContext, [d.model_dump() for d in body.documents], max_chars=60000, per_doc=15000
    )


def _lending_chat_context(body: LendingChatBody, with_docs: bool = True) -> str:
    df, fx_warning, c = _lending_pipeline(body)
    ue_data, ltv_data = c["ue"].as_dict(), c["ltv"].as_dict()
    checks = _lending_data_quality_checks(
        df, c["cohorts"], LENDING_DQ_CONFIG, c["gi"], c["filtered"]
    )
    return _build_lending_chat_context_lite(
        c,
        ue_data,
        ltv_data,
        checks,
        _chat_user_context(body, with_docs),
        fx_warning=fx_warning,
        ltv_calc=body.ltvCalculator,
        ue_model_inputs=body.ueModelInputs,
    )


@app.post("/api/ai/chat/lending")
def ai_chat_lending(body: LendingChatBody) -> dict[str, Any]:
    """Ask AI, Lending model. Builds an aggregate-only text context of every
    figure the Lending screens show (never raw loan rows - see
    _build_lending_chat_context_lite) and forwards the question to
    ai_client._ask_data_chatbot."""
    return _chat_answer(body, "lending", _lending_chat_context)


def _rental_chat_context(body: RentalChatBody, with_docs: bool = True) -> str:
    df, _, c = _rental_pipeline(body)
    ue_data, ltv_data, churn_data = c["ue"].as_dict(), c["ltv"].as_dict(), c["churn"].as_dict()
    checks = _rental_data_quality_checks(
        df, c["cohorts"], RENTAL_DQ_CONFIG, c["gi"], c["filtered"], ue_data
    )
    return _build_rental_chat_context(
        c["gi"],
        c["processed"],
        c["av"],
        c["cohorts"],
        ue_data,
        ltv_data,
        churn_data,
        c["ts"],
        checks,
        _chat_user_context(body, with_docs),
    )


@app.post("/api/ai/chat/rental")
def ai_chat_rental(body: RentalChatBody) -> dict[str, Any]:
    """Ask AI, Rental & Subscription model. Unlike the lending endpoint, this
    calls the real, unmodified scripts/chat_context.py builder directly -
    every object it needs (gi/df/av/cohorts/ue/ltv/churn/ts) already comes
    out of _compute_rental, no reduced version required."""
    return _chat_answer(body, "rental", _rental_chat_context)


# The Revolving calculation write-up, given to Ask AI so it can explain how
# each figure is built.
_REVOLVING_CALCULATIONS = (
    Path(__file__).resolve().parent.parent / "scripts" / "revolving_analysis" / "CALCULATIONS.md"
).read_text(encoding="utf-8")


def _revolving_chat_context(body: RevolvingChatBody, with_docs: bool = True) -> str:
    _, _, c = _revolving_pipeline(body)
    perf = c["performance"]
    s = c["eclSummary"]
    user_context = _chat_user_context(body, with_docs)
    lines = [
        "# Revolving Lending portfolio",
        f"As-of month {s['asOfMonth']}; roll-rate model {s['method']}; LGD {c['lgdUsed']}.",
        "",
        "## Summary tab - UE / LTV ratios",
        *[f"- {r['label']}: {_chat_value(r['value'])}" for r in c["ratios"]],
        "",
        "## Performance tab - one key per month (YYYY-MM)",
        json.dumps(perf["table"], default=str),
        "",
        "## LTV Calculator / Unit Economics inputs",
        f"- Avg term (mean of 1 / net collection ratio over the last {perf['termMonths']} "
        f"months): {_chat_value(perf['avgTerm'])}",
        f"- Interest yield (m, mean of the last {perf['interestYieldMonths']} months, "
        f"{perf['interestBasis']} interest): {_chat_value(perf['interestYield'])}",
        f"- Estimated loss rate at as-of: {_chat_value(perf['lossRateNow'])}",
        f"- Stress percentile loss rate ({perf['lossRateBasis']} basis, "
        f"{perf['lossRateMonths']} in-sample months): {_chat_value(perf['lossRate95'])} "
        "(the LTV Calculator stresses it by the credit stress factor)",
        f"- Product interest rate (interest yield x avg term): {_chat_value(c['ltvInterest'])}",
        *[f"- UE {u['item']}: monthly {_chat_value(u['monthly'])}" for u in c["unitEconomics"]],
        "",
        "## ECL at as-of",
        *_chat_items(s),
        "",
        "## Data Checks tab",
        *[f"- ({q['level']}) {q['message']}" for q in c["qualityChecks"]],
        *[f"- (warning) {w}" for w in c["warnings"]],
    ]
    if with_docs:
        lines += ["", "## How each figure is calculated", _REVOLVING_CALCULATIONS]
    if user_context.strip():
        lines += ["", "## User-provided context about this dataset", user_context.strip()]
    return "\n".join(lines)


@app.post("/api/ai/chat/revolving")
def ai_chat_revolving(body: RevolvingChatBody) -> dict[str, Any]:
    """Ask AI, Revolving Lending model. The context is the monthly aggregates
    and every figure the Revolving tabs show - the tape is already one row
    per month, so no loan-level data exists to leak."""
    return _chat_answer(body, "revolving", _revolving_chat_context)


# Ask AI as a background job the browser polls. An answer takes 5-30 s (two
# AI calls, plus an engine re-run for a what-if); a proxy or load balancer in
# front of the deployed app can give up on a request that long and return a
# bare 502/503. Start and status each return at once, so nothing times out.
# In memory: one worker, and a lost job just means asking again.
_CHAT_JOBS: dict[str, dict[str, Any]] = {}
_CHAT_JOBS_LOCK = threading.Lock()
_CHAT_JOB_TTL_SECONDS = 15 * 60


def _start_chat_job(identity: str, run) -> dict[str, str]:
    job_id = uuid.uuid4().hex
    now = time.time()
    with _CHAT_JOBS_LOCK:
        for old in [k for k, j in _CHAT_JOBS.items() if now - j["created"] > _CHAT_JOB_TTL_SECONDS]:
            del _CHAT_JOBS[old]
        _CHAT_JOBS[job_id] = {"owner": identity, "created": now, "state": {"status": "running"}}

    def work() -> None:
        try:
            state = {"status": "done", **run()}
        except HTTPException as exc:
            state = {"status": "error", "code": exc.status_code, "detail": exc.detail}
        except Exception as exc:  # surface it to the poller instead of losing it
            state = {"status": "error", "code": 500, "detail": f"Ask AI failed: {exc}"}
        with _CHAT_JOBS_LOCK:
            if job_id in _CHAT_JOBS:
                _CHAT_JOBS[job_id]["state"] = state

    threading.Thread(target=work, daemon=True).start()
    return {"jobId": job_id}


@app.post("/api/ai/chat/lending/start")
def ai_chat_lending_start(
    body: LendingChatBody, identity: str = Depends(_require_identity)
) -> dict[str, str]:
    return _start_chat_job(identity, lambda: ai_chat_lending(body))


@app.post("/api/ai/chat/rental/start")
def ai_chat_rental_start(
    body: RentalChatBody, identity: str = Depends(_require_identity)
) -> dict[str, str]:
    return _start_chat_job(identity, lambda: ai_chat_rental(body))


@app.post("/api/ai/chat/revolving/start")
def ai_chat_revolving_start(
    body: RevolvingChatBody, identity: str = Depends(_require_identity)
) -> dict[str, str]:
    return _start_chat_job(identity, lambda: ai_chat_revolving(body))


@app.get("/api/ai/chat/status/{job_id}")
def ai_chat_status(job_id: str, identity: str = Depends(_require_identity)) -> dict[str, Any]:
    """{"status": "running"}, then the answer ({"status": "done", "answer",
    "whatif"}) or the error ({"status": "error", "code", "detail"})."""
    with _CHAT_JOBS_LOCK:
        job = _CHAT_JOBS.get(job_id)
        if job is None or job["owner"] != identity:
            raise HTTPException(status_code=404, detail="That question has expired - ask again.")
        return dict(job["state"])


def _infer_model_key(mapping: dict[str, Any]) -> str:
    """A saved profile/share doesn't record which model it was mapped for -
    infer it from which template fields the mapping uses (Rental fields are
    a disjoint set from Lending's, see RENTAL_REQUIRED_COLUMNS/REQUIRED_FIELDS)
    rather than adding a new field to s3_store.py's shared schema."""
    if any(f in mapping for f in RENTAL_REQUIRED_COLUMNS):
        return "rental"
    if "principal_outstanding-active" in mapping:  # revolving fields are disjoint too
        return "revolving"
    return "lending"


def _restore_payload(profile: dict[str, Any]) -> dict[str, Any]:
    """Re-ingest a saved/shared upload and build the payload the frontend
    needs to jump straight back into the mapping/analysis screens with the
    same file, mapping, general inputs and chat history."""
    info = profile.get("ingest_info")
    if info and "load_upload_bytes" in profile:
        # Saved with its ingest info: skip the data until something reads it.
        ingest = _register_pending_ingest(info, profile["load_upload_bytes"])
    else:
        # Older saves have no ingest info, so the data is loaded up front.
        upload_bytes = profile.get("upload_bytes")
        if upload_bytes is None:
            try:
                upload_bytes = profile["load_upload_bytes"]()
            except Exception as exc:
                raise HTTPException(
                    status_code=404, detail="This saved run's data could not be found."
                ) from exc
        ingest = _register_ingest(
            upload_bytes, profile.get("upload_name") or "upload", enforce_size_cap=False
        )
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
            "viewState": profile.get("view_state") or {},
            "derivedColumns": profile.get("derived_columns") or [],
        }
    )


class FeedbackBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    model: Literal["Lending", "Rental", "Revolving", "General"]
    severity: Literal["Low", "Medium", "High"]
    deal: str = Field("", max_length=200)


@app.post("/api/feedback")
def submit_feedback(
    body: FeedbackBody, identity: str = Depends(_require_identity)
) -> dict[str, Any]:
    """In-app feedback, appended to the tracker sheet's Feedback tab alongside
    the Google Form's responses. Deployed app only, like the usage log."""
    if not usage_log.feedback_enabled():
        raise HTTPException(
            status_code=503, detail="Feedback is only recorded on the deployed app."
        )
    try:
        usage_log.append_feedback(
            body.message.strip(), body.model, body.severity, identity, body.deal.strip()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Couldn't save your feedback - please try again."
        ) from exc
    return {"ok": True}


class SaveProfileBody(BaseModel):
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
    derivedColumns: list[DerivedColumn] = Field(default_factory=list)
    viewState: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/profiles/save")
def save_profile(
    body: SaveProfileBody, identity: str = Depends(_require_identity)
) -> dict[str, bool]:
    """Save this deal's current upload + mapping/inputs/chat history under
    the caller's identity, for the "Resume my saved session" picker -
    scripts/s3_store.py does the actual persistence (unmodified)."""
    upload_bytes, upload_name = _ingest_raw_bytes(body.ingestId)
    s3_store.save_profile(
        identity,
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
        ingest_info=INGEST_INFO.get(body.ingestId),
        derived_columns=_derived_dicts(body.derivedColumns),
        view_state=body.viewState,
    )
    _PROFILES_CACHE.pop(identity, None)
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
def list_profiles(identity: str = Depends(_require_identity)) -> list[dict[str, Any]]:
    cached = _PROFILES_CACHE.get(identity)
    if cached and time.time() - cached[0] < _PROFILES_CACHE_TTL:
        return cached[1]
    result = s3_store.list_profiles(identity)
    _PROFILES_CACHE[identity] = (time.time(), result)
    return result


@app.get("/api/profiles/{session_id}")
def load_profile(session_id: str, identity: str = Depends(_require_identity)) -> dict[str, Any]:
    profile = s3_store.load_profile(identity, session_id, lazy_data=True)
    if profile is None:
        raise HTTPException(status_code=404, detail="No saved session found under that id.")
    return _restore_payload(profile)


class CreateShareBody(BaseModel):
    ingestId: str
    mapping: dict[str, str] = Field(default_factory=dict)
    generalInputs: dict[str, Any] = Field(default_factory=dict)
    chatHistory: list[ChatMessage] = Field(default_factory=list)
    analysisContext: str = ""
    analysisResult: dict[str, Any] | None = None
    # LTV calculator / UE Model / cohort selection / custom charts / dedupe.
    viewState: dict[str, Any] = Field(default_factory=dict)
    derivedColumns: list[DerivedColumn] = Field(default_factory=list)


@app.post("/api/share")
def create_share(
    body: CreateShareBody, identity: str = Depends(_require_identity)
) -> dict[str, str]:
    """Snapshot this deal under a new random token anyone with the link can
    load (no allow-list - matches s3_store.create_share's documented
    behaviour). Returns the token; the frontend turns it into a link."""
    upload_bytes, upload_name = _ingest_raw_bytes(body.ingestId)
    token = s3_store.create_share(
        identity,
        upload_bytes=upload_bytes,
        upload_name=upload_name,
        mapping=body.mapping,
        currency=None,
        gi_overrides=body.generalInputs,
        chat_history=[m.model_dump() for m in body.chatHistory],
        analysis_context=body.analysisContext,
        analysis_result=body.analysisResult,
        ingest_info=INGEST_INFO.get(body.ingestId),
        view_state=body.viewState,
        derived_columns=_derived_dicts(body.derivedColumns),
    )
    return {"token": token}


@app.get("/api/share/{token}")
def load_share(token: str) -> dict[str, Any]:
    profile = s3_store.load_share(token, lazy_data=True)
    if profile is None:
        raise HTTPException(status_code=404, detail="This share link is invalid or has expired.")
    return _restore_payload(profile)


@app.get("/api/auth/config")
def auth_config() -> dict[str, bool]:
    """Tells the frontend whether real Google sign-in is available, so
    LoginScreen can show the "Sign in with Google" button only when a
    client id/secret are actually configured - otherwise it falls back to
    the self-declared identity box (same fallback logic as
    scripts/login_gates.py's _render_login_gate).

    cognitoEnabled switches LoginScreen to the single Cognito sign-in button;
    googleEnabled then only means "Connect Google Drive" is available."""
    return {
        "cognitoEnabled": cognito.is_configured(),
        "googleEnabled": bool(_get_google_client_id() and _get_google_client_secret()),
    }


@app.get("/api/auth/login", include_in_schema=False)
async def cognito_login(request: Request, next: str = "/") -> Response:
    """Start the Cognito authorization-code flow. `next` (a same-origin path,
    e.g. "/?share=<token>") is where the callback lands, so a share link
    survives the sign-in round trip."""
    if not cognito.is_configured():
        raise HTTPException(status_code=404, detail="Cognito sign-in is not configured.")
    request.session["next"] = cognito.safe_next(next)
    return await cognito.client().authorize_redirect(request, cognito.redirect_uri())


@app.get(cognito.CALLBACK_PATH, include_in_schema=False)
async def cognito_callback(request: Request) -> Response:
    """Exchange the code, verify the ID token (Authlib checks its signature
    against the pool's JWKS plus iss/aud/exp/nonce), then issue the same
    signed session cookie every other route already trusts. Registered here,
    before the SPA catch-all at the end of the file, which would otherwise
    answer this path with index.html."""
    if not cognito.is_configured():
        raise HTTPException(status_code=404, detail="Not found.")
    try:
        token = await cognito.client().authorize_access_token(request)
    except Exception as exc:  # noqa: BLE001 - any failure here means "not signed in"
        # The provider's message stays in the logs, not the response.
        raise HTTPException(status_code=401, detail="Sign-in failed. Please try again.") from exc
    claims = token.get("userinfo") or {}
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(
            status_code=403, detail="Your account has no email address, which this app requires."
        )
    target = cognito.safe_next(request.session.pop("next", "/"))
    response = RedirectResponse(url=target, status_code=303)
    _set_react_session_cookie(response, email, claims.get("name") or email)
    return response


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
        "gdrive": bool(_service_account_info()) or session["sid"] in _GOOGLE_TOKENS,
    }


@app.post("/api/auth/logout")
def auth_logout(response: Response) -> dict[str, Any]:
    """Clear the signed session cookie (mirrors app.py's "Log out" button in
    its non-Cognito branches, which clears its own session cookie the same
    way - see _clear_session_cookie_and_reload). The Google token cached
    server-side under this session's sid is left in place: it's keyed by an
    unguessable sid the browser can no longer present, and dropping it here
    would just force a fresh OAuth consent on the next sign-in for no
    benefit.

    Under Cognito it also returns Cognito's hosted logout URL for the browser
    to follow - clearing only this cookie would leave the Cognito session live,
    so "Sign in" would silently sign the same person straight back in."""
    response.delete_cookie(_REACT_SESSION_COOKIE, path="/")
    if cognito.is_configured():
        return {"ok": True, "logoutUrl": cognito.logout_url()}
    return {"ok": True}


class DummySignInBody(BaseModel):
    email: str


@app.post("/api/auth/dummy")
def dummy_sign_in(body: DummySignInBody, response: Response) -> dict[str, Any]:
    """Set the remember-me cookie for the self-declared "temporary sign-in"
    fallback (shown when Google isn't configured - see LoginScreen.tsx).
    Identity here is unverified by design, so the cookie just remembers
    whatever email the user typed - no worse than the existing client-only
    behaviour, but it survives a refresh.

    Refused outright under Cognito: it mints a session for any email the
    caller types, so leaving it reachable would bypass the login gate."""
    if cognito.is_configured():
        raise HTTPException(status_code=404, detail="Not found.")
    email = (body.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required.")
    _set_react_session_cookie(response, email, email)
    # Same Drive flag as /api/auth/me: the service account gives Drive access
    # without a Google sign-in.
    return {"email": email, "name": email, "gdrive": bool(_service_account_info())}


@app.get("/api/auth/google/login-url")
def google_login_url(request: Request) -> dict[str, str]:
    # Under Cognito, Google is "Connect Google Drive" for an already signed-in
    # user, never a way in - so it needs the session first.
    if cognito.is_configured() and _read_react_session(request) is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    if not (_get_google_client_id() and _get_google_client_secret()):
        raise HTTPException(status_code=503, detail="Google sign-in is not configured.")
    state = secrets.token_urlsafe(16)
    now = time.time()
    pending = _PENDING_GOOGLE_OAUTH_STATES
    with _OAUTH_STATE_LOCK:
        while pending and (
            len(pending) >= _OAUTH_STATE_MAX
            or now - next(iter(pending.values())) > _OAUTH_STATE_TTL_SECONDS
        ):
            pending.pop(next(iter(pending)))
        pending[state] = now
    url = _google_auth_url(
        state, scope=GOOGLE_REACT_APP_SCOPE, redirect_uri=GOOGLE_REACT_APP_REDIRECT_URI
    )
    return {"url": url}


class GoogleCallbackBody(BaseModel):
    code: str
    state: str


@app.post("/api/auth/google/callback")
def google_callback(
    body: GoogleCallbackBody, request: Request, response: Response
) -> dict[str, Any]:
    """Exchange the authorization code Google's redirect handed back to the
    React app's /auth/google/callback route for a real identity - the
    server-side half of the flow, so the client secret never reaches the
    browser. The OAuth token (Drive-scoped, since GOOGLE_REACT_APP_SCOPE
    includes drive.readonly/drive.file) is kept server-side keyed by the
    session id, so the React app's "From Google Drive" import can list and
    download the user's files without the token ever touching the browser.
    Also issues the signed remember-me cookie, so later loads skip the
    login screen.

    Under Cognito this only CONNECTS Drive: the Google account must be the
    signed-in Cognito user's own (same verified email), its token is stored
    under the existing session, and no new identity cookie is issued."""
    session = _read_react_session(request) if cognito.is_configured() else None
    if cognito.is_configured() and session is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    with _OAUTH_STATE_LOCK:
        issued = _PENDING_GOOGLE_OAUTH_STATES.pop(body.state, None)
    if issued is None or time.time() - issued > _OAUTH_STATE_TTL_SECONDS:
        raise HTTPException(
            status_code=400, detail="Invalid or expired sign-in attempt - please try again."
        )
    try:
        token = _exchange_google_code(body.code, redirect_uri=GOOGLE_REACT_APP_REDIRECT_URI)
        userinfo = _fetch_google_userinfo(token["access_token"])
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Google sign-in failed: {exc}") from exc
    email = (userinfo.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=502, detail="Google did not return an email address.")
    if session is not None:
        if email != session["email"]:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Connect the Google account for {session['email']} - "
                    f"{email} is a different account."
                ),
            )
        _store_google_token(session["sid"], token)
        return {"email": session["email"], "name": session["name"], "gdrive": True}
    name = userinfo.get("name") or email
    sid = _set_react_session_cookie(response, email, name)
    _store_google_token(sid, token)
    return {"email": email, "name": name, "gdrive": True}


@app.get("/api/auth/gdrive/files")
def gdrive_files_list(request: Request) -> list[dict[str, Any]]:
    """List the signed-in user's Drive files that can be analysed
    (spreadsheets, CSV, Excel), newest first - backs the React app's
    "From Google Drive" picker (app.py's _list_drive_files)."""
    session = _require_gdrive_session(request)
    return _list_drive_files(_gdrive_access_token(session))


_DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"


def _drive_get(access_token: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = requests.get(
        f"https://www.googleapis.com/drive/v3/{path}",
        params=params,
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


@app.get("/api/auth/gdrive/browse")
def gdrive_browse(request: Request, folder: str = "", q: str = "") -> list[dict[str, Any]]:
    """One level of the Drive folder browser. No folder/q: the top level -
    every Shared Drive visible, plus "Shared with me" (and "My Drive" for a
    per-user OAuth token; the service account's own My Drive is empty).
    folder=<id> (or "shared"): its subfolders + importable files, folders
    first. q: name search across everything visible (files only)."""
    session = _require_gdrive_session(request)
    token = _gdrive_access_token(session)
    if not folder and not q:
        drives = _drive_get(token, "drives", {"pageSize": 100, "fields": "drives(id,name)"})
        top = [] if _service_account_info() else [{"id": "root", "name": "My Drive"}]
        top += [{"id": d["id"], "name": d["name"]} for d in drives.get("drives", [])]
        top.append({"id": "shared", "name": "Shared with me"})
        return [{**t, "mimeType": _DRIVE_FOLDER_MIME, "modifiedTime": ""} for t in top]
    browse_types = _DRIVE_FILE_QUERY.replace(
        "(mimeType=", f"(mimeType='{_DRIVE_FOLDER_MIME}' or mimeType=", 1
    )
    if q:
        name = q.replace("\\", "\\\\").replace("'", "\\'")
        query = f"name contains '{name}' and {_DRIVE_FILE_QUERY}"
    elif folder == "shared":
        query = f"sharedWithMe and {browse_types}"
    else:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", folder):
            raise HTTPException(status_code=422, detail="Invalid folder id.")
        query = f"'{folder}' in parents and {browse_types}"
    params = {
        "q": query,
        "pageSize": 100 if q else 1000,
        "fields": "files(id,name,mimeType,modifiedTime)",
        "orderBy": "folder,modifiedTime desc",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    if folder != "shared":
        params["corpora"] = "allDrives"
    return _drive_get(token, "files", params).get("files", [])


class GDriveImportBody(BaseModel):
    fileId: str = Field(..., description="Drive file id from /api/auth/gdrive/files.")


@app.post("/api/auth/gdrive/import")
def gdrive_import(body: GDriveImportBody, request: Request) -> dict[str, Any]:
    """Download a chosen Drive file (exporting a Google Sheet as a full
    workbook) and register it as a normal ingest, returning the same
    ingest payload as a local upload - app.py's "My Google Drive" source."""
    session = _require_gdrive_session(request)
    token = _gdrive_access_token(session)
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
    source. With a service account configured, links shared with it (or in
    a Shared Drive it belongs to) import too, not just public ones."""
    if _service_account_info():
        token = _service_account_access_token()
        meta = _gdrive_file_meta(token, _extract_google_id(body.url.strip()))
        data, name = _download_drive_file(token, meta)
        return _json_safe(_register_ingest(data, name))
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
        # Resolve before checking: "\" is a separator on Windows, so a plain
        # "/" test let ..%5C escape dist and serve any file (e.g. .env).
        static_path = os.path.realpath(os.path.join(_WEB_DIST_DIR, path))
        if os.path.dirname(static_path) == os.path.realpath(_WEB_DIST_DIR) and os.path.isfile(
            static_path
        ):
            return Response(
                content=open(static_path, "rb").read(),
                media_type="image/svg+xml" if path.endswith(".svg") else "application/octet-stream",
            )
        return Response(content=open(index_path, "rb").read(), media_type="text/html")
