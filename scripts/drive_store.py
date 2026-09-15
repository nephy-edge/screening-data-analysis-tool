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
import hashlib
import json
import time

import requests
from snapshot_common import snapshot_fields

DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
APP_FOLDER_NAME = "SC Analysis Tool"
# Pre-multi-session fixed names (one saved profile per user) - kept as the
# "legacy" pseudo session_id below so a profile saved before this feature
# existed is still listed and resumable.
PROFILE_FILE_NAME = "sc_analysis_saved_session.json"
PROFILE_DATA_FILE_NAME = "sc_analysis_saved_upload.bin"
# Each saved deal after that gets its own pair of files, named from its
# session_id, so a user can save and resume several deals independently.
SESSION_META_PREFIX = "sc_analysis_session_"
SESSION_META_SUFFIX = ".json"
SESSION_DATA_SUFFIX = ".bin"
_TIMEOUT = 30
_UPLOAD_TIMEOUT = 60


def _session_meta_name(session_id: str) -> str:
    return f"{SESSION_META_PREFIX}{session_id}{SESSION_META_SUFFIX}"


def _session_data_name(session_id: str) -> str:
    return f"{SESSION_META_PREFIX}{session_id}{SESSION_DATA_SUFFIX}"


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


def _upload_raw(
    access_token: str,
    file_id: str | None,
    name: str,
    parent_id: str,
    data: bytes,
    mime_type: str,
) -> str:
    if file_id:
        resp = requests.patch(
            f"{DRIVE_UPLOAD_URL}/{file_id}?uploadType=media",
            headers={**_headers(access_token), "Content-Type": mime_type},
            data=data,
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
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode()
        + data
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


def _upload_json(
    access_token: str, file_id: str | None, name: str, parent_id: str, payload: dict
) -> str:
    return _upload_raw(
        access_token, file_id, name, parent_id, json.dumps(payload).encode("utf-8"),
        "application/json",
    )


def _download_bytes(access_token: str, file_id: str) -> bytes:
    resp = requests.get(
        f"{DRIVE_FILES_URL}/{file_id}",
        headers=_headers(access_token),
        params={"alt": "media"},
        timeout=_UPLOAD_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.content


def _download_json(access_token: str, file_id: str) -> dict:
    return json.loads(_download_bytes(access_token, file_id))


def _build_snapshot(
    *,
    upload_bytes: bytes,
    upload_name: str,
    mapping: dict | None,
    currency: str | None,
    gi_overrides: dict | None,
    exclude_rule: dict | None = None,
    active_status_values: list | None = None,
    analysis_context: str = "",
    chat_history: list | None = None,
    extra: dict | None = None,
) -> dict:
    return {
        "schema": 1,
        "upload_b64": base64.b64encode(upload_bytes).decode("ascii"),
        **snapshot_fields(
            upload_name=upload_name,
            mapping=mapping,
            currency=currency,
            gi_overrides=gi_overrides,
            exclude_rule=exclude_rule,
            active_status_values=active_status_values,
            analysis_context=analysis_context,
            chat_history=chat_history,
        ),
        **(extra or {}),
    }


def _build_metadata(
    *,
    upload_hash: str,
    upload_name: str,
    mapping: dict | None,
    currency: str | None,
    gi_overrides: dict | None,
    exclude_rule: dict | None = None,
    active_status_values: list | None = None,
    analysis_context: str = "",
    chat_history: list | None = None,
    label: str | None = None,
) -> dict:
    return {
        "schema": 2,
        "label": label or upload_name,
        "upload_hash": upload_hash,
        **snapshot_fields(
            upload_name=upload_name,
            mapping=mapping,
            currency=currency,
            gi_overrides=gi_overrides,
            exclude_rule=exclude_rule,
            active_status_values=active_status_values,
            analysis_context=analysis_context,
            chat_history=chat_history,
        ),
    }


def _snapshot_to_state(payload: dict) -> dict:
    state = dict(payload)
    state["upload_bytes"] = base64.b64decode(state.pop("upload_b64", "") or "")
    return state


def list_profiles(access_token: str) -> list:
    """List this user's saved deals in their own Drive, as
    [{"session_id", "label", "saved_at"}], newest first, for the "Resume my
    saved session" picker. Includes a "legacy" entry if a profile was saved
    before multi-session support (one fixed-name file per user)."""
    try:
        folder_id = _get_or_create_app_folder(access_token)
        resp = requests.get(
            DRIVE_FILES_URL,
            headers=_headers(access_token),
            params={
                "q": (
                    f"'{folder_id}' in parents and trashed = false "
                    f"and name contains '{SESSION_META_PREFIX}'"
                ),
                "fields": "files(id,name)",
                "spaces": "drive",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        out = []
        for f in resp.json().get("files", []):
            if not f["name"].endswith(SESSION_META_SUFFIX):
                continue
            session_id = f["name"][len(SESSION_META_PREFIX) : -len(SESSION_META_SUFFIX)]
            try:
                meta = _download_json(access_token, f["id"])
            except Exception:
                continue
            out.append({
                "session_id": session_id,
                "label": meta.get("label") or meta.get("upload_name") or session_id,
                "saved_at": meta.get("saved_at", 0),
            })

        legacy_file_id = _find_file(access_token, PROFILE_FILE_NAME, folder_id)
        if legacy_file_id:
            try:
                meta = _download_json(access_token, legacy_file_id)
                out.append({
                    "session_id": "legacy",
                    "label": meta.get("label") or meta.get("upload_name") or "Saved session",
                    "saved_at": meta.get("saved_at", 0),
                })
            except Exception:
                pass

        out.sort(key=lambda s: s["saved_at"], reverse=True)
        return out
    except Exception:
        return []


def save_profile(access_token: str, session_id: str, **snapshot_kwargs) -> None:
    """Replace this deal's saved-session record in the user's own Drive,
    identified by session_id (each deal a user saves gets its own record -
    see list_profiles). "legacy" targets the pre-multi-session fixed-name
    files instead of a session_id-named pair, for a profile saved before
    this feature existed.

    The uploaded loan tape (large, unchanged most of the time) and the
    mapping/settings/chat-history metadata (small, rewritten on almost every
    save - e.g. every AI chat question) are stored as two separate Drive
    files. The loan tape is only re-uploaded when its content hash actually
    changes; otherwise only the small metadata file is rewritten. Without
    this split, every chat message or "Run analysis" click would re-upload
    the entire file (base64-encoded, ~33% larger) even though only the chat
    history changed."""
    folder_id = _get_or_create_app_folder(access_token)
    upload_bytes = snapshot_kwargs.pop("upload_bytes")
    upload_hash = hashlib.sha256(upload_bytes).hexdigest()

    meta_name = PROFILE_FILE_NAME if session_id == "legacy" else _session_meta_name(session_id)
    data_name = (
        PROFILE_DATA_FILE_NAME if session_id == "legacy" else _session_data_name(session_id)
    )

    data_file_id = _find_file(access_token, data_name, folder_id)
    meta_file_id = _find_file(access_token, meta_name, folder_id)
    existing_hash = None
    if meta_file_id:
        try:
            existing_hash = _download_json(access_token, meta_file_id).get("upload_hash")
        except Exception:
            existing_hash = None

    if not data_file_id or existing_hash != upload_hash:
        data_file_id = _upload_raw(
            access_token,
            data_file_id,
            data_name,
            folder_id,
            upload_bytes,
            "application/octet-stream",
        )

    payload = _build_metadata(upload_hash=upload_hash, **snapshot_kwargs)
    _upload_json(access_token, meta_file_id, meta_name, folder_id, payload)


def load_profile(access_token: str, session_id: str) -> dict | None:
    try:
        folder_id = _get_or_create_app_folder(access_token)
        meta_name = (
            PROFILE_FILE_NAME if session_id == "legacy" else _session_meta_name(session_id)
        )
        meta_file_id = _find_file(access_token, meta_name, folder_id)
        if not meta_file_id:
            return None
        meta = _download_json(access_token, meta_file_id)
        if "upload_b64" in meta:
            # Pre-split profile, saved before the loan tape and metadata were
            # separated - bytes are still embedded inline. The next
            # save_profile call migrates it to the split layout.
            return _snapshot_to_state(meta)
        data_name = (
            PROFILE_DATA_FILE_NAME if session_id == "legacy" else _session_data_name(session_id)
        )
        data_file_id = _find_file(access_token, data_name, folder_id)
        if not data_file_id:
            return None
        state = dict(meta)
        state["upload_bytes"] = _download_bytes(access_token, data_file_id)
        return state
    except Exception:
        return None


def create_share(access_token: str, shared_by: str, **snapshot_kwargs) -> str:
    """Save a new, timestamped snapshot file in the sharer's Drive, share it
    read-only with anyone on the sharer's own Google Workspace domain (never
    fully public), and return its Drive file ID to use as the share token.

    Unlike save_profile, a share is a one-time, standalone write (never
    resaved), so it keeps the simpler self-contained bundle (bytes inlined
    as base64) rather than the split layout - there's no repeated-upload
    cost to optimize away here."""
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
