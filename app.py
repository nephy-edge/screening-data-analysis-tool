import streamlit as st
import pandas as pd
import altair as alt
import io
import json
import sys
import os
import tempfile
import requests
import certifi
from datetime import datetime as _dt
from openpyxl.chart import LineChart, BarChart, AreaChart, ScatterChart, Reference, Series

DEEPINFRA_MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"
DEEPINFRA_CHAT_URL = "https://api.deepinfra.com/v1/openai/chat/completions"

_EXTRA_CA_PEM = os.path.join(os.path.dirname(__file__), "certs", "corporate_root.pem")


@st.cache_resource
def _ca_bundle_path() -> str:
    """Certifi's trust store plus this machine's corporate proxy root CA (if bundled),
    so HTTPS calls work on networks with TLS-inspecting proxies (e.g. Zscaler, Cisco
    Umbrella) without depending on an environment variable being set before launch."""
    if not os.path.exists(_EXTRA_CA_PEM):
        return certifi.where()
    with open(certifi.where(), "r", encoding="utf-8") as f:
        bundle = f.read()
    with open(_EXTRA_CA_PEM, "r", encoding="utf-8") as f:
        bundle += "\n" + f.read()
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".pem", delete=False, encoding="utf-8"
    )
    tmp.write(bundle)
    tmp.close()
    return tmp.name

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))

from template_analysis.general_inputs import GeneralInputs
from template_analysis.data_questionnaire import QUESTIONS
from template_analysis.data_input import process_data_input
from template_analysis.cohorts import build_cohorts
from template_analysis.cohorts_for_x_or_more_loans import filter_cohorts
from template_analysis.ltv_analysis import LtvAnalysis
from template_analysis.ue_analysis import UeAnalysis
from template_analysis.general_analysis import describe as general_analysis

st.set_page_config(
    page_title="SC Analysis - LTV & UE",
    layout="wide",
    initial_sidebar_state="collapsed",
)

_FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700'
    '&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">'
)

_THEME_CSS = """*,*::before,*::after{box-sizing:border-box;}
::selection{background:#C7E2D9;}

/* ── Base typography & background ── */
html, body, .stApp, section.main{background:#F2F6F4 !important;color:#16312E !important;font-family:"Inter",system-ui,Arial,sans-serif !important;font-size:13.5px !important;line-height:1.6 !important;-webkit-font-smoothing:antialiased !important;}
section.main .block-container{padding-top:2rem !important;padding-bottom:4rem !important;}

/* ── Hide default Streamlit header and footer ── */
[data-testid="stHeader"]{display:none !important;}
footer{visibility:hidden !important;}

/* ── Masthead ── */
.masthead{
    background:#155B54;color:#EDF3F1;
    padding:13px 30px;margin:-2rem -2rem 0 -4rem;
    display:flex;align-items:center;gap:16px;
    border-bottom:3px solid #36C186;
    position:sticky;top:0;z-index:100;
}
.masthead .brand{display:flex;align-items:center;gap:9px;}
.masthead .brand .dot{width:11px;height:11px;border-radius:50%;background:#36C186;box-shadow:0 0 0 3px rgba(54,193,134,.28);}
.masthead .brand .name{font-family:"Inter",sans-serif;font-weight:600;font-size:1.12rem;color:#fff;}
.masthead .divider{width:1px;height:26px;background:#3A6A64;}
.masthead .doctype{font-size:.62rem;text-transform:uppercase;letter-spacing:.16em;color:#A9C2BD;}
.masthead .spacer{flex:1;}
.masthead .mhead-meta{display:flex;align-items:center;gap:12px;}
.masthead .mhead-meta .mdate{font-size:.74rem;color:#C9D9D5;font-weight:500;}

/* ── Cover / document title ── */
.cover{
    padding:30px 30px 4px 30px;margin:0 -2rem 1rem -4rem;
}
.cover h1.cover-title{
    font-family:"Inter",sans-serif !important;font-size:1.75rem !important;font-weight:700 !important;
    color:#1B312E !important;letter-spacing:-0.01em !important;
    background:transparent !important;border:none !important;padding:0 !important;
    margin:0 0 8px 0 !important;line-height:1.15 !important;
}
.cover .cover-sub{font-size:.9rem;color:#525252;max-width:780px;}

/* ── Tabs ── */
div[data-testid="stTabs"] [data-baseweb="tab-list"]{
    gap:0;border-bottom:2px solid #D8DEE5;background:transparent;
}
div[data-testid="stTabs"] [data-baseweb="tab-list"] button{
    font-family:"Inter",sans-serif !important;font-weight:600 !important;font-size:0.82rem !important;
    color:#666666 !important;border-radius:7px 7px 0 0;padding:9px 22px !important;
    background:transparent !important;border:none !important;
}
div[data-testid="stTabs"] [data-baseweb="tab-list"] button[aria-selected="true"]{
    color:#0E4A43 !important;border-bottom:2px solid #155B54 !important;background:transparent !important;
}
div[data-testid="stTabs"] [data-baseweb="tab-list"] button:hover{color:#0E4A43 !important;}

/* ── Headers ── */
h1,h2,h3,h4,h5,h6{font-family:"Inter",sans-serif !important;color:#16312E !important;font-weight:600 !important;}
h1{font-size:1.72rem !important;letter-spacing:-0.005em !important;}
h2{font-size:1.18rem !important;padding:9px 14px !important;background:#EDF3F1 !important;border-left:3px solid #155B54 !important;margin-bottom:14px !important;margin-top:30px !important;scroll-margin-top:130px !important;}
h3{font-size:.92rem !important;font-weight:700 !important;margin:18px 0 7px !important;}

/* ── Cards / Metrics ── */
div[data-testid="stMetric"]{
    background:#ffffff !important;border:1px solid #D8DEE5 !important;border-radius:7px !important;
    padding:18px 22px !important;box-shadow:none !important;
}
div[data-testid="stMetric"] label{
    font-size:.58rem !important;text-transform:uppercase !important;letter-spacing:.11em !important;
    color:#666666 !important;font-family:"Inter",sans-serif !important;font-weight:600 !important;
}
div[data-testid="stMetric"] div[data-testid="stMetricValue"]{
    color:#16312E !important;font-size:1.6rem !important;
    font-family:"Inter",sans-serif !important;font-weight:600 !important;
}

/* ── Buttons ── */
.stButton > button, button[kind="primary"], button[data-baseweb="button"], button[data-testid="stBaseButton-formSubmit"], button[data-testid="baseButton-primary"]{
    background:#155B54 !important;color:#ffffff !important;
    border:none !important;border-radius:7px !important;
    font-family:"Inter",sans-serif !important;font-weight:600 !important;font-size:.82rem !important;
    padding:9px 26px !important;
}
.stButton > button *, button[kind="primary"] *, button[data-baseweb="button"] *, button[data-testid="stBaseButton-formSubmit"] *, button[data-testid="baseButton-primary"] *{
    color:#ffffff !important;
}
.stButton > button:hover, button[kind="primary"]:hover, button[data-baseweb="button"]:hover{
    background:#0E4A43 !important;color:#ffffff !important;
}
.stDownloadButton > button{
    background:#155B54 !important;color:#ffffff !important;
    border:none !important;border-radius:7px !important;
    font-family:"Inter",sans-serif !important;font-weight:600 !important;font-size:.82rem !important;
    padding:9px 26px !important;
}
.stDownloadButton > button *{color:#ffffff !important;}
.stDownloadButton > button:hover{background:#0E4A43 !important;color:#ffffff !important;}

/* ── Inputs ── */
div[data-baseweb="select"] > div, .stSelectbox div[data-baseweb="select"] > div{
    border-color:#D8DEE5 !important;border-radius:7px !important;background:#ffffff !important;
}
div[data-baseweb="select"] > div:focus-within, .stSelectbox div[data-baseweb="select"] > div:focus-within{
    border-color:#155B54 !important;box-shadow:0 0 0 2px rgba(21,91,84,.12) !important;
}
.stTextInput input, input[data-baseweb="input"]{
    border-color:#D8DEE5 !important;border-radius:7px !important;
    background:#ffffff !important;font-family:"Inter",sans-serif !important;
}
.stTextInput input:focus, input[data-baseweb="input"]:focus{
    border-color:#155B54 !important;box-shadow:0 0 0 2px rgba(21,91,84,.12) !important;
}

/* ── Radio ── */
.stRadio > div[role="radiogroup"], div[data-testid="stRadio"] > div:first-child{
    background:#ffffff !important;border:1px solid #D8DEE5 !important;border-radius:7px !important;
    padding:14px 18px !important;
}
.stRadio label, div[data-testid="stRadio"] label{
    font-family:"Inter",sans-serif !important;color:#525252 !important;font-weight:500 !important;
}

/* ── File uploader ── */
.stFileUploader > section, section[data-testid="stFileUploader"]{
    border:1px dashed #D3DDD9 !important;border-radius:7px !important;background:#F2F6F4 !important;
}
.stFileUploader section p, section[data-testid="stFileUploader"] p{color:#666666 !important;}

/* ── DataFrames ── */
.stDataFrame, div[data-testid="stTable"]{
    border:1px solid #D8DEE5 !important;border-radius:7px !important;overflow:hidden !important;
}
.stDataFrame thead th, div[data-testid="stTable"] thead th, .stDataFrame th, div[data-testid="stTable"] th{
    background:#155B54 !important;color:#ffffff !important;
    font-size:.62rem !important;text-transform:uppercase !important;letter-spacing:.06em !important;
    font-weight:600 !important;font-family:"Inter",sans-serif !important;
    padding:9px 10px !important;border-bottom:1px solid #155B54 !important;
}
.stDataFrame tbody td, div[data-testid="stTable"] tbody td, .stDataFrame td, div[data-testid="stTable"] td{
    font-family:"Inter",sans-serif !important;font-size:.8rem !important;
    color:#525252 !important;padding:8px 10px !important;border-bottom:1px solid #D8DEE5 !important;
}
.stDataFrame tbody tr:hover td, div[data-testid="stTable"] tbody tr:hover td{
    background:#F2F6F4 !important;
}
/* Zebra striping per Lendable table style (alt #F3F3F3) */
.stDataFrame tbody tr:nth-child(even) td, div[data-testid="stTable"] tbody tr:nth-child(even) td{
    background:#F3F3F3 !important;
}

/* ── Progress bar ── */
.stProgress > div > div{background:#D8DEE5 !important;border-radius:10px !important;}
.stProgress > div > div > div{background:#155B54 !important;}

/* ── Alerts ── */
div[data-testid="stSuccess"]{
    border-left:3px solid #155B54 !important;background:#E3EFEC !important;
    border-radius:0 7px 7px 0 !important;
}
div[data-testid="stError"]{
    border-left:3px solid #CB4B3A !important;background:#FBE9E4 !important;
    border-radius:0 7px 7px 0 !important;
}
div[data-testid="stWarning"]{
    border-left:3px solid #CB4B3A !important;background:#FBE9E4 !important;
    border-radius:0 7px 7px 0 !important;
}
div[data-testid="stInfo"]{
    border-left:3px solid #155B54 !important;background:#E8EFED !important;
    border-radius:0 7px 7px 0 !important;
}

/* ── Expander ── */
.stExpander > details, details[data-testid="stExpander"]{
    border:1px solid #D8DEE5 !important;border-radius:7px !important;
}
.stExpander > details > summary, details[data-testid="stExpander"] > summary{
    font-family:"Inter",sans-serif !important;color:#525252 !important;font-weight:600 !important;
}

/* ── Checkbox ── */
.stCheckbox label, div[data-testid="stCheckbox"] label{
    font-family:"Inter",sans-serif !important;color:#525252 !important;
}
.stCheckbox label span, div[data-testid="stCheckbox"] label span{font-weight:500 !important;}

/* ── Sidebar ── */
[data-testid="stSidebar"]{background:#16312E !important;}
[data-testid="stSidebar"] *{color:#EDF3F1 !important;}
[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3{color:#ffffff !important;}

/* ── Captions / muted text ── */
.stCaption, .caption, small{color:#666666 !important;font-family:"Inter",sans-serif !important;}
p, div[data-testid="stMarkdownContainer"] p{color:#525252 !important;}

/* ── Force white button text (overrides label markdown color) ── */
.stButton button div[data-testid="stMarkdownContainer"] p,
.stButton button p,
.stButton button span,
.stDownloadButton button div[data-testid="stMarkdownContainer"] p,
.stDownloadButton button p,
.stDownloadButton button span,
[data-testid^="stBaseButton"] div[data-testid="stMarkdownContainer"] p,
[data-testid^="stBaseButton"] p,
[data-testid^="stBaseButton"] span,
[data-testid^="baseButton"] div[data-testid="stMarkdownContainer"] p,
[data-testid^="baseButton"] p,
[data-testid^="baseButton"] span,
button[data-testid^="stBaseButton"],
button[data-testid^="baseButton"]{
    color:#ffffff !important;
}

/* ── Buttons on white/light backgrounds use dark text ── */
button[kind="secondary"],
button[kind="tertiary"],
button[data-testid="stBaseButton-secondary"],
button[data-testid="stBaseButton-tertiary"],
button[data-testid="baseButton-secondary"],
button[data-testid="baseButton-tertiary"],
button[data-testid="stBaseButton-secondaryFormSubmit"],
button[data-testid="stBaseButton-tertiaryFormSubmit"]{
    background:#ffffff !important;color:#16312E !important;
    border:1px solid #D8DEE5 !important;
}
button[kind="secondary"]:hover,
button[kind="tertiary"]:hover,
button[data-testid="stBaseButton-secondary"]:hover,
button[data-testid="stBaseButton-tertiary"]:hover,
button[data-testid="baseButton-secondary"]:hover,
button[data-testid="baseButton-tertiary"]:hover,
button[data-testid="stBaseButton-secondaryFormSubmit"]:hover,
button[data-testid="stBaseButton-tertiaryFormSubmit"]:hover{
    background:#F2F6F4 !important;color:#16312E !important;
}
button[kind="secondary"] div[data-testid="stMarkdownContainer"] p,
button[kind="secondary"] p,
button[kind="secondary"] span,
button[kind="tertiary"] div[data-testid="stMarkdownContainer"] p,
button[kind="tertiary"] p,
button[kind="tertiary"] span,
button[data-testid="stBaseButton-secondary"] div[data-testid="stMarkdownContainer"] p,
button[data-testid="stBaseButton-secondary"] p,
button[data-testid="stBaseButton-secondary"] span,
button[data-testid="stBaseButton-tertiary"] div[data-testid="stMarkdownContainer"] p,
button[data-testid="stBaseButton-tertiary"] p,
button[data-testid="stBaseButton-tertiary"] span,
button[data-testid="baseButton-secondary"] div[data-testid="stMarkdownContainer"] p,
button[data-testid="baseButton-secondary"] p,
button[data-testid="baseButton-secondary"] span,
button[data-testid="baseButton-tertiary"] div[data-testid="stMarkdownContainer"] p,
button[data-testid="baseButton-tertiary"] p,
button[data-testid="baseButton-tertiary"] span,
button[data-testid="stBaseButton-secondaryFormSubmit"] div[data-testid="stMarkdownContainer"] p,
button[data-testid="stBaseButton-secondaryFormSubmit"] p,
button[data-testid="stBaseButton-secondaryFormSubmit"] span,
button[data-testid="stBaseButton-tertiaryFormSubmit"] div[data-testid="stMarkdownContainer"] p,
button[data-testid="stBaseButton-tertiaryFormSubmit"] p,
button[data-testid="stBaseButton-tertiaryFormSubmit"] span{
    color:#16312E !important;
}

/* ── Spacing ── */
div[data-testid="stVerticalBlock"]{gap:0.5rem !important;}

/* ── Scrollbar ── */
::-webkit-scrollbar{width:8px;}
::-webkit-scrollbar-track{background:#F2F6F4;}
::-webkit-scrollbar-thumb{background:#D3DDD9;border-radius:4px;}
::-webkit-scrollbar-thumb:hover{background:#98A8A4;}

/* ── Responsive ── */
@media (max-width:768px){
    .masthead,.cover{margin-left:-1rem;margin-right:-1rem;padding-left:16px;padding-right:16px;}
    .cover{padding-top:20px;}
}"""

st.html(_FONT_LINKS + "<style>" + _THEME_CSS + "</style>")

st.html(
    f"""<div class="masthead">
    <div class="brand">
        <span class="dot"></span>
        <span class="name">Lendable</span>
    </div>
    <span class="divider"></span>
    <span class="doctype">SC Analysis - Lending</span>
    <span class="spacer"></span>
    <div class="mhead-meta">
        <span class="mdate">{_dt.now().strftime("%d %b %Y")}</span>
    </div>
</div>"""
)

st.html(
    """<div class="cover">
    <h1 class="cover-title">Structured Credit Analysis - LTV & Unit Economics</h1>
    <div class="cover-sub">Upload loan-level portfolio data, map your columns to the Data Input template, and the analysis is computed automatically across all sheets.</div>
</div>"""
)

INPUT_COLUMNS = [
    ("Loan ID", True),
    ("Disbursement Date", True),
    ("Expected Completion Date", True),
    ("Principal Value", True),
    ("Expected Interest", True),
    ("Expected Fee", True),
    ("Total Paid", True),
    ("Total Due", False),
]

DATE_FIELDS = {"Disbursement Date", "Expected Completion Date"}
NUMERIC_FIELDS = {"Principal Value", "Expected Interest", "Expected Fee", "Total Paid", "Total Due"}
REQUIRED_FIELDS = {t for t, required in INPUT_COLUMNS if required}

MAPPING_CACHE_PATH = os.path.join(os.path.expanduser("~"), ".sc_analysis_column_mappings.json")


def _mapping_cache_key(columns) -> str:
    return "|".join(sorted(str(c) for c in columns))


def _load_cached_mapping(columns) -> dict | None:
    if not os.path.exists(MAPPING_CACHE_PATH):
        return None
    try:
        with open(MAPPING_CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return cache.get(_mapping_cache_key(columns))


def _save_cached_mapping(columns, mapping: dict) -> None:
    cache = {}
    if os.path.exists(MAPPING_CACHE_PATH):
        try:
            with open(MAPPING_CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            cache = {}
    cache[_mapping_cache_key(columns)] = mapping
    try:
        with open(MAPPING_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except OSError:
        pass


def _validate_mapping(raw: pd.DataFrame, mapping: dict) -> tuple[list[str], list[str]]:
    """Returns (blocking_errors, warnings) for the chosen column mapping."""
    errors, warnings = [], []
    for target, source in mapping.items():
        if not source:
            continue
        series = raw[source]
        non_null = series.notna()
        if non_null.sum() == 0:
            msg = f"**{target}** → column '{source}' is entirely empty."
            (errors if target in REQUIRED_FIELDS else warnings).append(msg)
            continue

        if target in DATE_FIELDS:
            parsed = pd.to_datetime(series, errors="coerce", dayfirst=True)
            fail_rate = 1 - (parsed.notna().sum() / non_null.sum())
            if fail_rate > 0.5:
                msg = (
                    f"**{target}** → column '{source}' doesn't look like dates "
                    f"({fail_rate:.0%} unparseable)."
                )
                (errors if target in REQUIRED_FIELDS else warnings).append(msg)
        elif target in NUMERIC_FIELDS:
            parsed = pd.to_numeric(series, errors="coerce")
            fail_rate = 1 - (parsed.notna().sum() / non_null.sum())
            if fail_rate > 0.5:
                msg = (
                    f"**{target}** → column '{source}' doesn't look numeric "
                    f"({fail_rate:.0%} unparseable)."
                )
                (errors if target in REQUIRED_FIELDS else warnings).append(msg)

    return errors, warnings


def fmt_pct(v):
    return f"{v:.4%}" if pd.notna(v) else "—"


def fmt_num(v):
    return f"{v:,.2f}" if pd.notna(v) else "—"


def _coerce_dates(raw: pd.DataFrame) -> pd.DataFrame:
    for col in ["Disbursement Date", "Expected Completion Date", "Begin Date"]:
        if col in raw.columns:
            raw[col] = pd.to_datetime(raw[col], errors="coerce", dayfirst=True)
    return raw


DERIVED_OPS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "×": lambda a, b: a * b,
    "÷": lambda a, b: a.divide(b).replace([float("inf"), float("-inf")], pd.NA),
}


def _apply_derived_columns(raw: pd.DataFrame, defs: list) -> pd.DataFrame:
    for d in defs:
        col_a = pd.to_numeric(raw[d["col_a"]], errors="coerce")
        col_b = pd.to_numeric(raw[d["col_b"]], errors="coerce")
        raw[d["name"]] = DERIVED_OPS[d["op"]](col_a, col_b)
    return raw


def _get_deepinfra_api_key():
    try:
        return st.secrets["DEEPINFRA_API_KEY"]
    except Exception:
        return os.environ.get("DEEPINFRA_API_KEY")


def _suggest_derived_column(user_request: str, columns: list) -> dict:
    """Ask DeepSeek V4 Flash (via DeepInfra) to turn a plain-English request into a
    two-column formula using only the columns actually present in the uploaded file."""
    api_key = _get_deepinfra_api_key()
    if not api_key:
        raise RuntimeError(
            "No DEEPINFRA_API_KEY found. Add it to Streamlit secrets or the environment."
        )

    ops = list(DERIVED_OPS.keys())
    system_prompt = (
        "You help map a loan-portfolio spreadsheet's raw columns to a derived column. "
        "You can only combine exactly two existing columns with one of these operators: "
        f"{', '.join(ops)} (division). "
        "Pick the two columns and operator that best satisfy the user's request. "
        "If the request truly needs more than two columns or a non-arithmetic transform, "
        "still return your best two-column approximation and say so in the explanation.\n\n"
        f"Available columns (use these exact names): {', '.join(columns)}\n"
        f"Available operators (use exactly one of these characters): {', '.join(ops)}\n\n"
        "Respond with ONLY a JSON object, no markdown fences, matching this shape:\n"
        '{"name": "<short column name>", "col_a": "<one of the available columns>", '
        '"op": "<one of the available operators>", "col_b": "<one of the available columns>", '
        '"explanation": "<one sentence>"}'
    )

    resp = requests.post(
        DEEPINFRA_CHAT_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": DEEPINFRA_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_request},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 512,
        },
        timeout=30,
        verify=_ca_bundle_path(),
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    suggestion = json.loads(content)

    missing = [k for k in ("name", "col_a", "op", "col_b", "explanation") if k not in suggestion]
    if missing:
        raise ValueError(f"Model response missing fields: {', '.join(missing)}")
    if suggestion["col_a"] not in columns or suggestion["col_b"] not in columns:
        raise ValueError("Model suggested a column that isn't in your file.")
    if suggestion["op"] not in DERIVED_OPS:
        raise ValueError(f"Model suggested an unsupported operator: {suggestion['op']}")

    return suggestion


@st.cache_data(show_spinner=False)
def _run_pipeline(raw: pd.DataFrame, extraction_date, days_after_term: int, min_loans_per_cohort: int):
    df = process_data_input(raw, extraction_date, days_after_term)
    cohorts = build_cohorts(df)
    filtered = filter_cohorts(cohorts, min_loans_per_cohort)
    return df, cohorts, filtered


uploaded = st.file_uploader("Choose a CSV or Excel file", type=["csv", "xlsx"])

if uploaded:
    if st.session_state.get("uploaded_file_id") != uploaded.file_id:
        st.session_state["uploaded_file_id"] = uploaded.file_id
        st.session_state["analysis_ran"] = False
        st.session_state.pop("analysis_mapping", None)
        st.session_state["derived_columns"] = []
        for target, _ in INPUT_COLUMNS:
            st.session_state.pop(f"map_{target}", None)

    if uploaded.name.endswith(".csv"):
        raw = pd.read_csv(uploaded)
    else:
        raw = pd.read_excel(uploaded)

    st.success(f"Loaded {len(raw):,} rows — {len(raw.columns)} columns.")
    st.dataframe(raw.head(10), width="stretch", height=300)
    st.caption("Columns in your file: " + ", ".join(map(str, raw.columns)))

    if "derived_columns" not in st.session_state:
        st.session_state["derived_columns"] = []

    with st.expander("Need a column that isn't in your file? Calculate one from existing columns"):
        st.caption(
            "E.g. if your file has separate Principal / Interest / Fee columns but no "
            "Total Due, combine them here — the result becomes selectable in the mapping below."
        )

        st.markdown("**Not sure which columns to combine? Describe it and let AI suggest a formula.**")
        ai1, ai2 = st.columns([4, 1])
        with ai1:
            ai_request = st.text_area(
                "What do you want to calculate?",
                placeholder="e.g. Total amount the borrower owes, combining principal and interest",
                key="dc_ai_request",
                height=70,
            )
        with ai2:
            st.write("")
            ask_ai = st.button("Suggest formula", key="dc_ai_ask")

        if ask_ai:
            if not ai_request.strip():
                st.warning("Describe what you want to calculate first.")
            else:
                try:
                    with st.spinner("Asking DeepSeek..."):
                        st.session_state["dc_ai_suggestion"] = _suggest_derived_column(
                            ai_request, list(raw.columns)
                        )
                except Exception as e:
                    st.session_state["dc_ai_suggestion"] = None
                    st.error(f"Couldn't get a suggestion: {e}")

        suggestion = st.session_state.get("dc_ai_suggestion")
        if suggestion:
            st.info(
                f"**Suggested:** {suggestion['name']} = {suggestion['col_a']} "
                f"{suggestion['op']} {suggestion['col_b']}\n\n{suggestion['explanation']}"
            )
            if st.button("Use this suggestion", key="dc_ai_use"):
                name = suggestion["name"].strip()
                existing_names = {d["name"] for d in st.session_state["derived_columns"]}
                if name in raw.columns or name in existing_names:
                    base, i = name, 2
                    while f"{base} ({i})" in raw.columns or f"{base} ({i})" in existing_names:
                        i += 1
                    name = f"{base} ({i})"
                st.session_state["derived_columns"].append({
                    "name": name,
                    "col_a": suggestion["col_a"],
                    "op": suggestion["op"],
                    "col_b": suggestion["col_b"],
                })
                st.session_state["dc_ai_suggestion"] = None
                st.session_state["dc_ai_request"] = ""
                st.rerun()

        st.markdown("**Or build it manually:**")
        dc1, dc2, dc3, dc4 = st.columns([2, 2, 1, 2])
        with dc1:
            new_name = st.text_input("New column name", key="dc_name")
        with dc2:
            col_a = st.selectbox("Column A", options=list(raw.columns), key="dc_col_a")
        with dc3:
            op = st.selectbox("Operator", options=list(DERIVED_OPS.keys()), key="dc_op")
        with dc4:
            col_b = st.selectbox("Column B", options=list(raw.columns), key="dc_col_b")

        if st.button("Add derived column", key="dc_add"):
            existing_names = {d["name"] for d in st.session_state["derived_columns"]}
            if not new_name.strip():
                st.error("Give the derived column a name.")
            elif new_name in raw.columns or new_name in existing_names:
                st.error(f"'{new_name}' already exists — choose a different name.")
            else:
                st.session_state["derived_columns"].append(
                    {"name": new_name.strip(), "col_a": col_a, "op": op, "col_b": col_b}
                )
                st.rerun()

        if st.session_state["derived_columns"]:
            st.markdown("**Derived columns:**")
            for i, d in enumerate(st.session_state["derived_columns"]):
                rc1, rc2 = st.columns([5, 1])
                rc1.write(f"`{d['name']}` = {d['col_a']} {d['op']} {d['col_b']}")
                if rc2.button("Remove", key=f"dc_remove_{i}"):
                    st.session_state["derived_columns"].pop(i)
                    st.rerun()

    raw = _apply_derived_columns(raw, st.session_state["derived_columns"])
    if st.session_state["derived_columns"]:
        st.dataframe(
            raw[[d["name"] for d in st.session_state["derived_columns"]]].head(10),
            width="stretch", height=150,
        )

    cached_mapping = _load_cached_mapping(raw.columns) or {}
    if cached_mapping:
        st.caption(
            "A saved mapping was found for a file with these same column headers — "
            "pre-filled below. Adjust and run, or change any field as needed."
        )

    with st.form("column_mapping"):
        st.subheader("Map your columns to the Data Input template")
        st.caption(
            "For each template field below, select the matching column in your file. "
            "Required fields must be mapped to run the analysis."
        )
        used = set()
        mapping = {}
        for target, required in INPUT_COLUMNS:
            options = ["(not provided)"] + [
                c for c in raw.columns if c not in used
            ]
            cached_choice = cached_mapping.get(target)
            default_index = options.index(cached_choice) if cached_choice in options else 0
            chosen = st.selectbox(
                f"Map to **{target}** ({'required' if required else 'optional'})",
                options=options,
                index=default_index,
                key=f"map_{target}",
            )
            mapping[target] = None if chosen == "(not provided)" else chosen
            if chosen != "(not provided)":
                used.add(chosen)
        submitted = st.form_submit_button("Run analysis")

    if submitted:
        missing_required = [
            t for t, required in INPUT_COLUMNS
            if required and not mapping[t]
        ]
        if missing_required:
            st.error(
                "Map these required fields before running: "
                + ", ".join(missing_required)
            )
            st.stop()

        mapping_errors, mapping_warnings = _validate_mapping(raw, mapping)
        if mapping_errors:
            st.error(
                "Fix these mappings before running:\n\n"
                + "\n".join(f"- {e}" for e in mapping_errors)
            )
            st.stop()
        st.session_state["analysis_mapping"] = mapping
        st.session_state["analysis_mapping_warnings"] = mapping_warnings
        st.session_state["analysis_ran"] = True
        _save_cached_mapping(raw.columns, mapping)

    if not st.session_state.get("analysis_ran"):
        st.info(
            "Map your file's columns above, then click "
            "'Run analysis' to compute all sheets."
        )
        st.stop()

    mapping = st.session_state["analysis_mapping"]
    rename_map = {src: tgt for tgt, src in mapping.items() if src}
    raw = raw.rename(columns=rename_map)
    _coerce_dates(raw)

    unmapped = [
        t for t, _ in INPUT_COLUMNS
        if t not in raw.columns
    ]
    if unmapped:
        st.warning(
            "Not mapped (optional) — related metrics will be unavailable: "
            + ", ".join(unmapped)
        )

    mapping_warnings = st.session_state.get("analysis_mapping_warnings") or []
    if mapping_warnings:
        st.warning(
            "Mapping quality warnings:\n\n"
            + "\n".join(f"- {w}" for w in mapping_warnings)
        )

    with st.spinner("Running analysis..."):
        gi = GeneralInputs(raw)
        df, cohorts, filtered = _run_pipeline(
            raw, gi.extraction_date, gi.days_after_term, gi.min_loans_per_cohort
        )
        st.caption(f"Mapped & computed columns: {', '.join(df.columns)}")
        if "Total Due" not in df.columns:
            st.warning("No 'Total Due' or payment schedule mapped — loss rate proxy and PvD ratio will be unavailable.")
        ltv = LtvAnalysis(df, filtered)
        ue = UeAnalysis(df)

    tab_names = [
        "General Inputs", "Data Questionnaire", "Data Input",
        "Cohorts", "Cohorts for X or more loans",
        "LTV Analysis", "Unit Economics Analysis", "General Analysis",
        "Custom Visualizations",
    ]
    tabs = st.tabs(tab_names)

    # ── General Inputs ──
    with tabs[0]:
        st.subheader("General Inputs")
        col1, col2, col3 = st.columns(3)
        col1.metric("Date of extraction", str(gi.extraction_date.date()))
        col2.metric("Days after term", gi.days_after_term)
        col3.metric("Minimum loans per cohort", gi.min_loans_per_cohort)

    # ── Data Questionnaire ──
    with tabs[1]:
        st.subheader("Data Questionnaire")
        for i, q in enumerate(QUESTIONS, 1):
            with st.expander(f"Q{i}"):
                st.write(q)

    # ── Data Input ──
    with tabs[2]:
        st.subheader("Data Input")
        st.caption(
            f"{len(raw):,} rows × {len(raw.columns)} columns uploaded. "
            f"Computed columns (Cohort, Term, Reached T+3?) used in downstream sheets."
        )
        st.dataframe(raw, width="stretch", height=400)

    # ── Cohorts ──
    with tabs[3]:
        st.subheader("Cohorts")
        st.caption(f"{len(cohorts)} monthly cohorts")
        st.dataframe(cohorts, width="stretch", height=400)

    # ── Cohorts for X or more loans ──
    with tabs[4]:
        st.subheader(f"Cohorts for X or more loans (≥ {gi.min_loans_per_cohort})")
        st.caption(f"{len(filtered)} cohorts pass the minimum-loan filter")
        st.dataframe(filtered, width="stretch", height=400)

    chart_data = cohorts.dropna(subset=["Loss Rate"]).copy()
    if not chart_data.empty:
        chart_data["Fee %"] = chart_data["Total Fee"] / chart_data["Total Principal"]
        chart_data["Interest %"] = chart_data["Total Interest"] / chart_data["Total Principal"]

    # ── LTV Analysis ──
    with tabs[5]:
        st.subheader("LTV Analysis")
        ltv_data = ltv.as_dict()
        c1, c2, c3 = st.columns(3)
        c1.metric("95th Percentile Losses", fmt_pct(ltv_data["95th Percentile Losses"]))
        c2.metric("Average Total Revenue %", fmt_pct(ltv_data["Average Total Revenue %"]))
        c3.metric("Average Term (days)", fmt_num(ltv_data["Average Term"]))
        if not chart_data.empty:
            st.altair_chart(
                alt.Chart(chart_data).mark_line(point=True).encode(
                    x=alt.X("Cohort:T", title="Cohort"),
                    y=alt.Y("Loss Rate:Q", title="Loss Rate", axis=alt.Axis(format="%")),
                    tooltip=["Cohort:T", alt.Tooltip("Loss Rate:Q", format=".2%")],
                ).properties(title="Loss per Cohort", height=350),
                width="stretch",
            )

    # ── Unit Economics Analysis ──
    with tabs[6]:
        st.subheader("Unit Economics Analysis")
        ue_data = ue.as_dict()
        row1 = st.columns(3)
        row1[0].metric("Average Expected Term (days)", fmt_num(ue_data["Average Expected Term"]))
        row1[1].metric("Average Loss", fmt_pct(ue_data["Average Loss"]))
        row1[2].metric("Loss Rate Proxy (1-PvD)", fmt_pct(ue_data["Loss Rate Proxy (1-PvD)"]))
        row2 = st.columns(3)
        row2[0].metric("Average Principal Amount", fmt_num(ue_data["Average Principal Amount"]))
        row2[1].metric("Average Fee %", fmt_pct(ue_data["Average Fee %"]))
        row2[2].metric("Average Interest %", fmt_pct(ue_data["Average Interest %"]))
        row3 = st.columns(3)
        row3[0].metric("Sense-check Margin", fmt_pct(ue_data["Sense-check Margin"]))
        if not chart_data.empty:
            st.altair_chart(
                alt.Chart(chart_data).mark_line(point=True).encode(
                    x=alt.X("Cohort:T", title="Cohort"),
                    y=alt.Y("Loss Rate:Q", title="Loss Rate", axis=alt.Axis(format="%")),
                    tooltip=["Cohort:T", alt.Tooltip("Loss Rate:Q", format=".2%")],
                ).properties(title="Loss per Cohort", height=300),
                width="stretch",
            )
            st.altair_chart(
                alt.Chart(chart_data).mark_line(point=True).encode(
                    x=alt.X("Cohort:T", title="Cohort"),
                    y=alt.Y("Weighted Avg Term:Q", title="Avg Term (days)"),
                    tooltip=["Cohort:T", alt.Tooltip("Weighted Avg Term:Q", format=".1f")],
                ).properties(title="Average Term per Cohort (days)", height=300),
                width="stretch",
            )
            fee_int = chart_data.melt(
                id_vars=["Cohort"], value_vars=["Fee %", "Interest %"],
                var_name="Metric", value_name="Pct",
            )
            st.altair_chart(
                alt.Chart(fee_int).mark_line(point=True).encode(
                    x=alt.X("Cohort:T", title="Cohort"),
                    y=alt.Y("Pct:Q", title="% of Principal", axis=alt.Axis(format="%")),
                    color="Metric:N",
                    tooltip=["Cohort:T", "Metric:N", alt.Tooltip("Pct:Q", format=".2%")],
                ).properties(title="Average Fee and Interest Percent per Cohort", height=300),
                width="stretch",
            )

    # ── General Analysis ──
    with tabs[7]:
        st.subheader("General Analysis")
        summary = general_analysis(df)
        st.write(f"**Shape:** {summary['shape'][0]:,} rows × {summary['shape'][1]} columns")
        st.write("**Columns:**", ", ".join(summary["columns"]))
        orig = cohorts[["Cohort", "Total Principal"]].dropna()
        if not orig.empty:
            st.altair_chart(
                alt.Chart(orig).mark_bar().encode(
                    x=alt.X("Cohort:T", title="Cohort"),
                    y=alt.Y("Total Principal:Q", title="Total Principal"),
                    tooltip=["Cohort:T", alt.Tooltip("Total Principal:Q", format=",.0f")],
                ).properties(title="Originations per month (LCY)", height=350),
                width="stretch",
            )

    # ── Custom Visualizations ──
    with tabs[8]:
        st.subheader("Custom Visualizations")
        st.caption(
            "Build your own charts from the mapped and computed data. "
            "Add as many chart cards as you like, each with its own data source, chart type, and columns."
        )

        CUSTOM_DATA_SOURCES = {
            "Data Input (loan-level)": df,
            "Cohorts": cohorts,
            "Cohorts for X or more loans": filtered,
        }

        if "custom_chart_cards" not in st.session_state:
            st.session_state["custom_chart_cards"] = [0]
            st.session_state["custom_chart_next_id"] = 1

        def _render_chart_card(card_id):
            k = lambda name: f"cv_{name}_{card_id}"
            cv1, cv2 = st.columns([1, 1])
            with cv1:
                source_name = st.selectbox(
                    "Data source", options=list(CUSTOM_DATA_SOURCES.keys()), key=k("source")
                )
            source_df = CUSTOM_DATA_SOURCES[source_name]

            if source_df is None or source_df.empty:
                st.info("This data source has no rows to plot.")
                return

            all_cols = list(source_df.columns)
            numeric_cols = [c for c in all_cols if pd.api.types.is_numeric_dtype(source_df[c])]
            datetime_cols = [c for c in all_cols if pd.api.types.is_datetime64_any_dtype(source_df[c])]
            categorical_cols = [c for c in all_cols if c not in numeric_cols and c not in datetime_cols]

            with cv2:
                chart_kind = st.selectbox(
                    "Chart type", options=["Line", "Bar", "Scatter", "Area"], key=k("kind"),
                )

            cv3, cv4, cv5, cv6 = st.columns(4)
            with cv3:
                x_col = st.selectbox(
                    "X axis", options=all_cols,
                    index=0 if not datetime_cols else all_cols.index(datetime_cols[0]),
                    key=k("x"),
                )
            with cv4:
                y_options = numeric_cols if numeric_cols else all_cols
                y_col = st.selectbox("Y axis", options=y_options, key=k("y"))
            with cv5:
                color_col = st.selectbox(
                    "Group / color by (optional)",
                    options=["(none)"] + [c for c in categorical_cols if c != x_col],
                    key=k("color"),
                )
            with cv6:
                agg_func = st.selectbox(
                    "Aggregate Y by X (optional)",
                    options=["(none — raw rows)", "Sum", "Mean", "Count", "Median", "Min", "Max"],
                    key=k("agg"),
                )

            base_cols = [c for c in {x_col, y_col, color_col} if c in source_df.columns]
            plot_df = source_df[base_cols].dropna(subset=[x_col, y_col])

            if agg_func != "(none — raw rows)" and not plot_df.empty:
                group_cols = [x_col] + ([color_col] if color_col != "(none)" else [])
                agg_name_map = {
                    "Sum": "sum", "Mean": "mean", "Count": "count",
                    "Median": "median", "Min": "min", "Max": "max",
                }
                if agg_func == "Count":
                    plot_df = (
                        plot_df.groupby(group_cols, dropna=False)[y_col]
                        .count().reset_index()
                    )
                else:
                    plot_df = (
                        plot_df.groupby(group_cols, dropna=False)[y_col]
                        .agg(agg_name_map[agg_func]).reset_index()
                    )

            if plot_df.empty:
                st.info("No rows with data for the selected columns.")
                return

            x_type = "T" if x_col in datetime_cols else ("O" if x_col in categorical_cols else "Q")
            mark_map = {
                "Line": alt.Chart(plot_df).mark_line(point=True),
                "Bar": alt.Chart(plot_df).mark_bar(),
                "Scatter": alt.Chart(plot_df).mark_circle(size=60),
                "Area": alt.Chart(plot_df).mark_area(opacity=0.6),
            }
            base = mark_map[chart_kind]
            y_title = f"{agg_func} of {y_col}" if agg_func != "(none — raw rows)" else y_col
            title_prefix = y_title
            chart_title = f"{title_prefix} by {x_col}" + (f" ({color_col})" if color_col != "(none)" else "")

            encode_kwargs = dict(
                x=alt.X(f"{x_col}:{x_type}", title=x_col),
                y=alt.Y(f"{y_col}:Q", title=y_title),
                tooltip=[x_col, y_col] + ([color_col] if color_col != "(none)" else []),
            )
            if color_col != "(none)":
                encode_kwargs["color"] = alt.Color(f"{color_col}:N", title=color_col)

            custom_chart = base.encode(**encode_kwargs).properties(title=chart_title, height=400)
            st.altair_chart(custom_chart, width="stretch")

            bc1, bc2 = st.columns([1, 1])
            with bc1:
                if st.button("Add this chart to the Excel export", key=k("add_export")):
                    export_charts = st.session_state.setdefault("export_charts", [])
                    export_charts.append({
                        "title": chart_title,
                        "kind": chart_kind,
                        "x_col": x_col,
                        "y_col": y_col,
                        "color_col": None if color_col == "(none)" else color_col,
                        "y_title": y_title,
                        "data": plot_df[[c for c in {x_col, y_col, color_col} if c in plot_df.columns]].copy(),
                    })
                    st.success(f"Added '{chart_title}' to the Excel export queue.")
            with bc2:
                if len(st.session_state["custom_chart_cards"]) > 1:
                    if st.button("Remove this chart card", key=k("remove_card")):
                        st.session_state["custom_chart_cards"].remove(card_id)
                        st.rerun()

        for i, card_id in enumerate(st.session_state["custom_chart_cards"]):
            st.markdown(f"#### Chart {i + 1}")
            _render_chart_card(card_id)
            st.markdown("---")

        if st.button("Add another chart", key="cv_add_card"):
            st.session_state["custom_chart_cards"].append(st.session_state["custom_chart_next_id"])
            st.session_state["custom_chart_next_id"] += 1
            st.rerun()

        export_charts = st.session_state.get("export_charts", [])
        if export_charts:
            st.markdown("**Charts queued for the Excel export:**")
            for i, ec in enumerate(export_charts):
                qc1, qc2 = st.columns([5, 1])
                qc1.write(f"{i + 1}. {ec['title']} ({ec['kind']})")
                if qc2.button("Remove", key=f"cv_remove_{i}"):
                    export_charts.pop(i)
                    st.rerun()
            if st.button("Clear all queued charts", key="cv_clear_export"):
                st.session_state["export_charts"] = []
                st.rerun()

    st.markdown("---")

    chart_source = cohorts.dropna(subset=["Loss Rate"]).copy()
    if not chart_source.empty:
        chart_source["Fee %"] = chart_source["Total Fee"] / chart_source["Total Principal"]
        chart_source["Interest %"] = chart_source["Total Interest"] / chart_source["Total Principal"]
    orig_source = cohorts[["Cohort", "Total Principal"]].dropna()

    raw_cols = ["Loan ID", "Disbursement Date", "Expected Completion Date",
                "Principal Value", "Expected Interest", "Expected Fee",
                "Total Due", "Total Paid"]

    data_frame = pd.DataFrame({col: df.get(col, pd.Series([None] * len(df))) for col in raw_cols})
    num_data = len(df)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        gs = writer.book.create_sheet("General Inputs", 0)
        for r, label, val, note in [
            (2, "Inputs", None, None),
            (3, "Date of extraction", '=MAX(\'Data Input\'!B:B)', "Defaults to most recent disbursement date"),
            (4, "Days after term", gi.days_after_term, "Ignore any loans for loss rates that are less than this number of days after term"),
            (5, "Minimum loans per cohort", gi.min_loans_per_cohort, "Ignore any cohorts for loss rates that are less than this number of loans"),
        ]:
            if label:
                gs.cell(row=r, column=2, value=label)
            if val is not None:
                gs.cell(row=r, column=3, value=val)
            if note:
                gs.cell(row=r, column=4, value=note)
        for r, note in [(7, "Notes"), (8, "All amounts in local currency"),
                        (9, "Please include loan data starting from first disbursement, ie All historical loans"),
                        (10, "Please read the comments explaining the fields requested in the Data Input tab")]:
            gs.cell(row=r, column=2, value=note)

        q_df = pd.DataFrame(
            [(i, q) for i, q in enumerate(QUESTIONS, 1)],
            columns=["#", "Question"],
        )
        q_df.to_excel(writer, sheet_name="Data Questionnaire", index=False)

        data_frame.to_excel(writer, sheet_name="Data Input", index=False, header=True)
        dws = writer.sheets["Data Input"]
        dws.cell(row=1, column=9, value="Reached T+3?")
        dws.cell(row=1, column=10, value="Cohort")
        dws.cell(row=1, column=11, value="Term (days)")
        for r in range(2, max(num_data, 1) + 2):
            dws.cell(row=r, column=9).value = \
                f'=IF(ISBLANK(C{r}),"",(C{r}+\'General Inputs\'!$C$4)<=\'General Inputs\'!$C$3)'
            dws.cell(row=r, column=10).value = \
                f'=IF(ISBLANK(B{r}),"",DATE(YEAR(B{r}),MONTH(B{r}),1))'
            dws.cell(row=r, column=11).value = \
                f'=IF(OR(ISBLANK(B{r}),ISBLANK(C{r})),"",C{r}-B{r})'

        ltv_df = pd.DataFrame([
            ("95th Percentile Losses", ltv_data["95th Percentile Losses"]),
            ("Average Total Revenue %", ltv_data["Average Total Revenue %"]),
            ("Average Term", ltv_data["Average Term"]),
        ], columns=["Metric", "Value"])
        ltv_df.to_excel(writer, sheet_name="LTV Analysis", index=False)
        ltv_ws = writer.sheets["LTV Analysis"]
        if not chart_source.empty:
            chart_source[["Cohort", "Loss Rate"]].to_excel(
                writer, sheet_name="LTV Analysis", startrow=6, index=False
            )
            c = LineChart()
            c.title = "Loss per Cohort"
            c.y_axis.numFmt = '0.00%'
            c.height = 14
            c.width = 24
            data = Reference(ltv_ws, min_col=1, min_row=7, max_col=2,
                             max_row=7 + len(chart_source))
            cats = Reference(ltv_ws, min_col=1, min_row=8, max_row=7 + len(chart_source))
            c.add_data(data, titles_from_data=True)
            c.set_categories(cats)
            ltv_ws.add_chart(c, "B8")

        ue_df = pd.DataFrame([
            ("Average Expected Term", ue_data["Average Expected Term"]),
            ("Average Loss", ue_data["Average Loss"]),
            ("Loss Rate Proxy (1-PvD)", ue_data["Loss Rate Proxy (1-PvD)"]),
            ("Average Principal Amount", ue_data["Average Principal Amount"]),
            ("Average Fee %", ue_data["Average Fee %"]),
            ("Average Interest %", ue_data["Average Interest %"]),
            ("Sense-check Margin", ue_data["Sense-check Margin"]),
        ], columns=["Metric", "Value"])
        ue_df.to_excel(writer, sheet_name="Unit Economics Analysis", index=False)
        ue_ws = writer.sheets["Unit Economics Analysis"]
        if not chart_source.empty:
            chart_source[["Cohort", "Loss Rate"]].to_excel(
                writer, sheet_name="Unit Economics Analysis", startrow=10, index=False
            )
            chart_source[["Cohort", "Weighted Avg Term"]].to_excel(
                writer, sheet_name="Unit Economics Analysis", startrow=10,
                startcol=4, index=False
            )
            chart_source[["Cohort", "Fee %", "Interest %"]].to_excel(
                writer, sheet_name="Unit Economics Analysis", startrow=10,
                startcol=8, index=False
            )
            sr = 11
            lr = sr + len(chart_source) - 1
            for title, col, anchor in [
                ("Loss per Cohort", (1, 2), "B29"),
                ("Average Term per Cohort (days)", (5, 6), "B48"),
                ("Average Fee and Interest Percent per Cohort", (9, 11), "B67"),
            ]:
                c = LineChart()
                c.title = title
                c.height = 14
                c.width = 24
                if col[1] - col[0] == 1:
                    c.y_axis.numFmt = '0.00%' if "Loss" in title else '0'
                data = Reference(ue_ws, min_col=col[0], min_row=sr - 1, max_col=col[1],
                                 max_row=lr)
                cats = Reference(ue_ws, min_col=col[0], min_row=sr, max_row=lr)
                c.add_data(data, titles_from_data=True)
                c.set_categories(cats)
                ue_ws.add_chart(c, anchor)

        ga_summary = general_analysis(df)
        ga_df = pd.DataFrame([
            ("Shape", f"{ga_summary['shape'][0]:,} rows × {ga_summary['shape'][1]} cols"),
            ("Columns", ", ".join(ga_summary["columns"])),
        ], columns=["Property", "Value"])
        ga_df.to_excel(writer, sheet_name="General Analysis", index=False)
        ga_ws = writer.sheets["General Analysis"]
        if not orig_source.empty:
            orig_source.to_excel(writer, sheet_name="General Analysis",
                                 startrow=4, index=False)
            c = BarChart()
            c.title = "Originations per month (LCY)"
            c.height = 14
            c.width = 24
            data = Reference(ga_ws, min_col=1, min_row=5, max_col=2,
                             max_row=4 + len(orig_source))
            cats = Reference(ga_ws, min_col=1, min_row=6, max_row=4 + len(orig_source))
            c.add_data(data, titles_from_data=True)
            c.set_categories(cats)
            ga_ws.add_chart(c, "A6")

        export_charts = st.session_state.get("export_charts", [])
        if export_charts:
            cc_ws = writer.book.create_sheet("Custom Charts")
            row = 1
            for ec in export_charts:
                if ec["color_col"]:
                    wide = ec["data"].pivot_table(
                        index=ec["x_col"], columns=ec["color_col"], values=ec["y_col"],
                        aggfunc="first",
                    ).reset_index()
                else:
                    wide = ec["data"][[ec["x_col"], ec["y_col"]]]

                header_row = row
                wide.to_excel(writer, sheet_name="Custom Charts", startrow=row - 1, index=False)
                data_start = header_row + 1
                data_end = header_row + len(wide)
                n_series_cols = len(wide.columns) - 1

                chart_map = {"Line": LineChart, "Bar": BarChart, "Area": AreaChart}
                if ec["kind"] in chart_map:
                    c = chart_map[ec["kind"]]()
                    c.title = ec["title"]
                    c.height = 10
                    c.width = 20
                    data = Reference(
                        cc_ws, min_col=2, min_row=header_row, max_col=1 + n_series_cols,
                        max_row=data_end,
                    )
                    cats = Reference(cc_ws, min_col=1, min_row=data_start, max_row=data_end)
                    c.add_data(data, titles_from_data=True)
                    c.set_categories(cats)
                    cc_ws.add_chart(c, f"{chr(ord('A') + n_series_cols + 3)}{header_row}")
                else:
                    sc = ScatterChart()
                    sc.title = ec["title"]
                    sc.height = 10
                    sc.width = 20
                    sc.x_axis.title = ec["x_col"]
                    sc.y_axis.title = ec["y_title"]
                    xvalues = Reference(cc_ws, min_col=1, min_row=data_start, max_row=data_end)
                    for col_idx in range(2, 2 + n_series_cols):
                        yvalues = Reference(cc_ws, min_col=col_idx, min_row=header_row, max_row=data_end)
                        series = Series(yvalues, xvalues, title_from_data=True)
                        series.marker.symbol = "circle"
                        series.graphicalProperties.line.noFill = True
                        sc.series.append(series)
                    cc_ws.add_chart(sc, f"{chr(ord('A') + n_series_cols + 3)}{header_row}")

                row = data_end + 3

        cohorts.to_excel(writer, sheet_name="Cohorts", index=False)
        filtered.to_excel(writer, sheet_name="Cohorts for X or more loans", index=False)

    buf.seek(0)

    st.download_button(
        "Download full workbook as Excel",
        buf,
        file_name=f"SC_Analysis_{pd.Timestamp.now():%Y-%m-%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

else:
    st.info("Upload a CSV file to begin.")
