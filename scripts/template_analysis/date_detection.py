import datetime
import re

import pandas as pd

SLASHED_DATE_RE = re.compile(r"^\s*(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})\s*$")


def infer_dayfirst(series) -> bool | None:
    """Decide day-first vs month-first from the column's own unambiguous rows -
    any numeric date where one component must exceed 12 settles its role
    unarguably, regardless of what the rest of the column looks like.

    Whether-more-values-parse can't be trusted for this (see detect_date):
    an ambiguous row like "05/06/2023" parses successfully under BOTH
    interpretations, so it never penalizes the wrong one - only unambiguous
    rows (day or month > 12) can actually discriminate. Returns None when
    the column has no such row to learn from (e.g. every date falls in the
    1st-12th of its month), leaving the caller to fall back to a default."""
    day_votes = month_votes = 0
    for s in series.dropna().astype(str):
        m = SLASHED_DATE_RE.match(s)
        if not m:
            continue
        a, b, c = m.groups()
        if len(a) == 4 or len(c) != 4:
            continue  # skip ISO-style Y-M-D, or anything without a clear 4-digit year
        a, b = int(a), int(b)
        if a > 12 >= b:
            day_votes += 1
        elif b > 12 >= a:
            month_votes += 1
    if day_votes == 0 and month_votes == 0:
        return None
    return day_votes >= month_votes


def detect_date(series) -> pd.Series:
    orig = series.copy()
    if pd.api.types.is_numeric_dtype(orig):
        # A date column can arrive as a raw Excel serial number (days since
        # 1899-12-30) when the source cell wasn't formatted as a date. Never
        # fall through to the string-parsing candidates below for numeric
        # input: pd.to_datetime treats a bare number as a nanosecond-epoch
        # timestamp and "succeeds" on every value, which would always win
        # the parse-rate comparison even though the result is silently wrong
        # (e.g. serial 44927 -> 1970-01-01, not 2023-01-01). Only trust
        # values in a plausible calendar-date range (~1900-2119).
        serial = pd.to_numeric(orig, errors="coerce")
        plausible = serial.where(serial.between(1, 80000))
        return pd.to_datetime(plausible, unit="D", origin="1899-12-30", errors="coerce")

    if pd.api.types.is_datetime64_any_dtype(orig):
        return orig

    dayfirst = infer_dayfirst(orig)
    if dayfirst is not None:
        parsed = _parse_mixed(orig, dayfirst=dayfirst)
        return parsed if parsed.notna().any() else orig

    # No unambiguous row anywhere to learn the format from - fall back to
    # whichever of the three interpretations parses the most values, same as
    # before. This can still misjudge a genuinely ambiguous column, but only
    # ever applies to columns with no day/month >12 anywhere in them.
    best, best_n = None, -1
    for kwargs in ({}, {"dayfirst": True}, {"yearfirst": True}):
        p = _parse_mixed(orig, **kwargs)
        n = int(p.notna().sum())
        if n > best_n:
            best_n, best = n, p
    return best if best_n > 0 else orig


def _parse_mixed(series, **kwargs) -> pd.Series:
    """pd.to_datetime with a fixed dayfirst/yearfirst hint infers ONE format
    from the column and applies it to every row - when a column genuinely
    mixes formats (e.g. "11/28/2024" alongside "2024-08-11 00:00:00", seen
    on real Rental & Subscription files), every row in the other format
    silently becomes NaT instead of falling back to per-row parsing.
    format="mixed" parses each element independently, so it self-selects the
    single-format fast path may have missed. Falls back to the single-format
    parse if "mixed" itself errors (e.g. an already-mixed datetime/text
    column, handled separately by mixed_parsed_and_text_warning)."""
    try:
        return pd.to_datetime(series, errors="coerce", format="mixed", **kwargs)
    except (ValueError, TypeError):
        return pd.to_datetime(series, errors="coerce", **kwargs)


def mixed_parsed_and_text_warning(series, field_name: str) -> str | None:
    """Flag the fingerprint of a column that's already been silently
    corrupted upstream of this app: some cells arrive as genuine datetime
    values (the source file's date-formatted cells) while others in the
    SAME column are still raw text (cells that were never date-formatted).
    When that happens, any ambiguous text cell (day and month both <=12)
    that a prior tool (e.g. Excel re-saving a mixed-locale export) already
    converted to a concrete date is unrecoverable - the original text is
    gone, so no parsing logic here can tell a correct conversion from a
    silently swapped day/month. Returns a warning message when the mix is
    large enough to matter, else None."""
    if series.dtype != "object":
        return None
    non_null = series.dropna()
    if non_null.empty:
        return None
    # datetime.date covers datetime.datetime and pd.Timestamp too, both subclass it
    is_datetime = non_null.map(lambda v: isinstance(v, datetime.date))
    n_datetime = int(is_datetime.sum())
    n_text = len(non_null) - n_datetime
    if n_datetime == 0 or n_text == 0:
        return None
    return (
        f"**{field_name}** has a mix of {n_datetime} already-parsed date(s) and {n_text} "
        "still-text date(s) in the same column - a sign that some rows were already "
        "converted to dates (by Excel or another tool) before this file was created, "
        "using a locale guess this app has no way to verify. Any ambiguous date among "
        "the already-converted ones (day and month both 12 or under) may have its day "
        "and month silently swapped, and that's unrecoverable at this point - the "
        "original text is gone. Re-export this column as consistent text (not a mix), "
        "ideally as YYYY-MM-DD, to be sure the dates are correct."
    )
