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
(keyed by their self-declared email, for "resume my own session") and one
directory per share under data/shares/ (keyed by a random unguessable
token, for "someone sent me a link"), each holding the relevant uploaded
loan tape plus the mapping/settings/chat-history needed to reproduce that
run. When real SSO lands, only the identity source changes (dummy text box
-> st.user.email); nothing here needs to change.
"""

import json
import re
import secrets
import time
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROFILES_DIR = _DATA_DIR / "profiles"
SHARES_DIR = _DATA_DIR / "shares"


def _safe_id(user_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", (user_id or "").strip().lower())
    return slug or "unknown"


def _gi_overrides_to_json(gi_overrides: dict | None) -> dict:
    if not gi_overrides:
        return {}
    out = dict(gi_overrides)
    extraction_date = out.get("extraction_date")
    if hasattr(extraction_date, "isoformat"):
        out["extraction_date"] = extraction_date.isoformat()
    return out


def _save_snapshot(
    snapshot_dir: Path,
    *,
    upload_bytes: bytes,
    upload_name: str,
    mapping: dict | None,
    currency: str | None,
    gi_overrides: dict | None,
    analysis_context: str = "",
    chat_history: list | None = None,
    extra: dict | None = None,
) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    upload_ext = Path(upload_name or "upload").suffix or ".bin"
    upload_file = f"upload{upload_ext}"
    (snapshot_dir / upload_file).write_bytes(upload_bytes)

    state = {
        "schema": 1,
        "saved_at": time.time(),
        "upload_name": upload_name,
        "upload_file": upload_file,
        "mapping": mapping,
        "currency": currency,
        "gi_overrides": _gi_overrides_to_json(gi_overrides),
        "analysis_context": analysis_context,
        "chat_history": chat_history or [],
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


def has_profile(user_id: str) -> bool:
    return (PROFILES_DIR / _safe_id(user_id) / "state.json").exists()


def save_profile(
    user_id: str,
    *,
    upload_bytes: bytes,
    upload_name: str,
    mapping: dict | None,
    currency: str | None,
    gi_overrides: dict | None,
    analysis_context: str = "",
    chat_history: list | None = None,
) -> None:
    """Persist this user's current upload + settings + chat history, replacing
    any previously saved profile for them (one saved session per user)."""
    _save_snapshot(
        PROFILES_DIR / _safe_id(user_id),
        upload_bytes=upload_bytes,
        upload_name=upload_name,
        mapping=mapping,
        currency=currency,
        gi_overrides=gi_overrides,
        analysis_context=analysis_context,
        chat_history=chat_history,
    )


def load_profile(user_id: str) -> dict | None:
    """Return the saved state dict (plus "upload_bytes") for this user, or
    None if nothing is saved / the saved data can't be read."""
    return _load_snapshot(PROFILES_DIR / _safe_id(user_id))


def create_share(
    shared_by: str,
    *,
    upload_bytes: bytes,
    upload_name: str,
    mapping: dict | None,
    currency: str | None,
    gi_overrides: dict | None,
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
