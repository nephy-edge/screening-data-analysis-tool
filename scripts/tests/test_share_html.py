"""Share-as-HTML: every model's /api/export/html/* returns one self-contained,
read-only page (no form controls, no external scripts) with the app's tabs
and its charts drawn as inline SVG."""

import re
import sys
from html import unescape

from test_revolving_analysis import _rows as revolving_rows
from test_web_api_baas import _client, _template_rows
from test_web_api_rental import _tape

sys.path.insert(0, "scripts")
from share_html import build_share_html, chart, table  # noqa: E402


def _assert_standalone(html):
    assert html.startswith("<!doctype html>")
    assert not re.search(r"<(input|select|textarea|form)\b", html)
    assert not re.search(r"""(src|href)=["']https?:""", html)


def _tabs(html):
    return [unescape(t) for t in re.findall(r'<button data-t="t\d+"[^>]*>([^<]+)</button>', html)]


def _lending_rows():
    rows = []
    for i in range(40):
        month = 1 + i % 4
        rows.append(
            {
                "Loan ID": f"L{i}",
                "Disbursement Date": f"2024-0{month}-01",
                "Expected Completion Date": f"2024-0{month + 3}-01",
                "Principal Value": 1000.0,
                "Expected Interest": 100.0,
                "Total Paid": 1100.0 if i % 5 else 300.0,
            }
        )
    return rows


def test_lending_report_has_every_tab_and_svg_charts():
    res = _client().post(
        "/api/export/html/lending",
        json={
            "body": {"rows": _lending_rows(), "generalInputs": {"extractionDate": "2025-06-30"}},
            "ltv": {"country": "Kenya"},
        },
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/html")
    html = res.text
    _assert_standalone(html)
    assert _tabs(html) == [
        "General Inputs",
        "Summary",
        "Data Checks",
        "Cohorts",
        "LTV Analysis",
        "Unit Economics",
    ]
    assert "Cohort Loss Rates" in html and "Loss per Cohort" in html
    assert html.count("<svg") >= 6
    assert "Kenya" in html  # the LTV tab's dropdown choice reached the calculator
    assert "L17" not in html  # no loan-level rows


def test_rental_report():
    rows = _tape(["Open"] * 4)
    rows = rows.astype(object).where(rows.notna(), None)
    for c in ("start_date", "expected_end_date"):
        rows[c] = rows[c].map(lambda d: d.strftime("%Y-%m-%d"))
    res = _client().post(
        "/api/export/html/rental",
        json={"rows": rows.to_dict("records"), "generalInputs": {"extractionDate": "2025-01-01"}},
    )
    assert res.status_code == 200, res.text
    _assert_standalone(res.text)
    assert "Churn Analysis" in _tabs(res.text) and "TS Covenants" in _tabs(res.text)


def test_revolving_report_draws_performance_charts():
    res = _client().post("/api/export/html/revolving", json={"body": {"rows": revolving_rows()}})
    assert res.status_code == 200, res.text
    html = res.text
    _assert_standalone(html)
    assert _tabs(html)[1:] == [
        "Summary",
        "Data Checks",
        "Performance",
        "LTV Calculator",
        "Unit Economics",
        "Backtest & Stress",
        "Facility & Covenants",
    ]
    assert "Roll rates" in html and "<polyline" in html and "<polygon" in html


def test_baas_report():
    client = _client()
    res = client.post(
        "/api/export/html/baas",
        json={"rows": _template_rows(client), "generalInputs": {"valuationDate": "2026-09-30"}},
    )
    assert res.status_code == 200, res.text
    _assert_standalone(res.text)
    assert _tabs(res.text) == ["General Inputs", "Results", "Cash Flows"]
    assert "Amp-RW" in res.text


def test_html_report_requires_a_session():
    from fastapi.testclient import TestClient

    import web_api.main as m

    assert (
        TestClient(m.app, base_url="https://testserver")
        .post("/api/export/html/baas", json={})
        .status_code
        == 401
    )


def test_chart_scales_points_and_skips_missing_values():
    svg = chart(
        [{"x": "a", "y": 0}, {"x": "b", "y": None}, {"x": "c", "y": 10}], "x", "T", lines=["y"]
    )
    assert svg.count("<circle") == 2  # the gap is not plotted as zero
    assert svg.count("<polyline") == 2  # and breaks the line
    assert chart([], "x", "T", lines=["y"]) == ""


def test_values_are_escaped():
    html = table([{"name": "<script>alert(1)</script>"}])
    assert "<script>" not in html and "&lt;script&gt;" in html
    _assert_standalone(build_share_html("baas", {"markets": [], "cashflows": []}))
