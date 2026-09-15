"""Shared snapshot-shaping helpers for drive_store.py and profile_store.py -
same saved-session/share payload shape, one Drive-backed and one local-disk.
"""

import time


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
