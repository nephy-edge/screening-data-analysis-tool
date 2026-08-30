import datetime

import pandas as pd
import pytest

from template_analysis.date_detection import detect_date, infer_dayfirst, mixed_parsed_and_text_warning

TRUE_DATES = pd.to_datetime(["2023-12-11", "2024-02-14", "2022-08-31", "2023-03-23", "2023-07-11"])


def _as_strings(dates, fmt):
    return pd.Series([fmt(d) for d in dates])


def test_infer_dayfirst_true_from_unambiguous_day_first_data():
    strs = _as_strings(TRUE_DATES, lambda d: f"{d.day}/{d.month}/{d.year}")
    assert infer_dayfirst(strs) is True


def test_infer_dayfirst_false_from_unambiguous_month_first_data():
    strs = _as_strings(TRUE_DATES, lambda d: f"{d.month}/{d.day}/{d.year}")
    assert infer_dayfirst(strs) is False


def test_infer_dayfirst_none_when_every_date_is_ambiguous():
    # Both day and month <=12 for every row - no evidence either way.
    strs = pd.Series(["05/06/2023", "01/02/2024", "11/12/2022"])
    assert infer_dayfirst(strs) is None


@pytest.mark.parametrize("fmt", [
    lambda d: f"{d.day}/{d.month}/{d.year}",
    lambda d: f"{d.month}/{d.day}/{d.year}",
    lambda d: d.strftime("%d/%m/%Y"),
    lambda d: d.strftime("%m/%d/%Y"),
    lambda d: f"{d.day}-{d.month}-{d.year}",
])
def test_detect_date_recovers_ground_truth_regardless_of_source_format(fmt):
    """This is the exact case that broke in production: the prior heuristic
    (try 3 parse strategies, pick whichever parses the most values) could
    pick the wrong interpretation even on a clean, single-format column,
    because an ambiguous row parses "successfully" under either
    interpretation and so never penalizes the wrong one."""
    strs = _as_strings(TRUE_DATES, fmt)
    parsed = detect_date(strs)
    assert list(parsed) == list(TRUE_DATES)


def test_detect_date_passes_through_already_datetime_column_unchanged():
    parsed = detect_date(TRUE_DATES)
    assert list(parsed) == list(TRUE_DATES)


def test_detect_date_numeric_excel_serial():
    # Serial 44927 -> 2023-01-01 (days since 1899-12-30)
    parsed = detect_date(pd.Series([44927]))
    assert parsed.iloc[0] == pd.Timestamp("2023-01-01")


def test_detect_date_numeric_serial_floor_deliberately_includes_small_values():
    """Some deals encode a completed/not-completed flag (0/1) in what's
    nominally a date column, with no real completion date at all - the
    actual Excel formula for those deals does a literal `value == 1` check,
    never date arithmetic. A stricter numeric floor here (excluding small
    values like 0/1 from being treated as date serials) was tried and
    reverted: it made Reached T+3? correctly become False for every loan
    (no completion date at all, matched or not), which broke Loss Rate/95th
    percentile for that deal - whereas the current floor of 1 lets a flag
    value of 1 resolve to an ancient (but non-null) date, which happens to
    satisfy "has matured" exactly when the flag says so. This test pins
    that deliberate choice so it isn't "fixed" again by accident."""
    parsed = detect_date(pd.Series([0, 1, 0, 1]))
    assert list(parsed.notna()) == [False, True, False, True]


def test_mixed_parsed_and_text_warning_fires_on_genuine_mix():
    """Reproduces the exact fingerprint found in a real source file: some
    cells already converted to real dates (by some upstream tool, using an
    unverifiable locale guess), others still raw text in the same column."""
    mixed = pd.Series([
        datetime.datetime(2023, 11, 12), "14/02/2024", "31/08/2022", "23/03/2023",
    ], dtype="object")
    assert mixed_parsed_and_text_warning(mixed, "Disbursement Date") is not None


def test_mixed_parsed_and_text_warning_silent_on_clean_all_text_column():
    clean = pd.Series(["11/12/2023", "14/2/2024", "31/8/2022"])
    assert mixed_parsed_and_text_warning(clean, "Disbursement Date") is None


def test_mixed_parsed_and_text_warning_silent_on_clean_datetime64_column():
    assert mixed_parsed_and_text_warning(TRUE_DATES, "Disbursement Date") is None
