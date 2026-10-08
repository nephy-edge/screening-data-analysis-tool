"""Fast, offline check: every column-mapping target field (both models) has a
non-empty entry in app.py's FIELD_DESCRIPTIONS.

This is the regression test for the exact bug fixed 2026-09-11: the rental
model's 17 target fields (contract_id, start_date, ...) had zero entries in
FIELD_DESCRIPTIONS, so `_suggest_mapping`'s prompt silently sent the AI an
empty description for every rental field - undercutting the "give the model
field context during mapping" action item from the 2026-09-03 catch-up for
that model only. No network access, no API key - runs in the normal suite.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _app_source import get_field_descriptions, get_lending_input_columns  # noqa: E402

from rental_and_subscription.rental_analysis.data_input import (  # noqa: E402
    OPTIONAL_COLUMNS,
    REQUIRED_COLUMNS,
)


def test_every_lending_field_has_a_mapping_description():
    descriptions = get_field_descriptions()
    missing = [
        target
        for target, _ in get_lending_input_columns()
        if not descriptions.get(target, "").strip()
    ]
    assert not missing, (
        f"FIELD_DESCRIPTIONS in app.py has no description for: {missing}. The AI mapping "
        "prompt sends an empty description for these fields, weakening its ability to map "
        "them correctly."
    )


def test_every_rental_field_has_a_mapping_description():
    descriptions = get_field_descriptions()
    rental_targets = [*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS]
    missing = [target for target in rental_targets if not descriptions.get(target, "").strip()]
    assert not missing, (
        f"FIELD_DESCRIPTIONS in app.py has no description for: {missing}. Same failure mode "
        "as the lending check above, for the Rental & Subscription model."
    )
