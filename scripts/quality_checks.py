"""Data-quality checks surfaced on the Data Input / Summary tabs, shared or
model-specific, for both the Lending and Rental & Subscription models. Each
check is a plain function taking already-computed DataFrames (raw upload,
cohorts, General Inputs) and returning a list of check-result dicts - no
Streamlit widget or session-state dependency, so these are pure and easy to
unit test independently of the page that renders them."""

import pandas as pd
from data_normalize import _clean_numeric
from template_analysis.date_detection import (
    detect_date as _detect_date,
)
from template_analysis.date_detection import (
    mixed_parsed_and_text_warning as _mixed_parsed_and_text_warning,
)


def _validate_mapping(
    raw: pd.DataFrame, mapping: dict, required_fields, date_fields, numeric_fields, dayfirst=False
) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    for target, source in mapping.items():
        if not source:
            continue
        series = raw[source]
        non_null = series.notna()
        if non_null.sum() == 0:
            msg = f"**{target}** -> column '{source}' is entirely empty."
            (errors if target in required_fields else warnings).append(msg)
            continue
        if target in date_fields:
            parsed = _detect_date(series)
            fail_rate = 1 - (parsed.notna().sum() / non_null.sum())
            if fail_rate > 0.5:
                msg = (
                    f"**{target}** -> column '{source}' doesn't look like dates "
                    f"({fail_rate:.0%} unparseable)."
                )
                (errors if target in required_fields else warnings).append(msg)
            mixed_msg = _mixed_parsed_and_text_warning(series, target)
            if mixed_msg:
                warnings.append(mixed_msg)
        elif target in numeric_fields:
            parsed = pd.to_numeric(_clean_numeric(series), errors="coerce")
            fail_rate = 1 - (parsed.notna().sum() / non_null.sum())
            if fail_rate > 0.5:
                msg = (
                    f"**{target}** -> column '{source}' doesn't look numeric "
                    f"({fail_rate:.0%} unparseable)."
                )
                (errors if target in required_fields else warnings).append(msg)
    return errors, warnings


def _step2_data_quality_checks(raw: pd.DataFrame, config: dict) -> list[dict]:
    """Step 2 checks from the screening briefing: duplicate IDs, field
    completion below 80%, portfolio size above 5,000 rows, and history
    below 12 months. Driven entirely by the LENDING_CONFIG dict (id_field,
    input_columns, primary_date_field). Each check is a dict with "level"
    ("warning"/"info"), "message", an optional "detail" DataFrame, and an
    optional "detail_expander" title (if absent but "detail" is set, the
    table is shown directly, not collapsed)."""
    checks: list[dict] = []
    id_field = config["id_field"]

    if id_field in raw.columns:
        dup_mask = raw[id_field].notna() & raw[id_field].duplicated(keep=False)
        if dup_mask.any():
            checks.append(
                {
                    "level": "warning",
                    "check_id": "duplicate_loan_id",
                    "message": (
                        f"**Duplicate {id_field}s** - {raw.loc[dup_mask, id_field].nunique()} "
                        f"{id_field}(s) appear more than once ({int(dup_mask.sum())} rows). "
                        "Confirm with the borrower whether these are genuine duplicates."
                    ),
                    # key=str: id_field can be a mix of str/int/float (e.g. an Excel
                    # column read with some cells numeric, others text) - sort_values
                    # compares values pairwise and raises TypeError on a mixed-type
                    # column, so sort by each value's string form instead.
                    "detail": raw.loc[dup_mask].sort_values(id_field, key=lambda s: s.astype(str)),
                    "detail_expander": f"View {int(dup_mask.sum())} duplicate row(s)",
                }
            )

    low_completion = [
        f"{target} ({raw[target].notna().mean():.0%})"
        for target, _ in config["input_columns"]
        if target in raw.columns and raw[target].notna().mean() < 0.80
    ]
    if low_completion:
        checks.append(
            {
                "level": "warning",
                "message": (
                    "**Field completion below 80%** - " + ", ".join(low_completion) + ". "
                    "Records missing these fields may distort downstream metrics."
                ),
            }
        )

    if len(raw) > 5000:
        checks.append(
            {
                "level": "info",
                "message": (
                    f"**Large portfolio** - {len(raw):,} rows exceeds the 5,000-row "
                    "screening threshold. The tool itself scales well past this "
                    "(tested sub-second at ~419,000 rows); flagged in case screening "
                    "policy calls for a sampled or ad-hoc review at this size regardless."
                ),
            }
        )

    primary_date_field = config["primary_date_field"]
    if primary_date_field in raw.columns and raw[primary_date_field].notna().any():
        span_days = (raw[primary_date_field].max() - raw[primary_date_field].min()).days
        if span_days < 365:
            checks.append(
                {
                    "level": "warning",
                    "message": (
                        f"**Limited history** - dates span only "
                        f"{span_days / 30:.1f} months, below the 12-month screening "
                        "threshold; loss/churn-rate percentiles may be unreliable."
                    ),
                }
            )

    return checks


def _lending_variance_check(cohorts: pd.DataFrame) -> list[dict]:
    """Step 4: flags cohort-to-cohort loss-rate swings above 5 percentage
    points, with likely contributing factors diagnosed from signals already
    in the cohort table."""
    checks: list[dict] = []
    if cohorts is not None and "Loss Rate" in cohorts.columns:
        by_month = cohorts.dropna(subset=["Loss Rate"]).sort_values("Cohort")
        deltas = by_month["Loss Rate"].diff()
        flagged = by_month.loc[deltas.abs() > 0.05]
        if not flagged.empty:
            flagged_deltas = deltas.loc[flagged.index]
            loan_count_pct = by_month["Loan Count"].pct_change().loc[flagged.index]
            term_delta = by_month["Weighted Avg Term"].diff().loc[flagged.index]
            prior_term = by_month["Weighted Avg Term"].shift(1).loc[flagged.index]
            avg_loan_size = by_month["Total Principal"] / by_month["Loan Count"]
            avg_loan_size_pct = avg_loan_size.pct_change().loc[flagged.index]
            pvd_delta = (
                by_month["PvD Ratio"].diff().loc[flagged.index]
                if "PvD Ratio" in by_month.columns
                else None
            )

            def _diagnose(idx):
                causes = []
                matured = flagged.loc[idx, "Matured Count"]
                if pd.notna(matured) and matured < 10:
                    causes.append(
                        f"small matured sample ({int(matured)} loans) - swing may be noise"
                    )
                lc_pct = loan_count_pct.loc[idx]
                if pd.notna(lc_pct) and abs(lc_pct) > 0.5:
                    causes.append(
                        f"loan count {'dropped' if lc_pct < 0 else 'spiked'} "
                        f"{abs(lc_pct):.0%} vs prior cohort - check for missing loads"
                    )
                td, pt = term_delta.loc[idx], prior_term.loc[idx]
                if pd.notna(td) and pd.notna(pt) and pt > 0 and abs(td) / pt > 0.25:
                    causes.append(
                        f"weighted avg term shifted {td:+.0f} days vs prior cohort "
                        "- possible mix/definition change"
                    )
                als_pct = avg_loan_size_pct.loc[idx]
                if pd.notna(als_pct) and abs(als_pct) > 0.5:
                    causes.append(
                        f"average loan size {'dropped' if als_pct < 0 else 'grew'} "
                        f"{abs(als_pct):.0%} vs prior cohort - possible portfolio mix change"
                    )
                if pvd_delta is not None:
                    pvd = pvd_delta.loc[idx]
                    if pd.notna(pvd) and abs(pvd) > 0.10:
                        causes.append(
                            f"PvD ratio shifted {pvd * 100:+.0f}pp vs prior cohort "
                            "- possible collections/servicing change"
                        )
                return (
                    "; ".join(causes)
                    if causes
                    else (
                        "No obvious data-driven cause - likely a genuine portfolio "
                        "event, escalate to Borrower"
                    )
                )

            detail = pd.DataFrame(
                {
                    "Cohort": flagged["Cohort"].dt.strftime("%b %Y"),
                    "Loss Rate (%)": (flagged["Loss Rate"] * 100).round(1).values,
                    "Swing vs Prior Cohort (pp)": (flagged_deltas * 100).round(1).values,
                    "Likely Contributing Factor(s)": [_diagnose(i) for i in flagged.index],
                }
            )
            checks.append(
                {
                    "level": "warning",
                    "check_id": "unexplained_variance",
                    "message": (
                        f"**Unexplained variance** - {len(detail)} cohort(s) show a "
                        "loss-rate swing of more than 5 percentage points versus the "
                        "prior cohort. Likely contributing factors are diagnosed below "
                        "from loan-count, sample-size, and term-mix signals already in "
                        "the data - verify before relying on these cohorts."
                    ),
                    "detail": detail,
                }
            )

    return checks


def _negative_loss_rate_check(cohorts: pd.DataFrame) -> list[dict]:
    """Flags any cohort whose Loss Rate came out negative - i.e. Total Paid
    exceeded Principal+Interest+Fee for that cohort's matured loans. This can
    be legitimate (late fees/penalty interest collected but never added to
    the "owed" side) or a real data issue (refinanced/rolled-over principal
    double-counted in Total Paid, a scale/currency mismatch, a sign error
    upstream) - the detail table below shows the exact owed/paid components
    behind the number so either can be traced by hand, the same way the
    Loss Rate is actually computed in cohorts.py, rather than just asserting
    something looks wrong."""
    checks: list[dict] = []
    if cohorts is None or cohorts.empty or "Loss Rate" not in cohorts.columns:
        return checks
    flagged = cohorts[cohorts["Loss Rate"] < 0].sort_values("Cohort")
    if flagged.empty:
        return checks

    owed = flagged["Total Principal"] + flagged["Total Interest"] + flagged["Total Fee"]
    detail = pd.DataFrame(
        {
            "Cohort": flagged["Cohort"].dt.strftime("%b %Y"),
            "Matured Count": flagged["Matured Count"].values,
            "Loss Rate (%)": (flagged["Loss Rate"] * 100).round(2).values,
            "Owed = Principal+Interest+Fee": owed.round(2).values,
            "Total Paid": flagged["Total Paid"].round(2).values,
            "Excess Collected (Paid - Owed)": (flagged["Total Paid"] - owed).round(2).values,
        }
    )
    checks.append(
        {
            "level": "warning",
            "check_id": "negative_loss_rate",
            "message": (
                f"**Negative Loss Rate** - {len(detail)} cohort(s) show Total Paid exceeding "
                "Principal+Interest+Fee for their matured loans, i.e. a negative loss. This can "
                "be legitimate (late fees or penalty interest collected but never added to the "
                '"owed" side of the formula) or a real data issue (refinanced/rolled-over loans '
                "double-counting principal in Total Paid, a scale or currency mismatch, or a sign "
                "error upstream) - the owed/paid components below are the exact figures the Loss "
                "Rate is computed from, so the cause can be traced directly rather than guessed at."
            ),
            "detail": detail,
            "detail_expander": f"View {len(detail)} negative-loss cohort(s)",
        }
    )
    return checks


def _expected_interest_equals_fee_check(raw: pd.DataFrame, id_field: str) -> list[dict]:
    """Flags when Expected Interest and Expected Fee are identical (and
    non-zero) for a material share of loans - not two independently priced
    revenue components but, almost certainly, Expected Fee populated as a
    copy of Expected Interest (or both mapped to the same source column)
    somewhere upstream of this file. Negative-valued rows within that set are
    the most urgent subset - a negative fee/interest actively understates
    Product Interest Rate and fee-related metrics rather than just being
    suspicious - so the detail table sorts those to the top."""
    checks: list[dict] = []
    if "Expected Interest" not in raw.columns or "Expected Fee" not in raw.columns:
        return checks
    interest = pd.to_numeric(raw["Expected Interest"], errors="coerce")
    fee = pd.to_numeric(raw["Expected Fee"], errors="coerce")
    dup_mask = (interest == fee) & interest.notna() & (interest != 0)
    if not dup_mask.any():
        return checks
    share = dup_mask.mean()
    cols = [
        c
        for c in (
            id_field,
            "Disbursement Date",
            "Principal Value",
            "Expected Interest",
            "Expected Fee",
        )
        if c in raw.columns
    ]
    detail = raw.loc[dup_mask, cols].sort_values("Expected Fee")
    n_negative = int((fee[dup_mask] < 0).sum())
    checks.append(
        {
            "level": "warning",
            "check_id": "expected_interest_equals_fee",
            "message": (
                f"**Expected Interest = Expected Fee** - {dup_mask.sum():,} loan(s) "
                f"({share:.0%} of the tape) have an identical, non-zero Expected Interest and "
                "Expected Fee value - almost certainly a data issue (Expected Fee populated as "
                "a copy of Expected Interest, or both fields mapped to the same source column) "
                "rather than two genuinely distinct revenue components. "
                f"{n_negative} of these are also negative, which directly understates Product "
                "Interest Rate and fee-related metrics rather than just being suspicious. "
                "Verify with the source before trusting Product Interest Rate, Expected Fee %, "
                "or Avg Total Revenue."
            ),
            "detail": detail,
            "detail_expander": (
                f"View {dup_mask.sum():,} loan(s) where Expected Interest = Expected Fee"
            ),
        }
    )
    return checks


def _cohort_coverage_note(cohorts: pd.DataFrame) -> list[dict]:
    """Surfaces the exact date range and month count behind the cohort-level
    metrics, so a mismatch against a previously-downloaded Excel/Google
    Sheets export can be self-diagnosed rather than mistaken for an app bug.
    This tool always computes cohorts live from the uploaded Data Input rows,
    never from any cached pivot in a source file - but a Google Sheets export
    can itself go stale (its cached "Cohorts"/"Cohorts for X or more loans"
    pivots keep showing an old, narrower date range after the live sheet
    grows), which was the real explanation behind two deals this tool was
    checked against (Pintek, Teclogi Logtech) that appeared to "disagree"
    with their own Excel file."""
    checks: list[dict] = []
    if cohorts is not None and not cohorts.empty and "Cohort" in cohorts.columns:
        months = cohorts["Cohort"].dropna()
        if not months.empty:
            checks.append(
                {
                    "level": "info",
                    "check_id": "cohort_coverage",
                    "message": (
                        f"**Cohort coverage** - this analysis covers "
                        f"{months.nunique()} disbursement cohort(s), "
                        f"{months.min():%b %Y} to {months.max():%b %Y}, computed "
                        "live from the uploaded Data Input rows. If comparing "
                        "against a previously downloaded Excel/Google Sheets "
                        "export, check that its own Cohorts sheet covers the "
                        "same range - Google Sheets pivot caches can go stale "
                        "and keep showing an older, narrower range after the "
                        "live sheet has grown."
                    ),
                }
            )
    return checks


def _cohort_threshold_check(gi, cohorts: pd.DataFrame, filtered: pd.DataFrame) -> list[dict]:
    """Flags when 'Minimum loans per cohort' excludes most of this file's own
    cohorts. That threshold is a per-deal assumption baked into each Excel
    copy of the template (confirmed to vary: GoCab/Subbyx use 20, Ennoo/SME
    Go Rental use 1) - the app has no way to read the "right" value for a
    given file from its data alone, so a mismatch here is silent: cohorts
    keep rendering, just built from far fewer loans than the real template
    would have used. This can't fix that, only make the mismatch visible."""
    checks: list[dict] = []
    if cohorts is None or cohorts.empty:
        return checks
    total = len(cohorts)
    kept = len(filtered) if filtered is not None else 0
    if total >= 3 and kept / total < 0.5:
        checks.append(
            {
                "level": "warning",
                "check_id": "min_loans_per_cohort_too_high",
                "message": (
                    f"**Minimum loans per cohort ({gi.min_loans_per_cohort}) may not fit this "
                    f"file** - only {kept} of {total} cohorts meet it. This threshold is a "
                    "per-deal assumption that varies between real files (seen as low as 1, as "
                    "high as 20) and isn't derivable from the uploaded data - if this file's "
                    "real threshold is lower, loss/churn-rate percentiles are being computed "
                    "from a small, filtered slice of the portfolio. Check the file's own "
                    "General Inputs value if you have it, or lower the number in the form "
                    "above and re-run."
                ),
            }
        )
    return checks


def _lending_data_quality_checks(
    raw: pd.DataFrame,
    cohorts: pd.DataFrame,
    lending_config: dict,
    gi=None,
    filtered: pd.DataFrame = None,
) -> list[dict]:
    """Lending: Step 2 checks (shared) + Step 4 loss-rate variance diagnosis
    + negative-loss-rate flag + Expected-Interest-equals-Expected-Fee flag +
    cohort-coverage note + General Inputs sanity check (min loans/cohort vs
    this file's own cohort count)."""
    checks = (
        _step2_data_quality_checks(raw, lending_config)
        + _lending_variance_check(cohorts)
        + _negative_loss_rate_check(cohorts)
        + _expected_interest_equals_fee_check(raw, lending_config["id_field"])
        + _cohort_coverage_note(cohorts)
    )
    if gi is not None and filtered is not None:
        checks += _cohort_threshold_check(gi, cohorts, filtered)
    return checks


def _useful_life_sanity_check(gi, ue_data: dict) -> list[dict]:
    """Flags when 'Useful life of asset (years)' looks far off from this
    file's own average lease tenor. Also a per-deal Excel assumption
    (confirmed to range 3-5+ years across real files) that can't be inferred
    automatically - a mismatch distorts the repayment curve's cohort buckets
    and everything computed from it (LTV, UE), silently."""
    checks: list[dict] = []
    tenor_m = (ue_data or {}).get("lease_tenor_m")
    if tenor_m is None or pd.isna(tenor_m) or tenor_m <= 0:
        return checks
    if not gi.useful_life_years or gi.useful_life_years <= 0:
        return checks
    tenor_years = tenor_m / 12
    ratio = tenor_years / gi.useful_life_years
    if ratio < 0.5 or ratio > 2.0:
        checks.append({
            "level": "warning",
            "check_id": "useful_life_mismatch",
            "message": (
                f"**Useful life of asset ({gi.useful_life_years:.1f}yr) looks far from this "
                f"file's own average lease tenor ({tenor_years:.1f}yr)** - this is a per-deal "
                "Excel assumption (real files range from 3 to 5+ years), not something derived "
                "from the data. A mismatch here distorts the repayment curve's cohort buckets and "
                "the LTV/Unit Economics figures built on top of it. Confirm the intended asset "
                "life for this deal and adjust it in the General Inputs form above if needed."
            ),
        })
    return checks


def _rental_variance_check(cohorts: "pd.DataFrame") -> list:
    """Rental & Subscription: flags cohort-to-cohort churn-rate swings above 5
    percentage points, with likely contributing factors diagnosed from signals
    already in the lease_cohorts table (small active-lease sample size,
    active-lease-count swings, average-lease-value shifts, PvD shifts)."""
    checks: list = []
    if cohorts is None or "churn_rate" not in cohorts.columns:
        return checks

    by_month = cohorts.dropna(subset=["churn_rate"]).sort_values("cohort")
    deltas = by_month["churn_rate"].diff()
    flagged = by_month.loc[deltas.abs() > 0.05]
    if flagged.empty:
        return checks

    flagged_deltas = deltas.loc[flagged.index]
    active_pct = by_month["active_leases_in_month"].pct_change().loc[flagged.index]
    avg_lease_value = by_month["value_of_leases"] / by_month["new_leases"].where(
        by_month["new_leases"] > 0
    )
    avg_lease_value_pct = avg_lease_value.pct_change().loc[flagged.index]
    pvd_delta = (
        by_month["pvd"].diff().loc[flagged.index] if "pvd" in by_month.columns else None
    )

    def _diagnose(idx):
        causes = []
        active = flagged.loc[idx, "active_leases_in_month"]
        if pd.notna(active) and active < 10:
            causes.append(
                f"small active-lease sample ({int(active)} contracts) - swing may be noise"
            )
        ap = active_pct.loc[idx]
        if pd.notna(ap) and abs(ap) > 0.5:
            causes.append(
                f"active leases {'dropped' if ap < 0 else 'grew'} {abs(ap):.0%} vs "
                "prior cohort - check for missing loads or a status-mapping issue"
            )
        alv = avg_lease_value_pct.loc[idx]
        if pd.notna(alv) and abs(alv) > 0.5:
            causes.append(
                f"average lease value {'dropped' if alv < 0 else 'grew'} {abs(alv):.0%} "
                "vs prior cohort - possible portfolio mix change"
            )
        if pvd_delta is not None:
            pvd = pvd_delta.loc[idx]
            if pd.notna(pvd) and abs(pvd) > 0.10:
                causes.append(
                    f"PvD ratio shifted {pvd * 100:+.0f}pp vs prior cohort - "
                    "possible collections/servicing change"
                )
        return "; ".join(causes) if causes else (
            "No obvious data-driven cause - likely a genuine portfolio "
            "event, escalate to Borrower"
        )

    detail = pd.DataFrame({
        "Cohort": pd.to_datetime(flagged["cohort"]).dt.strftime("%b %Y"),
        "Churn Rate (%)": (flagged["churn_rate"] * 100).round(1).values,
        "Swing vs Prior Cohort (pp)": (flagged_deltas * 100).round(1).values,
        "Likely Contributing Factor(s)": [_diagnose(i) for i in flagged.index],
    })
    checks.append({
        "level": "warning",
        "check_id": "unexplained_variance",
        "message": (
            f"**Unexplained variance** - {len(detail)} cohort(s) show a churn-rate "
            "swing of more than 5 percentage points versus the prior cohort. Likely "
            "contributing factors are diagnosed below from active-lease-count, "
            "sample-size, and lease-value signals already in the data - verify "
            "before relying on these cohorts."
        ),
        "detail": detail,
    })
    return checks


def _status_mapping_check(gi, raw: pd.DataFrame) -> list[dict]:
    """Flags when a large share of this file's own `status` values aren't
    covered by General Inputs' status_map (open_label/closed_label/
    paidoff_label - see rental_analysis/general_inputs.py's DEFAULT_STATUS_MAP,
    which only recognises "Closed"/"Open"/"Repaid"/"Active"/"Write-off"
    exactly). status_mapping = raw["status"].map(status_map) silently returns
    NaN for anything else, and NaN never equals open_label/closed_label
    anywhere downstream (last_active_date, churn_rate, redeployed,
    days_to_recovery in data_input.py/lease_cohorts.py) - so an unrecognised
    status vocabulary doesn't error, it just silently zeroes out Churn
    Analysis (PCTILE_95/AVG_CHURN/STRESS_CHURN all computing to exactly 0)
    and can distort recovered/redeployed figures. This is the same failure
    mode already documented for the legacy Excel workbooks (the Status field
    never taking the literal value the churn formula filtered on) - this
    check can't fix a status vocabulary mismatch (status_map isn't yet
    user-editable in the UI), only make it visible instead of silent."""
    checks: list[dict] = []
    if raw is None or "status" not in raw.columns or raw.empty:
        return checks
    status_map = getattr(gi, "status_map", None) or {}
    mapped = raw["status"].map(status_map)
    unmapped_frac = mapped.isna().mean()
    if unmapped_frac > 0.1:
        sample = sorted(set(raw.loc[mapped.isna(), "status"].dropna().astype(str)))[:8]
        checks.append({
            "level": "warning",
            "check_id": "status_map_coverage",
            "message": (
                f"**{unmapped_frac:.0%} of rows have a Status value not recognised by the "
                f"open/closed/paid-off mapping** (recognised: {', '.join(sorted(status_map.keys()))}) "
                f"- unrecognised values seen: {', '.join(sample)}{'…' if len(sample) == 8 else ''}. "
                "An unrecognised status is silently treated as neither open nor closed, which "
                "zeroes out Churn Analysis (PCTILE_95/AVG_CHURN/STRESS_CHURN) and can distort "
                "recovered/redeployed figures - the same failure mode documented in the legacy "
                "Excel templates (the Status field never matching the literal string the churn "
                "formula filtered on)."
            ),
        })
    return checks


def _rental_data_quality_checks(
    raw: pd.DataFrame,
    cohorts: pd.DataFrame,
    rental_config: dict,
    gi=None,
    filtered: pd.DataFrame = None,
    ue_data: dict = None,
) -> list[dict]:
    """Rental & Subscription: Step 2 checks (shared) + Step 4 churn-rate
    variance diagnosis + General Inputs sanity checks (min loans/cohort and
    useful life vs this file's own data)."""
    checks = _step2_data_quality_checks(raw, rental_config) + _rental_variance_check(cohorts)
    if gi is not None and filtered is not None:
        checks += _cohort_threshold_check(gi, cohorts, filtered)
    if gi is not None and ue_data is not None:
        checks += _useful_life_sanity_check(gi, ue_data)
    if gi is not None:
        checks += _status_mapping_check(gi, raw)
    return checks
