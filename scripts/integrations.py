"""Thin clients for the external services app.py talks to that aren't the
AI model itself: live FX rates, Slack feedback, and the Redshift Query API
data source. Each secret lookup falls back from Streamlit secrets to an
environment variable so the same code works locally (.env) and on Streamlit
Cloud (secrets.toml)."""

import os
from datetime import datetime as _dt

import pandas as pd
import requests
import streamlit as st
from ca_bundle import _ca_bundle_path

import config as app_config

_FX_API_URL = app_config.FX["api_url"]


@st.cache_data(ttl=app_config.FX["cache_ttl_seconds"])
def _fetch_usd_fx_rates() -> dict:
    """Live USD exchange rates from a free, keyless, open API (open.er-api.com).
    Returns {currency_code: units of that currency per 1 USD}; cached for an
    hour so repeated reruns/uploads don't hammer the endpoint."""
    resp = requests.get(
        _FX_API_URL, timeout=app_config.FX["request_timeout_seconds"], verify=_ca_bundle_path()
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("result") != "success":
        raise ValueError(f"FX API returned {data.get('result', 'an error')}")
    return data["rates"]


def _get_slack_webhook_url():
    try:
        return st.secrets["SLACK_WEBHOOK_URL"]
    except Exception:
        return os.environ.get("SLACK_WEBHOOK_URL")


def _send_slack_feedback(message: str, model: str, user: str = "") -> tuple[bool, str]:
    """POST a feedback submission to the configured Slack Incoming Webhook."""
    webhook = _get_slack_webhook_url()
    if not webhook:
        return False, "SLACK_WEBHOOK_URL not configured."
    who = f" — {user.strip()}" if user.strip() else ""
    payload = {
        "text": (
            f"*Feedback* ({model}){who}\n{message.strip()}\n"
            f"_Submitted {_dt.now().strftime('%Y-%m-%d %H:%M')}_"
        )
    }
    try:
        resp = requests.post(
            webhook,
            json=payload,
            timeout=15,
            verify=_ca_bundle_path(),
        )
        resp.raise_for_status()
        return True, ""
    except Exception as e:
        return False, str(e)


def _get_redshift_api_url():
    try:
        return st.secrets["REDSHIFT_API_URL"]
    except Exception:
        return os.environ.get("REDSHIFT_API_URL")


def _get_redshift_api_key():
    try:
        return st.secrets["REDSHIFT_API_KEY"]
    except Exception:
        return os.environ.get("REDSHIFT_API_KEY")


@st.cache_data(ttl=300, show_spinner=False)
def _redshift_list_borrowers(api_url: str, api_key: str) -> list:
    """List available borrower slugs from the Redshift Query API's /borrowers
    endpoint (see Verifications/redshift-api). Cached client-side for 5min on
    top of that API's own 10min server-side cache - this data-source branch
    re-runs on every script rerun while selected, so without caching here
    every keystroke/click would re-hit the API even though the borrower list
    only changes when a new deal onboards."""
    resp = requests.get(
        f"{api_url.rstrip('/')}/borrowers",
        headers={"X-API-Key": api_key},
        timeout=30,
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    return resp.json().get("borrowers", [])


@st.cache_data(ttl=300, show_spinner=False)
def _redshift_fetch_loan_tape(
    api_url: str, api_key: str, borrower: str, limit: int
) -> pd.DataFrame:
    """Fetch one borrower's loan tape from the Redshift Query API's
    /loan-tape endpoint. Cached client-side so re-running the script for an
    unrelated widget change elsewhere doesn't re-run a potentially slow
    warehouse query against the same borrower/limit."""
    resp = requests.get(
        f"{api_url.rstrip('/')}/loan-tape",
        params={"borrower": borrower, "limit": limit},
        headers={"X-API-Key": api_key},
        # A bit above that API's own QUERY_TIMEOUT_SECONDS (120s default) so
        # the server's own timeout error comes back to us, rather than this
        # client cutting the connection first and masking it as a generic
        # read timeout.
        timeout=150,
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    payload = resp.json()
    return pd.DataFrame(payload["rows"], columns=payload["columns"])
