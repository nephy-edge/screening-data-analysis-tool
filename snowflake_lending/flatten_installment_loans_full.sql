-- One row per loan, keeping EVERY column of
-- MAESTRO_SRC.SALMON.SALMON_INSTALLMENT_LOANS - the wide counterpart to
-- flatten_installment_loans.sql (..._FLAT_V2), which keeps only the eight
-- fields the Lending engine strictly needs.
--
-- Why this exists: the source is a monthly performance panel (30,114,880
-- rows over 3,462,176 distinct loans, ~8.7 rows per loan). The narrow view
-- is enough to run LTV/UE, but throws away DPD, write-offs, recoveries,
-- product group and outstanding balance - all of which are worth having for
-- data-quality checks and for the AI chat's context. Flattened, this is
-- 3,462,176 x 38 ~= 131.6M cells, just under the app's 150M-cell fetch cap
-- (scripts/warehouse.py MAX_RESULT_CELLS). See the note at the bottom about
-- that headroom.
--
-- THE CENTRAL DISTINCTION - confirmed against real rows, not assumed:
--
--   * FLOW columns are monthly deltas and must be SUMmed. Verified on loan
--     617655: PRINCIPAL_ISSUED is 3954.63 in the origination month and 0.00
--     in all twelve later months, while PRINCIPAL_COLLECTED sums to exactly
--     -3954.63 across those months. (The *_COLLECTED / *_ADJUSTED /
--     *_WRITTEN_OFF families are stored NEGATIVE in the source - see the
--     sign note below.)
--
--   * STOCK columns are point-in-time and must NOT be summed - summing a
--     balance across months is meaningless. The same loan's
--     PRINCIPAL_OUTSTANDING_BALANCE runs 3954.63 -> 1255.85 -> 0.00 as it
--     amortises. These take the value from the LATEST reporting date via
--     MAX_BY(col, BUSINESS_DT).
--
-- Which columns are genuinely constant per loan was measured across all
-- 3.46M loans rather than eyeballed:
--
--   BORROWER_ID, ORIGINATION_DATE, ORIGINAL_TERM, INTEREST_RATE and
--   PRODUCT_GROUP never vary within a loan (0 loans with >1 distinct
--   value), so MAX() over the group is exact, not a guess.
--
--   ENTITY_LEGAL DOES vary, in 2,073,914 loans (60%) - a loan's legal
--   entity changes over its life, so it is taken as-of the latest reporting
--   date rather than collapsed with MAX(). Worth understanding before
--   slicing by entity: if that reflects a real book transfer, "the entity"
--   for a loan depends on when you ask.
--
--   LOAN_CLOSE_DATE varies in 730,842 loans and WRITE_OFF_DATE in 533,624,
--   so both are taken as the latest value rather than treated as fixed.
--
-- SIGNS ARE LEFT AS THE SOURCE STORES THEM. The *_COLLECTED columns are
-- negative here, unlike ..._FLAT_V2's TOTAL_PAID which negates them. This
-- view is a faithful flattening; the two derived engine columns at the end
-- do the negation, so both conventions are available and neither is hidden.
--
-- All source columns are VARCHAR, hence the TRY_TO_* casts throughout.
--
-- EVERY DATE IS RETURNED AS AN ISO STRING, not a DATE. Snowflake's SQL
-- Statement API (which is how this app fetches, see scripts/warehouse.py)
-- serialises a DATE as days-since-epoch: ORIGINATION_DATE arrived as the
-- integer 20080 rather than 2024-12-23, which the engine's date detection
-- would read as a number, not a date. TO_CHAR(..., 'YYYY-MM-DD') keeps the
-- dates dates by the time they reach pandas. Note the same trap applies to
-- any DATE column selected through that API, this view or not.
CREATE OR REPLACE VIEW MAESTRO_DEV_ANA.SALMON_INSTALLMENT_LOANS_SC.SALMON_INSTALLMENT_LOANS_FLAT_FULL AS
WITH panel AS (
    SELECT *, TRY_TO_DATE(BUSINESS_DT) AS _AS_OF
    FROM MAESTRO_SRC.SALMON.SALMON_INSTALLMENT_LOANS
)
SELECT
    LOANID,

    -- Constant per loan (verified: 0 loans with more than one value).
    MAX(BORROWER_ID) AS BORROWER_ID,
    TO_CHAR(MIN(TRY_TO_DATE(ORIGINATION_DATE)), 'YYYY-MM-DD') AS ORIGINATION_DATE,

    -- The panel's own reporting dimension. Collapsed to the loan's latest
    -- observation, so BUSINESS_DT doubles as "as of when is this row true"
    -- for every stock column below. PERIOD_FROM/PERIOD_TO become the full
    -- observed window rather than one month.
    MAX(BUSINESS_MT) AS BUSINESS_MT,
    TO_CHAR(MAX(_AS_OF), 'YYYY-MM-DD') AS BUSINESS_DT,
    TO_CHAR(MIN(TRY_TO_DATE(PERIOD_FROM)), 'YYYY-MM-DD') AS PERIOD_FROM,
    TO_CHAR(MAX(TRY_TO_DATE(PERIOD_TO)), 'YYYY-MM-DD') AS PERIOD_TO,

    MAX(TRY_TO_DECIMAL(ORIGINAL_TERM, 10, 0)) AS ORIGINAL_TERM,
    MAX(TRY_TO_DECIMAL(INTEREST_RATE, 18, 6)) AS INTEREST_RATE,
    MAX(PRODUCT_GROUP) AS PRODUCT_GROUP,

    -- Varies within 60% of loans - as-of latest, not MAX (see header).
    MAX_BY(ENTITY_LEGAL, _AS_OF) AS ENTITY_LEGAL,

    -- STOCKS: point-in-time, as of the latest reporting date. Note this is
    -- the loan's CURRENT delinquency, not its worst - a loan that hit 100
    -- DPD and then cured reads 0 here. Add MAX(TRY_TO_DECIMAL(DPD, 10, 0))
    -- as a separate PEAK_DPD column if the worst-ever is what you need;
    -- they answer different questions and loan 617655 shows both (peaks at
    -- 100, closes at 0).
    MAX_BY(TRY_TO_DECIMAL(DPD, 10, 0), _AS_OF) AS DPD,
    MAX_BY(DPD_BUCKET, _AS_OF) AS DPD_BUCKET,
    MAX_BY(TRY_TO_DECIMAL(PRINCIPAL_OUTSTANDING_BALANCE, 18, 2), _AS_OF)
        AS PRINCIPAL_OUTSTANDING_BALANCE,

    -- FLOWS: monthly deltas, summed over the loan's life.
    SUM(TRY_TO_DECIMAL(PRINCIPAL_ISSUED, 18, 2)) AS PRINCIPAL_ISSUED,
    SUM(TRY_TO_DECIMAL(PRINCIPAL_COLLECTED, 18, 2)) AS PRINCIPAL_COLLECTED,
    SUM(TRY_TO_DECIMAL(PRINCIPAL_ADJUSTED, 18, 2)) AS PRINCIPAL_ADJUSTED,
    SUM(TRY_TO_DECIMAL(PRINCIPAL_WRITTEN_OFF, 18, 2)) AS PRINCIPAL_WRITTEN_OFF,
    SUM(TRY_TO_DECIMAL(PRINCIPAL_RECOVERED, 18, 2)) AS PRINCIPAL_RECOVERED,

    SUM(TRY_TO_DECIMAL(INTEREST_ACCRUED, 18, 2)) AS INTEREST_ACCRUED,
    SUM(TRY_TO_DECIMAL(INTEREST_COLLECTED, 18, 2)) AS INTEREST_COLLECTED,
    SUM(TRY_TO_DECIMAL(INTEREST_ADJUSTED, 18, 2)) AS INTEREST_ADJUSTED,
    SUM(TRY_TO_DECIMAL(INTEREST_WRITTEN_OFF, 18, 2)) AS INTEREST_WRITTEN_OFF,
    SUM(TRY_TO_DECIMAL(INTEREST_RECOVERED, 18, 2)) AS INTEREST_RECOVERED,

    SUM(TRY_TO_DECIMAL(FEES_ACCRUED, 18, 2)) AS FEES_ACCRUED,
    SUM(TRY_TO_DECIMAL(FEES_COLLECTED, 18, 2)) AS FEES_COLLECTED,
    SUM(TRY_TO_DECIMAL(FEES_ADJUSTED, 18, 2)) AS FEES_ADJUSTED,
    SUM(TRY_TO_DECIMAL(FEES_WRITTEN_OFF, 18, 2)) AS FEES_WRITTEN_OFF,
    SUM(TRY_TO_DECIMAL(FEES_RECOVERED, 18, 2)) AS FEES_RECOVERED,
    SUM(TRY_TO_DECIMAL(UPFRONT_FEES_CHARGED, 18, 2)) AS UPFRONT_FEES_CHARGED,

    SUM(TRY_TO_DECIMAL(PENALTIES_ACCRUED, 18, 2)) AS PENALTIES_ACCRUED,
    SUM(TRY_TO_DECIMAL(PENALTIES_COLLECTED, 18, 2)) AS PENALTIES_COLLECTED,
    SUM(TRY_TO_DECIMAL(PENALTIES_ADJUSTED, 18, 2)) AS PENALTIES_ADJUSTED,
    SUM(TRY_TO_DECIMAL(PENALTIES_WRITTEN_OFF, 18, 2)) AS PENALTIES_WRITTEN_OFF,
    SUM(TRY_TO_DECIMAL(PENALTIES_RECOVERED, 18, 2)) AS PENALTIES_RECOVERED,

    -- Event fields: latest value, since both were measured to change within
    -- a loan. WRITE_OFF_AMOUNT is a flow and is summed.
    TO_CHAR(MAX(TRY_TO_DATE(WRITE_OFF_DATE)), 'YYYY-MM-DD') AS WRITE_OFF_DATE,
    SUM(TRY_TO_DECIMAL(WRITE_OFF_AMOUNT, 18, 2)) AS WRITE_OFF_AMOUNT,
    TO_CHAR(MAX(TRY_TO_DATE(LOAN_CLOSE_DATE)), 'YYYY-MM-DD') AS LOAN_CLOSE_DATE,

    -- ---------------------------------------------------------------
    -- Beyond the 38 source columns: the two derived fields the Lending
    -- engine needs, defined identically to ..._FLAT_V2 so both views agree.
    -- Drop these two if you want a pure flattening - they cost ~7M cells.
    --
    -- Total Due = principal + interest + fees + penalties accrued over the
    -- loan's life. Total Paid negates the *_COLLECTED columns (stored
    -- negative in the source) and deliberately EXCLUDES post-write-off
    -- *_RECOVERED amounts, matching ..._FLAT_V2.
    -- ---------------------------------------------------------------
    SUM(TRY_TO_DECIMAL(PRINCIPAL_ISSUED, 18, 2))
        + SUM(TRY_TO_DECIMAL(INTEREST_ACCRUED, 18, 2))
        + SUM(TRY_TO_DECIMAL(FEES_ACCRUED, 18, 2))
        + SUM(TRY_TO_DECIMAL(PENALTIES_ACCRUED, 18, 2)) AS TOTAL_DUE,
    -1 * (
        SUM(TRY_TO_DECIMAL(PRINCIPAL_COLLECTED, 18, 2))
        + SUM(TRY_TO_DECIMAL(INTEREST_COLLECTED, 18, 2))
        + SUM(TRY_TO_DECIMAL(FEES_COLLECTED, 18, 2))
        + SUM(TRY_TO_DECIMAL(PENALTIES_COLLECTED, 18, 2))
    ) AS TOTAL_PAID
FROM panel
GROUP BY LOANID;

-- HEADROOM WARNING: at 40 columns this is ~138.5M cells against a 150M cap,
-- about 8% spare, and the loan count only grows. When it crosses, either
-- raise MAX_RESULT_CELLS in scripts/warehouse.py (bounded by real RAM - the
-- cap exists because a 9.1M-row fetch once OOM-killed the API process), or
-- select a column subset rather than SELECT *. The narrow ..._FLAT_V2 stays
-- the right choice when only LTV/UE is needed.
