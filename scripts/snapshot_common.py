"""Shared snapshot-shaping helpers for drive_store.py, profile_store.py, and
s3_store.py - same saved-session/share payload shape across all three
storage backends.
"""

import re
import time


def safe_id(user_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", (user_id or "").strip().lower())
    return slug or "unknown"


def safe_session_id(session_id: str) -> str:
    # Unlike safe_id (user emails, where case-folding is desirable), a
    # session_id is an opaque, case-sensitive random token - lowercasing it
    # would risk two distinct generated ids colliding on disk/in a key.
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", (session_id or "").strip())
    return slug or "unknown"


def gi_overrides_to_json(gi_overrides: dict | None) -> dict:
    if not gi_overrides:
        return {}
    out = dict(gi_overrides)
    extraction_date = out.get("extraction_date")
    if hasattr(extraction_date, "isoformat"):
        out["extraction_date"] = extraction_date.isoformat()
    return out


def snapshot_fields(
    *,
    upload_name: str,
    mapping: dict | None,
    currency: str | None,
    gi_overrides: dict | None,
    exclude_rule: dict | None = None,
    active_status_values: list | None = None,
    analysis_context: str = "",
    chat_history: list | None = None,
) -> dict:
    """The fields common to every saved-session/share payload shape, minus
    each caller's own schema number, upload identifier, and label."""
    return {
        "saved_at": time.time(),
        "upload_name": upload_name,
        "mapping": mapping,
        "currency": currency,
        "gi_overrides": gi_overrides_to_json(gi_overrides),
        "exclude_rule": exclude_rule,
        "active_status_values": active_status_values,
        "analysis_context": analysis_context,
        "chat_history": chat_history or [],
    }
