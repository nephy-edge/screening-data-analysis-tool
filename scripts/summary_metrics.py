"""Summary-tab derived metrics for both models, shared by app.py's Streamlit
Summary tab and web_api/main.py's /api/analyse/* response.

These figures (Outstanding GBV, PvD, Weighted Avg Term, APR Proxy, the
Portfolio Snapshot rows) aren't part of the tested engine classes
(UeAnalysis/LtvAnalysis) - they're light arithmetic combining those classes'
outputs with the processed tape. They previously lived inline in app.py's
Summary tab; extracted here so the Streamlit tab and the React/FastAPI
Summary screen can never drift apart on a figure a screening analyst reads
off the page.

Pure computation only - no Streamlit, no formatting. Callers format.
"""

import pandas as pd


def lending_summary_metrics(
    df: pd.DataFrame, gi, ue_data: dict, ltv_data: dict, active_status_values=None
) -> dict:
    """Lending Summary tab: the 5 top metrics plus every Portfolio Snapshot
    row. `df` is the processed (post process_data_input) loan tape.

    `active_status_values` narrows Outstanding GBV to the Loan Status values
    the user picked as meaning "still active/outstanding" - vocabulary isn't
    standardized across deals, so an empty/omitted selection skips that
    filter entirely (identical to Loan Status not being mapped at all).
    """
    # "Outstanding" means still within its expected term, not yet matured
    # (Reached T+3? = False) - a loan past maturity with an unrecovered
    # shortfall is a credit loss (the Loss Rate metrics), not an outstanding
    # balance still being collected.
    gbv_df = df[~df["Reached T+3?"]]
    active_status_values = active_status_values or []
    if "Loan Status" in gbv_df.columns and active_status_values:
        gbv_df = gbv_df[gbv_df["Loan Status"].astype(str).isin(active_status_values)]
    if gbv_df.empty:
        outstanding_gbv = None
    else:
        outstanding_gbv = (
            gbv_df["Principal Value"].sum()
            + gbv_df["Expected Interest"].sum()
            + gbv_df["Expected Fee"].sum()
            - gbv_df["Total Paid"].sum()
        )

    matured = df[df["Reached T+3?"]]
    matured_paid = matured["Total Paid"].sum()
    matured_due = (
        matured["Total Due"].sum() if "Total Due" in matured.columns else float("nan")
    )
    pvd_ratio = matured_paid / matured_due if matured_due else float("nan")

    # Exclude any loan missing either Term (days) or Principal Value from BOTH
    # the numerator and denominator - a loan with an unknown term must not be
    # treated as having a 0-day term, which is what including its principal in
    # the denominator alone would imply.
    term_ok = matured[["Term (days)", "Principal Value"]].dropna()
    matured_principal = term_ok["Principal Value"].sum()
    weighted_avg_term = (
        (term_ok["Term (days)"] * term_ok["Principal Value"]).sum() / matured_principal
        if matured_principal
        else float("nan")
    )
    weighted_avg_term_months = (
        weighted_avg_term / 30.4375 if pd.notna(weighted_avg_term) else float("nan")
    )
    apr_proxy = (
        (ue_data["Average Interest %"] / weighted_avg_term_months) * 12
        if weighted_avg_term_months
        else float("nan")
    )

    return {
        # Top metrics
        "dataUpTo": str(df["Disbursement Date"].max().date()),
        "outstandingGbv": outstanding_gbv,
        "lossRate": ue_data["Average Loss"],
        "pctile95LossRate": ltv_data["95th Percentile Losses"],
        "productInterestRate": ue_data["Average Interest %"],
        # Portfolio Snapshot
        "dateRangeStart": str(df["Disbursement Date"].min().date()),
        # The tape's as-of date, NOT the last disbursement: the snapshot row is
        # labelled "...to the tape as-of date" in both UIs, and a tape can be
        # extracted months after its newest loan went out. `dataUpTo` above is
        # the last disbursement, so making this one extraction_date is also
        # what stops the two from being the same number twice.
        "dateRangeEnd": str(pd.Timestamp(gi.extraction_date).date()),
        "loans": int(len(df)),
        "principalDisbursed": df["Principal Value"].sum(),
        "totalCollected": df["Total Paid"].sum(),
        "expectedInterestPct": ue_data["Average Interest %"],
        "expectedFeePct": ue_data["Average Fee %"],
        "avgLoanValue": ue_data["Average Principal Amount"],
        "avgTotalRevenuePct": ltv_data["Average Total Revenue %"],
        "pvd": pvd_ratio,
        "weightedAvgTermMatured": weighted_avg_term,
        "aprProxy": apr_proxy,
    }


def rental_summary_metrics(
    df: pd.DataFrame, av: pd.DataFrame, gi, ue_data: dict, ltv_data: dict, ts: dict
) -> dict:
    """Rental & Subscription Summary tab: the headline metrics, the
    "LTV & UE highlights" row, and every Portfolio Snapshot row. `df` is the
    processed contract tape, `av` the asset view."""
    closed_contracts = int((df["status_mapping"] == gi.closed_label).sum())
    open_contracts = int((df["status_mapping"] == gi.open_label).sum())
    mrr = ltv_data["mrr"]

    return {
        # Headline metrics
        "contracts": int(len(av)),
        "mrr": mrr,
        "avgMrrPerActiveContract": (mrr / open_contracts) if open_contracts else None,
        "avgUsefulLifeM": ltv_data["avg_useful_life_m"],
        "pctile95Churn": ltv_data["pctile_95_churn"],
        "stressedChurn": ltv_data["stressed_churn"],
        "contractsChurned": (closed_contracts / len(df)) if len(df) else None,
        "defaultedGt3m": ltv_data["n_defaulted_gt_3m"],
        "pctRecovered": ltv_data["pct_recovered"],
        "lossNonRecoverability": ltv_data["loss_non_recoverability"],
        "mrrOverAvgCost": ltv_data["mrr_over_avg_cost"],
        "avgCollectionRate": ltv_data["avg_collection_rate"],
        # LTV & UE highlights
        "assetLtv": ltv_data["ltv_asset_value"],
        "netUeMargin": ue_data["net_margin"],
        "assetDslp30": ltv_data["asset_dslp30"],
        "receivablesCoverage180d": ts.get("receivables_coverage_observed"),
        # Portfolio Snapshot
        "dateRangeStart": str(df["start_date"].min().date()),
        # As-of date, not the last contract start - see the lending note above.
        "dateRangeEnd": str(pd.Timestamp(gi.extraction_date).date()),
        "contractRecords": int(len(df)),
        "uniqueAssets": int(len(av)),
        "totalCollected": df["total_paid"].sum(),
        "totalCostOfAssets": df["cost_of_asset"].sum(),
        "downpaymentPct": ue_data["downpayment_pct"],
        "leaseTenorM": ue_data["lease_tenor_m"],
        "margin": ue_data["margin"],
        "pvd": ue_data["pvd"],
        "utilisationRate": ue_data["utilisation_rate"],
        "historicalPctCollectedOnPrincipal": ue_data["historical_pct_collected_on_principal"],
    }
