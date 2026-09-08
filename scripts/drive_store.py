"""Google Drive-backed persistence for signed-in-with-Google users.

Mirrors profile_store.py's shape (save/load a profile, create/load a share)
but stores everything in the SIGNED-IN USER'S OWN Google Drive instead of
this server's local disk - so client loan-tape data never accumulates on
the server, and revoking a share is just deleting the file / its sharing
permission from Drive, not something this app has to build.

Requires an OAuth access token carrying the drive.file scope (create/read/
update files this app itself created - never broader Drive access). See the
Google sign-in flow in app.py (_render_google_login_gate).
"""

import base64
import json
import time

import requests

DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
APP_FOLDER_NAME = "SC Analysis Tool"
PROFILE_FILE_NAME = "sc_analysis_saved_session.json"
_TIMEOUT = 30
_UPLOAD_TIMEOUT = 60


def _headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def _find_file(
    access_token: str, name: str, parent_id: str | None, mime_type: str | None = None
) -> str | None:
    q_parts = [f"name = '{name}'", "trashed = false"]
    if parent_id:
        q_parts.append(f"'{parent_id}' in parents")
    if mime_type:
        q_parts.append(f"mimeType = '{mime_type}'")
    resp = requests.get(
        DRIVE_FILES_URL,
        headers=_headers(access_token),
        params={"q": " and ".join(q_parts), "fields": "files(id,name)", "spaces": "drive"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    files = resp.json().get("files", [])
    return files[0]["id"] if files else None


def _get_or_create_app_folder(access_token: str) -> str:
    folder_id = _find_file(
        access_token, APP_FOLDER_NAME, None, mime_type="application/vnd.google-apps.folder"
    )
    if folder_id:
        return folder_id
    resp = requests.post(
        DRIVE_FILES_URL,
        headers={**_headers(access_token), "Content-Type": "application/json"},
        json={"name": APP_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _upload_json(
    access_token: str, file_id: str | None, name: str, parent_id: str, payload: dict
) -> str:
    content = json.dumps(payload).encode("utf-8")
    if file_id:
        resp = requests.patch(
            f"{DRIVE_UPLOAD_URL}/{file_id}?uploadType=media",
            headers=_headers(access_token),
            data=content,
            timeout=_UPLOAD_TIMEOUT,
        )
        resp.raise_for_status()
        return file_id

    metadata = {"name": name, "parents": [parent_id]}
    boundary = "sc_analysis_tool_boundary"
    body = (
        (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\n"
            "Content-Type: application/json\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--".encode()
    )
    resp = requests.post(
        f"{DRIVE_UPLOAD_URL}?uploadType=multipart",
        headers={
            **_headers(access_token),
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        data=body,
        timeout=_UPLOAD_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _download_json(access_token: str, file_id: str) -> dict:
    resp = requests.get(
        f"{DRIVE_FILES_URL}/{file_id}",
        headers=_headers(access_token),
        params={"alt": "media"},
        timeout=_UPLOAD_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _gi_overrides_to_json(gi_overrides: dict | None) -> dict:
    if not gi_overrides:
        return {}
    out = dict(gi_overrides)
    extraction_date = out.get("extraction_date")
    if hasattr(extraction_date, "isoformat"):
        out["extraction_date"] = extraction_date.isoformat()
    return out


def _build_snapshot(
    *,
    upload_bytes: bytes,
    upload_name: str,
    mapping: dict | None,
    currency: str | None,
    gi_overrides: dict | None,
    analysis_context: str = "",
    chat_history: list | None = None,
    extra: dict | None = None,
) -> dict:
    return {
        "schema": 1,
        "saved_at": time.time(),
        "upload_name": upload_name,
        "upload_b64": base64.b64encode(upload_bytes).decode("ascii"),
        "mapping": mapping,
        "currency": currency,
        "gi_overrides": _gi_overrides_to_json(gi_overrides),
        "analysis_context": analysis_context,
        "chat_history": chat_history or [],
        **(extra or {}),
    }


def _snapshot_to_state(payload: dict) -> dict:
    state = dict(payload)
    state["upload_bytes"] = base64.b64decode(state.pop("upload_b64", "") or "")
    return state


def has_profile(access_token: str) -> bool:
    try:
        folder_id = _get_or_create_app_folder(access_token)
        return _find_file(access_token, PROFILE_FILE_NAME, folder_id) is not None
    except Exception:
        return False


def save_profile(access_token: str, **snapshot_kwargs) -> None:
    """Replace this user's saved profile file in their own Drive (create it
    the first time, then update the same file on every later save)."""
    folder_id = _get_or_create_app_folder(access_token)
    file_id = _find_file(access_token, PROFILE_FILE_NAME, folder_id)
    payload = _build_snapshot(**snapshot_kwargs)
    _upload_json(access_token, file_id, PROFILE_FILE_NAME, folder_id, payload)


def load_profile(access_token: str) -> dict | None:
    try:
        folder_id = _get_or_create_app_folder(access_token)
        file_id = _find_file(access_token, PROFILE_FILE_NAME, folder_id)
        if not file_id:
            return None
        return _snapshot_to_state(_download_json(access_token, file_id))
    except Exception:
        return None


def create_share(access_token: str, shared_by: str, **snapshot_kwargs) -> str:
    """Save a new, timestamped snapshot file in the sharer's Drive, share it
    read-only with anyone on the sharer's own Google Workspace domain (never
    fully public), and return its Drive file ID to use as the share token."""
    folder_id = _get_or_create_app_folder(access_token)
    name = f"sc_analysis_share_{int(time.time())}.json"
    payload = _build_snapshot(extra={"shared_by": shared_by}, **snapshot_kwargs)
    file_id = _upload_json(access_token, None, name, folder_id, payload)

    domain = (shared_by.split("@", 1)[-1] or "").strip().lower()
    permission = (
        {"type": "domain", "role": "reader", "domain": domain}
        if domain
        else {"type": "anyone", "role": "reader"}
    )
    resp = requests.post(
        f"{DRIVE_FILES_URL}/{file_id}/permissions",
        headers={**_headers(access_token), "Content-Type": "application/json"},
        json=permission,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return file_id


def load_share(access_token: str, file_id: str) -> dict | None:
    """`file_id` comes from an untrusted URL query param; Drive itself is the
    access-control check here (a 403/404 from a bad or unauthorized id just
    surfaces as None, same as any other read failure)."""
    try:
        return _snapshot_to_state(_download_json(access_token, file_id))
    except Exception:
        return None
