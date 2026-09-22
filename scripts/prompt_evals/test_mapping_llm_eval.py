"""Live eval of the column-mapping prompt against a small golden set.

Opt-in only - skipped unless RUN_LLM_EVALS=1 is set, since it calls the real
DeepSeek model via DeepInfra (costs real API calls and real time, and is
only as deterministic as the model itself). Run it by hand after changing
`mapping_suggestion` in config.toml or FIELD_DESCRIPTIONS in app.py:

    RUN_LLM_EVALS=1 pytest scripts/prompt_evals/test_mapping_llm_eval.py -v

Builds the exact same system prompt app.py's `_suggest_mapping` builds (same
config.toml template, same FIELD_DESCRIPTIONS via _app_source, same request
shape) so a pass here means the real app's prompt is doing the right thing,
not a paraphrase of it.
"""

import json
import os
import sys
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

import config as app_config  # noqa: E402
from rental_and_subscription.rental_analysis.data_input import (  # noqa: E402
    OPTIONAL_COLUMNS,
    REQUIRED_COLUMNS,
)

from _app_source import get_field_descriptions, get_lending_input_columns  # noqa: E402
from mapping_golden_set import LENDING_CASES, RENTAL_CASES  # noqa: E402

RUN_EVAL = os.environ.get("RUN_LLM_EVALS") == "1"
API_KEY = os.environ.get("DEEPINFRA_API_KEY")

pytestmark = pytest.mark.skipif(
    not RUN_EVAL,
    reason="opt-in live LLM eval - set RUN_LLM_EVALS=1 to run (costs real API calls)",
)


def _ask_mapping(
    columns: list[str], input_columns: list[tuple[str, bool]], descriptions: dict
) -> dict:
    if not API_KEY:
        pytest.fail(
            "RUN_LLM_EVALS=1 but no DEEPINFRA_API_KEY found (checked .env and the environment)"
        )

    fields = "\n".join(
        f"- {target} ({'required' if required else 'optional'}): {descriptions.get(target, '')}"
        for target, required in input_columns
    )
    system_prompt = app_config.render_prompt(
        app_config.AI_PROMPTS["mapping_suggestion"],
        columns=", ".join(columns),
        fields=fields,
    ).strip()
    resp = requests.post(
        app_config.AI["chat_url"],
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={
            "model": app_config.AI["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Guess the best mapping for these columns."},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": app_config.AI["mapping_suggestion_max_tokens"],
        },
        timeout=app_config.AI["request_timeout_seconds"],
    )
    resp.raise_for_status()
    return json.loads(resp.json()["choices"][0]["message"]["content"])


def _mismatches(columns, input_columns, expected, descriptions):
    got = _ask_mapping(columns, input_columns, descriptions)
    return [
        (target, expected_col, got.get(target))
        for target, expected_col in expected.items()
        if got.get(target) != expected_col
    ]


def _format_mismatches(mismatches):
    return "\n".join(f"  {t}: expected {e!r}, got {a!r}" for t, e, a in mismatches)


@pytest.mark.parametrize("case", LENDING_CASES, ids=lambda c: c["name"])
def test_lending_mapping_golden_set(case):
    descriptions = get_field_descriptions()
    input_columns = get_lending_input_columns()
    mismatches = _mismatches(case["columns"], input_columns, case["expected"], descriptions)
    assert not mismatches, "Mapping mismatches:\n" + _format_mismatches(mismatches)


@pytest.mark.parametrize("case", RENTAL_CASES, ids=lambda c: c["name"])
def test_rental_mapping_golden_set(case):
    descriptions = get_field_descriptions()
    input_columns = [(c, True) for c in REQUIRED_COLUMNS] + [(c, False) for c in OPTIONAL_COLUMNS]
    mismatches = _mismatches(case["columns"], input_columns, case["expected"], descriptions)
    assert not mismatches, "Mapping mismatches:\n" + _format_mismatches(mismatches)
