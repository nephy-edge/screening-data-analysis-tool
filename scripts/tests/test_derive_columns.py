"""The derived-column evaluator turns user/AI-written formulas into numbers on
a real loan tape, so a wrong answer here is a wrong answer in the analysis -
and unlike a crash, it looks fine.

That is not hypothetical. Before spreadsheet functions existed in the grammar,
asking for "the larger of fees accrued and penalties accrued" produced
`(FEES + PENALTIES + (FEES - PENALTIES)) / 2` - the algebraic max identity with
the ABS dropped, because there was no ABS. It parsed, validated, and returned
the right answer whenever the first argument happened to be larger. The
`test_max_*` cases below pin that specific failure.

Everything here is offline - no AI, no network.
"""

import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, "web_api")
from derive_columns import FormulaError, apply_derived_columns  # noqa: E402


@pytest.fixture
def tape():
    return pd.DataFrame(
        {
            "DPD": [120.0, 10.0, 95.0, 0.0],
            "PRODUCT_GROUP": ["pos", "cash", "POS", "cash"],
            "PRINCIPAL_ISSUED": [100.0, 200.0, 300.0, 400.0],
            "FEES_ACCRUED": [10.0, 2.0, 7.0, 1.0],
            "PENALTIES_ACCRUED": [2.0, 10.0, 7.0, 0.0],
            "WRITTEN_OFF": [20.0, 0.0, 0.0, 50.0],
        }
    )


def derive(df, formula, name="X"):
    return apply_derived_columns(df, [{"name": name, "formula": formula}])[name]


def test_arithmetic_and_precedence(tape):
    assert list(derive(tape, "FEES_ACCRUED + PENALTIES_ACCRUED")) == [12, 12, 14, 1]
    # Multiplication binds tighter than addition.
    assert list(derive(tape, "1 + FEES_ACCRUED * 2")) == [21, 5, 15, 3]
    assert list(derive(tape, "(1 + FEES_ACCRUED) * 2")) == [22, 6, 16, 4]


def test_leading_equals_is_stripped(tape):
    assert list(derive(tape, "=FEES_ACCRUED * 2")) == [20, 4, 14, 2]


def test_max_picks_the_larger_either_way(tape):
    """The regression the whole grammar change was for: MAX must be right when
    the SECOND argument is larger, which the old algebraic substitute was not
    (row 2 here: fees 2, penalties 10)."""
    assert list(derive(tape, "MAX(FEES_ACCRUED, PENALTIES_ACCRUED)")) == [10, 10, 7, 1]


def test_min_picks_the_smaller_either_way(tape):
    assert list(derive(tape, "MIN(FEES_ACCRUED, PENALTIES_ACCRUED)")) == [2, 2, 7, 0]


def test_if_is_row_wise(tape):
    assert list(derive(tape, "IF(DPD>90, 1, 0)")) == [1, 0, 1, 0]
    assert list(derive(tape, "IF(DPD>=95, PRINCIPAL_ISSUED, 0)")) == [100, 0, 300, 0]


def test_comparison_operators(tape):
    assert list(derive(tape, "IF(DPD<>0, 1, 0)")) == [1, 1, 1, 0]
    assert list(derive(tape, "IF(DPD<=10, 1, 0)")) == [0, 1, 0, 1]


def test_text_comparison_is_case_insensitive(tape):
    """Spreadsheets match text case-insensitively, and this tape really does
    carry both "pos" and "POS"."""
    assert list(derive(tape, 'IF(PRODUCT_GROUP="pos", 1, 0)')) == [1, 0, 1, 0]


def test_abs_and_round(tape):
    assert list(derive(tape, "ABS(PENALTIES_ACCRUED - FEES_ACCRUED)")) == [8, 8, 0, 1]
    assert list(derive(tape, "ROUND(FEES_ACCRUED / 3, 2)")) == [3.33, 0.67, 2.33, 0.33]


def test_aggregate_repeats_on_every_row(tape):
    """An aggregate is one number filled down the column, as a spreadsheet
    formula over absolute ranges would be."""
    assert list(derive(tape, "SUM(PRINCIPAL_ISSUED)")) == [1000] * 4


def test_share_of_book(tape):
    assert list(derive(tape, "PRINCIPAL_ISSUED / SUM(PRINCIPAL_ISSUED)")) == [
        0.1,
        0.2,
        0.3,
        0.4,
    ]


def test_sumifs_both_syntaxes_agree(tape):
    """The spreadsheet form and the condition form must give the same answer -
    the model writes both."""
    excel = list(derive(tape, 'SUMIFS(PRINCIPAL_ISSUED, DPD, ">90")'))
    natural = list(derive(tape, "SUMIFS(PRINCIPAL_ISSUED, DPD>90)"))
    assert excel == natural == [400] * 4


def test_sumifs_multiple_criteria_are_anded(tape):
    # >90 DPD is rows 0 and 2; both are "pos"/"POS", so principal 100 + 300.
    got = list(derive(tape, 'SUMIFS(PRINCIPAL_ISSUED, DPD, ">90", PRODUCT_GROUP, "pos")'))
    assert got == [400] * 4
    # Narrowing to "cash" excludes both, so the sum is zero, not the whole book.
    got = list(derive(tape, 'SUMIFS(PRINCIPAL_ISSUED, DPD, ">90", PRODUCT_GROUP, "cash")'))
    assert got == [0] * 4


def test_countif_and_averageif(tape):
    assert list(derive(tape, 'COUNTIF(DPD, ">90")')) == [2] * 4
    assert list(derive(tape, 'AVERAGEIF(DPD, ">90", PRINCIPAL_ISSUED)')) == [200] * 4


def test_npl_ratio_combines_aggregates(tape):
    got = list(derive(tape, 'SUMIFS(PRINCIPAL_ISSUED, DPD, ">90") / SUM(PRINCIPAL_ISSUED)'))
    assert got == [0.4] * 4


def test_division_by_zero_is_nan_not_a_dead_column(tape):
    """A zero denominator must lose only that row. Raising killed the whole
    column, which made "recovery rate" unusable on any tape where some loans
    have no write-off - i.e. every tape."""
    got = derive(tape, "PRINCIPAL_ISSUED / WRITTEN_OFF")
    assert got.iloc[0] == 5.0
    assert np.isnan(got.iloc[1]) and np.isnan(got.iloc[2])
    assert got.iloc[3] == 8.0


def test_currency_formatted_text_is_cleaned():
    df = pd.DataFrame({"A": ["$1,200", "85,000", "(300)"], "B": [2, 2, 2]})
    assert list(derive(df, "A / B")) == [600.0, 42500.0, -150.0]


def test_unknown_function_is_rejected(tape):
    with pytest.raises(FormulaError, match="Unknown function"):
        derive(tape, "VLOOKUP(DPD, PRINCIPAL_ISSUED, 2)")


def test_missing_column_is_rejected(tape):
    with pytest.raises(FormulaError, match="missing columns"):
        derive(tape, "DPD + NOT_A_COLUMN")


def test_wrong_arity_is_rejected(tape):
    with pytest.raises(FormulaError, match="arguments"):
        derive(tape, "ABS(DPD, PRINCIPAL_ISSUED)")


def test_cannot_overwrite_an_existing_column(tape):
    with pytest.raises(FormulaError, match="overwrite"):
        derive(tape, "DPD * 2", name="DPD")


def test_later_formula_can_use_an_earlier_derived_column(tape):
    out = apply_derived_columns(
        tape,
        [
            {"name": "Expected Fee", "formula": "FEES_ACCRUED + PENALTIES_ACCRUED"},
            {"name": "Fee Rate", "formula": "Expected Fee / PRINCIPAL_ISSUED"},
        ],
    )
    assert list(out["Fee Rate"]) == [0.12, 0.06, 14 / 300, 0.0025]


def test_no_code_execution(tape):
    """The parser must never reach Python semantics, whatever is thrown at it."""
    for hostile in [
        "__import__('os').system('echo hi')",
        "DPD.__class__",
        "1; import os",
        "eval('1+1')",
    ]:
        with pytest.raises(FormulaError):
            derive(tape, hostile)


def test_min_max_over_one_column_aggregate_like_excel(tape):
    """Excel's own split: MIN over a single range is that column's minimum,
    while MIN(a, b) compares arguments row by row. The one-argument case
    originally fell through to the row-wise path and returned the column
    unchanged — which looks like it worked."""
    assert list(derive(tape, "MAX(PRINCIPAL_ISSUED)")) == [400] * 4
    assert list(derive(tape, "MIN(PRINCIPAL_ISSUED)")) == [100] * 4
    assert list(derive(tape, "PRINCIPAL_ISSUED / MAX(PRINCIPAL_ISSUED)")) == [
        0.25,
        0.5,
        0.75,
        1.0,
    ]


@pytest.fixture
def dated():
    return pd.DataFrame(
        {
            # Row 1 is the case that matters: 31 Jan + 1 month must clamp to
            # 29 Feb (a leap year), not roll into March.
            "ORIGINATION_DATE": ["2024-12-23", "2024-01-31", "2023-03-15", "not a date"],
            "TERM_MONTHS": [12.0, 1.0, 24.0, 6.0],
            "AMOUNT_TEXT": ["100", "200", "300", "400"],
        }
    )


def test_edate_adds_months_and_clamps_to_month_end(dated):
    got = [str(v)[:10] for v in derive(dated, "EDATE(ORIGINATION_DATE, TERM_MONTHS)")]
    assert got[:3] == ["2025-12-23", "2024-02-29", "2025-03-15"]
    assert got[3] == "NaT"  # unparseable date costs that row, not the column


def test_a_month_is_not_thirty_days(dated):
    """The reason EDATE exists rather than `+ 30 * months`: adding 30 days to
    31 Jan gives 1 Mar, and the drift compounds over a 24-month term."""
    edate = derive(dated, "EDATE(ORIGINATION_DATE, TERM_MONTHS)")
    naive = derive(dated, "DATEVALUE(ORIGINATION_DATE) + 30 * TERM_MONTHS")
    assert str(edate.iloc[2])[:10] == "2025-03-15"
    assert str(naive.iloc[2])[:10] != "2025-03-15"


def test_subtracting_two_dates_gives_days(dated):
    """This is how a term in days is written - the field the Lending engine
    derives its maturity from."""
    got = derive(dated, "EDATE(ORIGINATION_DATE, TERM_MONTHS) - ORIGINATION_DATE")
    assert list(got[:3]) == [365.0, 29.0, 731.0]


def test_eomonth_and_date_parts(dated):
    assert [str(v)[:10] for v in derive(dated, "EOMONTH(ORIGINATION_DATE, 0)")][:2] == [
        "2024-12-31",
        "2024-01-31",
    ]
    assert list(derive(dated, "YEAR(ORIGINATION_DATE)")[:2]) == [2024.0, 2024.0]
    assert list(derive(dated, "MONTH(ORIGINATION_DATE)")[:2]) == [12.0, 1.0]


def test_datedif_units(dated):
    end = "EDATE(ORIGINATION_DATE, TERM_MONTHS)"
    assert list(derive(dated, f'DATEDIF(ORIGINATION_DATE, {end}, "D")')[:1]) == [365.0]
    assert list(derive(dated, f'DATEDIF(ORIGINATION_DATE, {end}, "M")')[:1]) == [12.0]
    assert list(derive(dated, f'DATEDIF(ORIGINATION_DATE, {end}, "Y")')[:1]) == [1.0]


def test_numeric_text_is_never_mistaken_for_dates(dated):
    """Date promotion must not swallow a column of numeric strings just
    because a date is on the other side of the operator."""
    assert list(derive(dated, "AMOUNT_TEXT * 2")) == [200.0, 400.0, 600.0, 800.0]


def test_iferror_fills_the_blanks(tape):
    """The companion to division-by-zero yielding NaN."""
    got = derive(tape, "IFERROR(PRINCIPAL_ISSUED / WRITTEN_OFF, 0)")
    assert list(got) == [5.0, 0.0, 0.0, 8.0]


def test_text_columns_compare_as_text():
    """Two text columns used to compare as NaN = NaN, always False."""
    df = pd.DataFrame({"A": ["POS", "NEG", "pos"], "B": ["POS", "POS", "POS"]})
    out = apply_derived_columns(df, [{"name": "SAME", "formula": "IF({A} = {B}, 1, 0)"}])
    assert out["SAME"].tolist() == [1, 0, 1]


@pytest.mark.parametrize(
    "formula",
    ["ROUND({N}, {N})", " + ".join(["1"] * 3000)],
    ids=["round-by-column", "deeply-nested"],
)
def test_evaluation_errors_become_formula_errors(formula):
    """Anything the evaluator throws must surface as a 4xx FormulaError, not a 500."""
    df = pd.DataFrame({"N": [1.5, 2.5]})
    with pytest.raises(FormulaError):
        apply_derived_columns(df, [{"name": "X", "formula": formula}])


def test_max_of_a_date_column_is_the_latest_date_for_case_when_style_rules():
    # "total due = case when expected completion date < max date then total else 0"
    df = pd.DataFrame(
        {
            "ecd": ["2024-01-01", "2025-06-01", "2024-03-01"],
            "disb": ["2023-01-01", "2024-06-01", "2024-02-01"],
            "total": [10, 20, 30],
        }
    )
    out = apply_derived_columns(df, [{"name": "td", "formula": "IF(ecd < MAX(disb), total, 0)"}])
    assert out["td"].tolist() == [10.0, 0.0, 30.0]
