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
from openpyxl import Workbook, load_workbook
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


def _write_summary_sheet(writer, rows, sheet_name="Summary"):
    """Write the app's Summary tab (both models) as a Metric/Value/How it is
    calculated sheet, mirroring app.py's Summary tab so the workbook carries
    the same headline figures the React Summary screen shows.

    `rows` is an ordered list of (label, value, note, formula). When `formula`
    is provided AND `value` is None, the metric is a derived figure whose
    arithmetic relationship to other cells in THIS workbook is exact, so a real
    Excel formula is written instead of a hardcoded number - the reviewer can
    click the cell and see how it was computed. When `value` is given it is
    written literally (engine output that isn't reproduced cell-by-cell in the
    workbook) and any `formula` text is recorded in the how-calculated column
    as documentation rather than as a live formula.

    The two are split deliberately: only emit a live formula where its
    referenced rows provably exist in the sheet, so the workbook never shows a
    #REF!/#VALUE! error next to a real metric."""
    ws = writer.book.create_sheet(sheet_name)
    ws.cell(row=1, column=1, value="Metric")
    ws.cell(row=1, column=2, value="Value")
    ws.cell(row=1, column=3, value="How it is calculated")
    r = 2
    for label, value, note, formula in rows:
        ws.cell(row=r, column=1, value=label)
        cell = ws.cell(row=r, column=2)
        if value is None and formula:
            cell.value = formula
        else:
            cell.value = value
        ws.cell(row=r, column=3, value=note or formula)
        r += 1


def _write_checks_sheet(writer, checks, sheet_name="Data Checks"):
    """Write the app's Data Checks findings (scripts/quality_checks.py output)
    as one row per check, followed by each finding's `detail` table - so the
    workbook carries the same data-quality warnings the Data Checks tab shows.
    A no-op when there are no findings (nothing useful to write)."""
    if not checks:
        return
    ws = writer.book.create_sheet(sheet_name)
    ws.cell(row=1, column=1, value="Level")
    ws.cell(row=1, column=2, value="Check ID")
    ws.cell(row=1, column=3, value="Message")
    r = 2
    for chk in checks:
        ws.cell(row=r, column=1, value=chk.get("level", ""))
        ws.cell(row=r, column=2, value=chk.get("check_id", ""))
        ws.cell(row=r, column=3, value=chk.get("message", ""))
        r += 1
        detail = chk.get("detail")
        if detail:
            if chk.get("detail_expander"):
                ws.cell(row=r, column=3, value=chk["detail_expander"])
                r += 1
            # detail rows are all dicts with the same keys - write a header
            # from the first row's keys, then one row per record.
            detail_df = pd.DataFrame(list(detail))
            for c_idx, col in enumerate(detail_df.columns, 1):
                ws.cell(row=r, column=c_idx, value=col)
            r += 1
            for _, row in detail_df.iterrows():
                for c_idx, col in enumerate(detail_df.columns, 1):
                    ws.cell(row=r, column=c_idx, value=row[col])
                r += 1
            r += 1


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
    summary_metrics=None,
    ltv_calculator=None,
    ue_model=None,
    checks=None,
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

        if summary_metrics:
            sm = summary_metrics
            # "Reached T+3?" is column 9 (I) when Term was supplied, else 10 (J)
            # - see the matured_col shift above. Used only in the how-calculated
            # text so the note points at the real column.
            matured_letter = "I" if term_supplied else "J"
            _write_summary_sheet(
                writer,
                [
                    (
                        "Data up to",
                        sm.get("dataUpTo"),
                        "Most recent loan disbursement date in the tape (as-of point for the snapshot).",
                        None,
                    ),
                    (
                        "Outstanding GBV",
                        sm.get("outstandingGbv"),
                        f"SUM of (Principal + Expected Interest + Expected Fee - Total Paid) over 'Data Input' rows where Reached T+3? ({matured_letter} column) is FALSE.",
                        None,
                    ),
                    (
                        "Loss Rate",
                        sm.get("lossRate"),
                        "Engine 'Average Loss' = portfolio average loss rate across matured cohorts.",
                        None,
                    ),
                    (
                        "95th %ile Loss Rate",
                        sm.get("pctile95LossRate"),
                        "Engine '95th Percentile Losses' = the loss-rate level only 5% of matured cohorts exceed.",
                        None,
                    ),
                    (
                        "Product Interest Rate",
                        sm.get("productInterestRate"),
                        "Engine 'Average Interest %' = Expected Interest as a share of Principal across all loans.",
                        None,
                    ),
                    ("", None, "", None),
                    ("Portfolio Snapshot", None, "", None),
                    (
                        "Date Range",
                        f"{sm.get('dateRangeStart')} to {sm.get('dateRangeEnd')}",
                        "Earliest disbursement date to the tape as-of (extraction) date.",
                        None,
                    ),
                    ("Loans", sm.get("loans"), "Total loan records in the tape.", None),
                    (
                        "Value of Principal Disbursed",
                        None,
                        "SUM('Data Input'!D:D) - total principal lent.",
                        "=SUM('Data Input'!D:D)",
                    ),
                    (
                        "Total Collected",
                        sm.get("totalCollected"),
                        "SUM('Data Input'!H:H) - total cash collected to date.",
                        "=SUM('Data Input'!H:H)",
                    ),
                    (
                        "Expected Interest %",
                        sm.get("expectedInterestPct"),
                        "Expected Interest as a share of total principal: SUM(Expected Interest)/SUM(Principal).",
                        None,
                    ),
                    (
                        "Expected Fee %",
                        sm.get("expectedFeePct"),
                        "Expected Fee as a share of total principal: SUM(Expected Fee)/SUM(Principal).",
                        None,
                    ),
                    (
                        "Avg Loan Value",
                        sm.get("avgLoanValue"),
                        "Average principal per loan: SUM(Principal)/COUNT(loans).",
                        None,
                    ),
                    (
                        "Avg Total Revenue",
                        sm.get("avgTotalRevenuePct"),
                        "Expected interest + fees as a share of total principal.",
                        None,
                    ),
                    (
                        "PvD",
                        sm.get("pvd"),
                        "Paid-vs-Due = SUM(Total Paid)/SUM(Total Due) over matured loans (Reached T+3? = TRUE).",
                        None,
                    ),
                    (
                        "Weighted Avg Term (matured)",
                        sm.get("weightedAvgTermMatured"),
                        "Principal-weighted average term (days) over matured loans only.",
                        None,
                    ),
                    (
                        "APR Proxy",
                        sm.get("aprProxy"),
                        "Product Interest Rate / (Weighted Avg Term in months) x 12, where months = days / 30.4375 - a simple flat-rate annualization (no compounding, no amortization solve).",
                        None,
                    ),
                ],
            )

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

        # ---- LTV Calculator (app's lending "LTV Analysis" tab) ----
        # The app computes this client-side via /api/ltv-calculator; the API
        # passes the same result dict here so the workbook carries the matrix
        # and both backup tables the analyst sees on screen.
        if ltv_calculator and ltv_calculator.get("ok") and ltv_calculator.get("result"):
            calc = ltv_calculator["result"]
            lc_rows = [
                ("Inputs", None, None),
                ("Data Input Source", ltv_calculator.get("dataSource"), None),
                ("Alt Lender Country", ltv_calculator.get("country"), None),
                ("Macro FX Risk Rating", ltv_calculator.get("macroFxRisk"), None),
                ("Segmentation", ltv_calculator.get("segmentation"), None),
                (
                    "Term bucket (months)",
                    ltv_calculator.get("greenTermBucket"),
                    "Weighted average receivable term, snapped to the nearest tenor bucket.",
                ),
                (
                    "Product Interest Rate",
                    ltv_calculator.get("greenInterest"),
                    "Weighted average gross interest rate, pulled from this analysis.",
                ),
                (
                    "95th %ile Loss",
                    ltv_calculator.get("greenLoss"),
                    "95th percentile loss rate at T+3, or the Min Sector Loss Rate fallback.",
                ),
                ("", None, None),
                ("Suggested LTV", None, None),
                (
                    "FX High - Advance on Principal",
                    calc.get("ltv_high"),
                    "FX High is the stressed, headline scenario.",
                ),
                ("FX High - Advance on GBV", calc.get("ltgbv_high"), None),
                ("No FX - Advance on Principal", calc.get("ltv_no_fx"), None),
                ("No FX - Advance on GBV", calc.get("ltgbv_no_fx"), None),
                ("FX Low - Advance on Principal", calc.get("ltv_low"), None),
                ("FX Low - Advance on GBV", calc.get("ltgbv_low"), None),
                ("", None, None),
                ("FX Backup", None, None),
                (
                    "Historical Devaluation",
                    calc.get("historical_fx_deval"),
                    "99th-percentile historical FX devaluation for the country and tenor.",
                ),
                (
                    "Utilized Historical FX Deval",
                    calc.get("utilized_fx_deval"),
                    "The 6-month figure when the tenor is 6 months or less.",
                ),
                ("Stress FX Devaluation", calc.get("stress_fx_deval"), None),
                (
                    "Base FX Deval",
                    calc.get("base_fx_deval"),
                    "Stress FX devaluation, capped at the hedge rate when hedged.",
                ),
                (
                    "FX Slippage Stress",
                    calc.get("fx_slippage"),
                    "FX basis-risk slippage for the selected country.",
                ),
                ("Selected FX Deval (High)", calc.get("selected_fx_deval_high"), None),
                ("Selected FX Deval (Low)", calc.get("selected_fx_deval_low"), None),
                ("", None, None),
                ("Credit Stress Backup", None, None),
                (
                    "Stress Loss",
                    calc.get("stress_loss_rate"),
                    "Loss rate multiplied by the credit stress factor.",
                ),
                (
                    "Min Sector Loss Rate",
                    calc.get("minimum_stress_loss"),
                    "Sector-minimum stress loss floor.",
                ),
                (
                    "Selected Stress Loss",
                    calc.get("selected_stress_loss"),
                    "Max(stress loss rate, minimum stress loss floor).",
                ),
            ]
            _write_summary_sheet(
                writer, [(lbl, val, note, None) for lbl, val, note in lc_rows], "LTV Calculator"
            )
            lc_ws = writer.sheets["LTV Calculator"]
            for note in ltv_calculator.get("lossRateFallbackNotes") or []:
                lc_ws.cell(row=lc_ws.max_row + 1, column=1, value=note)

        # ---- UE Model (app's lending Unit Economics > UE Model sub-tab) ----
        if ue_model:
            _write_summary_sheet(
                writer,
                [
                    (
                        "Gross IRR",
                        ue_model.get("Gross IRR"),
                        "IRR of the matured-loan cash-flow profile at the default cost assumptions (no cost of finance).",
                        None,
                    ),
                    (
                        "Cost of Finance (% p.a.)",
                        ue_model.get("Cost of Finance (% p.a.)"),
                        "Financing cost + facility fee + hedge cost, as decimals per annum.",
                        None,
                    ),
                    (
                        "Net IRR",
                        ue_model.get("Net IRR"),
                        "Gross IRR minus Cost of Finance (% p.a.).",
                        None,
                    ),
                    ("", None, "", None),
                    (
                        "Loss Rate used (Actual)",
                        ue_model.get("Loss Rate used (Actual)"),
                        "Actual loss rate = (matured owed - matured paid) / matured owed.",
                        None,
                    ),
                    (
                        "Loss % of GBV",
                        ue_model.get("Loss % of GBV"),
                        "Losses in dollars as a share of total principal.",
                        None,
                    ),
                    (
                        "Estimated APR",
                        ue_model.get("Estimated APR"),
                        "(Average Interest % / term in months) x 12 x 2 - a flat-rate annualization.",
                        None,
                    ),
                    (
                        "Implied Monthly Interest %",
                        ue_model.get("Implied Monthly Interest %"),
                        "Estimated APR / 12.",
                        None,
                    ),
                    (
                        "Cost of Finance (over loan life)",
                        ue_model.get("Cost of Finance (over loan life)"),
                        "Cost of Finance (% p.a.) x (matured term in months / 12).",
                        None,
                    ),
                    ("", None, "", None),
                    (
                        "Income (net of losses)",
                        ue_model.get("Income (net of losses)"),
                        "Matured revenue + matured origination income - losses in dollars.",
                        None,
                    ),
                    (
                        "Income (net of losses) margin",
                        ue_model.get("Income (net of losses) margin"),
                        "Income (net of losses) / matured principal.",
                        None,
                    ),
                    (
                        "Variable costs",
                        ue_model.get("Variable costs"),
                        "(Upfront + ongoing cost %) x matured principal.",
                        None,
                    ),
                    (
                        "Product contribution",
                        ue_model.get("Product contribution"),
                        "Income (net of losses) - variable costs.",
                        None,
                    ),
                    (
                        "Product contribution margin",
                        ue_model.get("Product contribution margin"),
                        "Product contribution / matured principal.",
                        None,
                    ),
                    ("", None, "", None),
                    (
                        "Upfront cost (% of principal)",
                        ue_model.get("Upfront cost (% of principal)"),
                        "Default upfront cost assumption the analysis ran with.",
                        None,
                    ),
                    (
                        "Ongoing cost (% of principal)",
                        ue_model.get("Ongoing cost (% of principal)"),
                        "Default ongoing cost assumption.",
                        None,
                    ),
                    (
                        "Financing cost (% p.a.)",
                        ue_model.get("Financing cost (% p.a.)"),
                        "Default financing cost assumption.",
                        None,
                    ),
                    (
                        "Facility fee (% p.a.)",
                        ue_model.get("Facility fee (% p.a.)"),
                        "Default facility fee assumption.",
                        None,
                    ),
                    (
                        "Hedge cost (% p.a.)",
                        ue_model.get("Hedge cost (% p.a.)"),
                        "Default hedge cost assumption.",
                        None,
                    ),
                ],
                "UE Model",
            )

        # ---- Data Checks (app's Data Checks tab) ----
        _write_checks_sheet(writer, checks)

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
    summary_metrics=None,
    ltv_asset_analysis=None,
    checks=None,
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

        if summary_metrics:
            sm = summary_metrics
            # Rental 'Data Input' export column order is REQUIRED_COLUMNS +
            # OPTIONAL_COLUMNS, so total_paid is column G and cost_of_asset H.
            _write_summary_sheet(
                writer,
                [
                    (
                        "Contracts",
                        sm.get("contracts"),
                        "Number of unique assets (Asset View row count).",
                        None,
                    ),
                    (
                        "MRR",
                        sm.get("mrr"),
                        "Monthly recurring revenue from active contracts (engine LTV 'mrr').",
                        None,
                    ),
                    (
                        "Avg MRR per Active Contract",
                        sm.get("avgMrrPerActiveContract"),
                        "MRR divided by the number of open (active) contracts.",
                        None,
                    ),
                    (
                        "Average Useful Life (m)",
                        sm.get("avgUsefulLifeM"),
                        "Principal-weighted average useful asset life, in months.",
                        None,
                    ),
                    (
                        "95th %ile Churn",
                        sm.get("pctile95Churn"),
                        "The churn level only 5% of cohorts exceed.",
                        None,
                    ),
                    (
                        "Stressed Churn",
                        sm.get("stressedChurn"),
                        "Churn under the stress multiplier.",
                        None,
                    ),
                    (
                        "Contracts Churned",
                        sm.get("contractsChurned"),
                        "Closed contracts / total contract records.",
                        None,
                    ),
                    (
                        "Defaulted contracts > 3mo",
                        sm.get("defaultedGt3m"),
                        "Contracts in default for more than 3 months.",
                        None,
                    ),
                    (
                        "% recovered",
                        sm.get("pctRecovered"),
                        "Share of defaulted value recovered.",
                        None,
                    ),
                    (
                        "Loss (non-recoverability)",
                        sm.get("lossNonRecoverability"),
                        "Expected loss from defaulted value that is not recoverable.",
                        None,
                    ),
                    (
                        "MRR / average cost",
                        sm.get("mrrOverAvgCost"),
                        "Monthly recurring revenue relative to average asset cost.",
                        None,
                    ),
                    (
                        "Average Collection Rate",
                        sm.get("avgCollectionRate"),
                        "Share of expected payments actually collected.",
                        None,
                    ),
                    ("", None, "", None),
                    ("LTV & UE highlights", None, "", None),
                    (
                        "Asset LTV (max 95%)",
                        sm.get("assetLtv"),
                        "min(95%, (recoverability + subscription support) / (1 + FX devaluation)).",
                        None,
                    ),
                    (
                        "Net UE margin",
                        sm.get("netUeMargin"),
                        "Engine UE 'net_margin' - net unit-economics margin after costs.",
                        None,
                    ),
                    (
                        "Asset DSLP30",
                        sm.get("assetDslp30"),
                        "Asset-level days-sales-late over the last 30 days exposure.",
                        None,
                    ),
                    (
                        "Receivables Coverage (180d)",
                        sm.get("receivablesCoverage180d"),
                        "Coverage of expected receivables over a 180-day horizon (TS covenants).",
                        None,
                    ),
                    ("", None, "", None),
                    ("Portfolio Snapshot", None, "", None),
                    (
                        "Date Range",
                        f"{sm.get('dateRangeStart')} to {sm.get('dateRangeEnd')}",
                        "Earliest contract start date to the tape as-of (extraction) date.",
                        None,
                    ),
                    (
                        "Contracts",
                        sm.get("contractRecords"),
                        "Total contract records in the tape.",
                        None,
                    ),
                    (
                        "Unique Assets",
                        sm.get("uniqueAssets"),
                        "Distinct assets (Asset View row count).",
                        None,
                    ),
                    (
                        "Total Collected",
                        sm.get("totalCollected"),
                        "SUM('Data Input'!G:G) - total cash collected to date.",
                        "=SUM('Data Input'!G:G)",
                    ),
                    (
                        "Total Cost of Assets",
                        sm.get("totalCostOfAssets"),
                        "SUM('Data Input'!H:H) - total acquisition cost of the assets.",
                        "=SUM('Data Input'!H:H)",
                    ),
                    (
                        "Downpayment %",
                        sm.get("downpaymentPct"),
                        "Engine UE 'downpayment_pct' - downpayments as a share of asset cost.",
                        None,
                    ),
                    (
                        "Lease Tenor (m)",
                        sm.get("leaseTenorM"),
                        "Engine UE 'lease_tenor_m' - cost-weighted average lease term in months.",
                        None,
                    ),
                    (
                        "Margin",
                        sm.get("margin"),
                        "Engine UE 'margin' - scheduled payments over asset cost minus 1.",
                        None,
                    ),
                    (
                        "PvD",
                        sm.get("pvd"),
                        "Engine UE 'pvd' - total paid over total expected to date.",
                        None,
                    ),
                    (
                        "Utilisation Rate",
                        sm.get("utilisationRate"),
                        "Engine UE 'utilisation_rate' - share of assets on an open contract.",
                        None,
                    ),
                    (
                        "Historical % Collected on Principal",
                        sm.get("historicalPctCollectedOnPrincipal"),
                        "Collections (incl. recovery) over initial asset cost for assets that reached term.",
                        None,
                    ),
                ],
            )

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
        ltv_ws = writer.sheets["LTV Analysis"]
        # The app's rental LTV tab shows the Asset-value LTV waterfall below the
        # flat metric list. Append it, with Asset LTV written as a live formula
        # over the three waterfall inputs so the reader can see the arithmetic
        # (the app states: LTV = min(95%, (recoverability + support) / (1 + FX))).
        waterfall_start = len(ltv_df) + 3
        ltv_ws.cell(row=waterfall_start, column=1, value="Asset-value LTV waterfall (Guide)")
        waterfall = [
            (
                "Recoverability (95th %ile)",
                ltv_data.get("recoverability_pctile_95"),
                "95th-percentile recovery value on defaulted assets.",
            ),
            (
                "Subscription support",
                ltv_data.get("subscription_support"),
                "Subscription revenue supporting the asset value.",
            ),
            (
                "FX devaluation used",
                ltv_data.get("fx_deval_used"),
                "Devaluation applied to the asset value in the LTV calculation.",
            ),
        ]
        r = waterfall_start + 1
        rec_row = sup_row = fx_row = None
        for label, value, note in waterfall:
            ltv_ws.cell(row=r, column=1, value=label)
            ltv_ws.cell(row=r, column=2, value=value)
            ltv_ws.cell(row=r, column=3, value=note)
            if label.startswith("Recoverability"):
                rec_row = r
            elif label.startswith("Subscription"):
                sup_row = r
            elif label.startswith("FX devaluation"):
                fx_row = r
            r += 1
        ltv_ws.cell(row=r, column=1, value="Asset LTV (max 95%)")
        # Only a live formula when all three inputs are actually present - a
        # blank cell would be read as 0 by Excel, silently producing a wrong
        # (too low) LTV rather than the honest engine value.
        _wf_inputs = (
            ltv_data.get("recoverability_pctile_95"),
            ltv_data.get("subscription_support"),
            ltv_data.get("fx_deval_used"),
        )
        if rec_row and sup_row and fx_row and all(v is not None for v in _wf_inputs):
            ltv_ws.cell(
                row=r,
                column=2,
                value=f"=MIN(0.95,(B{rec_row}+B{sup_row})/(1+B{fx_row}))",
            )
        else:
            ltv_ws.cell(row=r, column=2, value=ltv_data.get("ltv_asset_value"))
        ltv_ws.cell(
            row=r,
            column=3,
            value="= min(95%, (recoverability + subscription support) / (1 + FX devaluation)).",
        )

        ts_df = pd.DataFrame(list(ts.items()), columns=["Metric", "Value"])
        ts_df.to_excel(writer, sheet_name="TS Covenants", index=False)

        # ---- Data Checks (app's Data Checks tab) ----
        _write_checks_sheet(writer, checks)

        # rental_analysis.general_analysis.describe() returns {"Rows", "Columns"}
        # (two counts), not the lending version's {"shape", "columns"} - reading
        # 'shape' here raised KeyError on every rental export. Write whatever
        # keys describe() actually returns so the two can't drift apart again.
        ga_summary = general_analysis(df)
        ga_df = pd.DataFrame(list(ga_summary.items()), columns=["Property", "Value"])
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


def _write_frame(ws, df, row, title=None, index=True):
    """Write `df` (with its index as the first column) starting at `row`, under
    an optional section title; returns the next free row after a blank gap."""
    if title:
        ws.cell(row=row, column=1, value=title)
        row += 1
    frame = df.reset_index() if index else df
    for c, name in enumerate(frame.columns, start=1):
        ws.cell(row=row, column=c, value=str(name))
    for r, values in enumerate(frame.itertuples(index=False), start=row + 1):
        for c, v in enumerate(values, start=1):
            ws.cell(row=r, column=c, value=None if pd.isna(v) else v)
    return row + len(frame) + 2


def _build_revolving_export_workbook(c, general_inputs):
    """Revolving Lending export: the template's Outputs tab (fact block A:K,
    then the UE / LTV ratio block five rows below it, as in the template) with
    every ratio the fact block can express written as a live formula, plus the
    Transition Matrices sheet and the inputs the run used."""
    facts = c["facts"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Outputs"
    for col, name in enumerate(facts.columns, start=1):
        ws.cell(row=1, column=col, value=name)
    for r, values in enumerate(facts.itertuples(index=False), start=2):
        for col, v in enumerate(values, start=1):
            if col == 1:
                v = pd.Timestamp(v).to_pydatetime()
            ws.cell(row=r, column=col, value=None if pd.isna(v) else v)

    a = len(facts) + 1  # as-of row (the last fact row)
    w0 = max(2, a - int(general_inputs["ratioWindowMonths"]) + 1)
    base = a + 5  # template: last fact row 3, "UE / LTV" header row 8

    def at(template_row):
        return base + template_row - 8

    ws.cell(row=base, column=1, value="UE / LTV")
    formulas = {
        9: f'=IFERROR(1/((E{a}+D{a}-F{a})/AVERAGE(G{w0}:G{a})),"")',
        10: f'=IFERROR(SUM(D{w0}:D{a})/SUM(G{w0}:G{a}),"")',
        11: f'=IFERROR(B{at(10)}*B{at(9)},"")',
        15: f'=IFERROR(H{a}/G{a},"")',
        16: f'=IFERROR(I{a}/G{a},"")',
        17: f'=IFERROR(J{a}/H{a},"")',
        18: f'=IFERROR(J{a}/G{a},"")',
        19: f'=IFERROR(K{a}/AVERAGE(G{w0}:G{a}),"")',
        21: f'=IFERROR(B{at(20)}/G{a},"")',
        22: f"=G{a}",
        23: f"=B{a}",
        24: f'=IFERROR(G{a}/C{a},"")',
    }
    for item in c["ratios"]:
        r = at(item["row"])
        ws.cell(row=r, column=1, value=item["label"])
        ws.cell(row=r, column=2, value=formulas.get(item["row"], item["value"]))
        if item["annotation"]:
            ws.cell(row=r, column=3, value=item["annotation"])
    ws.cell(row=at(12), column=4, value="Rollrate analysis use the monthly data > ")
    ws.cell(row=at(12), column=5, value="rcl_rollrate_ecl_3.xlsx")
    row = at(24) + 3
    for note in c["warnings"]:
        ws.cell(row=row, column=1, value=note)
        row += 1

    tm = wb.create_sheet("Transition Matrices")
    row = _write_frame(tm, c["balances"], 1, "1. Balances by bucket")
    row = _write_frame(tm, c["rollRates"], row, "2. Roll rates")
    row = _write_frame(tm, c["lossFactors"], row, "3. Cumulative loss factor")
    row = _write_frame(tm, c["ecl"], row, "4. Expected credit loss", index=False)
    summary = pd.Series(c["eclSummary"], name="Value").to_frame()
    summary.index.name = "Measure"
    row = _write_frame(tm, summary, row)
    row = _write_frame(tm, c["checks"], row, "5. Data quality")
    row = _write_frame(tm, c["distribution"], row, "6. Point-in-time loss rate - distribution")
    _write_frame(tm, c["pit"], row, "7. Point-in-time loss rate - by month", index=False)

    sm = wb.create_sheet("Standard Metrics")
    std = pd.DataFrame(c["standardMetrics"])[["label", "value", "formula", "note"]]
    row = _write_frame(sm, std, 1, "1. Standard-definition metrics at the as-of month", index=False)
    row = _write_frame(
        sm, pd.DataFrame(c["unitEconomics"]), row, "2. Unit economics after costs", index=False
    )
    ew = pd.DataFrame(c["earlyWarnings"])
    if not ew.empty:
        ew = ew[["label", "now", "prior", "change", "flag"]]
    row = _write_frame(sm, ew, row, "3. Early warnings (vs lookback average)", index=False)
    _write_frame(sm, c["overTime"], row, "4. Metrics over time")

    bs = wb.create_sheet("Backtest & Stress")
    summary = pd.Series(c["backtestSummary"], name="Value").to_frame()
    summary.index.name = "Backtest summary"
    row = _write_frame(bs, summary, 1, "1. Backtest - forecast vs actual write-offs")
    row = _write_frame(bs, c["backtest"], row, index=False)
    _write_frame(bs, pd.DataFrame(c["stress"]), row, "2. Stress scenarios", index=False)

    fac = wb.create_sheet("Facility")
    adv = pd.Series(c["advance"], name="Value").to_frame()
    adv.index.name = "Advance rate"
    row = _write_frame(fac, adv, 1, "1. Advance rate = MAX(0, 1 - stress loss rate - haircut)")
    _write_frame(fac, pd.DataFrame(c["covenants"]), row, "2. Covenant tests", index=False)

    inputs_ws = wb.create_sheet("Inputs")
    inputs = pd.Series(
        {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in general_inputs.items()},
        name="Value",
    ).to_frame()
    inputs.index.name = "Input"
    _write_frame(inputs_ws, inputs, 1)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_baas_export_workbook(markets, runs, cashflow_rows, valuation_date):
    """BaaS export (values): one results row per market (with any input
    issues), the monthly net cash flows, and the market table as uploaded."""
    wb = Workbook()
    results_ws = wb.active
    results_ws.title = "Results"
    rows = []
    for r in runs:
        res = r["result"] or {}
        be = res.get("break_even") or {}
        row = {"market": r["market"], "issues": " ".join(r["issues"]) or "OK"}
        row.update({k: v for k, v in res.items() if k not in ("market", "cashflows", "break_even")})
        row["vehicles_to_break_even"] = be.get("vehicles_to_break_even", "n/a")
        row["batteries_to_break_even"] = be.get("batteries_to_break_even", "n/a")
        rows.append(row)
    results_ws.cell(row=1, column=1, value="Valuation date (month 0)")
    results_ws.cell(row=1, column=2, value=valuation_date)
    _write_frame(results_ws, pd.DataFrame(rows), 3, index=False)
    _write_frame(wb.create_sheet("Cash flows"), pd.DataFrame(cashflow_rows), 1, index=False)
    _write_frame(wb.create_sheet("Inputs"), markets, 1, index=False)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
