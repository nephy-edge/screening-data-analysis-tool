import pandas as pd

try:
    # config.py lives at the repo root, alongside app.py - reachable when
    # this module is imported as part of the running app (repo root is on
    # sys.path), but not under the test suite (pythonpath = scripts only).
    # Falling back to the same hardcoded defaults keeps this module usable
    # standalone either way.
    import config as _app_config
    _DEFAULTS = _app_config.GENERAL_INPUTS_DEFAULTS
except Exception:
    _DEFAULTS = {"days_after_term": 90, "min_loans_per_cohort": 10}


class GeneralInputs:
    def __init__(self, df: pd.DataFrame, **overrides):
        self.extraction_date = (
            overrides["extraction_date"] if "extraction_date" in overrides
            else df["Disbursement Date"].max()
        )
        self.days_after_term = overrides.get("days_after_term", _DEFAULTS["days_after_term"])
        self.min_loans_per_cohort = overrides.get("min_loans_per_cohort", _DEFAULTS["min_loans_per_cohort"])

    def as_dict(self):
        return {
            "Date of extraction": self.extraction_date,
            "Days after term": self.days_after_term,
            "Minimum loans per cohort": self.min_loans_per_cohort,
        }
