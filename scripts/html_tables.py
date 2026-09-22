"""Small HTML-table/card rendering helpers shared by app.py's Summary,
Portfolio Snapshot, and UE Model tabs. Pure Streamlit markdown output - no
session-state or Streamlit widget state dependency, so these are safe to
call from anywhere."""

# Like app.py, this file is CSS-string-dominated - long lines are markup that
# can't be reflowed without changing the delivered styling. See app.py's own
# top-of-file E501 note.
# ruff: noqa: E501

import pandas as pd
import streamlit as st


def fmt(value, spec="{:.1%}"):
    return spec.format(value) if pd.notna(value) else "n/a"


def _esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_snapshot_table(rows: list) -> None:
    """A small metric/value/tooltip table, styled like the Summary tab's Portfolio Snapshot."""
    html = (
        """
        <style>
        div.snapshot-table table{border-collapse:collapse;font-family:"Inter",sans-serif;font-size:.82rem;}
        div.snapshot-table th{padding:8px 12px;text-align:left;border-bottom:1px solid #D8DEE5;font-weight:600;color:#16312E;}
        div.snapshot-table td{padding:7px 12px;border-bottom:1px solid #D8DEE5;color:#525252;}
        div.snapshot-table td.val{color:#16312E;}
        div.snapshot-table .q{display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;
            margin-left:6px;border-radius:50%;background:#D8DEE5;color:#16312E;font-size:.7rem;font-weight:700;
            cursor:help;position:relative;vertical-align:middle;}
        div.snapshot-table .tip{display:none;position:absolute;left:0;top:100%;width:230px;margin-top:6px;padding:8px 10px;
            background:#16312E;color:#EDF3F1;font-size:.72rem;font-weight:400;font-family:"Inter",sans-serif;
            border-radius:6px;z-index:50;line-height:1.4;}
        div.snapshot-table .q:hover .tip{display:block;}
        </style>
        <div class="snapshot-table">
        <table>
          <thead><tr><th>Metric</th><th>Value</th></tr></thead>
          <tbody>
        """
        + "".join(
            f'<tr><td>{_esc(name)}<span class="q">?<span class="tip">{_esc(desc)}</span></span></td>'
            f'<td class="val">{_esc(value)}</td></tr>'
            for name, value, desc in rows
        )
        + "</tbody></table></div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def _signed_color(value_str) -> str:
    """Red for a negative formatted value, green for positive, the table's
    normal dark neutral for exactly zero or an unparseable/"n/a" value -
    matches the red/green convention _render_big_number_card already uses
    for raw numbers, just applied to a pre-formatted display string."""
    s = str(value_str).strip()
    try:
        num = float(s.replace("%", "").replace(",", "").replace("$", ""))
    except ValueError:
        return "#16312E"
    if num > 0:
        return "#2ca02c"
    if num < 0:
        return "#d62728"
    return "#16312E"


def _matrix_row_html(row_label: str, row_values: list, is_big: bool) -> str:
    tr_class = ' class="bigrow"' if is_big else ""
    cells = "".join(f'<td style="color:{_signed_color(v)};">{_esc(v)}</td>' for v in row_values)
    return f'<tr{tr_class}><td class="rowhead">{_esc(row_label)}</td>{cells}</tr>'


def _render_matrix_table(
    row_labels: list, col_labels: list, values: list, big_row: int | None = None
) -> None:
    """A small 2D metric matrix (row label x column label -> formatted value), styled like
    _render_snapshot_table but with a header row of column labels instead of a single "Value"
    column - e.g. FX High/No FX/FX Low (rows) x Advance on Principal/Advance on GBV (columns).
    `values` is a list of rows, each a list of already-formatted strings, indexed [row][col] to
    match row_labels/col_labels. `big_row`, if given, renders that row's values large/bold/green
    - like the big-number cards used elsewhere on this page - so one headline row (e.g. the
    stressed FX-high scenario) stands out above the rest of the matrix."""
    html = (
        """
        <style>
        div.matrix-table table{border-collapse:collapse;font-family:"Inter",sans-serif;font-size:.82rem;}
        div.matrix-table th{padding:8px 12px;text-align:left;border-bottom:1px solid #D8DEE5;font-weight:600;color:#16312E;}
        div.matrix-table td{padding:7px 12px;border-bottom:1px solid #D8DEE5;color:#16312E;}
        div.matrix-table th.rowhead, div.matrix-table td.rowhead{color:#525252;font-weight:400;}
        div.matrix-table tr.bigrow td{font-size:1.4rem;font-weight:700;padding:16px 12px;}
        div.matrix-table tr.bigrow td.rowhead{font-size:.82rem;font-weight:600;color:#16312E;}
        </style>
        <div class="matrix-table">
        <table>
          <thead><tr><th class="rowhead"></th>"""
        + "".join(f"<th>{_esc(c)}</th>" for c in col_labels)
        + "</tr></thead><tbody>"
        + "".join(
            _matrix_row_html(row_labels[r], values[r], r == big_row) for r in range(len(row_labels))
        )
        + "</tbody></table></div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def _render_big_number_card(
    label: str, value: float, value_fmt: str = "{:.1%}", help_text: str = None
) -> None:
    """A metric-style card matching stMetric's card look (see theme.py), but
    with the big number colored green/red by sign - stMetric itself only
    colors its delta, not the primary value, so a plain st.metric can't do
    this."""
    if pd.isna(value):
        display, color = "n/a", "#16312E"
    else:
        display = value_fmt.format(value)
        color = "#2ca02c" if value >= 0 else "#d62728"
    tip = f'<span class="q">?<span class="tip">{_esc(help_text)}</span></span>' if help_text else ""
    st.markdown(
        f"""
        <style>
        div.big-number-card{{background:#fff;border:1px solid #D8DEE5;border-radius:7px;padding:18px 22px;}}
        div.big-number-card .lbl{{font-size:.58rem;text-transform:uppercase;letter-spacing:.11em;color:#666666;
            font-family:"Inter",sans-serif;font-weight:600;}}
        div.big-number-card .val{{font-size:1.9rem;font-family:"Inter",sans-serif;font-weight:700;margin-top:2px;}}
        div.big-number-card .q{{display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;
            margin-left:6px;border-radius:50%;background:#D8DEE5;color:#16312E;font-size:.7rem;font-weight:700;
            cursor:help;position:relative;vertical-align:middle;}}
        div.big-number-card .tip{{display:none;position:absolute;left:0;top:100%;width:230px;margin-top:6px;padding:8px 10px;
            background:#16312E;color:#EDF3F1;font-size:.72rem;font-weight:400;font-family:"Inter",sans-serif;
            text-transform:none;letter-spacing:normal;border-radius:6px;z-index:50;line-height:1.4;}}
        div.big-number-card .q:hover .tip{{display:block;}}
        </style>
        <div class="big-number-card">
            <div class="lbl">{_esc(label)}{tip}</div>
            <div class="val" style="color:{color};">{_esc(display)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _ue_model_badge(text: str, bg: str, fg: str) -> None:
    st.markdown(
        f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:4px;'
        f'font-size:.8rem;font-weight:600;">{_esc(text)}</span>',
        unsafe_allow_html=True,
    )
