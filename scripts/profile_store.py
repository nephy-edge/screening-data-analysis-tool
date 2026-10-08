"""Local, file-based per-user profile persistence, plus shareable-link snapshots.

This backs a temporary, self-declared login (a name/email text box - see
_render_login_gate in app.py) used to prototype "save my progress and resume
it later", and a "share this analysis as a link" feature, ahead of real
company SSO (Okta/Cognito) and any real access-control decision about who
should be able to view whose analysis. It is NOT an access control
mechanism: user identity here is whatever the user typed in, not verified by
any identity provider, and a share link grants access to anyone who has the
link/token - there is no allow-list.

Storage is deliberately simple - one directory per user under data/profiles/
(keyed by their self-declared email), holding one subdirectory per saved
deal under sessions/<session_id>/ (a user can save and resume several deals
independently - see list_profiles/save_profile/load_profile), plus one
directory per share under data/shares/ (keyed by a random unguessable token,
for "someone sent me a link"). Each session/share directory holds the
uploaded loan tape plus the mapping/settings/chat-history needed to
reproduce that run. When real SSO lands, only the identity source changes
(dummy text box -> st.user.email); nothing here needs to change.
"""

import hashlib
import json
import re
import secrets
from pathlib import Path

from snapshot_common import safe_id as _safe_id
from snapshot_common import safe_session_id as _safe_session_id
from snapshot_common import snapshot_fields

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROFILES_DIR = _DATA_DIR / "profiles"
SHARES_DIR = _DATA_DIR / "shares"


def _save_snapshot(
    snapshot_dir: Path,
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
    extra: dict | None = None,
) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    upload_ext = Path(upload_name or "upload").suffix or ".bin"
    upload_file = f"upload{upload_ext}"
    upload_hash = hashlib.sha256(upload_bytes).hexdigest()

    # Skip rewriting the (potentially large) loan tape to disk when its
    # content hasn't changed since the last save - e.g. every AI chat
    # question re-saves the profile but only the chat history changed.
    state_path = snapshot_dir / "state.json"
    previous_hash = None
    if state_path.exists():
        try:
            previous_hash = json.loads(state_path.read_text(encoding="utf-8")).get("upload_hash")
        except Exception:
            previous_hash = None
    if previous_hash != upload_hash or not (snapshot_dir / upload_file).exists():
        (snapshot_dir / upload_file).write_bytes(upload_bytes)

    state = {
        "schema": 1,
        "label": label or upload_name,
        "upload_file": upload_file,
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
        **(extra or {}),
    }
    (snapshot_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _load_snapshot(snapshot_dir: Path) -> dict | None:
    state_path = snapshot_dir / "state.json"
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        upload_path = snapshot_dir / state["upload_file"]
        state["upload_bytes"] = upload_path.read_bytes()
    except Exception:
        return None
    return state


def _session_dir(user_id: str, session_id: str) -> Path:
    # "legacy" is the pre-multi-session flat layout (state.json/upload.*
    # directly under the user dir, no sessions/ subdirectory) - kept in place
    # rather than migrated, so a profile saved before this feature existed is
    # still listed and resumable.
    if session_id == "legacy":
        return PROFILES_DIR / _safe_id(user_id)
    return PROFILES_DIR / _safe_id(user_id) / "sessions" / _safe_session_id(session_id)


def list_profiles(user_id: str) -> list[dict]:
    """Return this user's saved deals as [{"session_id", "label", "saved_at"}],
    newest first, for the "Resume my saved session" picker."""
    out = []
    sessions_dir = PROFILES_DIR / _safe_id(user_id) / "sessions"
    if sessions_dir.is_dir():
        for session_dir in sessions_dir.iterdir():
            state_path = session_dir / "state.json"
            if not state_path.exists():
                continue
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append(
                {
                    "session_id": session_dir.name,
                    "label": state.get("label") or state.get("upload_name") or session_dir.name,
                    "saved_at": state.get("saved_at", 0),
                }
            )
    legacy_state_path = PROFILES_DIR / _safe_id(user_id) / "state.json"
    if legacy_state_path.exists():
        try:
            state = json.loads(legacy_state_path.read_text(encoding="utf-8"))
            out.append(
                {
                    "session_id": "legacy",
                    "label": state.get("label") or state.get("upload_name") or "Saved session",
                    "saved_at": state.get("saved_at", 0),
                }
            )
        except Exception:
            pass
    out.sort(key=lambda s: s["saved_at"], reverse=True)
    return out


def save_profile(user_id: str, session_id: str, **snapshot_kwargs) -> None:
    """Persist this deal's current upload + settings + chat history under its
    own session_id, replacing any previous save under that same id - each
    deal a user saves gets its own independently resumable record (see
    list_profiles)."""
    _save_snapshot(_session_dir(user_id, session_id), **snapshot_kwargs)


def load_profile(user_id: str, session_id: str) -> dict | None:
    """Return the saved state dict (plus "upload_bytes") for this user's given
    saved session, or None if nothing is saved under that id / it can't be
    read."""
    return _load_snapshot(_session_dir(user_id, session_id))


def create_share(
    shared_by: str,
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
) -> str:
    """Save a snapshot of the current analysis under a new random token and
    return that token - the caller turns it into a link (?share=<token>).
    Anyone with the token/link can load this snapshot; there is no allow-list
    and the token never expires (a real access-control story is still an
    open product decision, not implemented here)."""
    token = secrets.token_urlsafe(16)
    _save_snapshot(
        SHARES_DIR / token,
        upload_bytes=upload_bytes,
        upload_name=upload_name,
        mapping=mapping,
        currency=currency,
        gi_overrides=gi_overrides,
        exclude_rule=exclude_rule,
        active_status_values=active_status_values,
        analysis_context=analysis_context,
        chat_history=chat_history,
        extra={"shared_by": shared_by},
    )
    return token


def load_share(token: str) -> dict | None:
    """Return the shared snapshot for this token, or None if the token is
    invalid/unknown. `token` is untrusted user input (from a URL query
    param) - _safe_id-style sanitizing keeps it confined to SHARES_DIR."""
    safe_token = re.sub(r"[^A-Za-z0-9_-]", "", token or "")
    if not safe_token:
        return None
    return _load_snapshot(SHARES_DIR / safe_token)
