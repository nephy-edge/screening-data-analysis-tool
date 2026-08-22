"""Lendable Streamlit design system (colors, typography, masthead, cover).

Mirrors the Lendable house style used across Lendable's internal tools.
"""

from datetime import datetime

STYLE_CSS = """
*,*::before,*::after{box-sizing:border-box;}
::selection{background:#C7E2D9;}

/* -- Base typography & background -- */
html, body, .stApp, section.main{background:#F2F6F4 !important;color:#16312E !important;font-family:"Inter",system-ui,Arial,sans-serif !important;font-size:13.5px !important;line-height:1.6 !important;-webkit-font-smoothing:antialiased !important;}
section.main .block-container{padding-top:2rem !important;padding-bottom:4rem !important;}

/* -- Hide default Streamlit header and footer -- */
[data-testid="stHeader"]{display:none !important;}
footer{visibility:hidden !important;}

/* -- Masthead -- */
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

/* -- Cover / document title -- */
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

/* -- Tabs -- */
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

/* -- Headers -- */
h1,h2,h3,h4,h5,h6{font-family:"Inter",sans-serif !important;color:#16312E !important;font-weight:600 !important;}
h1{font-size:1.72rem !important;letter-spacing:-0.005em !important;}
h2{font-size:1.18rem !important;padding:9px 14px !important;background:#EDF3F1 !important;border-left:3px solid #155B54 !important;margin-bottom:14px !important;margin-top:30px !important;scroll-margin-top:130px !important;}
h3{font-size:.92rem !important;font-weight:700 !important;margin:18px 0 7px !important;}

/* -- Cards / Metrics -- */
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

/* -- Buttons -- */
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

/* -- Inputs -- */
/* Inputs/selects always sit on a white surface (incl. inside the dark sidebar),
   so force dark text explicitly rather than inheriting the surrounding color. */
div[data-baseweb="select"] > div, .stSelectbox div[data-baseweb="select"] > div{
    border-color:#D8DEE5 !important;border-radius:7px !important;background:#ffffff !important;
    color:#16312E !important;
}
div[data-baseweb="select"] > div *{color:#16312E !important;}
div[data-baseweb="select"] > div:focus-within, .stSelectbox div[data-baseweb="select"] > div:focus-within{
    border-color:#155B54 !important;box-shadow:0 0 0 2px rgba(21,91,84,.12) !important;
}
.stTextInput input, input[data-baseweb="input"], .stNumberInput input{
    border-color:#D8DEE5 !important;border-radius:7px !important;
    background:#ffffff !important;font-family:"Inter",sans-serif !important;
    color:#16312E !important;
}
.stTextInput input:focus, input[data-baseweb="input"]:focus{
    border-color:#155B54 !important;box-shadow:0 0 0 2px rgba(21,91,84,.12) !important;
}
.stNumberInput button, div[data-testid="stNumberInputStepUp"], div[data-testid="stNumberInputStepDown"]{
    background:#ffffff !important;border-color:#D8DEE5 !important;
}
div[data-testid="stNumberInputStepUp"] svg, div[data-testid="stNumberInputStepDown"] svg{
    fill:#16312E !important;
}
[data-testid="stDateInput"] input{color:#16312E !important;background:#ffffff !important;}

/* -- Radio -- */
.stRadio > div[role="radiogroup"], div[data-testid="stRadio"] > div:first-child{
    background:#ffffff !important;border:1px solid #D8DEE5 !important;border-radius:7px !important;
    padding:14px 18px !important;
}
.stRadio label, div[data-testid="stRadio"] label{
    font-family:"Inter",sans-serif !important;color:#525252 !important;font-weight:500 !important;
}

/* -- File uploader -- */
.stFileUploader > section, section[data-testid="stFileUploader"]{
    border:1px dashed #D3DDD9 !important;border-radius:7px !important;background:#F2F6F4 !important;
}
.stFileUploader section p, section[data-testid="stFileUploader"] p{color:#666666 !important;}

/* -- DataFrames -- */
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
.stDataFrame tbody tr:nth-child(even) td, div[data-testid="stTable"] tbody tr:nth-child(even) td{
    background:#F3F3F3 !important;
}

/* -- Progress bar -- */
.stProgress > div > div{background:#D8DEE5 !important;border-radius:10px !important;}
.stProgress > div > div > div{background:#155B54 !important;}

/* -- Alerts -- */
div[data-testid="stSuccess"]{border-left:3px solid #155B54 !important;background:#E3EFEC !important;border-radius:0 7px 7px 0 !important;}
div[data-testid="stError"]{border-left:3px solid #CB4B3A !important;background:#FBE9E4 !important;border-radius:0 7px 7px 0 !important;}
div[data-testid="stWarning"]{border-left:3px solid #CB4B3A !important;background:#FBE9E4 !important;border-radius:0 7px 7px 0 !important;}
div[data-testid="stInfo"]{border-left:3px solid #155B54 !important;background:#E8EFED !important;border-radius:0 7px 7px 0 !important;}

/* -- Expander -- */
.stExpander > details, details[data-testid="stExpander"]{border:1px solid #D8DEE5 !important;border-radius:7px !important;}
.stExpander > details > summary, details[data-testid="stExpander"] > summary{font-family:"Inter",sans-serif !important;color:#525252 !important;font-weight:600 !important;}

/* -- Checkbox -- */
.stCheckbox label, div[data-testid="stCheckbox"] label{font-family:"Inter",sans-serif !important;color:#525252 !important;}
.stCheckbox label span, div[data-testid="stCheckbox"] label span{font-weight:500 !important;}

/* -- Sidebar -- */
[data-testid="stSidebar"]{background:#16312E !important;}
[data-testid="stSidebar"] *{color:#EDF3F1 !important;}
/* The sticky masthead occupies the same top-left corner as the native chevron
   that re-opens a collapsed sidebar. Rather than fight over stacking order,
   move the chevron below the masthead's height so the two never overlap. */
[data-testid="stSidebarCollapsedControl"]{
  position:fixed !important;top:64px !important;left:12px !important;
  z-index:1000 !important;
  color:#16312E !important;background:#ffffff !important;
  border-radius:6px !important;box-shadow:0 1px 3px rgba(0,0,0,0.25) !important;
}
[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3{color:#ffffff !important;}
/* Section-banner h2 styling assumes a light background; neutralise it on the dark sidebar */
[data-testid="stSidebar"] h2{background:transparent !important;border-left:none !important;padding:0 !important;margin-top:18px !important;}
/* Text areas on the dark sidebar: white input with dark text (and a dark placeholder) */
[data-testid="stSidebar"] [data-testid="stTextArea"] textarea,
[data-testid="stSidebar"] [data-testid="stTextArea"] div[data-baseweb="textarea"] textarea{
  background:#ffffff !important;color:#1a1a1a !important;caret-color:#1a1a1a !important;
}
[data-testid="stSidebar"] [data-testid="stTextArea"] textarea::placeholder{color:#6b7280 !important;}
[data-testid="stSidebar"] [data-testid="stTextArea"] div[data-baseweb="textarea"]{
  background:#ffffff !important;border-color:#cbd5e1 !important;
}
[data-testid="stSidebar"] [data-testid="stTextArea"] label p,
[data-testid="stSidebar"] [data-testid="stTextArea"] div[data-testid="stMarkdownContainer"] p{
  color:#EDF3F1 !important;
}

/* -- Captions / muted text -- */
.stCaption, .caption, small{color:#666666 !important;font-family:"Inter",sans-serif !important;}
p, div[data-testid="stMarkdownContainer"] p{color:#525252 !important;}

/* -- Force white button text -- */
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

/* -- Dark text on buttons that keep Streamlit's default white background --
   (form-submit buttons and the file-uploader's Browse button aren't wrapped in
   .stButton, so they don't get the dark-green background above and would
   otherwise inherit the white text forced globally, going invisible.) */
.stFormSubmitButton button,
.stFormSubmitButton button div[data-testid="stMarkdownContainer"] p,
.stFormSubmitButton button p,
.stFormSubmitButton button span,
.stFileUploader button,
.stFileUploader button div[data-testid="stMarkdownContainer"] p,
.stFileUploader button p,
.stFileUploader button span{
    color:#16312E !important;
}

/* -- Spacing -- */
div[data-testid="stVerticalBlock"]{gap:0.5rem !important;}

/* -- Scrollbar -- */
::-webkit-scrollbar{width:8px;}
::-webkit-scrollbar-track{background:#F2F6F4;}
::-webkit-scrollbar-thumb{background:#D3DDD9;border-radius:4px;}
::-webkit-scrollbar-thumb:hover{background:#98A8A4;}

/* -- Responsive -- */
@media (max-width:768px){
    .masthead,.cover{margin-left:-1rem;margin-right:-1rem;padding-left:16px;padding-right:16px;}
    .cover{padding-top:20px;}
}
"""

FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700'
    '&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">'
)


def inject_style():
    import streamlit as st
    st.html(f"{FONT_LINKS}<style>{STYLE_CSS}</style>")


def render_masthead(doctype):
    import streamlit as st
    live_date = datetime.now().strftime("%d %b %Y")
    st.html(f"""
    <div class="masthead">
        <div class="brand">
            <span class="dot"></span>
            <span class="name">Lendable</span>
        </div>
        <span class="divider"></span>
        <span class="doctype">{doctype}</span>
        <span class="spacer"></span>
        <div class="mhead-meta">
            <span class="mdate">{live_date}</span>
        </div>
    </div>
    """)


def render_cover(title, sub):
    import streamlit as st
    st.html(f"""
    <div class="cover">
        <h1 class="cover-title">{title}</h1>
        <div class="cover-sub">{sub}</div>
    </div>
    """)
