"""Deterministic, safe evaluation of user-derived column formulas for the
React app's "Ask AI to derive a column" feature.

A derived column is ``{name, formula}`` where ``formula`` is a spreadsheet-style
expression over other source-column names - e.g. "Gross Loan Value - Principal
Value", or "SUMIFS(PRINCIPAL_ISSUED, DPD, \\">90\\")". The AI (DeepSeek) only
*generates* the formula text; this module is the only thing that ever turns it
into numbers, so arithmetic stays deterministic and auditable (an LLM never
computes values - see AGENTS.md).

Safety: no eval/exec, no Python function calls, no arbitrary code. The formula
goes through a hand-written recursive-descent parser whose leaves are numeric
literals, string literals, TRUE/FALSE and column references, and whose
operators and functions are only the ones named in ``_FUNCTIONS`` below.
Anything else fails validation. Column names may contain letters, digits,
spaces, underscores, hyphens and full stops (e.g. "Gross Loan Value"); braces
are also accepted as an explicit column-ref marker ({Total Paid}).

If the user has written a spreadsheet-style formula with a leading ``=``
(e.g. "=A-B"), the leading ``=`` is stripped.

TWO KINDS OF FUNCTION, and the distinction matters:

  * ROW-WISE (IF, MIN, MAX, ABS, ROUND) compute a value per loan. IF(DPD>90,
    1, 0) gives each loan its own flag.

  * AGGREGATE (SUM, COUNT, AVERAGE, and the *IF/*IFS family) collapse a whole
    column to ONE number, which is then repeated down every row. That is
    exactly what a spreadsheet does when a SUMIFS over absolute ranges is
    filled down a column, and it is how the SC Analysis templates use them -
    e.g. SUMIFS('Data Input'!L:L, W:W, TRUE) is a portfolio total, not a
    per-loan value. So `PRINCIPAL_ISSUED / SUM(PRINCIPAL_ISSUED)` is each
    loan's share of the book, which is usually what people mean.

Evaluation is VECTORISED over pandas, not row-by-row. The previous version
looped `for i in out.index` in Python and called a scalar evaluator per row,
which is fine on a 5,000-row tape and unusable on a 3.4M-row one.

Division by zero yields NaN for the affected rows rather than raising. The
previous version raised, which killed the entire column: a "recovery rate =
PRINCIPAL_RECOVERED / PRINCIPAL_WRITTEN_OFF" formula is perfectly reasonable
and most loans have no write-off, so one zero should not lose every value.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class FormulaError(ValueError):
    """Raised when a derived-column formula is invalid or cannot be evaluated."""


# name -> (min_args, max_args or None for "any"), used to reject a call with
# the wrong shape at parse time rather than mid-evaluation.
_FUNCTIONS: dict[str, tuple[int, int | None]] = {
    # row-wise
    "IF": (2, 3),
    "MIN": (1, None),
    "MAX": (1, None),
    "ABS": (1, 1),
    "ROUND": (1, 2),
    "AND": (1, None),
    "OR": (1, None),
    "NOT": (1, 1),
    "IFERROR": (2, 2),
    # dates
    "EDATE": (2, 2),
    "EOMONTH": (2, 2),
    "YEAR": (1, 1),
    "MONTH": (1, 1),
    "DAY": (1, 1),
    "DATEDIF": (2, 3),
    "DATEVALUE": (1, 1),
    "TODAY": (0, 0),
    # aggregate
    "SUM": (1, 1),
    "COUNT": (1, 1),
    "AVERAGE": (1, 1),
    "SUMIF": (2, 3),
    "SUMIFS": (2, None),
    "COUNTIF": (2, 2),
    "COUNTIFS": (1, None),
    "AVERAGEIF": (2, 3),
    "AVERAGEIFS": (2, None),
}

_AGGREGATES = {
    "SUM",
    "COUNT",
    "AVERAGE",
    "SUMIF",
    "SUMIFS",
    "COUNTIF",
    "COUNTIFS",
    "AVERAGEIF",
    "AVERAGEIFS",
}

_COMPARISONS = {"=", "<>", ">", ">=", "<", "<="}


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------

_NAME_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_. ")
_DIGITS = set("0123456789")


def _tokenize(formula: str) -> list:
    """Scan a formula into tokens.

    Column names may be bare (letters, digits, spaces, underscores, dots) or
    braced to allow other characters. `-` is ALWAYS an operator, never part of
    a bare column name. Function names are not tokenized specially: they come
    through as bare names and the parser decides, by looking for a following
    '(', whether a name is a call or a column - which keeps a column that
    happens to be called "Min" working.
    """
    tokens: list = []
    s = formula.strip()
    if s.startswith("="):
        s = s[1:].strip()
    if not s:
        raise FormulaError("The formula is empty.")
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t":
            i += 1
            continue
        if c in _DIGITS:
            j = i
            while j < n and (s[j] in _DIGITS or s[j] == "."):
                j += 1
            tokens.append(("num", s[i:j]))
            i = j
            continue
        if c in "\"'":
            # String literal, for criteria like ">90" and text comparisons.
            j = s.find(c, i + 1)
            if j == -1:
                raise FormulaError(f"Unclosed {c} string in the formula.")
            tokens.append(("str", s[i + 1 : j]))
            i = j + 1
            continue
        if c == "{":
            j = s.find("}", i + 1)
            if j == -1:
                raise FormulaError("Unclosed '{' in the formula.")
            name = s[i + 1 : j].strip()
            if not name:
                raise FormulaError("Empty column reference in the formula.")
            tokens.append(("col", name))
            i = j + 1
            continue
        # Two-character comparisons first, so "<=" is not read as "<" then "=".
        if s[i : i + 2] in ("<>", ">=", "<="):
            tokens.append((s[i : i + 2], s[i : i + 2]))
            i += 2
            continue
        if c in "()+-*/,<>=":
            tokens.append((c, c))
            i += 1
            continue
        if c in _NAME_CHARS:
            j = i
            while j < n and s[j] in _NAME_CHARS:
                j += 1
            name = s[i:j].strip()
            if not name:
                raise FormulaError(f"Unexpected token near: {s[i : i + 12]!r}.")
            tokens.append(("name", name))
            i = j
            continue
        raise FormulaError(f"Invalid character near: {s[i : i + 12]!r}.")
    return tokens


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


class _Parser:
    """Recursive-descent parser for the grammar:

    expr   := cmp
    cmp    := add (('='|'<>'|'>'|'>='|'<'|'<=') add)?
    add    := mul (('+'|'-') mul)*
    mul    := unary (('*'|'/') unary)*
    unary  := '-' unary | atom
    atom   := NUMBER | STRING | TRUE | FALSE
            | NAME '(' args ')' | '(' expr ')' | COLREF
    args   := expr (',' expr)*
    """

    def __init__(self, tokens: list):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else ("end", None)

    def next(self):
        tok = self.peek()
        self.pos += 1
        return tok

    def parse(self):
        node = self.expr()
        if self.peek()[0] != "end":
            raise FormulaError("Unexpected characters after the formula.")
        return node

    def expr(self):
        return self.cmp()

    def cmp(self):
        node = self.add()
        if self.peek()[0] in _COMPARISONS:
            op = self.next()[0]
            return ("cmp", op, node, self.add())
        return node

    def add(self):
        node = self.mul()
        while self.peek()[0] in ("+", "-"):
            op = self.next()[0]
            node = ("binop", op, node, self.mul())
        return node

    def mul(self):
        node = self.unary()
        while self.peek()[0] in ("*", "/"):
            op = self.next()[0]
            node = ("binop", op, node, self.unary())
        return node

    def unary(self):
        if self.peek()[0] == "-":
            self.next()
            return ("neg", self.unary())
        return self.atom()

    def atom(self):
        kind, val = self.next()
        if kind == "num":
            return ("num", float(val))
        if kind == "str":
            return ("str", val)
        if kind == "col":
            return ("col", val)
        if kind == "(":
            node = self.expr()
            if self.peek()[0] != ")":
                raise FormulaError("Unbalanced parentheses in the formula.")
            self.next()
            return node
        if kind == "name":
            upper = val.upper()
            if self.peek()[0] == "(":
                if upper not in _FUNCTIONS:
                    raise FormulaError(
                        f'Unknown function "{val}". Available: {", ".join(sorted(_FUNCTIONS))}.'
                    )
                self.next()
                args = self._args()
                lo, hi = _FUNCTIONS[upper]
                if len(args) < lo or (hi is not None and len(args) > hi):
                    want = f"{lo}" if hi == lo else f"{lo}-{hi if hi else 'any'}"
                    raise FormulaError(f"{upper} takes {want} arguments, got {len(args)}.")
                return ("func", upper, args)
            if upper in ("TRUE", "FALSE"):
                return ("bool", upper == "TRUE")
            return ("col", val)
        raise FormulaError(f"Unexpected token in the formula: {val or '<end>'}.")

    def _args(self):
        args = []
        if self.peek()[0] == ")":
            self.next()
            return args
        while True:
            args.append(self.expr())
            kind = self.next()[0]
            if kind == ")":
                return args
            if kind != ",":
                raise FormulaError("Expected ',' or ')' in the function arguments.")


def _parse_formula(formula: str):
    try:
        return _Parser(_tokenize(formula)).parse()
    except FormulaError:
        raise
    except Exception as exc:  # defensive - never let a bad formula 500
        raise FormulaError(f"Could not parse the formula ({exc}).") from exc


def _collect_cols(node, acc=None):
    """Every column name a parsed formula references, so the caller can reject
    a formula naming a column the file doesn't have before evaluating it."""
    if acc is None:
        acc = []
    kind = node[0]
    if kind == "col":
        acc.append(node[1])
    elif kind in ("binop", "cmp"):
        _collect_cols(node[2], acc)
        _collect_cols(node[3], acc)
    elif kind == "neg":
        _collect_cols(node[1], acc)
    elif kind == "func":
        for arg in node[2]:
            _collect_cols(arg, acc)
    return acc


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def _parse_numeric_series(series) -> pd.Series:
    """Return a numeric pandas Series for a column, cleaning currency / comma /
    percent formatting (e.g. "85,000", "$1,200", "3%") the same way the rest of
    the app does (data_normalize._clean_numeric). Non-numeric cells become
    NaN."""
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    cleaned = series.astype(str).str.strip()
    cleaned = cleaned.str.replace(r"[\$,£€\s%]", "", regex=True)
    cleaned = cleaned.str.replace(r"\(([^)]*)\)", r"-\1", regex=True)
    cleaned = cleaned.str.replace(",", "", regex=False)
    return pd.to_numeric(cleaned, errors="coerce")


def _num(value):
    """Coerce an evaluated operand to numbers for arithmetic."""
    if isinstance(value, pd.Series):
        return _parse_numeric_series(value)
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, str):
        n = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        return float(n) if pd.notna(n) else float("nan")
    return float(value)


def _as_bool(value, index):
    """Coerce an evaluated operand to a boolean mask over `index`."""
    if isinstance(value, pd.Series):
        if value.dtype == bool:
            return value
        return _parse_numeric_series(value).fillna(0) != 0
    truth = bool(value) if not isinstance(value, str) else value.strip().upper() == "TRUE"
    return pd.Series(truth, index=index)


def _dates(value):
    """Coerce an operand to datetimes. Unparseable cells become NaT rather
    than raising, matching how _parse_numeric_series treats bad numbers - one
    malformed date should cost that row, not the column."""
    if isinstance(value, pd.Series):
        if pd.api.types.is_datetime64_any_dtype(value):
            return value
        return pd.to_datetime(value, errors="coerce")
    return pd.to_datetime(value, errors="coerce")


def _is_dates(value) -> bool:
    return isinstance(value, pd.Series) and pd.api.types.is_datetime64_any_dtype(value)


def _maybe_dates(value):
    """Promote a text column to real dates, but only when it clearly is one.

    Raw loan-tape columns arrive as strings, so ORIGINATION_DATE is text until
    something parses it. Without this, `EDATE(ORIGINATION_DATE, TERM) -
    ORIGINATION_DATE` - the natural way to write a term in days - had a date
    on one side and text on the other, sent the text through _num, and
    produced NaT for every row.

    Only called when the OTHER operand is already a date, so the to_datetime
    attempt never runs on ordinary arithmetic over a multi-million-row tape.
    The column must parse as dates AND not parse as numbers, so a column of
    numeric strings is never mistaken for dates.
    """
    if not isinstance(value, pd.Series) or _is_dates(value):
        return value
    if pd.api.types.is_numeric_dtype(value):
        return value
    as_dates = pd.to_datetime(value, errors="coerce")
    if as_dates.notna().any() and not _parse_numeric_series(value).notna().any():
        return as_dates
    return value


def _shift_months(start, months, to_month_end: bool):
    """EDATE / EOMONTH. Month arithmetic is not `+30 days`: EDATE keeps the
    day-of-month and CLAMPS to the month's length, so 31 Jan + 1 month is 28
    Feb, not 3 March. Done with period arithmetic so it stays vectorised and
    NaT-safe."""
    s = _dates(start)
    m = _num(months)
    if not isinstance(m, pd.Series):
        m = pd.Series(m, index=s.index)
    valid = s.notna() & m.notna()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if not valid.any():
        return out
    base = s[valid].dt.to_period("M") + m[valid].round().astype("int64")
    month_end = base.dt.end_time.dt.normalize()
    if to_month_end:
        out.loc[valid] = month_end
        return out
    day = np.minimum(s[valid].dt.day, month_end.dt.day)
    out.loc[valid] = base.dt.start_time + pd.to_timedelta(day - 1, unit="D")
    return out


def _text(value):
    """Upper-cased, trimmed text for a comparison operand - spreadsheets match
    text case-insensitively, so "POS" = "pos"."""
    if isinstance(value, pd.Series):
        return value.astype(str).str.strip().str.upper()
    return str(value).strip().upper()


def _compare(op, left, right):
    """Apply a comparison, numerically when both sides look numeric and
    case-insensitively as text otherwise - matching spreadsheet behaviour,
    where "POS" = "pos"."""
    ln, rn = _num(left), _num(right)

    # A column counts as numeric when any value parses (or it's all blank); a
    # text column like POS/NEG parses to all-NaN and must compare as text.
    def ok(raw, n):
        if isinstance(n, pd.Series):
            return bool(n.notna().any() or raw.isna().all())
        return not np.isnan(n)

    numeric = ok(left, ln) and ok(right, rn)
    if numeric:
        a, b = ln, rn
    else:
        a = _text(left)
        b = _text(right)
    if op == "=":
        return a == b
    if op == "<>":
        return a != b
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    if op == "<":
        return a < b
    return a <= b if op == "<=" else a < b


def _criterion_mask(values, criterion, index):
    """Turn a spreadsheet criterion into a boolean mask over `values`.

    Accepts the spreadsheet forms: ">90", "<=30", "<>0", "=pos", a bare number
    or a bare string (equality). A boolean Series passes straight through, for
    the lone-condition form handled in _pairs_mask.
    """
    if isinstance(criterion, pd.Series) and criterion.dtype == bool:
        return criterion
    if isinstance(criterion, bool):
        return _compare("=", values, criterion)
    if isinstance(criterion, str):
        text = criterion.strip()
        for op in ("<>", ">=", "<=", ">", "<", "="):
            if text.startswith(op):
                return _compare(op, values, text[len(op) :].strip())
        return _compare("=", values, text)
    return _compare("=", values, criterion)


def _eval(node, cols: dict, index):
    """Evaluate a parsed node to a pandas Series or a scalar."""
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "str":
        return node[1]
    if kind == "bool":
        return node[1]
    if kind == "col":
        try:
            return cols[node[1]]
        except KeyError:
            raise FormulaError(
                f'Formula references a column that isn\'t present: "{node[1]}".'
            ) from None
    if kind == "neg":
        return -_num(_eval(node[1], cols, index))
    if kind == "cmp":
        return _compare(node[1], _eval(node[2], cols, index), _eval(node[3], cols, index))
    if kind == "binop":
        op = node[1]
        raw_a = _eval(node[2], cols, index)
        raw_b = _eval(node[3], cols, index)
        # Spreadsheets treat a date as a serial number, so date - date is a
        # span in days and date +/- n shifts by n days. Without this, any
        # formula touching a date silently produced NaN (dates coerce to NaN
        # through _num), which is how a maturity-date column came out empty.
        if _is_dates(raw_a) or _is_dates(raw_b):
            raw_a, raw_b = _maybe_dates(raw_a), _maybe_dates(raw_b)
            if op in ("+", "-") and _is_dates(raw_a) and _is_dates(raw_b):
                if op == "-":
                    return (raw_a - raw_b).dt.total_seconds() / 86400.0
            elif op in ("+", "-") and _is_dates(raw_a):
                days = pd.to_timedelta(_num(raw_b), unit="D")
                return raw_a + days if op == "+" else raw_a - days
            elif op == "+" and _is_dates(raw_b):
                return raw_b + pd.to_timedelta(_num(raw_a), unit="D")
            raise FormulaError(
                "That combination of a date and an arithmetic operator isn't "
                "supported - subtract two dates for a span in days, or add/"
                "subtract a number of days."
            )
        a = _num(raw_a)
        b = _num(raw_b)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        # Division: zero denominators become NaN for those rows rather than
        # failing the whole column - see the module docstring.
        if isinstance(b, pd.Series):
            b = b.replace(0, np.nan)
        elif b == 0:
            return float("nan") if not isinstance(a, pd.Series) else pd.Series(np.nan, index=index)
        with np.errstate(divide="ignore", invalid="ignore"):
            return a / b
    if kind == "func":
        return _eval_func(node[1], node[2], cols, index)
    raise FormulaError("Unrecognised formula element.")


def _pairs_mask(args, cols, index, start):
    """AND together the (criteria_range, criterion) pairs of a *IFS call.

    A trailing argument with no partner is taken as a boolean condition on its
    own, so both the spreadsheet form SUMIFS(X, DPD, ">90") and the form the
    model tends to write unprompted, SUMIFS(X, DPD>90), do the same thing.
    Rejecting the second on an arity technicality would just look broken.
    """
    mask = pd.Series(True, index=index)
    i = start
    while i < len(args):
        rng = _eval(args[i], cols, index)
        if i + 1 >= len(args):
            mask &= _as_bool(rng, index)
            break
        mask &= _criterion_mask(rng, _eval(args[i + 1], cols, index), index)
        i += 2
    return mask


def _eval_func(name, args, cols, index):
    ev = [_eval(a, cols, index) for a in args]

    if name == "IF":
        cond = _as_bool(ev[0], index)
        yes = _num(ev[1])
        no = _num(ev[2]) if len(ev) > 2 else 0.0
        return pd.Series(np.where(cond, yes, no), index=index)
    if name == "ABS":
        return abs(_num(ev[0]))
    if name == "ROUND":
        digits = int(_num(ev[1])) if len(ev) > 1 else 0
        value = _num(ev[0])
        return value.round(digits) if isinstance(value, pd.Series) else round(value, digits)
    if name in ("MIN", "MAX"):
        # Excel's own split, reproduced: MIN over a single range is the
        # aggregate minimum of that column, while MIN(a, b, ...) compares its
        # arguments row by row. Treating the one-argument case as row-wise
        # returned the column unchanged, which looks like it worked.
        if len(ev) == 1:
            col = _num(ev[0])
            if isinstance(col, pd.Series):
                return float(col.min() if name == "MIN" else col.max())
            return float(col)
        frame = pd.concat([pd.Series(_num(v), index=index) for v in ev], axis=1)
        return frame.min(axis=1) if name == "MIN" else frame.max(axis=1)
    if name in ("AND", "OR"):
        masks = [_as_bool(v, index) for v in ev]
        out = masks[0]
        for m in masks[1:]:
            out = (out & m) if name == "AND" else (out | m)
        return out
    if name == "NOT":
        return ~_as_bool(ev[0], index)
    if name == "IFERROR":
        # The companion to division-by-zero yielding NaN: this is how a caller
        # says what the blank should be instead.
        value, fallback = ev[0], ev[1]
        if _is_dates(value):
            return value.fillna(_dates(fallback))
        value = _num(value)
        fallback = _num(fallback)
        if not isinstance(value, pd.Series):
            value = pd.Series(value, index=index)
        return value.replace([np.inf, -np.inf], np.nan).fillna(fallback)

    # ---- dates ----
    if name == "DATEVALUE":
        # Forces a text column to dates, for when no other date function is
        # involved to trigger the promotion - e.g. DATEVALUE(ORIGINATION) + 30.
        return _dates(ev[0])
    if name == "TODAY":
        return pd.Series(pd.Timestamp.today().normalize(), index=index)
    if name == "EDATE":
        return _shift_months(ev[0], ev[1], to_month_end=False)
    if name == "EOMONTH":
        return _shift_months(ev[0], ev[1], to_month_end=True)
    if name in ("YEAR", "MONTH", "DAY"):
        d = _dates(ev[0])
        return getattr(d.dt, name.lower()).astype("float64")
    if name == "DATEDIF":
        start, end = _dates(ev[0]), _dates(ev[1])
        unit = str(ev[2]).strip().upper() if len(ev) > 2 else "D"
        days = (end - start).dt.total_seconds() / 86400.0
        if unit == "D":
            return days
        if unit == "M":
            return (
                (end.dt.year - start.dt.year) * 12
                + (end.dt.month - start.dt.month)
                - (end.dt.day < start.dt.day).astype(int)
            ).astype("float64")
        if unit == "Y":
            # Whole years only, like Excel: an anniversary not yet reached
            # doesn't count. MMDD as one number makes that comparison cheap
            # and vectorised.
            before_anniversary = (end.dt.month * 100 + end.dt.day) < (
                start.dt.month * 100 + start.dt.day
            )
            return ((end.dt.year - start.dt.year) - before_anniversary.astype(int)).astype(
                "float64"
            )
        raise FormulaError('DATEDIF unit must be "D", "M" or "Y".')

    # ---- aggregates: one number, repeated down the column ----
    if name == "SUM":
        return float(_num(ev[0]).sum())
    if name == "COUNT":
        return float(_num(ev[0]).notna().sum())
    if name == "AVERAGE":
        return float(_num(ev[0]).mean())
    if name == "COUNTIF":
        return float(_criterion_mask(ev[0], ev[1], index).sum())
    if name == "COUNTIFS":
        return float(_pairs_mask(args, cols, index, 0).sum())
    if name in ("SUMIF", "AVERAGEIF"):
        mask = _criterion_mask(ev[0], ev[1], index)
        target = _num(ev[2]) if len(ev) > 2 else _num(ev[0])
        picked = target[mask] if isinstance(target, pd.Series) else target
        if not isinstance(picked, pd.Series):
            return float(picked)
        return float(picked.sum() if name == "SUMIF" else picked.mean())
    if name in ("SUMIFS", "AVERAGEIFS"):
        mask = _pairs_mask(args, cols, index, 1)
        target = _num(ev[0])[mask]
        return float(target.sum() if name == "SUMIFS" else target.mean())
    raise FormulaError(f'Unknown function "{name}".')


def apply_derived_columns(df: pd.DataFrame, derived_columns: list) -> pd.DataFrame:
    """Return `df` plus one new column per entry in `derived_columns`
    [{name, formula}], computed deterministically. Preserves the base df (works
    on a copy); re-uses any earlier derived column for a later formula."""
    if not derived_columns:
        return df
    out = df.copy()
    seen = dict(out.to_dict(orient="series"))
    for entry in derived_columns:
        name = (entry.get("name") or "").strip()
        formula = (entry.get("formula") or "").strip()
        if not name:
            raise FormulaError("A derived column is missing its name.")
        if not formula:
            raise FormulaError(f'Derived column "{name}" has no formula.')
        if name in out.columns:
            raise FormulaError(f'Derived column "{name}" would overwrite an existing column.')
        node = _parse_formula(formula)
        try:
            missing = [c for c in _collect_cols(node) if c not in seen]
            if missing:
                raise FormulaError(
                    f'Formula for "{name}" references missing columns: {", ".join(missing)}.'
                )
            value = _eval(node, seen, out.index)
        except FormulaError:
            raise
        except Exception as exc:  # defensive - never let a bad formula 500
            raise FormulaError(f'Could not evaluate "{name}" ({type(exc).__name__}).') from exc
        if not isinstance(value, pd.Series):
            # A formula that is purely aggregate (or constant) still produces a
            # column - the same number on every row, as filling a spreadsheet
            # formula down would.
            value = pd.Series(value, index=out.index)
        if value.dtype == bool:
            value = value.astype(float)
        out[name] = value.replace([np.inf, -np.inf], np.nan)
        seen[name] = out[name]
    return out
