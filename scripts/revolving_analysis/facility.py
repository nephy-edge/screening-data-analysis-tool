"""Facility view: the advance rate implied by the stressed loss rate, and
covenant tests with headroom. Limits are deal-specific inputs."""

from .metrics import BY_KEY

# Covenant metrics beyond the catalogue: ECL-based figures and net yield.
EXTRA_METRICS = {
    "coverage_perf": ("ECL coverage (performing)", "pct"),
    "coverage_total": ("ECL coverage (total)", "pct"),
    "net_yield_annual": ("Net yield (annualised)", "pct"),
}


def advance_rate(stress_loss_rate: float | None, haircut: float) -> dict:
    """'Loss Rate LTV' row 65: MAX(0, 1 - stressed loss rate - haircut)."""
    value = max(0.0, 1 - stress_loss_rate - haircut) if stress_loss_rate is not None else None
    return {"stressLossRate": stress_loss_rate, "haircut": haircut, "advanceRate": value}


def covenant_label(key: str) -> tuple[str, str]:
    if key in BY_KEY:
        return BY_KEY[key].label, BY_KEY[key].kind
    return EXTRA_METRICS.get(key, (key, "pct"))


def covenant_tests(values: dict, covenants: list[dict]) -> list[dict]:
    """PASS / BREACH / N/A per covenant. Headroom is how far the value sits
    inside the limit (negative = breached)."""
    out = []
    for c in covenants:
        label, kind = covenant_label(c["metric"])
        value, limit, op = values.get(c["metric"]), c["limit"], c["operator"]
        if value is None:
            status, headroom = "N/A", None
        else:
            headroom = limit - value if op == "<=" else value - limit
            status = "PASS" if headroom >= 0 else "BREACH"
        out.append(
            {
                "metric": c["metric"],
                "label": label,
                "kind": kind,
                "operator": op,
                "limit": limit,
                "value": value,
                "headroom": headroom,
                "status": status,
            }
        )
    return out
