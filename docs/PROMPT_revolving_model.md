# Prompt: Build a Revolving Lending Model with an Outputs tab

## Goal
Build a revolving-credit-line ("RCL") analytics model, in the style of the existing
term-lending model, whose *result surface* matches the `Outputs` tab of the lending
workbook. The engine must reproduce the roll-rate / ECL methodology already present
in `rcl_rollrate_ecl_3.xlsx`. Everything must be deterministic, auditable, and
recalculate correctly from raw monthly data.

## Source files (read-only reference)
- `C:\Users\NephyNdwiga\Downloads\projects\Investments\revolving\rcl_rollrate_ecl_3.xlsx`
  - Tabs: `Monthly Report`, `Roll Rate ECL`, `Loss Rate LTV`, `Revenue Monthly`,
    `Cohort Data`, `Vintage Analysis`.
- `C:\Users\NephyNdwiga\Downloads\projects\Investments\revolving\Template SC Analysis for LTV and UE _ Revolving Lending.xlsx`
  - **Only read the `Outputs` tab** (see its exact shape below). The other tabs are
    context for the *style* of the model (inputs tab, LTV analysis, unit-economics
    analysis, cohorts), not to be migrated wholesale.

---

## 1. The target `Outputs` tab (the contract — reproduce exactly)

### 1.1 Monthly fact block (row 1 = header, one row per report month)

| Col | Field | Type | Notes |
|-----|-------|------|-------|
| A | `report_month` | date | month-start |
| B | `number_of_customers` | int | borrowers on book that month |
| C | `total_credit_line` | currency | sum of approved limits |
| D | `interest_collected` | currency | cash interest received in the month |
| E | `principal_collected` | currency | cash principal received in the month |
| F | `principal_issued` | currency | new principal drawn in the month |
| G | `principal_outstanding` | currency | closing total principal outstanding |
| H | `principal_outstanding-active` | currency | Current + New balance |
| I | `principal_outstanding-par30-60` | currency | 1–30 + 31–60 (see assumption) |
| J | `principal_outstanding-par60-90` | currency | 61–90 (see assumption) |
| K | `principal_written-off` | currency | flow written off in the month |

Sample rows provided in the workbook (Jan–Feb 2025) are illustrative; real values
come from the data. Header names, column order, and units are fixed.

### 1.2 Ratio block (rows ~8–24, same sheet, columns A–C)

| Cell | Label | Meaning |
|------|-------|---------|
| A8 | `UE / LTV` | section header |
| A9 / B9 | `Expected Tenor (m)` | months, from turnover |
| A10 / B10 | `Interest Rate (m)` | monthly interest yield |
| A11 / B11 | `Interest Rate (overall)` | tenor × monthly |
| A12 / A13 | `Loss Rate`, `95th %ile` | stressed loss rate |
| A15 / B15 | `Active` | share of book active |
| A16 / B16 | `PAR30` | share in 1–60 |
| A17 / B17 | `PAR60` | share in 61–90 |
| A18 / B18 | `PAR90` | share 91+ |
| A19 / B19 | `Write offs` | write-off rate |
| A20–A23 | `ECL`, `Loss Rate`, `Outstanding`, `number of customers` | labels |
| A24 / B24 | `% of credit line utilized` | utilisation |
| C9..C24 | `UE`, `LTV`, `today & overtime`, `UE `, `LTV` | annotations |

### 1.3 The workbook's own formulas for the ratio block (must be replicated)

```
B9  = 1 / ((SUM(E3+D3) - F3) / AVERAGE(G2:G3))          ' Expected Tenor (m)
B10 = SUM(D2:D3) / SUM(G2:G3)                            ' Interest Rate (m)
B11 = B10 * B9                                           ' Interest Rate (overall)
B15 = H2 / G2                                            ' Active %
B16 = I2 / G2                                            ' PAR30 %
B17 = J2 / H2                                            ' PAR60 % (vs active)
B18 = J2 / G2                                            ' PAR90 % (vs total)
B19 = K2 / AVERAGE(G2:G3)                                ' Write-off rate
B24 = G2 / C2                                            ' % of credit line utilised
```

Notes on the source formulas (keep faithful, but generalise):
- `B9` uses the *latest* month's `E+D-F` flow divided by average outstanding.
- `B10` and `B19` divide by `SUM` or `AVERAGE` of `G` over the shown window; make
  the window configurable (`general` vs `latest_month` vs `trailing_12m`) rather
  than hardcoding rows 2:3.
- `B15/B16/B18` use the *first* data row in the source; generalise to "as-of
  month" so the ratio block always reflects the selected as-of date.
- `B17` uses `H` (active) as denominator while `B18` uses `G`. Preserve both
  definitions explicitly; do not silently unify them.
- `A19 'WRite offs'` has no formula in the source; define it as `B19 = K/G`.

---

## 2. Pricing / unit-economics block (rows 8–24, columns C–E annotations)
Reproduce the labelled outputs so downstream consumers can key off them:
- `UE` (unit economics) — expected revenue minus expected loss per unit issued.
- `LTV` — loan-to-value product view.
- `LTV Product Interest Rate` — the contractual rate used for the product.
- The footnote row 12: `Rollrate analysis use the monthly data >` `rcl_rollrate_ecl_3.xlsx`.

---

## 3. Engine — roll-rate transition model (from `Roll Rate ECL`)

### 3.1 Buckets (fixed order)
`New` (origination month), `Current`, `PAR 1-30`, `PAR 31-60`, `PAR 61-90`,
`PAR 91-120`, `PAR 121-150`, `PAR 151-180`, `PAR 180+`, `Default/Written-off stock`.

### 3.2 Monthly inputs (from `Monthly Report`)
Counts (`N_*`) and balances (`BAL_*`) per bucket, plus:
`PRINCIPAL_OUTSTANDING`, `CREDIT_LIMIT_TOTAL`, `UTILISATION`, `PRINCIPAL_ISSUED`,
`PRINCIPAL_COLLECTED`, `PRINCIPAL_WRITTEN_OFF`, `WRITE_OFF_AMOUNT`,
`REVENUE_ACCRUED`, `REVENUE_COLLECTED`, and rates `PAR1_RATE`, `PAR30_RATE`,
`PAR90_RATE`, `DEFAULT_RATE_BAL`.

### 3.3 Roll rates (aggregate bucket-to-bucket, balances transposed months-across)
For each transition edge between consecutive months:
```
roll_rate(t) = balance_next_bucket(t+1) / balance_current_bucket(t)
```
with the in-month flow handled as in the source:
- New → Current: `=Z15/(Y14+Y13)` (denominator is prior Current + prior New).
- Current → PAR 1-30: `=Z16/Y15`.
- PAR 1-30 → PAR 31-60: `=Z17/Y16`.
- PAR 31-60 → PAR 61-90: `=Z18/Y17`.
- PAR 61-90 → PAR 91-120: `=Z24/Y18` (uses written-off flow).
- PAR 91-120 → write-off: `=(Z22-Y22)/Y18` (change in defaulted stock ÷ starting balance).
Guard every division with `IF(denominator=0,"",...)`.

### 3.4 Averaging window and selection
Provide `T3`, `T6`, `T12`, `ALL` rolling averages per edge, and a `SELECTED`
column driven by a single selector cell (`$B$6`): `=INDEX(T3:T12,MATCH(sel,headers,0))`.
Default selector `T12`.

### 3.5 Cumulative loss factor (chain)
Multiply the selected links across from each starting bucket down to write-off:
```
LF(PAR 91-120) = selected(91-120 → write-off)
LF(PAR 61-90)  = selected(61-90 → 91-120) * LF(PAR 91-120)
LF(PAR 31-60)  = selected(31-60 → 61-90)  * LF(PAR 61-90)
LF(PAR 1-30)   = selected(1-30 → 31-60)   * LF(PAR 31-60)
LF(Current+New)= selected(New/Current)     * LF(PAR 1-30)
```

### 3.6 ECL
```
ECL_bucket = balance_at_as_of(bucket) * loss_factor(bucket) * LGD
ECL_performing = SUM(ECL over New, Current, PAR 1-30..91-120)
ECL_defaulted  = defaulted_stock * 1 * LGD
TOTAL ECL      = ECL_performing + ECL_defaulted
Coverage (performing) = ECL_performing / performing_balance
Coverage (total)      = TOTAL ECL / total_balance_incl_defaulted
```
Inputs: `LGD` (default 1.0), `Roll-rate window` (`T3/T6/T12/ALL`),
`As-of month` (last data month), `Chain terminates at` (default `PAR 91-120`).

### 3.7 Data-quality checks (must be visible, not silent)
- Per-month balance identity: `ABS(SUM(bucket balances) - total) < 1` → `OK`/`CHECK`.
- Track `DUP_LOAN_MONTHS`, `SOURCE_ROWS`, `MONTHS_FILLED_BY_CARRYFWD`,
  `UNEXPLAINED` and surface them on the Outputs tab or a checks tab.
- Flag cohorts with broken denominators (balance > cumulative issued).
- Do **not** publish an ECL figure without the "OK" check for the as-of month.

---

## 4. Engine — loss rate, revenue, cohorts, vintages

### 4.1 `Loss Rate LTV`
```
loss_rate = (ECL_performing + cumulative_write_offs) * LGD / principal_issued
```
Point-in-time (each month uses only roll rates observable by then — no look-ahead).
Inputs: `LGD`, window, window length (T3=3, T6=6, T12=12, ALL=999), minimum
outstanding in sample, **stress percentile** (default 0.95 → feeds the Outputs
`95th %ile`), denominator lag in months (default 5), blend weight on lagged rate
(default 0.5 = midpoint of naive and lagged).

### 4.2 `Revenue Monthly`
```
scheduled_revenue = (PERFORMING ? performing_balance : total_balance) * monthly_rate
monthly_rate     = annual_nominal_rate / 12      ' source used 1.44 p.a. = 12%/mo
revenue_accrued  = source REVENUE_ACCRUED
revenue_collected= source REVENUE_COLLECTED
accrued/scheduled, accrued yield /mo & p.a., collected yield /mo & p.a.,
cash_conversion = collected / accrued
```
State clearly in the model that the source extract mixes interest, fees and
penalties, and that "scheduled" is derived, not taken from the data.

### 4.3 `Cohort Data` and `Vintage Analysis`
Per cohort month: `N_LOANS_COHORT`, `MAX_MOB_AVAILABLE`, `FIRST_MOB_OBSERVED`,
`COHORT_IS_LEFT_TRUNCATED`, `CUM_ISSUED_LATEST`, `CUM_WRITTEN_OFF_LATEST`,
`CUM_COLLECTED_LATEST`, `OUTSTANDING_LATEST`, `CUM_LOSS_RATE_LATEST`,
`LOSS_RATE_MOB3`, `LOSS_RATE_MOB6`, plus MOB 9/12/18/24.
Vintage table: compare cohorts **down a MOB column only** (values at different MOB
are not comparable). Seasoning horizon default MOB 12. Extrapolation ratios:
`MOB12/MOB9 = MEDIAN(...)`, `MOB12/MOB6 = MEDIAN(...)`. Exclude broken-denominator
cohorts via a flag.

---

## 5. Hard requirements
1. **Reproduce the `Outputs` tab exactly** — same field names, same column order,
   same ratio definitions, same as-of semantics.
2. **Recompute, don't hardcode.** The sample rows in the lending workbook are
   illustrative placeholders; every B-column ratio must be a live formula driven by
   the monthly fact block and a single `as_of_month` control.
3. **One source of truth per input.** LGD, window, percentile, rate, as-of month
   live in one Inputs sheet; everything else references them.
4. **Config is data, not code.** Buckets, windows, thresholds, rates, and the
   stress percentile must be configurable without editing engine code.
5. **Guard all divisions** with `IFERROR`/`IF(denom=0,...)`; never emit `#DIV/0!`.
6. **Point-in-time discipline.** Loss-rate and roll-rate statistics must never use
   future months relative to the as-of date.
7. **Balance identity check** must pass (`OK`) before any ECL or loss rate is
   considered valid; surface `CHECK` loudly.
8. **Round-trip test.** Feed the `Monthly Report` data through the engine and assert:
   - bucket balances sum to `PRINCIPAL_OUTSTANDING` each month;
   - roll rates reproduce the `Roll Rate ECL` sheet for `T12`;
   - ECL per bucket reproduces rows 48–57 for the as-of month;
   - Outputs B9–B24 reproduce the lending workbook's formula results for its sample.
9. **No runtime artifacts / secrets in source control**; keep generated outputs and
   extracts out of tracked files.
10. **Deterministic core.** All arithmetic and reconciliation in code/config; no LLM
    in the calculation path.

## 6. Deliverables
- An `Outputs` sheet with the exact §1 shape, fully formula-driven.
- An `Inputs` sheet holding every tunable from §3.6 and §4.
- Engine sheets for roll rates, cumulative loss factor, ECL, loss rate, revenue,
  cohorts, and vintages.
- A `Checks` sheet with the balance-identity and data-quality flags.
- A short README documenting confirmed vs assumed methodology and the round-trip
  test results.

## 7. Open questions to confirm before building
1. `principal_outstanding-par30-60` in Outputs col I: the name implies only 31–60,
   but the lending workbook's `B16` uses `H` (active) as the PAR30 denominator.
   Confirm the intended bucket mapping for Outputs cols H/I/J.
2. `B9` Expected Tenor uses only two rows (`E3+D3-F3`); confirm the intended
   averaging window for production.
3. Confirm the contractual annual rate (source hints 1.44 nominal p.a.) to use for
   the revenue engine.
4. Confirm whether the Outputs ratio block should reflect the as-of month or a
   trailing average.
