import streamlit as st
import pandas as pd
import altair as alt
import io
import sys
import os
from datetime import datetime as _dt
from openpyxl.chart import LineChart, BarChart, Reference

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
    ("Total Due", True),
    ("Begin Date", False),
    ("Payment per Period", False),
    ("Payment Frequency", False),
]


def fmt_pct(v):
    return f"{v:.4%}" if pd.notna(v) else "—"


def fmt_num(v):
    return f"{v:,.2f}" if pd.notna(v) else "—"


def guess_column(df: pd.DataFrame, target: str):
    tokens = [t for t in target.lower().replace("/", " ").split() if t]
    best = None
    for col in df.columns:
        cl = str(col).lower()
        hits = sum(1 for t in tokens if t in cl)
        if hits == len(tokens):
            return col
        if best is None and hits:
            best = col
    return best


def _coerce_dates(raw: pd.DataFrame) -> pd.DataFrame:
    for col in ["Disbursement Date", "Expected Completion Date", "Begin Date"]:
        if col in raw.columns:
            raw[col] = pd.to_datetime(raw[col], errors="coerce", dayfirst=True)
    return raw


uploaded = st.file_uploader("Choose a CSV or Excel file", type=["csv", "xlsx"])

if uploaded:
    if uploaded.name.endswith(".csv"):
        raw = pd.read_csv(uploaded)
    else:
        raw = pd.read_excel(uploaded)

    st.success(f"Loaded {len(raw):,} rows — {len(raw.columns)} columns.")
    st.dataframe(raw.head(10), width="stretch", height=300)
    st.caption("Columns in your file: " + ", ".join(map(str, raw.columns)))

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
            guess = guess_column(raw, target)
            index = 0
            if guess is not None and guess in options:
                index = options.index(guess)
            chosen = st.selectbox(
                f"Map to **{target}** ({'required' if required else 'optional'})",
                options=options,
                index=index,
                key=f"map_{target}",
            )
            mapping[target] = None if chosen == "(not provided)" else chosen
            if chosen != "(not provided)":
                used.add(chosen)
        submitted = st.form_submit_button("Run analysis")

    if not submitted:
        st.info(
            "Map your file's columns above, then click "
            "'Run analysis' to compute all sheets."
        )
        st.stop()

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

    with st.spinner("Running analysis..."):
        gi = GeneralInputs(raw)
        df = process_data_input(raw, gi.extraction_date, gi.days_after_term)
        st.caption(f"Mapped & computed columns: {', '.join(df.columns)}")
        if "Total Due" not in df.columns:
            st.warning("No 'Total Due' or payment schedule mapped — loss rate proxy and PvD ratio will be unavailable.")
        cohorts = build_cohorts(df)
        filtered = filter_cohorts(cohorts, gi.min_loans_per_cohort)
        ltv = LtvAnalysis(df, filtered)
        ue = UeAnalysis(df)

    tab_names = [
        "General Inputs", "Data Questionnaire", "Data Input",
        "Cohorts", "Cohorts for X or more loans",
        "LTV Analysis", "Unit Economics Analysis", "General Analysis",
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
