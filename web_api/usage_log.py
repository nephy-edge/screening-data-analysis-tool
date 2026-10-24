"""Append one row per tracked API call to the usage-tracker Google Sheet.

Metadata only (who, what, status, how long) - never tape contents. Writes go
through the existing service account (the sheet must be shared with its
client_email as Editor) on a daemon thread, and every failure is swallowed:
logging must never slow down or break a user's run. Only the deployed app
(Cognito configured) logs, so local/dev runs never reach the tracker.
"""

import re
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


def append_row(row: list, tab: str | None = None, value_input: str = "RAW") -> dict:
    sheet_id, tab = USAGE_LOG["sheet_id"], tab or USAGE_LOG["tab"]
    resp = requests.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/'{tab}'!A1:append",
        params={"valueInputOption": value_input, "insertDataOption": "INSERT_ROWS"},
        headers={"Authorization": f"Bearer {_access_token()}"},
        json={"values": [row]},
        timeout=15,
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    return resp.json()


FEEDBACK_STATUSES = ["Open", "In progress", "Done"]


def feedback_enabled() -> bool:
    return bool(USAGE_LOG["sheet_id"] and _service_account_info() and cognito.is_configured())


def append_feedback(message: str, model: str, severity: str, user: str, deal: str = "") -> None:
    """Add one in-app feedback row to the Feedback tab, in the same column
    order as the Google Form's (Timestamp, message, Model, Severity, Email,
    Status, then Deal in G - the form leaves it blank), and give its Status
    cell the Open/In progress/Done dropdown.
    Raises on failure so the user is told their feedback wasn't saved."""
    sheet_id, tab = USAGE_LOG["sheet_id"], USAGE_LOG["feedback_tab"]
    # The form writes sheet-local time; USER_ENTERED makes this a real date too.
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    result = append_row([stamp, message, model, severity, user, "Open", deal], tab, "USER_ENTERED")
    row = int(re.search(r"!A(\d+)", result["updates"]["updatedRange"]).group(1))
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    headers = {"Authorization": f"Bearer {_access_token()}"}
    meta = requests.get(
        base,
        params={"fields": "sheets.properties"},
        headers=headers,
        timeout=15,
        verify=_ca_bundle_path(),
    )
    meta.raise_for_status()
    gid = next(
        s["properties"]["sheetId"] for s in meta.json()["sheets"] if s["properties"]["title"] == tab
    )
    rule = {
        "condition": {
            "type": "ONE_OF_LIST",
            "values": [{"userEnteredValue": v} for v in FEEDBACK_STATUSES],
        },
        "showCustomUi": True,
        "strict": True,
    }
    status_cell = {
        "sheetId": gid,
        "startRowIndex": row - 1,
        "endRowIndex": row,
        "startColumnIndex": 5,
        "endColumnIndex": 6,
    }
    requests.post(
        f"{base}:batchUpdate",
        headers=headers,
        json={"requests": [{"setDataValidation": {"range": status_cell, "rule": rule}}]},
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
