import pandas as pd

from template_analysis.general_inputs import GeneralInputs


def test_extraction_date_defaults_to_max_disbursement_date():
    df = pd.DataFrame({"Disbursement Date": pd.to_datetime(["2024-01-05", "2024-03-20", "2024-02-01"])})
    gi = GeneralInputs(df)
    assert gi.extraction_date == pd.Timestamp("2024-03-20")


def test_explicit_extraction_date_override_is_used_verbatim():
    df = pd.DataFrame({"Disbursement Date": pd.to_datetime(["2024-01-05", "2024-03-20"])})
    gi = GeneralInputs(df, extraction_date=pd.Timestamp("2025-01-01"))
    assert gi.extraction_date == pd.Timestamp("2025-01-01")


def test_explicit_override_skips_computing_the_default_entirely():
    """Regression: the default was previously computed eagerly as a
    dict.get() default argument, even when extraction_date was explicitly
    overridden - so a messy/mixed-type Disbursement Date column (e.g. text
    dates mixed with real dates) could crash GeneralInputs() even though the
    caller never needed that column touched at all."""
    df = pd.DataFrame({"Disbursement Date": ["not a date", 12345, None]})
    gi = GeneralInputs(df, extraction_date=pd.Timestamp("2025-01-01"))
    assert gi.extraction_date == pd.Timestamp("2025-01-01")


def test_days_after_term_and_min_loans_defaults():
    df = pd.DataFrame({"Disbursement Date": pd.to_datetime(["2024-01-05"])})
    gi = GeneralInputs(df)
    assert gi.days_after_term == 90
    assert gi.min_loans_per_cohort == 10
