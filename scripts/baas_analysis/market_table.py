"""The BaaS upload: one row per market, one column per BaasMarket field.
Turns the mapped table into markets and runs each one; a market that fails
validation is reported with its issues and the others still run."""

import datetime
from dataclasses import asdict, fields

import pandas as pd

from .engine import REQUIRED_FIELDS, BaasMarket, run_market, validate

ALL_FIELDS = [f.name for f in fields(BaasMarket)]
OPTIONAL_FIELDS = [f for f in ALL_FIELDS if f not in REQUIRED_FIELDS]
_TEXT_FIELDS = {"market", "currency"}
_BOOL_FIELDS = {"station_capex_upfront"}
_TRUE = {"1", "1.0", "true", "yes", "y"}


def template_frame(markets: list[BaasMarket]) -> pd.DataFrame:
    """The upload template: every field as a column, one row per market."""
    return pd.DataFrame([asdict(m) for m in markets], columns=ALL_FIELDS)


def _market_from_row(row: dict) -> tuple[BaasMarket | None, list[str]]:
    kwargs, issues = {}, []
    for name in ALL_FIELDS:
        v = row.get(name)
        blank = v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == ""
        if blank:
            if name in REQUIRED_FIELDS:
                issues.append(f"{name} is missing.")
            continue
        if name in _TEXT_FIELDS:
            kwargs[name] = str(v).strip()
        elif name in _BOOL_FIELDS:
            kwargs[name] = str(v).strip().lower() in _TRUE
        else:
            try:
                kwargs[name] = float(str(v).replace(",", "").strip())
            except ValueError:
                issues.append(f"{name} is not a number ({v!r}).")
    if issues:
        return None, issues
    market = BaasMarket(**kwargs)
    return market, validate(market)


def run(df: pd.DataFrame, valuation_date: datetime.date) -> list[dict]:
    """One entry per non-blank row: {market, issues, result} where result is
    run_market()'s output, or None when the row has issues."""
    out = []
    for i, row in enumerate(df.to_dict("records"), start=1):
        if all(v is None or str(v).strip() in ("", "nan") for v in row.values()):
            continue
        market, issues = _market_from_row(row)
        name = str(row.get("market") or f"Row {i}").strip()
        result = run_market(market, valuation_date) if market and not issues else None
        out.append({"market": name, "issues": issues, "result": result})
    if not out:
        raise ValueError("The file has no market rows.")
    return out
