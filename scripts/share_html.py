# ruff: noqa: E501 - inline HTML/SVG/CSS markup reads better unwrapped.
"""Standalone, read-only HTML report of an analysis - what Share downloads
until the app is deployed.

Built from the same analyse-endpoint JSON the React tabs render (no figures
are recomputed here), one section per app results tab. Charts are drawn as
inline SVG so the file opens offline with no CDN. Loan-level "Data Input",
Custom Viz and Ask AI are left out; there are no inputs, so nothing can be
edited.
"""

from __future__ import annotations

import datetime
import html
import math
import re
from typing import Any

Row = dict[str, Any]
MONTH_KEY = re.compile(r"^\d{4}-\d{2}$")
PALETTE = ["#2f6db5", "#d9822b", "#3a9a5b", "#c0392b", "#8e5bb5", "#7f8c8d", "#b5a22f", "#2fa3b5"]
MAX_CATEGORY_TICKS = 12


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    n = _num(value)
    if n is None:
        return str(value)
    if n == int(n) and abs(n) < 1e15:
        return f"{int(n):,}"
    return f"{n:,.2f}" if abs(n) >= 100 else f"{n:,.4f}"


def _label(key: str) -> str:
    """camelCase / snake_case keys -> "Title case" headings."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(key)).replace("_", " ")
    return spaced[:1].upper() + spaced[1:]


# --- blocks ------------------------------------------------------------------


def table(rows: list[Row] | None, title: str | None = None) -> str:
    if not rows:
        return ""
    cols: list[str] = []
    for row in rows:
        cols += [k for k, v in row.items() if k not in cols and not isinstance(v, (list, dict))]
    head = "".join(f"<th>{_esc(c)}</th>" for c in cols)
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="{"n" if _num(r.get(c)) is not None else ""}">{_esc(_fmt(r.get(c)))}</td>'
            for c in cols
        )
        + "</tr>"
        for r in rows
    )
    cap = f"<h3>{_esc(title)}</h3>" if title else ""
    return f'{cap}<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def kv(data: dict | None, title: str | None = None) -> str:
    """Scalars as a two-column table; nested values become their own blocks."""
    if not data:
        return ""
    flat = [(k, v) for k, v in data.items() if not isinstance(v, (list, dict))]
    nested = [(k, v) for k, v in data.items() if isinstance(v, (list, dict)) and v]
    out = table([{"Metric": _label(k), "Value": v} for k, v in flat], title) if flat else ""
    for k, v in nested:
        if isinstance(v, dict):
            out += kv(v, _label(k))
        elif all(isinstance(x, dict) for x in v):
            out += table(v, _label(k))
        else:
            out += table([{"Value": x} for x in v], _label(k))
    return out


def checks(items: list[Row] | None) -> str:
    if not items:
        return '<p class="muted">No data checks flagged.</p>'
    out = ""
    for c in items:
        level = c.get("level", "info")
        out += f'<div class="chk {_esc(level)}"><b>{_esc(level.upper())}</b> {_esc(c.get("message", ""))}</div>'
        out += table(c.get("detail"))
    return out


def chart(
    data: list[Row] | None,
    x: str,
    title: str,
    lines: list[str] = (),
    bars: list[str] = (),
    areas: list[str] = (),
    right: list[str] = (),
    refs: list[tuple[str, float | None]] = (),
) -> str:
    """Inline SVG chart over category x values. ``areas`` are stacked;
    ``right`` lines get their own right-hand axis (the Summary chart's
    principal bars vs loss-rate line); ``refs`` are dashed horizontal lines."""
    data = [d for d in (data or []) if d.get(x) is not None]
    series = [*bars, *areas, *lines]
    if not data or not series:
        return ""
    w, h, ml, mr, mt, mb = 760, 300, 64, 64 if right else 16, 16, 64
    pw, ph = w - ml - mr, h - mt - mb
    n = len(data)
    step = pw / n
    xs = [ml + step * (i + 0.5) for i in range(n)]

    def val(d: Row, s: str) -> float:
        return _num(d.get(s)) or 0.0

    stacks = [sum(max(val(d, s), 0) for s in areas) for d in data]
    left_vals = [
        v for d in data for s in series if s not in right and (v := _num(d.get(s))) is not None
    ]
    left_vals += stacks + [r for _, r in refs if r is not None]
    right_vals = [v for d in data for s in right if (v := _num(d.get(s))) is not None]

    def scale(vals: list[float]):
        lo, hi = min([0.0, *vals]), max([0.0, *vals])
        if hi == lo:
            hi = lo + 1
        return lo, hi, (lambda v: mt + ph - (v - lo) / (hi - lo) * ph)

    lo, hi, yl = scale(left_vals)
    rlo, rhi, yr = scale(right_vals) if right else (0, 1, yl)
    color = {s: PALETTE[i % len(PALETTE)] for i, s in enumerate(series)}
    parts = [f'<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}" class="plot"/>']
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        y = yl(v)
        parts.append(f'<line x1="{ml}" x2="{ml + pw}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        parts.append(
            f'<text x="{ml - 6}" y="{y + 4:.1f}" class="ax" text-anchor="end">{_esc(_fmt_tick(v))}</text>'
        )
        if right:
            rv = rlo + (rhi - rlo) * i / 4
            parts.append(
                f'<text x="{ml + pw + 6}" y="{y + 4:.1f}" class="ax">{_esc(_fmt_tick(rv))}</text>'
            )
    every = max(1, math.ceil(n / MAX_CATEGORY_TICKS))
    for i, d in enumerate(data):
        if i % every == 0:
            parts.append(
                f'<text x="{xs[i]:.1f}" y="{mt + ph + 14}" class="ax" text-anchor="end" '
                f'transform="rotate(-35 {xs[i]:.1f} {mt + ph + 14})">{_esc(str(d[x])[:10])}</text>'
            )
    base = [0.0] * n
    for s in areas:
        top = [base[i] + max(val(d, s), 0) for i, d in enumerate(data)]
        pts = [f"{xs[i]:.1f},{yl(top[i]):.1f}" for i in range(n)]
        pts += [f"{xs[i]:.1f},{yl(base[i]):.1f}" for i in reversed(range(n))]
        parts.append(
            f'<polygon points="{" ".join(pts)}" fill="{color[s]}" fill-opacity="0.55"><title>{_esc(s)}</title></polygon>'
        )
        base = top
    bw = step * 0.8 / max(len(bars), 1)
    for j, s in enumerate(bars):
        for i, d in enumerate(data):
            v = _num(d.get(s))
            if v is None:
                continue
            y0, y1 = sorted((yl(0), yl(v)))
            bx = xs[i] - step * 0.4 + j * bw
            parts.append(
                f'<rect x="{bx:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{max(y1 - y0, 0.5):.1f}" '
                f'fill="{color[s]}" fill-opacity="0.75"><title>{_esc(d[x])}: {_esc(_fmt(v))}</title></rect>'
            )
    for s in lines:
        ys = yr if s in right else yl
        segs, cur = [], []
        for i, d in enumerate(data):
            v = _num(d.get(s))
            if v is None:
                if cur:
                    segs.append(cur)
                cur = []
                continue
            cur.append(f"{xs[i]:.1f},{ys(v):.1f}")
            parts.append(
                f'<circle cx="{xs[i]:.1f}" cy="{ys(v):.1f}" r="2.5" fill="{color[s]}">'
                f"<title>{_esc(s)} · {_esc(d[x])}: {_esc(_fmt(v))}</title></circle>"
            )
        segs.append(cur)
        for seg in filter(None, segs):
            parts.append(
                f'<polyline points="{" ".join(seg)}" fill="none" stroke="{color[s]}" stroke-width="2"/>'
            )
    for name, r in refs:
        if r is not None:
            parts.append(
                f'<line x1="{ml}" x2="{ml + pw}" y1="{yl(r):.1f}" y2="{yl(r):.1f}" class="ref"/>'
            )
            parts.append(
                f'<text x="{ml + pw - 4}" y="{yl(r) - 4:.1f}" class="ax" text-anchor="end">{_esc(name)} {_esc(_fmt_tick(r))}</text>'
            )
    legend = "".join(
        f'<span><i style="background:{color[s]}"></i>{_esc(s)}{" (right axis)" if s in right else ""}</span>'
        for s in series
    )
    svg = f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{_esc(title)}">{"".join(parts)}</svg>'
    return f'<figure class="card"><figcaption>{_esc(title)}</figcaption>{svg}<div class="lg">{legend}</div></figure>'


def _fmt_tick(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.1f}B"
    if a >= 1e6:
        return f"{v / 1e6:.1f}M"
    if a >= 1e4:
        return f"{v / 1e3:.0f}k"
    return f"{v:,.0f}" if a >= 100 else f"{v:.3g}"


def by_month(
    rows: list[Row] | None, label_key: str, keep=lambda label: True
) -> tuple[list[Row], list[str]]:
    """Months-across rows -> one point per month, one series per row label
    (RevolvingTabs.tsx's byMonth)."""
    rows = rows or []
    kept = [r for r in rows if keep(str(r.get(label_key)))]
    months = [k for k in (rows[0] if rows else {}) if MONTH_KEY.match(k)]
    data = [{"month": m, **{str(r[label_key]): r.get(m) for r in kept}} for m in months]
    return data, [str(r[label_key]) for r in kept]


# --- per-model tabs ----------------------------------------------------------


def _percentile(values: list[float], q: float) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    pos = (len(vals) - 1) * q
    lo = math.floor(pos)
    return vals[lo] + (vals[min(lo + 1, len(vals) - 1)] - vals[lo]) * (pos - lo)


def _ltv_blocks(ltv_calc: dict | None) -> str:
    if not ltv_calc:
        return ""
    if not ltv_calc.get("ok"):
        return (
            f'<p class="muted">{_esc(ltv_calc.get("warning") or "LTV calculator unavailable.")}</p>'
        )
    choices = {
        k: ltv_calc.get(k)
        for k in (
            "dataSource",
            "country",
            "macroFxRisk",
            "segmentation",
            "hedgeRate",
            "rolledHedge",
            "greenInterest",
            "greenTermBucket",
            "greenLoss",
        )
    }
    return kv(choices, "Calculator inputs") + kv(ltv_calc.get("result"), "LTV calculator results")


def lending_tabs(r: dict, ltv_calc: dict | None) -> list[tuple[str, str]]:
    sc = r.get("summaryChart") or []
    loss95 = _percentile([_num(d.get("Loss Rate")) for d in sc], 0.95)
    summary_rate = (r.get("summaryMetrics") or {}).get("lossRate")
    sc_short = [{**d, "Cohort": str(d.get("Cohort"))[:7]} for d in sc]
    ue = r.get("ue") or {}
    chart_rows = r.get("chart") or []
    fx = (ltv_calc or {}).get("fxCountryData") or {}
    fx_rows = [
        {"tenor": t, "deval": v} for t, v in zip(fx.get("tenors") or [], fx.get("values") or [])
    ]
    ue_charts = "".join(
        chart(
            chart_rows,
            "Cohort",
            f"{y} per Cohort",
            lines=[y],
            refs=[("Average", _num(ue.get(ref)))],
        )
        for y, ref in [
            ("Loss Rate", "Average Loss"),
            ("Weighted Avg Term", "Average Expected Term"),
            ("Interest %", "Average Interest %"),
            ("Fee %", "Average Fee %"),
        ]
    )
    return [
        (
            "Summary",
            kv(r.get("summaryMetrics"), "Summary metrics")
            + chart(
                sc_short,
                "Cohort",
                "Cohort Loss Rates",
                bars=["Total Principal"],
                lines=["Loss Rate"],
                right=["Loss Rate"],
            )
            + table([{"Loss rate": summary_rate, "95th percentile": loss95}], "Reference lines"),
        ),
        ("Data Checks", checks(r.get("checks"))),
        (
            "Cohorts",
            table(r.get("cohorts"), "Cohorts (matured loans)")
            + table(r.get("cohortsAllLoans"), "Cohorts (all loans)"),
        ),
        (
            "LTV Analysis",
            kv(r.get("ltv"), "LTV analysis")
            + _ltv_blocks(ltv_calc)
            + chart(fx_rows, "tenor", "FX devaluation by tenor", lines=["deval"])
            + chart(
                [{"cohort": d.get("Cohort"), "lossRate": d.get("Loss Rate")} for d in chart_rows],
                "cohort",
                "Loss per Cohort",
                lines=["lossRate"],
                refs=[("95th %ile", _num((ltv_calc or {}).get("greenLoss")))],
            ),
        ),
        (
            "Unit Economics",
            kv(ue, "Unit economics") + ue_charts + kv(r.get("ueModel"), "UE model inputs"),
        ),
    ]


def rental_tabs(r: dict, ltv_calc: dict | None) -> list[tuple[str, str]]:
    churn = {k: v for k, v in (r.get("churn") or {}).items() if k != "residual_curve"}
    return [
        ("Summary", kv(r.get("summaryMetrics"), "Summary metrics")),
        ("Data Checks", checks(r.get("checks"))),
        ("Asset View", table(r.get("assetView"))),
        (
            "Unit Economics",
            kv(r.get("ue"), "Unit economics")
            + chart(
                r.get("curveRows"),
                "mob",
                "Repayment curve (% of average collection by month on book)",
                lines=["pct_avg_collection"],
            )
            + table(r.get("curveRows"), "Repayment curve"),
        ),
        ("Churn Analysis", kv(churn, "Churn")),
        ("LTV Analysis", kv(r.get("ltv"), "LTV analysis")),
        ("TS Covenants", kv(r.get("ts"), "TS covenants")),
        ("General Analysis", kv(r.get("general"), "General analysis")),
    ]


PERF_LINES = [
    ("ncr", "Net collection ratio"),
    ("term", "Estimated term"),
    ("ecl", "ECL"),
    ("loss_rate", "Estimated loss rate"),
    ("avg_credit_line", "Average credit line"),
    ("interest_paid", "Interest paid"),
    ("principal_paid", "Principal paid"),
    ("principal_issued", "Principal issued"),
    ("cum_write_off", "Cumulative write-off"),
    ("write_off", "Write-off"),
    ("customers", "Customers"),
    ("issued_per_customer", "Issued per customer"),
]


def revolving_tabs(r: dict, ltv_calc: dict | None) -> list[tuple[str, str]]:
    perf = r.get("performance") or {}
    ptable = perf.get("table") or []
    breakdown, b_series = by_month([x for x in ptable if x.get("group") == "breakdown"], "Metric")
    perf_charts = chart(breakdown, "month", "Balance by bucket", areas=b_series)
    for key, title in PERF_LINES:
        rows = [x for x in ptable if x.get("key") == key]
        data, series = by_month(rows, "Metric")
        perf_charts += chart(data, "month", title, lines=series)
    bal, bal_series = by_month(
        r.get("balances"), "Bucket", lambda s: not s.startswith(("Total", "Principal written off"))
    )
    roll, roll_series = by_month(r.get("rollRates"), "Transition")
    bt = [b for b in r.get("backtest") or [] if b.get("Outcome observed")]
    actual = next((k for k in (bt[0] if bt else {}) if k.startswith("Actual write-offs")), None)
    ratios = [
        {"Ratio": x.get("label"), "Value": x.get("value"), "Note": x.get("annotation")}
        for x in r.get("ratios") or []
    ]
    return [
        (
            "Summary",
            table(ratios, "Key ratios")
            + kv(r.get("eclSummary"), "ECL summary")
            + table(r.get("unitEconomics"), "Unit economics")
            + table(r.get("earlyWarnings"), "Early warnings")
            + table(r.get("ageingMix"), "Ageing mix")
            + table(r.get("stress"), "Stress")
            + table(r.get("covenants"), "Covenants"),
        ),
        ("Data Checks", checks(r.get("qualityChecks"))),
        (
            "Performance",
            perf_charts
            + table(ptable, "Performance by month")
            + chart(bal, "month", "Balances by bucket", areas=bal_series)
            + table(r.get("balances"), "Balances")
            + chart(roll, "month", "Roll rates", lines=roll_series)
            + table(r.get("rollRates"), "Roll rates")
            + table(r.get("lossFactors"), "Loss factors")
            + table(r.get("ecl"), "ECL")
            + table(r.get("checks"), "Reconciliation checks"),
        ),
        ("LTV Calculator", _ltv_blocks(ltv_calc)),
        (
            "Unit Economics",
            kv({k: v for k, v in perf.items() if k != "table"}, "Performance inputs")
            + table(ratios, "Ratios")
            + table(r.get("facts"), "Monthly facts")
            + table(r.get("lossRateDistribution"), "Loss rate distribution")
            + table(r.get("standardMetrics"), "Standard metrics")
            + table(r.get("unitEconomics"), "Unit economics")
            + kv(r.get("tenorInputs"), "Tenor inputs")
            + table(r.get("overTime"), "Metrics over time"),
        ),
        (
            "Backtest & Stress",
            kv(r.get("backtestSummary"), "Backtest summary")
            + chart(
                bt,
                "As-of",
                "Backtest: forecast vs actual write-offs",
                lines=["Forecast write-offs", *([actual] if actual else [])],
            )
            + table(r.get("backtest"), "Backtest")
            + table(r.get("stress"), "Stress"),
        ),
        (
            "Facility & Covenants",
            kv(r.get("advance"), "Advance") + table(r.get("covenants"), "Covenants"),
        ),
    ]


TABS = {
    "lending": lending_tabs,
    "rental": rental_tabs,
    "revolving": revolving_tabs,
}
TITLES = {
    "lending": "Lending",
    "rental": "Rental & Subscription",
    "revolving": "Revolving Lending",
}

CSS = """
:root{color-scheme:light;--bg:#f7f7f5;--fg:#1d1d1b;--mut:#6b6b66;--line:#e2e1dc;--card:#fff;--acc:#2f6db5}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{padding:24px 24px 0}h1{margin:0 0 4px;font-size:22px}.muted{color:var(--mut)}
nav{display:flex;flex-wrap:wrap;gap:4px;padding:16px 24px 0;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg)}
nav button{border:0;background:none;padding:8px 12px;font:inherit;color:var(--mut);cursor:pointer;border-bottom:2px solid transparent}
nav button[aria-selected=true]{color:var(--fg);border-color:var(--acc);font-weight:600}
main{padding:16px 24px 48px}section{display:none}section.on{display:block}
h3{margin:20px 0 8px;font-size:15px}.tw{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:12.5px}th,td{padding:5px 9px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{background:#f0efea;position:sticky;top:0}td.n{text-align:right;font-variant-numeric:tabular-nums}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;margin:16px 0;padding:12px}figcaption{color:var(--mut);margin-bottom:6px}
svg{width:100%;height:auto;max-width:100%}.plot{fill:#fff}.grid{stroke:#ecebe6}.ax{font-size:10px;fill:#6b6b66}.ref{stroke:#c0392b;stroke-dasharray:4 3}
.lg{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--mut)}.lg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}
.chk{padding:8px 10px;border-radius:6px;margin:12px 0 6px;background:#eef3fa}.chk.warning{background:#fdf2e3}
@media print{nav{display:none}section{display:block}}
"""

JS = """document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
document.querySelectorAll('nav button').forEach(x=>x.setAttribute('aria-selected',x===b));
document.querySelectorAll('section').forEach(s=>s.classList.toggle('on',s.id===b.dataset.t));});"""


def build_share_html(
    model: str, response: dict, inputs: dict | None = None, ltv_calc: dict | None = None
) -> str:
    """The full report page for ``model`` from its analyse response."""
    tabs = [("General Inputs", kv(inputs, "Inputs used for this run"))] if inputs else []
    tabs += [
        (name, body or '<p class="muted">Nothing to show for this run.</p>')
        for name, body in TABS[model](response, ltv_calc)
    ]
    nav = "".join(
        f'<button data-t="t{i}" aria-selected="{str(i == 0).lower()}">{_esc(n)}</button>'
        for i, (n, _) in enumerate(tabs)
    )
    secs = "".join(
        f'<section id="t{i}" class="{"on" if i == 0 else ""}"><h2>{_esc(n)}</h2>{b}</section>'
        for i, (n, b) in enumerate(tabs)
    )
    title = f"SC Analysis - {TITLES[model]}"
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_esc(title)}</title><style>{CSS}</style></head><body>"
        f'<header><h1>{_esc(title)}</h1><p class="muted">Read-only snapshot generated {stamp}. Figures are as computed by the app at export time.</p></header>'
        f"<nav>{nav}</nav><main>{secs}</main><script>{JS}</script></body></html>"
    )
