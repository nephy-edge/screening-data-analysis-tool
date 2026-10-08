import datetime
import re

import pandas as pd

SLASHED_DATE_RE = re.compile(r"^\s*(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})\s*$")
# Tight ISO-8601 day form (YYYY-MM-DD, optionally with a time part) - matches
# Python's fast format="ISO8601" reader. Columns that are entirely this shape
# currently fall through detect_date's ISO-*skipping* infer_dayfirst (which
# never votes on 4-digit-year rows) into the 3x format="mixed" fallback, which
# costs minutes on a multi-million-row tape (measured ~340s on a 3.4M-row
# file); the ISO reader does the identical conversion in ~1.5s.
_ISO_DATE_RE = re.compile(r"^\s*\d{4}-\d{1,2}-\d{1,2}")


def _is_iso_dominant(series) -> bool:
    """True when every non-null unique value is ISO-8601-day shaped, so the
    column can be parsed with pandas' fast format="ISO8601" reader exactly.
    Sampled over unique values only (like infer_dayfirst) - a given date
    string is ISO or not regardless of count, and a pure-ISO column is the
    common real-world shape (bank tape exports) that the slow mixed path
    would otherwise hit."""
    for v in series.dropna().astype(str).unique():
        if not _ISO_DATE_RE.match(v):
            return False
    return True


def infer_dayfirst(series) -> bool | None:
    """Decide day-first vs month-first from the column's own unambiguous rows -
    any numeric date where one component must exceed 12 settles its role
    unarguably, regardless of what the rest of the column looks like.

    Whether-more-values-parse can't be trusted for this (see detect_date):
    an ambiguous row like "05/06/2023" parses successfully under BOTH
    interpretations, so it never penalizes the wrong one - only unambiguous
    rows (day or month > 12) can actually discriminate. Returns None when
    the column has no such row to learn from (e.g. every date falls in the
    1st-12th of its month), leaving the caller to fall back to a default.

    Votes by unique string value (weighted by how often it occurs) instead
    of iterating every row - a given date string always resolves to the
    same verdict, so grouping first is exact, not an approximation, while
    turning an O(rows) Python/regex loop into O(unique values). The naive
    per-row loop took ~1.25s per million rows (run several times per date
    column per analysis run, since this app calls detect_date/infer_dayfirst
    more than once - see callers), which dominated "Run analysis" wall time
    on large tapes; a real date column usually has vastly fewer unique
    values than rows (e.g. many loans disbursed on the same day), so this
    is a large win in practice; even a pathological every-value-unique
    column is roughly on par with the original loop, not worse in any way
    that matters."""
    day_votes = month_votes = 0
    match = SLASHED_DATE_RE.match
    for s, count in series.dropna().astype(str).value_counts().items():
        m = match(s)
        if not m:
            continue
        a, b, c = m.groups()
        if len(a) == 4 or len(c) != 4:
            continue  # skip ISO-style Y-M-D, or anything without a clear 4-digit year
        a, b = int(a), int(b)
        if a > 12 >= b:
            day_votes += count
        elif b > 12 >= a:
            month_votes += count
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

    # Fast ISO-8601 path: when every value is YYYY-MM-DD shaped, parse once
    # with format="ISO8601" and skip the pricey mixed-format search below. The
    # slow path would try three format="mixed" passes over the whole column,
    # minutes on a multi-million-row tape; the ISO reader is value-identical
    # (verified on a real 3.4M-row bank tape) in ~1.5s. Never applied when any
    # value is non-ISO, so genuinely mixed/slashed columns keep old behavior.
    if not pd.api.types.is_numeric_dtype(orig) and _is_iso_dominant(orig):
        parsed = pd.to_datetime(orig, errors="coerce", format="ISO8601")
        return parsed if parsed.notna().any() else orig

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
    column, handled separately by mixed_parsed_and_text_warning).

    Parses each distinct value once and maps back: "mixed" is per-element, so
    this is exact, and a date column has far fewer distinct values than rows
    (~78s -> well under 1s per 1M rows)."""
    codes, uniques = pd.factorize(series)
    uniq = pd.Series(uniques)
    try:
        parsed = pd.to_datetime(uniq, errors="coerce", format="mixed", **kwargs)
    except (ValueError, TypeError):
        parsed = pd.to_datetime(uniq, errors="coerce", **kwargs)
    # code -1 (a null) has no label in parsed's RangeIndex, so reindex gives NaT.
    return parsed.reindex(codes).set_axis(series.index).rename(series.name)


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
