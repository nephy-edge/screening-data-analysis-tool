-- Collapses MAESTRO_SRC.SALMON.SALMON_INSTALLMENT_LOANS (a monthly
-- performance panel - one row per loan per calendar month) into one row per
-- loan, matching what the Lending engine (scripts/template_analysis)
-- expects. All source columns are VARCHAR, hence the TRY_TO_* casts.
--
-- Field definitions confirmed with the business owner:
--   - Expected Interest = lifetime accrued interest.
--   - Expected Fee      = lifetime accrued fees + penalties (the engine's
--                         revenue model only has an Interest/Fee split, so
--                         penalties are folded into "fee" as the closest
--                         "other charges" bucket - flag if that's wrong).
--   - Total Due   = Principal Value + Expected Interest + Expected Fee
--                   (contractual obligation over the loan's life) - kept
--                   alongside the Interest/Fee split since UeAnalysis's
--                   loss_rate_proxy()/cohorts.py's PvD Ratio use it
--                   directly, even though the primary Loss Rate calc in
--                   cohorts.py deliberately uses Principal+Interest+Fee
--                   instead (verified against real deals - see that file).
--   - Total Paid  = lifetime collected principal + interest + fees +
--                   penalties (*_COLLECTED is stored as a negative delta in
--                   the source, hence the negation) - post-write-off
--                   *_RECOVERED amounts are excluded on purpose.
--   - ORIGINAL_TERM is in months.
--
-- Re-run this any time the source table's data changes shape; the view
-- itself always reflects the latest rows (not materialized).
CREATE OR REPLACE VIEW MAESTRO_DEV_ANA.SALMON_INSTALLMENT_LOANS_SC.SALMON_INSTALLMENT_LOANS_FLAT_V2 AS
SELECT
    LOANID,
    MIN(TRY_TO_DATE(ORIGINATION_DATE)) AS DISBURSEMENT_DATE,
    DATEADD(
        'month',
        MAX(TRY_TO_DECIMAL(ORIGINAL_TERM, 10, 0)),
        MIN(TRY_TO_DATE(ORIGINATION_DATE))
    ) AS EXPECTED_COMPLETION_DATE,
    SUM(TRY_TO_DECIMAL(PRINCIPAL_ISSUED, 18, 2)) AS PRINCIPAL_VALUE,
    SUM(TRY_TO_DECIMAL(INTEREST_ACCRUED, 18, 2)) AS EXPECTED_INTEREST,
    SUM(TRY_TO_DECIMAL(FEES_ACCRUED, 18, 2))
        + SUM(TRY_TO_DECIMAL(PENALTIES_ACCRUED, 18, 2)) AS EXPECTED_FEE,
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
FROM MAESTRO_SRC.SALMON.SALMON_INSTALLMENT_LOANS
GROUP BY LOANID;
