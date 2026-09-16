"""Shared TLS trust-store helper for every outbound HTTPS call this app
makes (Google OAuth/Drive, DeepInfra, Redshift Query API, Slack, FX)."""

import os
import tempfile

import certifi
import streamlit as st

_EXTRA_CA_PEM = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "certs", "corporate_root.pem"
)


@st.cache_resource
def _ca_bundle_path() -> str:
    """Certifi's trust store plus this machine's corporate proxy root CA (if
    bundled), so HTTPS calls work on networks with TLS-inspecting proxies
    (e.g. Zscaler, Cisco Umbrella) without an env var set before launch."""
    if not os.path.exists(_EXTRA_CA_PEM):
        return certifi.where()
    with open(certifi.where(), encoding="utf-8") as f:
        bundle = f.read()
    with open(_EXTRA_CA_PEM, encoding="utf-8") as f:
        bundle += "\n" + f.read()
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False, encoding="utf-8")
    tmp.write(bundle)
    tmp.close()
    return tmp.name
