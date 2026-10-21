"""Append one row per tracked API call to the usage-tracker Google Sheet.

Metadata only (who, what, status, how long) - never tape contents. Writes go
through the existing service account (the sheet must be shared with its
client_email as Editor) on a daemon thread, and every failure is swallowed:
logging must never slow down or break a user's run. Only the deployed app
(Cognito configured) logs, so local/dev runs never reach the tracker.
"""

import threading
import time
from datetime import UTC, datetime

import cognito
import requests
from ca_bundle import _ca_bundle_path
from google_oauth import _delegated_drive_token, _service_account_info

from config import USAGE_LOG

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
TRACKED_PREFIXES = (
    "/api/ingest",
    "/api/analyse/",
    "/api/export/",
    "/api/ai/",
    "/api/profiles/save",
)

_token: dict | None = None
_lock = threading.Lock()


def _access_token() -> str:
    global _token
    with _lock:
        if not _token or _token["expires_at"] - time.time() < 60:
            _token = _delegated_drive_token(None, scope=SHEETS_SCOPE)
        return _token["access_token"]


def _row(path: str, user: str | None, status: int, seconds: float) -> list:
    parts = path.removeprefix("/api/").split("/")
    action = parts[0]
    model = next((p for p in parts[1:] if p in ("lending", "rental", "revolving")), "")
    return [
        datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        user or "",
        action,
        model,
        path,
        status,
        round(seconds, 2),
    ]


def append_row(row: list) -> None:
    sheet_id, tab = USAGE_LOG["sheet_id"], USAGE_LOG["tab"]
    requests.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{tab}!A1:append",
        params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
        headers={"Authorization": f"Bearer {_access_token()}"},
        json={"values": [row]},
        timeout=15,
        verify=_ca_bundle_path(),
    ).raise_for_status()


def _safe_append(row: list) -> None:
    try:
        append_row(row)
    except Exception:
        pass


def log_call(path: str, method: str, user: str | None, status: int, seconds: float) -> None:
    if (
        method != "POST"
        or not path.startswith(TRACKED_PREFIXES)
        or not USAGE_LOG["sheet_id"]
        or not _service_account_info()
        or not cognito.is_configured()
    ):
        return
    threading.Thread(
        target=_safe_append, args=(_row(path, user, status, seconds),), daemon=True
    ).start()
