"""S3-backed per-user profile persistence, plus shareable-link snapshots.

Same interface shape as profile_store.py (identity is a plain string, not an
OAuth token - see drive_store.py for the token-carrying variant used by
Google sign-in) because this backs the Cognito auth path (see
_render_cognito_login_gate in login_gates.py): st.user.email is already a
verified identity, there is no per-user cloud-storage token to thread
through the way Google Drive needs one.

Storage: one prefix per user under <S3_PREFIX>users/<safe_id>/sessions/
(one <session_id>.json + <session_id>.bin pair per saved deal, split so an
unchanged loan tape isn't re-uploaded on every chat message - see
save_profile), plus one prefix per share under <S3_PREFIX>shares/<token>/
(keyed by a random unguessable token). Access control is enforced here, in
application code, not by AWS: unlike Drive (where the OAuth token itself
scopes access to the signed-in user's own files), the S3 credentials here
are shared across every user, so a user's data is only private because
nothing lists or reads outside their own users/<safe_id>/ prefix.

Credentials: explicit AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_REGION in
st.secrets or the environment for local dev; when absent (the ECS Express
deployment), boto3 falls back to its default credential chain, which picks
up the task role automatically - no static keys in production.
"""

import hashlib
import json
import os
import re
import secrets as _secrets_mod
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import boto3
import streamlit as st
from snapshot_common import safe_id as _safe_id
from snapshot_common import safe_session_id as _safe_session_id
from snapshot_common import snapshot_fields

DEFAULT_BUCKET = "lendable-production-data"
DEFAULT_PREFIX = "ai-tools/"


def _secret(name: str) -> str | None:
    try:
        return st.secrets[name]
    except Exception:
        return os.environ.get(name)


def _bucket() -> str:
    return _secret("S3_BUCKET_NAME") or DEFAULT_BUCKET


def _prefix() -> str:
    prefix = _secret("S3_PREFIX") or DEFAULT_PREFIX
    return prefix if prefix.endswith("/") else prefix + "/"


@lru_cache(maxsize=1)
def _client():
    # Memoized: a fresh boto3.client() starts with an empty HTTPS connection
    # pool, so every call used to pay a brand-new TCP+TLS handshake - on this
    # network (behind a TLS-inspecting corporate proxy, see ca_bundle.py)
    # that alone measured ~1.3-1.8s, vs ~0.3s once a connection is warm and
    # reused. One client for the process's lifetime lets every S3 call after
    # the first reuse that warm connection. Safe to cache: boto3's credential
    # provider refreshes expiring/temporary credentials (e.g. the ECS task
    # role) internally without needing a new client.
    key_id = _secret("AWS_ACCESS_KEY_ID")
    secret_key = _secret("AWS_SECRET_ACCESS_KEY")
    # Falls back to the bucket's own region rather than leaving this to
    # boto3's region resolution, which needs AWS_REGION/AWS_DEFAULT_REGION
    # to be set in the environment and isn't guaranteed on every ECS task
    # definition.
    kwargs = {"region_name": _secret("AWS_REGION") or "us-west-2"}
    if key_id and secret_key:
        kwargs["aws_access_key_id"] = key_id
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client("s3", **kwargs)


def _user_sessions_prefix(user_id: str) -> str:
    return f"{_prefix()}users/{_safe_id(user_id)}/sessions/"


def _share_prefix(token: str) -> str:
    return f"{_prefix()}shares/{token}/"


def _get_json(key: str) -> dict | None:
    try:
        body = _client().get_object(Bucket=_bucket(), Key=key)["Body"].read()
    except Exception:
        return None
    return json.loads(body)


def _put_json(key: str, payload: dict) -> None:
    _client().put_object(
        Bucket=_bucket(),
        Key=key,
        Body=json.dumps(payload).encode("utf-8"),
        ContentType="application/json",
    )


def _save_snapshot(
    meta_key: str,
    data_key: str,
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
    label: str | None = None,
    analysis_result: dict | None = None,
    ingest_info: dict | None = None,
    view_state: dict | None = None,
    derived_columns: list | None = None,
    extra: dict | None = None,
) -> None:
    upload_hash = hashlib.sha256(upload_bytes).hexdigest()

    # Skip re-uploading the (potentially large) loan tape when its content
    # hasn't changed since the last save - e.g. every AI chat question
    # re-saves the profile but only the chat history changed.
    previous = _get_json(meta_key)
    previous_hash = (previous or {}).get("upload_hash")
    if previous_hash != upload_hash:
        _client().put_object(
            Bucket=_bucket(),
            Key=data_key,
            Body=upload_bytes,
            ContentType="application/octet-stream",
        )

    payload = {
        "schema": 1,
        "label": label or upload_name,
        "upload_hash": upload_hash,
        # The already-computed analysis result (summary/cohorts/checks/etc.,
        # same shape POST /api/analyse/* returns), so resuming a saved run
        # can render it directly instead of re-fetching the raw upload from
        # S3, re-parsing the whole file, and re-running the engine - see
        # web_api/main.py's _restore_payload.
        "analysis_result": analysis_result,
        # The parsed upload's columns/rowCount/preview/sheet, so a lazy load
        # can hand back a usable ingest without downloading the data.
        "ingest_info": ingest_info,
        # The React screen-side inputs (LTV calculator, UE Model, cohort
        # selection, custom charts, dedupe) so a share opens as it was seen.
        "view_state": view_state,
        # AI-derived columns ([{name, formula}]) the mapping may point at.
        "derived_columns": derived_columns,
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
    _put_json(meta_key, payload)


def _load_snapshot(meta_key: str, data_key: str, lazy_data: bool = False) -> dict | None:
    """`lazy_data=True` skips downloading the upload: the state gets a
    `load_upload_bytes()` callable instead of `upload_bytes`, so a caller
    that can render from `analysis_result` pays for the data only if needed."""
    meta = _get_json(meta_key)
    if meta is None:
        return None
    if lazy_data:
        state = dict(meta)
        state["load_upload_bytes"] = lambda: (
            _client().get_object(Bucket=_bucket(), Key=data_key)["Body"].read()
        )
        return state
    try:
        upload_bytes = _client().get_object(Bucket=_bucket(), Key=data_key)["Body"].read()
    except Exception:
        return None
    state = dict(meta)
    state["upload_bytes"] = upload_bytes
    return state


def list_profiles(user_id: str) -> list[dict]:
    """Return this user's saved deals as [{"session_id", "label", "saved_at"}],
    newest first, for the "Resume my saved session" picker.

    Each saved deal's metadata is its own S3 object (one GET per session_id,
    on top of the initial listing) and each S3 round trip from this process
    costs roughly a second - fetching them one at a time made this scale
    linearly with how many deals a user has saved. boto3 clients are
    thread-safe for concurrent reads, so a small thread pool fetches them
    all at once instead - same number of requests, wall-clock time of the
    slowest one rather than the sum."""
    prefix = _user_sessions_prefix(user_id)
    try:
        resp = _client().list_objects_v2(Bucket=_bucket(), Prefix=prefix)
    except Exception:
        return []
    session_ids = [
        key[len(prefix) : -len(".json")]
        for obj in resp.get("Contents", [])
        if (key := obj["Key"]).endswith(".json")
    ]
    if not session_ids:
        return []
    with ThreadPoolExecutor(max_workers=min(len(session_ids), 16)) as pool:
        metas = pool.map(lambda sid: _get_json(f"{prefix}{sid}.json"), session_ids)
    out = [
        {
            "session_id": session_id,
            "label": meta.get("label") or meta.get("upload_name") or session_id,
            "saved_at": meta.get("saved_at", 0),
        }
        for session_id, meta in zip(session_ids, metas)
        if meta is not None
    ]
    out.sort(key=lambda s: s["saved_at"], reverse=True)
    return out


def save_profile(user_id: str, session_id: str, **snapshot_kwargs) -> None:
    """Persist this deal's current upload + settings + chat history under its
    own session_id, replacing any previous save under that same id."""
    prefix = _user_sessions_prefix(user_id)
    safe_session = _safe_session_id(session_id)
    _save_snapshot(f"{prefix}{safe_session}.json", f"{prefix}{safe_session}.bin", **snapshot_kwargs)


def load_profile(user_id: str, session_id: str, lazy_data: bool = False) -> dict | None:
    prefix = _user_sessions_prefix(user_id)
    safe_session = _safe_session_id(session_id)
    return _load_snapshot(
        f"{prefix}{safe_session}.json", f"{prefix}{safe_session}.bin", lazy_data=lazy_data
    )


def create_share(shared_by: str, **snapshot_kwargs) -> str:
    """Save a snapshot of the current analysis under a new random token and
    return that token - the caller turns it into a link (?share=<token>).
    Anyone with the token/link can load this snapshot; there is no
    allow-list and the token never expires."""
    token = _secrets_mod.token_urlsafe(16)
    prefix = _share_prefix(token)
    _save_snapshot(
        f"{prefix}state.json",
        f"{prefix}upload.bin",
        extra={"shared_by": shared_by},
        **snapshot_kwargs,
    )
    return token


def load_share(token: str, lazy_data: bool = False) -> dict | None:
    safe_token = re.sub(r"[^A-Za-z0-9_-]", "", token or "")
    if not safe_token:
        return None
    prefix = _share_prefix(safe_token)
    return _load_snapshot(f"{prefix}state.json", f"{prefix}upload.bin", lazy_data=lazy_data)
