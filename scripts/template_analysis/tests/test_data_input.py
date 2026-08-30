import pandas as pd

from template_analysis.data_input import process_data_input

EXTRACTION_DATE = pd.Timestamp("2024-06-01")


def _base_raw(**overrides):
    raw = pd.DataFrame({
        "Loan ID": [1, 2, 3],
        "Disbursement Date": [pd.Timestamp("2024-01-05")] * 3,
        "Expected Completion Date": [pd.NaT, pd.NaT, pd.NaT],
        "Principal Value": [100, 200, 300],
        "Expected Interest": [10, 20, 30],
        "Expected Fee": [1, 2, 3],
        "Total Paid": [111, 222, 333],
    })
    for k, v in overrides.items():
        raw[k] = v
    return raw


def test_term_days_supplied_directly_is_used_as_is():
    """Some deals only ever record a completed/not-completed flag rather than
    a true completion date - Term (days) can't be derived from that, so a
    genuine tenor column supplied directly must be trusted over any date
    subtraction."""
    raw = _base_raw(**{"Term (days)": [30, 45, 60]})
    df = process_data_input(raw, EXTRACTION_DATE, days_after_term=90)
    assert list(df["Term (days)"]) == [30, 45, 60]


def test_term_days_derived_from_dates_when_not_supplied():
    raw = _base_raw(**{
        "Expected Completion Date": [
            pd.Timestamp("2024-02-04"), pd.Timestamp("2024-02-19"), pd.Timestamp("2024-03-05"),
        ],
    })
    df = process_data_input(raw, EXTRACTION_DATE, days_after_term=90)
    assert list(df["Term (days)"]) == [30, 45, 60]


def test_reached_t3_uses_days_after_term_cutoff():
    raw = _base_raw(**{
        "Expected Completion Date": [
            pd.Timestamp("2024-03-01"),  # 92 days before extraction - matured
            pd.Timestamp("2024-03-03"),  # 90 days before extraction - matured (boundary, inclusive)
            pd.Timestamp("2024-03-10"),  # 83 days before extraction - not yet matured
        ],
    })
    df = process_data_input(raw, EXTRACTION_DATE, days_after_term=90)
    assert list(df["Reached T+3?"]) == [True, True, False]


def test_cohort_is_month_truncated_disbursement_date():
    raw = _base_raw(**{"Disbursement Date": [
        pd.Timestamp("2024-01-05"), pd.Timestamp("2024-01-28"), pd.Timestamp("2024-02-01"),
    ]})
    df = process_data_input(raw, EXTRACTION_DATE, days_after_term=90)
    assert list(df["Cohort"]) == [
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01"),
    ]
