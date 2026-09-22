"""Deterministic, safe evaluation of user-derived column formulas for the
React app's "Ask AI to derive a column" feature.

A derived column is ``{name, formula}`` where ``formula`` is a small arithmetic
expression over other source-column names - e.g. "Gross Loan Value - Principal
Value". The AI (DeepSeek) only *generates* the formula text; this module is the
only thing that ever turns it into numbers, so arithmetic stays deterministic
and auditable (an LLM never computes values - see AGENTS.md).

Safety: no eval/exec, no function calls, no arbitrary code. The formula is run
through a hand-written recursive-descent parser whose leaves are only numeric
literals and column references and whose operators are only ``+ - * / ( )``.
Anything else fails validation. Column names may contain letters, digits,
spaces, underscores, hyphens and full stops (e.g. "Gross Loan Value",
"Total Paid"); braces are also accepted as an explicit column-ref marker.

If the user has already written a spreadsheet-style formula with a leading
``=`` (e.g. "=A-B" or "=Principal-Expected Fees"), the leading ``=`` is
stripped. A formula that references a non-existent column, uses an invalid
operator, or would divide by zero raises ``FormulaError`` so the caller can
show a clear message instead of silently returning garbage.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd


class FormulaError(ValueError):
    """Raised when a derived-column formula is invalid or cannot be evaluated."""


class _Parser:
    """Recursive-descent parser for the tiny arithmetic grammar:
    expr := term (('+'|'-') term)*
    term := factor (('*'|'/') factor)*
    factor := NUMBER | '(' expr ')' | '-' factor | COLREF
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
        node = self.term()
        while self.peek()[0] in ("+", "-"):
            op = self.next()[0]
            rhs = self.term()
            node = ("binop", op, node, rhs)
        return node

    def term(self):
        node = self.factor()
        while self.peek()[0] in ("*", "/"):
            op = self.next()[0]
            rhs = self.factor()
            node = ("binop", op, node, rhs)
        return node

    def factor(self):
        kind, val = self.next()
        if kind == "num":
            return ("num", float(val))
        if kind == "col":
            return ("col", val)
        if kind == "(":
            node = self.expr()
            if self.peek()[0] != ")":
                raise FormulaError("Unbalanced parentheses in the formula.")
            self.next()
            return node
        if kind == "-":
            return ("neg", self.factor())
        raise FormulaError(f"Unexpected token in the formula: {val or '<end>'}.")


_NAME_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_. ")
_DIGITS = set("0123456789")


def _tokenize(formula: str) -> list:
    """Scan a formula into tokens. Column names may be bare (letters, digits,
    spaces, underscores, dots - e.g. "Principal Value") or braced to allow other
    characters ({Gross Loan Value}). The arithmetic operators + - * / and
    parentheses are single-character tokens; `-` is ALWAYS an operator, never
    part of a bare column name."""
    tokens: list = []
    s = formula.strip()
    if s.startswith("="):
        s = s[1:].strip()
    if not s:
        raise FormulaError("The formula is empty.")
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c in " \t":
            i += 1
            continue
        if c in "0123456789":
            j = i
            while j < n and (s[j] in _DIGITS or s[j] == "."):
                j += 1
            tokens.append(("num", s[i:j]))
            i = j
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
        if c in "()+-*/":
            tokens.append((c, c))
            i += 1
            continue
        if c in _NAME_CHARS:
            j = i
            while j < n and s[j] in _NAME_CHARS:
                j += 1
            name = s[i:j].strip()
            if not name:
                raise FormulaError(f"Unexpected token near: {s[i:i+12]!r}.")
            tokens.append(("col", name))
            i = j
            continue
        raise FormulaError(f"Invalid character near: {s[i:i+12]!r}.")
    return tokens


def _parse_formula(formula: str):
    try:
        return _Parser(_tokenize(formula)).parse()
    except FormulaError:
        raise
    except Exception as exc:  # defensive - never let a bad formula 500
        raise FormulaError(f"Could not parse the formula ({exc}).") from exc


def eval_formula_scalar(node, lookup: Callable[[str], float]) -> float:
    """Evaluate a parsed formula AST for a single row, resolving column names
    through ``lookup`` (returns NaN for missing/non-numeric values)."""
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "col":
        try:
            v = lookup(node[1])
        except (KeyError, TypeError):
            raise FormulaError(f'Formula references a column that isn\'t present: "{node[1]}".')
        return v
    if kind == "neg":
        return -eval_formula_scalar(node[1], lookup)
    # binop
    _, op, a, b = node
    x = eval_formula_scalar(a, lookup)
    y = eval_formula_scalar(b, lookup)
    if op == "+":
        return x + y
    if op == "-":
        return x - y
    if op == "*":
        return x * y
    # division
    if not np.isfinite(y) or y == 0:
        raise FormulaError("Division by zero in the derived-column formula.")
    return x / y


def _parse_numeric_series(series) -> pd.Series:
    """Return a numeric pandas Series for a column, cleaning currency / comma /
    percent formatting (e.g. "85,000", "$1,200", "3%") the same way the rest of
    the app does (data_normalize._clean_numeric). Non-numeric cells become
    NaN."""
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    # Strip $,£,€, spaces, %, and commas; turn (parens) into a negative sign.
    cleaned = series.astype(str).str.strip()
    cleaned = cleaned.str.replace(r"[\$,£€\s%]", "", regex=True)
    cleaned = cleaned.str.replace(r"\(([^)]*)\)", r"-\1", regex=True)
    cleaned = cleaned.str.replace(",", "", regex=False)
    return pd.to_numeric(cleaned, errors="coerce")


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
        col_names = _collect_cols(node)
        missing = [c for c in col_names if c not in seen]
        if missing:
            raise FormulaError(
                f'Formula for "{name}" references missing columns: {", ".join(missing)}.'
            )
        # Precompute a cleaned numeric array for each referenced column (once,
        # not per row) so formatting like "85,000" is handled consistently and
        # evaluation stays fast even on a large tape.
        numeric = {}
        for col in col_names:
            if col not in numeric:
                numeric[col] = _parse_numeric_series(seen[col])

        vals = []
        for i in out.index:
            lookup = lambda col: numeric[col].loc[i]  # noqa: E731
            v = eval_formula_scalar(node, lookup)
            vals.append(v if np.isfinite(v) else float("nan"))
        out[name] = vals
        seen[name] = out[name]
    return out


def _collect_cols(node, acc=None):
    if acc is None:
        acc = []
    if node[0] == "col":
        acc.append(node[1])
    elif node[0] in ("binop",):
        _collect_cols(node[2], acc)
        _collect_cols(node[3], acc)
    elif node[0] == "neg":
        _collect_cols(node[1], acc)
    return acc
