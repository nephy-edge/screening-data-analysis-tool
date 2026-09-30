"""The revolving model's editable settings: config.toml [revolving_defaults],
loaded through the repo-root config.py (which falls back to its own built-in
defaults if the file is missing or invalid). Every tunable in this package
reads from here, so a change to config.toml needs no code edit."""

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:  # the test suite puts only scripts/ on the path
    sys.path.insert(0, _ROOT)

from config import REVOLVING_DEFAULTS as DEFAULTS  # noqa: E402

# Averaging windows: name -> months; 0 means every observable month ("ALL").
WINDOW_LENGTHS: dict[str, int | None] = {
    name: (months or None) for name, months in DEFAULTS["roll_rate_windows"].items()
}


def default_scenarios() -> list[dict]:
    """config.toml's scenarios in the engine's (API-style) key names."""
    return [
        {
            "name": s["name"],
            "rollRateMultiplier": s["roll_rate_multiplier"],
            "lgdMultiplier": s["lgd_multiplier"],
        }
        for s in DEFAULTS["scenarios"]
    ]


def default_covenants() -> list[dict]:
    return [dict(c) for c in DEFAULTS["covenants"]]
