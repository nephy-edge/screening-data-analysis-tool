"""Raw-upload column/value normalization shared by both models: coercing
date columns, cleaning currency/percent-formatted numeric strings, and
matching a file's raw headers onto canonical template field names via the
alias tables in config.toml. Pure pandas transforms - no Streamlit widget or
session-state dependency (the @st.cache_data on _format_normalize is keyed
on its arguments, not on any UI state)."""

import re

import pandas as pd
import streamlit as st
from template_analysis.date_detection import detect_date as _detect_date


def _coerce_dates(raw: pd.DataFrame, date_fields, dayfirst=False) -> pd.DataFrame:
    for col in date_fields:
        if col in raw.columns:
            raw[col] = _detect_date(raw[col])
    return raw


def _norm_key(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _clean_numeric(series) -> pd.Series:
    s = series.astype(str).str.strip()
    s = s.str.replace(r"[\$,£€\s%]", "", regex=True)
    s = s.str.replace(r"\(([^)]*)\)", r"-\1", regex=True)
    s = s.str.replace(",", "", regex=False)
    return s


def _normalize_columns(raw: pd.DataFrame, fields, aliases=()) -> pd.DataFrame:
    alias_map = dict(aliases)
    reverse = {}
    for canon in fields:
        reverse[_norm_key(canon)] = canon
        for alias in alias_map.get(canon, ()):
            reverse[_norm_key(alias)] = canon
    rename = {}
    claimed = set()
    for col in raw.columns:
        k = _norm_key(col)
        if k in reverse:
            canon = reverse[k]
            # Only the first raw column matching a given canonical field claims
            # it - a second alias for the same field (e.g. "Principal" and
            # "Loan Amount" both meaning "Principal Value") is left renamed,
            # otherwise both would collide into one duplicate-labeled column.
            if canon not in raw.columns and canon not in claimed:
                rename[col] = canon
                claimed.add(canon)
    return raw.rename(columns=rename)


@st.cache_data(
    show_spinner=False,
    max_entries=8,
)
def _format_normalize(
    raw: pd.DataFrame, date_fields, numeric_fields, model_fields=(), aliases=()
) -> pd.DataFrame:
    # model_fields is scoped to the active model's own canonical names, and
    # aliases likewise to that model's own alias table (see config.toml's
    # [field_aliases.<model>]), so aliases never leak across models.
    raw = _normalize_columns(
        raw, set(date_fields) | set(numeric_fields) | set(model_fields), aliases
    )
    for col in date_fields:
        if col in raw.columns and raw[col].dtype != "datetime64[ns]":
            raw[col] = _detect_date(raw[col])
    for col in numeric_fields:
        if col in raw.columns:
            cleaned = pd.to_numeric(_clean_numeric(raw[col]), errors="coerce")
            if cleaned.notna().sum() >= raw[col].notna().sum() * 0.5:
                raw[col] = cleaned
    # Value-level normalization for any remaining (unmapped/passthrough) columns:
    # coerce to numeric when most non-null values parse as numbers, so columns
    # like "Total EMI" (with $, commas, trailing %) become proper numerics.
    for col in raw.columns:
        if col in date_fields or col in numeric_fields:
            continue
        if pd.api.types.is_numeric_dtype(raw[col]):
            continue
        non_null = raw[col].notna().sum()
        if non_null == 0:
            continue
        cleaned = pd.to_numeric(_clean_numeric(raw[col]), errors="coerce")
        if cleaned.notna().sum() >= non_null * 0.5:
            raw[col] = cleaned
    return raw
