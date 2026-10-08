"""First checks on the Rental & Subscription engine (rental_analysis had no
tests). A four-row tape small enough to compute by hand, run through the
same apply_fallbacks -> process_data_input -> build_asset_view path the web
API uses."""

import sys

import pandas as pd
import pytest

sys.path.insert(0, "rental_and_subscription")
from rental_analysis.asset_view import build_asset_view  # noqa: E402
from rental_analysis.data_input import apply_fallbacks, process_data_input  # noqa: E402
from rental_analysis.general_inputs import GeneralInputs  # noqa: E402

T = pd.Timestamp


@pytest.fixture
def processed():
    raw = pd.DataFrame(
        {
            # Asset A: leased, closed 2024-05-01, re-leased 2024-06-01 (redeployed).
            # Asset B: leased, closed 2024-04-01, never re-leased.
            # Last row: no asset id - pandas drops it from every per-asset rollup.
            "asset_id": ["A", "A", "B", None],
            "start_date": [T("2024-01-01"), T("2024-06-01"), T("2024-02-01"), T("2024-03-01")],
            "expected_end_date": [
                T("2025-01-01"),
                T("2025-06-01"),
                T("2025-02-01"),
                T("2025-03-01"),
            ],
            "closed_date": [T("2024-05-01"), pd.NaT, T("2024-04-01"), pd.NaT],
            "status": ["Closed", "Open", "Closed", "Open"],
            "cost_of_asset": [1000.0, 0.0, 500.0, 300.0],
            "total_contract_value": [1200.0, 1200.0, 600.0, 400.0],
            "amount_expected_to_date": [400.0, 300.0, 100.0, 200.0],
            "total_paid": [400.0, 250.0, 100.0, 200.0],
        }
    )
    gi = GeneralInputs(raw, extraction_date=T("2025-01-01")).as_calc_dict()
    df, _ = apply_fallbacks(raw, gi["useful_life_years"], gi["extraction_date"])
    return process_data_input(df, gi), gi


def test_days_to_recovery_uses_the_next_lease_or_extraction(processed):
    df, _ = processed
    # A's first lease: redeployed, so recovery = next start - close = 31 days.
    # B: never redeployed, so recovery runs to the extraction date = 275 days.
    assert df.loc[0, "days_to_recovery"] == 31
    assert df.loc[2, "days_to_recovery"] == 275


def test_asset_view_counts_statuses_per_asset(processed):
    df, gi = processed
    av = build_asset_view(df, gi).set_index("asset_id")
    assert list(av.index) == ["A", "B"]
    assert av["open_count"].tolist() == [1, 0]
    assert av["closed_count"].tolist() == [1, 1]
    assert av["paidoff_count"].tolist() == [0, 0]
