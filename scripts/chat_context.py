"""Builds the text summary of already-computed portfolio/cohort statistics
that the Ask AI chatbot (ai_client._ask_data_chatbot) answers questions
against - never raw loan-level rows, only aggregates already shown on the
page."""

# Like app.py, this file is dominated by long human-readable prompt lines
# that can't be reflowed without changing the delivered text. See app.py's
# own top-of-file E501 note.
# ruff: noqa: E501

import pandas as pd
from html_tables import fmt

# Rental & Subscription formulas, as rental_analysis computes them (the same
# wording as the Summary / UE tooltips), so Ask AI explains the real method.
_RENTAL_CHAT_METHODOLOGY = """\
Term: each contract's term is expected end date minus start date, in days; months = days / 30.5.
Lease Tenor (m) and Average Useful Life (m): contract term weighted by cost of asset (currently the same formula).
Downpayment %: sum of downpayments / sum of cost of asset.
Margin: average total value leased per asset / average original asset cost - 1, where total value leased = cost-weighted monthly expected payment x Lease Tenor, and original cost counts each asset's first lease only.
PvD: sum of total paid / sum of amount expected to date, all contracts.
Average Collection Rate: the same ratio for open contracts only.
Utilisation Rate: assets with an open contract / all assets.
Historical % Collected on Principal: for assets that reached term, (paid + recovered) / original asset cost; the lowest of the template's four estimates is used.
Monthly Observed Repayment: assets are grouped by months on books (months since the asset's first contract, days / 30, rounded); for each group, total paid / asset cost / months on books; then the cost-weighted average over months 1-19, using groups with more assets than the minimum per cohort.
Cost of Funding over Life: (funding cost % p.a. + direct cost % p.a.) x Lease Tenor / 12.
Net UE margin: Margin - Cost of Funding over Life (not Margin minus the annual cost rates).
MRR: sum of monthly expected payment on open contracts. MRR / average cost: sum of monthly expected payment on all contracts / sum of cost of asset.
Churn (per cohort month): contracts whose last active month is that month / contracts active that month. Cohort months are only the months in which leases started. 95th %ile and average churn use cohorts with more active leases than the minimum per cohort.
Stressed churn: 95th %ile churn x churn stress multiplier, capped at 100%.
MRR survival multipliers (n = 12, 24, 36 months): (1 - (1 - c)^(n + 1)) / c with c = stressed churn; total = 1 / c.
Defaulted contracts > 3mo: contracts closed at least N months (General Inputs, months since default) before the extraction date.
% recovered: defaulted contracts whose asset was redeployed / all defaulted contracts (a count, not a value). Loss (non-recoverability) = 1 - % recovered.
Recoverability (95th %ile): from recovery amounts / lendable asset value at default when recovery amounts exist, otherwise falls back to % recovered.
Subscription margin: MRR (DSLP30-eligible MRR when payment dates exist) / sum of cost of asset. Subscription support: subscription margin x min(3-year MRR multiplier, 6), capped at 100%.
Asset-value LTV: min((Recoverability 95th %ile + subscription support, capped at 100%) / (1 + FX devaluation), 95%); FX devaluation is the General Inputs value, halved when the 50% discount is on, and 0 when blank.
Asset DSLP30: initial value of active assets with no payment in the last 30 days / initial value of all active assets; n/a without a last payment date column.
Receivables Coverage (180d): performing receivables over the next 180 days / debt service due in the next 180 days (a General Inputs value; n/a when blank).
Contracts Churned (Summary): contracts with Closed status / all contracts."""


def _build_lending_chat_context(
    gi,
    df: pd.DataFrame,
    cohorts: pd.DataFrame,
    ue_data: dict,
    ltv_data: dict,
    ue_model_ai_data: dict,
    dq_checks: list,
    user_context: str = "",
) -> str:
    lines = [
        "## Lending portfolio - summary statistics",
        f"Loans: {len(df):,}. Mapped/computed columns: {', '.join(map(str, df.columns))}.",
        f"Data extraction date: {gi.extraction_date.date()}. Days after term (T+3 cutoff): {gi.days_after_term}.",
        f"Minimum loans per cohort (template threshold): {gi.min_loans_per_cohort}.",
        f"Value of Principal Disbursed: ${df['Principal Value'].sum():,.0f}.",
        f"Total Collected: ${df['Total Paid'].sum():,.0f}.",
        "",
        "## Overview metrics",
        f"Average Loss (owed vs paid, matured loans, Reached T+3?=True): {fmt(ue_data['Average Loss'])}.",
        f"Loss Rate Proxy (1-PvD, needs Total Due mapped): {fmt(ue_data['Loss Rate Proxy (1-PvD)'])}.",
        f"Average Principal Amount per loan: ${ue_data['Average Principal Amount']:,.0f}.",
        f"Average Fee %: {fmt(ue_data['Average Fee %'])}. Average Interest %: {fmt(ue_data['Average Interest %'])}.",
        f"Average Expected Term: {fmt(ue_data['Average Expected Term'], '{:,.1f}')} days.",
        "Sense-check Margin (Interest%+Fee%, netted against Average Loss - mixes full-book revenue "
        "with matured-only losses, a known approximation): " + fmt(ue_data["Sense-check Margin"]),
        f"95th Percentile Loss Rate across cohorts (Loan Count >= {gi.min_loans_per_cohort}): "
        + fmt(ltv_data["95th Percentile Losses"]),
        f"Average Total Revenue % (interest+fees / principal): {fmt(ltv_data['Average Total Revenue %'])}.",
        "",
        "## Cohorts",
        f"{len(cohorts)} monthly cohorts by Disbursement Date with at least one matured loan "
        "(Reached T+3? = True) - a cohort with none doesn't appear at all, matching the Excel "
        "template's Cohorts pivot, which has the same Reached T+3? filter. Loan Count and Matured "
        f"Count are therefore the same figure. Columns: {', '.join(cohorts.columns)}.",
        "Loss Rate per cohort is NaN when its Matured Count is below the minimum-loans threshold "
        "(too few matured loans to be statistically meaningful).",
        # JSON, not CSV: easier for the model to parse reliably (per Niraj's suggestion in the
        # 2026-09-03 catch-up) and avoids the risk of a value containing a comma being misread
        # as a column boundary.
        cohorts.to_json(orient="records", date_format="iso"),
        "",
        "## UE Model (AI) tab - matured-loans-only unit economics",
        "This tab replicates the master UE Model workbook's 'For AI' sheet. Figures below with "
        "'(full book)' cover all loans; figures under 'Derived' are recomputed on ONLY the matured "
        "subset (Reached T+3?=True) for revenue, origination income, losses AND the GBV denominator, "
        "so margins reflect a complete, at-maturity outcome rather than mixing unrealized revenue "
        "against a small realized-loss sample.",
        f"Revenue (interest, full book): ${ue_model_ai_data['revenue']:,.0f}. "
        f"Origination Income (full book): ${ue_model_ai_data['origination_income']:,.0f}.",
        "Estimated APR (Product Interest Rate / avg weighted monthly term x 12 x 2, full book): "
        + fmt(ue_model_ai_data["implied_interest"]),
        f"Losses (matured-only $, owed-paid): ${ue_model_ai_data['losses_dollar']:,.0f}. "
        f"Losses as a literal % of total book GBV: {fmt(ue_model_ai_data['losses_pct_of_gbv'])}.",
        f"Term (m, full-book weighted average): {fmt(ue_model_ai_data['term_m'], '{:,.1f}')}.",
        f"Matured coverage: {ue_model_ai_data['n_matured']:,} of {ue_model_ai_data['n_total']:,} loans "
        f"({fmt(ue_model_ai_data['matured_gbv_pct'])} of GBV) have matured.",
        f"Derived, matured-only: Income (net losses) = ${ue_model_ai_data['income_net_losses']:,.0f} "
        f"({fmt(ue_model_ai_data['income_net_losses_margin'])} of matured-loan GBV).",
        "Derived, matured-only: Product Margin (after any user-entered variable costs) = "
        + fmt(ue_model_ai_data["product_margin"]),
        "User-entered Cost of Finance (% p.a., 0 unless the user filled it in): "
        + fmt(ue_model_ai_data["cost_of_finance_pct"]),
        "'IRR of cash flow' and the workbook's true cash-flow-basis 'Net Unit Economics' are NOT "
        "computed by this app - they'd need a full monthly repayment/loss/cost schedule.",
    ]
    if user_context.strip():
        lines += ["", "## User-provided context about this dataset", user_context.strip()]
    if dq_checks:
        lines += ["", "## Data quality checks flagged"]
        lines += [f"- ({c.get('level', 'info')}) {c.get('message', '')}" for c in dq_checks]
    return "\n".join(lines)


def _build_rental_chat_context(
    gi,
    df: pd.DataFrame,
    av: pd.DataFrame,
    cohorts: pd.DataFrame,
    ue_data: dict,
    ltv_data: dict,
    ca_data: dict,
    ts: dict,
    dq_checks: list,
    user_context: str = "",
) -> str:
    lines = [
        "## Rental & Subscription portfolio - summary statistics",
        f"Contracts: {len(df):,}. Unique assets: {len(av):,}. Mapped/computed columns: "
        + ", ".join(map(str, df.columns))
        + ".",
        f"Data extraction date: {gi.extraction_date.date()}. Useful life of asset (years): "
        + str(gi.useful_life_years),
        f"Minimum loans (contracts) per cohort: {gi.min_loans_per_cohort}.",
        "",
        "## Unit Economics",
        f"Downpayment %: {fmt(ue_data['downpayment_pct'])}. Lease Tenor (m): "
        + fmt(ue_data["lease_tenor_m"], "{:.1f}"),
        f"Margin: {fmt(ue_data['margin'])}. Utilisation Rate: {fmt(ue_data['utilisation_rate'])}.",
        f"PvD: {fmt(ue_data['pvd'])}. Historical % Collected on Principal: "
        + fmt(ue_data["historical_pct_collected_on_principal"]),
        f"Funding cost (% p.a.): {fmt(ue_data['funding_cost_pct'], '{:.2%}')}. "
        f"Direct cost (% p.a.): {fmt(ue_data['direct_cost_pct'], '{:.2%}')}. "
        f"Net UE margin (after funding + direct costs): {fmt(ue_data['net_margin'])}.",
        "",
        "## Churn Analysis",
        f"95th %ile monthly churn: {fmt(ca_data['pctile_95'], '{:.2%}')}. Average monthly churn: "
        + fmt(ca_data["avg_churn"], "{:.2%}"),
        f"Stressed churn (1.7x): {fmt(ca_data['stress_churn'], '{:.2%}')}.",
        f"1/2/3-year and total MRR survival multipliers (months of MRR expected under stressed churn): {fmt(ca_data['multiplier_1y'], '{:.2f}')} / "
        f"{fmt(ca_data['multiplier_2y'], '{:.2f}')} / {fmt(ca_data['multiplier_3y'], '{:.2f}')} / "
        + fmt(ca_data["multiplier_total"], "{:.2f}"),
        "",
        "## LTV Analysis",
        f"Average Useful Life (m): {fmt(ltv_data['avg_useful_life_m'], '{:.1f}')}. "
        f"Defaulted contracts > 3mo: {fmt(ltv_data['n_defaulted_gt_3m'], '{:.0f}')}. "
        f"Defaulted last 12m: {fmt(ltv_data['n_defaulted_last_12m'], '{:.0f}')}.",
        f"% recovered: {fmt(ltv_data['pct_recovered'])}. Recovery Rate (12m): "
        + fmt(ltv_data["pct_recovered_last_12m"]),
        f"Recoverability (95th %ile): {fmt(ltv_data['recoverability_pctile_95'])}. "
        f"Loss (non-recoverability): {fmt(ltv_data['loss_non_recoverability'])}.",
        f"MRR: {fmt(ltv_data['mrr'], '{:,.0f}')}. MRR (active <= DSLP30): "
        + fmt(ltv_data["mrr_dslp30"], "{:,.0f}"),
        f"Subscription margin: {fmt(ltv_data['subscription_margin'], '{:.2%}')}. "
        f"Subscription support: {fmt(ltv_data['subscription_support'], '{:.2%}')}. "
        f"MRR Multiplier (3y): {fmt(ltv_data['mrr_multiplier'], '{:.2f}')}.",
        f"Asset-value LTV (max 95%): {fmt(ltv_data['ltv_asset_value'], '{:.1%}')} with "
        f"FX devaluation of {fmt(ltv_data['fx_deval_used'], '{:.2%}')}.",
        "",
        "## Covenants",
        f"Asset DSLP30 (observed): {fmt(ts.get('asset_dslp30_observed'))}. "
        f"Receivables coverage (180d): {fmt(ts.get('receivables_coverage_observed'), '{:.2f}')} "
        "over the facility debt service due in the next 180 days.",
        f"Recovery Rate (observed): {fmt(ts.get('recovery_rate_observed'))}. "
        f"Paid at 6/12/24m over cost: {fmt(ts.get('paid_at_6m_over_cost'))} / "
        f"{fmt(ts.get('paid_at_12m_over_cost'))} / {fmt(ts.get('paid_at_24m_over_cost'))}.",
        "",
        "## Cohorts",
        f"{len(cohorts)} monthly cohorts. Columns: {', '.join(cohorts.columns)}. "
        "Rates in this JSON (pvd, pct_recovered, churn_rate) are fractions: 0.496 means 49.6%.",
        # JSON, not CSV: easier for the model to parse reliably (per Niraj's suggestion in the
        # 2026-09-03 catch-up) and avoids the risk of a value containing a comma being misread
        # as a column boundary.
        cohorts.to_json(orient="records", date_format="iso"),
        "",
        "## How each figure is calculated",
        _RENTAL_CHAT_METHODOLOGY,
    ]
    if user_context.strip():
        lines += ["", "## User-provided context about this dataset", user_context.strip()]
    if dq_checks:
        lines += ["", "## Data quality checks flagged"]
        lines += [f"- ({c.get('level', 'info')}) {c.get('message', '')}" for c in dq_checks]
    return "\n".join(lines)
