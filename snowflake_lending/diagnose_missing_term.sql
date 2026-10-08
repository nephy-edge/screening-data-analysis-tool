-- Diagnostic: why ~15,189 loans have no EXPECTED_COMPLETION_DATE, and whether
-- their ORIGINAL_TERM can be recovered.
--
-- Background. flatten_installment_loans.sql derives the completion date as
-- DATEADD('month', MAX(TRY_TO_DECIMAL(ORIGINAL_TERM, 10, 0)), MIN(TRY_TO_DATE(
-- ORIGINATION_DATE))). Every source column is VARCHAR, and TRY_TO_* yields NULL
-- rather than failing, so an unparseable term silently produces a NULL end date.
-- proc_handler.py then drops those loans outright before the engine runs.
--
-- The counts say the term, not the date, is the culprit: only 4 loans lost their
-- DISBURSEMENT_DATE (which would null both columns), against 15,193 total drops.
-- So ~15,189 loans have a good origination date and an unusable term.
--
-- Note the view groups by LOANID and takes MAX(TRY_TO_DECIMAL(ORIGINAL_TERM)).
-- The source is a monthly panel - one row per loan per calendar month - so a
-- loan only fails if EVERY one of its rows has an unparseable term. These are
-- not scattered nulls; they are loans where the field is unusable throughout,
-- which usually means one product, one vintage, or one bad load.
--
-- Run these in order. Query 1 decides what Queries 3 and 4 can do.

-- ---------------------------------------------------------------------------
-- 1. What else is in the source panel? Determines what we can segment and
--    impute by (product, branch, channel, a second term column, ...).
-- ---------------------------------------------------------------------------
SELECT COLUMN_NAME, DATA_TYPE
FROM MAESTRO_SRC.INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'SALMON'
  AND TABLE_NAME = 'SALMON_INSTALLMENT_LOANS'
ORDER BY ORDINAL_POSITION;

-- ---------------------------------------------------------------------------
-- 2. What do the unparseable ORIGINAL_TERM values actually look like?
--    This is the question that decides everything downstream. A blank or a
--    literal 'NULL'/'N/A' means the data is genuinely absent and has to be
--    imputed. Something like '12 months', '12.0', ' 12' or a thousands
--    separator means the value is there and only the cast is too strict -
--    in which case the fix is a better parse, not an imputation.
-- ---------------------------------------------------------------------------
WITH bad_loans AS (
    SELECT LOANID
    FROM MAESTRO_SRC.SALMON.SALMON_INSTALLMENT_LOANS
    GROUP BY LOANID
    HAVING MAX(TRY_TO_DECIMAL(ORIGINAL_TERM, 10, 0)) IS NULL
)
SELECT
    -- Quote it so trailing spaces and empty-vs-NULL are visible in the output.
    '[' || COALESCE(s.ORIGINAL_TERM, '<SQL NULL>') || ']' AS raw_term_value,
    COUNT(*)                        AS panel_rows,
    COUNT(DISTINCT s.LOANID)        AS loans
FROM MAESTRO_SRC.SALMON.SALMON_INSTALLMENT_LOANS s
JOIN bad_loans b ON b.LOANID = s.LOANID
GROUP BY 1
ORDER BY panel_rows DESC
LIMIT 50;

-- ---------------------------------------------------------------------------
-- 3. Do they cluster, or are they spread evenly across the book?
--    This is the question that decides whether dropping them was ever safe.
--    0.44% of the tape spread evenly is noise; 0.44% concentrated in two
--    months is most of those cohorts.
-- ---------------------------------------------------------------------------
WITH per_loan AS (
    SELECT
        LOANID,
        MIN(TRY_TO_DATE(ORIGINATION_DATE))                      AS origination_date,
        MAX(TRY_TO_DECIMAL(ORIGINAL_TERM, 10, 0))               AS term_months
    FROM MAESTRO_SRC.SALMON.SALMON_INSTALLMENT_LOANS
    GROUP BY LOANID
)
SELECT
    DATE_TRUNC('month', origination_date)                       AS cohort,
    COUNT(*)                                                    AS loans,
    COUNT_IF(term_months IS NULL)                               AS loans_missing_term,
    ROUND(100.0 * COUNT_IF(term_months IS NULL) / COUNT(*), 2)  AS pct_missing
FROM per_loan
WHERE origination_date IS NOT NULL
GROUP BY 1
ORDER BY 1;

-- ---------------------------------------------------------------------------
-- 4. If the term has to be imputed, what would we impute from? This is the
--    shape of the healthy population, as a whole and by origination month.
--    A term that is one dominant value (say 92% at 12 months) is safe to
--    impute; a broad spread is not, and those loans stay unmatured.
--
--    Add whatever product/segment column Query 1 turns up to both the SELECT
--    and the GROUP BY - a per-product median is far better than a global one.
-- ---------------------------------------------------------------------------
WITH per_loan AS (
    SELECT
        LOANID,
        MIN(TRY_TO_DATE(ORIGINATION_DATE))            AS origination_date,
        MAX(TRY_TO_DECIMAL(ORIGINAL_TERM, 10, 0))     AS term_months
    FROM MAESTRO_SRC.SALMON.SALMON_INSTALLMENT_LOANS
    GROUP BY LOANID
)
SELECT
    DATE_TRUNC('month', origination_date)             AS cohort,
    COUNT(*)                                          AS loans_with_term,
    MEDIAN(term_months)                               AS median_term_months,
    MODE(term_months)                                 AS modal_term_months,
    MIN(term_months)                                  AS min_term,
    MAX(term_months)                                  AS max_term,
    -- How concentrated the term is. Near 100% means a single contractual
    -- term dominates that month and imputing it is low-risk.
    ROUND(100.0 * COUNT_IF(term_months = MODE(term_months)) / COUNT(*), 1)
                                                      AS pct_at_modal_term
FROM per_loan
WHERE term_months IS NOT NULL
  AND origination_date IS NOT NULL
GROUP BY 1
ORDER BY 1;
