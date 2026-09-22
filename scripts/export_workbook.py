"""Builds the downloadable Excel workbook for both models, mirroring each
model's own Excel template's sheet layout so the export can be re-uploaded
and auto-detected (see app.py's session-restore flow) or compared directly
against a client's original file."""

# Like app.py, this file has long lines of Excel-formula strings and
# human-readable sheet labels that can't be reflowed without changing the
# delivered content. See app.py's own top-of-file E501 note.
# ruff: noqa: E501

import io
import json

import pandas as pd
import streamlit as st
from openpyxl import load_workbook
from openpyxl.chart import AreaChart, BarChart, LineChart, Reference, ScatterChart, Series
from openpyxl.utils import get_column_letter
from rental_analysis.data_input import OPTIONAL_COLUMNS, REQUIRED_COLUMNS
from rental_analysis.data_questionnaire import QUESTIONS
from rental_analysis.general_analysis import describe as general_analysis
from template_analysis.data_questionnaire import QUESTIONS as LENDING_QUESTIONS
from template_analysis.general_analysis import describe as lending_general_analysis

EXCEL_MAX_ROWS = 1_048_576  # xlsx hard limit, rows per sheet (incl. header)


def _write_data_input_sheet(writer, data_frame, sheet_name="Data Input"):
    """Write `data_frame` to `sheet_name`, guarding against Excel's
    1,048,576-row-per-sheet limit. Real portfolios can exceed this (a
    4M-row lending tape triggered `ValueError: This sheet is too large`
    from pandas' to_excel) - when that happens, embed a note instead of the
    raw rows rather than crashing the whole export, since the aggregated
    analysis sheets (LTV/UE/Cohorts/General Analysis) are computed from the
    full dataset regardless and stay well within the limit. Returns the
    number of data rows written, or None if the sheet was too large and a
    note was written instead."""
    max_data_rows = EXCEL_MAX_ROWS - 1  # header takes row 1
    if len(data_frame) > max_data_rows:
        note_df = pd.DataFrame(
            {
                "Note": [
                    f"Data Input omitted: this dataset has {len(data_frame):,} rows, "
                    f"which exceeds Excel's {EXCEL_MAX_ROWS:,}-row-per-sheet limit. "
                    "The analysis sheets in this workbook were computed from the full "
                    "dataset; refer to the source file for the raw row-level data."
                ]
            }
        )
        note_df.to_excel(writer, sheet_name=sheet_name, index=False, header=False)
        return None
    data_frame.to_excel(writer, sheet_name=sheet_name, index=False, header=True)
    return len(data_frame)


def _write_custom_charts_sheet(writer, export_charts):
    """Shared by both export functions - identical chart-embedding logic,
    independent of which model produced the queued chart definitions."""
    if not export_charts:
        return
    cc_ws = writer.book.create_sheet("Custom Charts")
    row = 1
    for ec in export_charts:
        if ec["color_col"]:
            wide = (
                ec["data"]
                .pivot_table(
                    index=ec["x_col"], columns=ec["color_col"], values=ec["y_col"], aggfunc="first"
                )
                .reset_index()
            )
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
            c.height, c.width = 10, 20
            data = Reference(
                cc_ws, min_col=2, min_row=header_row, max_col=1 + n_series_cols, max_row=data_end
            )
            cats = Reference(cc_ws, min_col=1, min_row=data_start, max_row=data_end)
            c.add_data(data, titles_from_data=True)
            c.set_categories(cats)
            cc_ws.add_chart(c, f"{get_column_letter(n_series_cols + 4)}{header_row}")
        else:
            sc = ScatterChart()
            sc.title = ec["title"]
            sc.height, sc.width = 10, 20
            sc.x_axis.title = ec["x_col"]
            sc.y_axis.title = ec["y_title"]
            xvalues = Reference(cc_ws, min_col=1, min_row=data_start, max_row=data_end)
            for col_idx in range(2, 2 + n_series_cols):
                yvalues = Reference(cc_ws, min_col=col_idx, min_row=header_row, max_row=data_end)
                series = Series(yvalues, xvalues, title_from_data=True)
                series.marker.symbol = "circle"
                series.graphicalProperties.line.noFill = True
                sc.series.append(series)
            cc_ws.add_chart(sc, f"{get_column_letter(n_series_cols + 4)}{header_row}")
        row = data_end + 3


@st.cache_data(
    show_spinner="Building the Excel export — this can take a while for large portfolios..."
)
def _build_lending_export_workbook(
    df,
    cohorts,
    filtered,
    days_after_term,
    min_loans_per_cohort,
    ltv_data,
    ue_data,
    lending_chart_data,
    export_charts,
    term_supplied=False,
    mapping=None,
    currency=None,
    extraction_date=None,
    exclude_rule=None,
    active_status_values=None,
):
    """Cached on its inputs so it's only rebuilt when the analysis or queued
    custom charts actually change, rather than on every Streamlit rerun (e.g.
    toggling an unrelated checkbox) -- at scale (hundreds of thousands of
    rows) rebuilding this from scratch every rerun took 45-60+ seconds."""
    raw_cols = [
        "Loan ID",
        "Disbursement Date",
        "Expected Completion Date",
        "Principal Value",
        "Expected Interest",
        "Expected Fee",
        "Total Due",
        "Total Paid",
    ]
    if term_supplied:
        raw_cols.append("Term (days)")
    data_frame = pd.DataFrame({col: df.get(col, pd.Series([None] * len(df))) for col in raw_cols})
    orig_source = cohorts[["Cohort", "Total Principal"]].dropna()

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        gs = writer.book.create_sheet("General Inputs", 0)
        for r, label, val, note in [
            (2, "Inputs", None, None),
            (
                3,
                "Date of extraction",
                "=MAX('Data Input'!B:B)",
                "Defaults to most recent disbursement date",
            ),
            (
                4,
                "Days after term",
                days_after_term,
                "Ignore any loans for loss rates that are less than this number of days after term",
            ),
            (
                5,
                "Minimum loans per cohort",
                min_loans_per_cohort,
                "Ignore any cohorts for loss rates that are less than this number of loans",
            ),
        ]:
            if label:
                gs.cell(row=r, column=2, value=label)
            if val is not None:
                gs.cell(row=r, column=3, value=val)
            if note:
                gs.cell(row=r, column=4, value=note)

        q_df = pd.DataFrame(
            [(i, q) for i, q in enumerate(LENDING_QUESTIONS, 1)], columns=["#", "Question"]
        )
        q_df.to_excel(writer, sheet_name="Data Questionnaire", index=False)

        written_rows = _write_data_input_sheet(writer, data_frame)
        dws = writer.sheets["Data Input"]
        if written_rows is not None:
            # "Term (days)" is column 9 (real supplied values, already written by
            # to_excel above) when term_supplied, otherwise it's derived here as a
            # formula in column 11 same as the other computed columns - shift
            # Reached T+3?/Cohort's column to match.
            matured_col, cohort_col = (10, 11) if term_supplied else (9, 10)
            dws.cell(row=1, column=matured_col, value="Reached T+3?")
            dws.cell(row=1, column=cohort_col, value="Cohort")
            if not term_supplied:
                dws.cell(row=1, column=11, value="Term (days)")
            for r in range(2, max(written_rows, 1) + 2):
                dws.cell(
                    row=r, column=matured_col
                ).value = (
                    f"=IF(ISBLANK(C{r}),\"\",(C{r}+'General Inputs'!$C$4)<='General Inputs'!$C$3)"
                )
                dws.cell(
                    row=r, column=cohort_col
                ).value = f'=IF(ISBLANK(B{r}),"",DATE(YEAR(B{r}),MONTH(B{r}),1))'
                if not term_supplied:
                    dws.cell(
                        row=r, column=11
                    ).value = f'=IF(OR(ISBLANK(B{r}),ISBLANK(C{r})),"",C{r}-B{r})'

        ltv_df = pd.DataFrame(list(ltv_data.items()), columns=["Metric", "Value"])
        ltv_df.to_excel(writer, sheet_name="LTV Analysis", index=False)
        ltv_ws = writer.sheets["LTV Analysis"]
        if not lending_chart_data.empty:
            lending_chart_data[["Cohort", "Loss Rate"]].to_excel(
                writer, sheet_name="LTV Analysis", startrow=6, index=False
            )
            c = LineChart()
            c.title = "Loss per Cohort"
            c.y_axis.numFmt = "0.00%"
            c.height, c.width = 14, 24
            data = Reference(
                ltv_ws, min_col=1, min_row=7, max_col=2, max_row=7 + len(lending_chart_data)
            )
            cats = Reference(ltv_ws, min_col=1, min_row=8, max_row=7 + len(lending_chart_data))
            c.add_data(data, titles_from_data=True)
            c.set_categories(cats)
            ltv_ws.add_chart(c, "B8")

        ue_df = pd.DataFrame(list(ue_data.items()), columns=["Metric", "Value"])
        ue_df.to_excel(writer, sheet_name="Unit Economics Analysis", index=False)
        ue_ws = writer.sheets["Unit Economics Analysis"]
        if not lending_chart_data.empty:
            lending_chart_data[["Cohort", "Loss Rate"]].to_excel(
                writer, sheet_name="Unit Economics Analysis", startrow=10, index=False
            )
            lending_chart_data[["Cohort", "Weighted Avg Term"]].to_excel(
                writer, sheet_name="Unit Economics Analysis", startrow=10, startcol=4, index=False
            )
            lending_chart_data[["Cohort", "Fee %"]].to_excel(
                writer, sheet_name="Unit Economics Analysis", startrow=10, startcol=8, index=False
            )
            lending_chart_data[["Cohort", "Interest %"]].to_excel(
                writer, sheet_name="Unit Economics Analysis", startrow=10, startcol=11, index=False
            )
            sr = 11
            lr = sr + len(lending_chart_data) - 1
            for title, col, anchor in [
                ("Loss per Cohort", (1, 2), "B29"),
                ("Average Term per Cohort (days)", (5, 6), "B48"),
                ("Average Fee Percent per Cohort", (9, 10), "B67"),
                ("Average Product Interest Percent per Cohort", (12, 13), "B86"),
            ]:
                c = LineChart()
                c.title = title
                c.height, c.width = 14, 24
                if col[1] - col[0] == 1:
                    c.y_axis.numFmt = "0.00%" if "Loss" in title or "Percent" in title else "0"
                data = Reference(ue_ws, min_col=col[0], min_row=sr - 1, max_col=col[1], max_row=lr)
                cats = Reference(ue_ws, min_col=col[0], min_row=sr, max_row=lr)
                c.add_data(data, titles_from_data=True)
                c.set_categories(cats)
                ue_ws.add_chart(c, anchor)

        ga_summary = lending_general_analysis(df)
        ga_df = pd.DataFrame(
            [
                ("Shape", f"{ga_summary['shape'][0]:,} rows x {ga_summary['shape'][1]} cols"),
                ("Columns", ", ".join(ga_summary["columns"])),
            ],
            columns=["Property", "Value"],
        )
        ga_df.to_excel(writer, sheet_name="General Analysis", index=False)
        ga_ws = writer.sheets["General Analysis"]
        if not orig_source.empty:
            orig_source.to_excel(writer, sheet_name="General Analysis", startrow=4, index=False)
            c = BarChart()
            c.title = "Originations per month"
            c.height, c.width = 14, 24
            data = Reference(ga_ws, min_col=1, min_row=5, max_col=2, max_row=4 + len(orig_source))
            cats = Reference(ga_ws, min_col=1, min_row=6, max_row=4 + len(orig_source))
            c.add_data(data, titles_from_data=True)
            c.set_categories(cats)
            ga_ws.add_chart(c, "A6")

        _write_custom_charts_sheet(writer, export_charts)

        cohorts.to_excel(writer, sheet_name="Cohorts", index=False)
        filtered.to_excel(writer, sheet_name="Cohorts for X or more loans", index=False)

        # Embed the session config (mapping/currency/GI overrides) as a
        # hidden sheet so re-uploading this same file lets the app detect it
        # and pre-fill the mapping form to reproduce this exact run, instead
        # of the recipient re-mapping/re-tuning from scratch.
        if mapping is not None:
            session_cfg = {
                "schema": 1,
                "mapping": mapping,
                "currency": currency,
                "gi_overrides": {
                    "extraction_date": extraction_date.isoformat()
                    if extraction_date is not None
                    else None,
                    "days_after_term": days_after_term,
                    "min_loans_per_cohort": min_loans_per_cohort,
                },
                "exclude_rule": exclude_rule,
                "active_status_values": active_status_values,
            }
            cfg_ws = writer.book.create_sheet("_App Session")
            cfg_ws["A1"] = json.dumps(session_cfg)
            cfg_ws.sheet_state = "hidden"

    buf.seek(0)
    return buf.getvalue()


def _add_chat_history_sheet(workbook_bytes: bytes, history: list) -> bytes:
    """Append an 'AskAI Chat History' sheet (Role/Message columns, one row per turn) to an
    already-built workbook's raw bytes. Deliberately NOT part of _build_lending_export_workbook
    (which is @st.cache_data-cached on its inputs, expensive to rebuild, and would otherwise
    need to be rebuilt from scratch on every single new chat message) - appending a sheet is a
    cheap, purely programmatic post-process with no LLM call, so it stays outside that cache
    and only runs when the user actually opts in via the checkbox at download time."""
    wb = load_workbook(io.BytesIO(workbook_bytes))
    ws = wb.create_sheet("AskAI Chat History")
    ws.append(["Role", "Message"])
    for msg in history:
        ws.append([msg.get("role", ""), msg.get("content", "")])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _build_rental_export_workbook(
    gi,
    df,
    av,
    curve,
    cohorts,
    filtered,
    ue_data,
    ltv_data,
    ca_data,
    ts,
    export_charts,
    mapping=None,
    currency=None,
    exclude_rule=None,
    status_map=None,
):
    """Build the Rental & Subscription SC-analysis Excel export, mirroring the
    workbook's sheet layout (General Inputs, Data Questionnaire, Data Input,
    Asset View, Unit Economics Analysis + repayment curve, Churn Analysis +
    residual curve, LTV Analysis, TS Covenants, General Analysis + chart,
    Lease Cohorts, Cohorts for X or more loans, Custom Charts)."""
    export_col_order = REQUIRED_COLUMNS + OPTIONAL_COLUMNS
    data_input_export = df[export_col_order + [c for c in df.columns if c not in export_col_order]]

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        gs = writer.book.create_sheet("General Inputs", 0)
        for r, label, val, note in [
            (2, "Inputs", None, None),
            (
                3,
                "Date of extraction",
                "=MAX('Data Input'!C:C)",
                "Defaults to most recent contract start date",
            ),
            (
                4,
                "Days after term",
                gi.days_after_term,
                "Ignore any contracts for loss rates that are less than this number of days after term",
            ),
            (
                5,
                "Months since default",
                gi.months_since_default,
                "Includes only contracts that defaulted over this many months ago",
            ),
            (
                6,
                "Minimum loans per cohort",
                gi.min_loans_per_cohort,
                "Ignore any cohorts for loss rates that are less than this number of contracts",
            ),
            (
                7,
                "Useful life of asset (years)",
                gi.useful_life_years,
                "Assumed Useful Life of Asset",
            ),
            (
                8,
                "Funding cost (% p.a.)",
                gi.funding_cost_pct,
                "Lendable cost of funding, applied to asset cost over the useful life",
            ),
            (
                9,
                "Direct cost (% p.a.)",
                gi.direct_cost_pct,
                "Operating/direct costs as % of asset cost per year",
            ),
            (
                10,
                "FX devaluation (95th %ile, %)",
                gi.fx_deval_pct,
                "FX risk used in the asset-value LTV (max 50% discount applied when enabled)",
            ),
            (11, "FX max 50% discount", gi.fx_discount_50pct, "Guide: max 50% FX discount"),
            (
                12,
                "Debt service due over 180d",
                gi.debt_service_180d,
                "Facility debt service input for the Receivables Coverage Ratio",
            ),
        ]:
            if label:
                gs.cell(row=r, column=2, value=label)
            if val is not None:
                gs.cell(row=r, column=3, value=val)
            if note:
                gs.cell(row=r, column=4, value=note)

        q_df = pd.DataFrame([(i, q) for i, q in enumerate(QUESTIONS, 1)], columns=["#", "Question"])
        q_df.to_excel(writer, sheet_name="Data Questionnaire", index=False)

        _write_data_input_sheet(writer, data_input_export)

        av.to_excel(writer, sheet_name="Asset View", index=False)

        ue_df = pd.DataFrame(list(ue_data.items()), columns=["Metric", "Value"])
        ue_df.to_excel(writer, sheet_name="Unit Economics Analysis", index=False)
        ue_ws = writer.sheets["Unit Economics Analysis"]
        curve_export = curve.dropna(subset=["pct_avg_collection"])
        if not curve_export.empty:
            curve_export[["mob", "pct_avg_collection"]].to_excel(
                writer,
                sheet_name="Unit Economics Analysis",
                startrow=len(ue_df) + 2,
                index=False,
            )
            sr = len(ue_df) + 3
            lr = sr + len(curve_export) - 1
            c = LineChart()
            c.title = "High level repayment curve"
            c.y_axis.numFmt = "0.00%"
            c.height, c.width = 14, 24
            data = Reference(ue_ws, min_col=1, min_row=sr - 1, max_col=2, max_row=lr)
            cats = Reference(ue_ws, min_col=1, min_row=sr, max_row=lr)
            c.add_data(data, titles_from_data=True)
            c.set_categories(cats)
            ue_ws.add_chart(c, f"D{sr - 1}")

        ca_export = {k: v for k, v in ca_data.items() if k != "residual_curve"}
        ca_df = pd.DataFrame(list(ca_export.items()), columns=["Metric", "Value"])
        ca_df.to_excel(writer, sheet_name="Churn Analysis", index=False)
        ca_ws = writer.sheets["Churn Analysis"]
        residual = ca_data.get("residual_curve")
        if residual is not None and not residual.empty:
            residual.to_excel(
                writer, sheet_name="Churn Analysis", startrow=len(ca_df) + 2, index=False
            )
            sr = len(ca_df) + 3
            lr = sr + len(residual) - 1
            c = LineChart()
            c.title = "Expected residual portfolio by month"
            c.height, c.width = 14, 24
            data = Reference(ca_ws, min_col=1, min_row=sr - 1, max_col=2, max_row=lr)
            cats = Reference(ca_ws, min_col=1, min_row=sr, max_row=lr)
            c.add_data(data, titles_from_data=True)
            c.set_categories(cats)
            ca_ws.add_chart(c, f"D{sr - 1}")

        ltv_df = pd.DataFrame(list(ltv_data.items()), columns=["Metric", "Value"])
        ltv_df.to_excel(writer, sheet_name="LTV Analysis", index=False)

        ts_df = pd.DataFrame(list(ts.items()), columns=["Metric", "Value"])
        ts_df.to_excel(writer, sheet_name="TS Covenants", index=False)

        ga_summary = general_analysis(df)
        ga_df = pd.DataFrame(
            [
                ("Shape", f"{ga_summary['shape'][0]:,} rows x {ga_summary['shape'][1]} cols"),
                ("Columns", ", ".join(ga_summary["columns"])),
            ],
            columns=["Property", "Value"],
        )
        ga_df.to_excel(writer, sheet_name="General Analysis", index=False)
        ga_ws = writer.sheets["General Analysis"]
        orig_source = cohorts[["cohort", "value_of_leases"]].dropna()
        if not orig_source.empty:
            orig_source.to_excel(writer, sheet_name="General Analysis", startrow=4, index=False)
            c = BarChart()
            c.title = "New lease value per month"
            c.height, c.width = 14, 24
            data = Reference(ga_ws, min_col=1, min_row=5, max_col=2, max_row=4 + len(orig_source))
            cats = Reference(ga_ws, min_col=1, min_row=6, max_row=4 + len(orig_source))
            c.add_data(data, titles_from_data=True)
            c.set_categories(cats)
            ga_ws.add_chart(c, "D6")

        cohorts.to_excel(writer, sheet_name="Lease Cohorts", index=False)
        filtered.to_excel(writer, sheet_name="Cohorts for X or more loans", index=False)

        _write_custom_charts_sheet(writer, export_charts)

        # Embed the session config so re-uploading this file pre-fills the
        # mapping/inputs to reproduce this exact run (mirrors the lending export).
        if mapping is not None:
            session_cfg = {
                "schema": 1,
                "mapping": mapping,
                "currency": currency,
                "gi_overrides": {
                    "extraction_date": gi.extraction_date.isoformat(),
                    "days_after_term": gi.days_after_term,
                    "months_since_default": gi.months_since_default,
                    "min_loans_per_cohort": gi.min_loans_per_cohort,
                    "useful_life_years": gi.useful_life_years,
                    "churn_stress_multiplier": gi.churn_stress_multiplier,
                    "open_label": gi.open_label,
                    "closed_label": gi.closed_label,
                    "paidoff_label": gi.paidoff_label,
                    "funding_cost_pct": gi.funding_cost_pct,
                    "direct_cost_pct": gi.direct_cost_pct,
                    "fx_deval_pct": gi.fx_deval_pct,
                    "fx_discount_50pct": gi.fx_discount_50pct,
                    "debt_service_180d": gi.debt_service_180d,
                },
                "exclude_rule": exclude_rule,
                "status_map": status_map,
            }
            cfg_ws = writer.book.create_sheet("_App Session")
            cfg_ws["A1"] = json.dumps(session_cfg)
            cfg_ws.sheet_state = "hidden"

    buf.seek(0)
    return buf.getvalue()
