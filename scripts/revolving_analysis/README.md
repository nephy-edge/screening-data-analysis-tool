# Revolving Lending (RCL) model

The Revolving Lending model analyses a revolving credit-line book from a **monthly report**: one row per month for the whole book. It produces:

- the Revolving Lending template's **Outputs** tab (the fact block and UE / LTV ratios)
- a **roll-rate transition model with expected credit loss (ECL)**, following `rcl_rollrate_ecl_3.xlsx`
- a **backtest and stress scenarios**
- a **facility view**: the advance rate and covenant tests

All calculations are deterministic Python (`scripts/revolving_analysis/`). No AI is involved in any figure.

---

## 1. How to run it

The steps are the same as for the Lending and Rental models:

1. **Upload.** Choose **Revolving Lending** in the model picker, then upload a CSV or XLSX (pick the sheet if the workbook has several).
2. **Map columns.** Match your columns to the template fields (section 2). **Auto-fill** matches by name first.
3. **General Inputs.** Review the assumptions (section 3). Every default comes from `config.toml` under `[revolving_defaults]`.
4. **Run analysis →.** One run calculates everything. The results have four tabs (section 4).

Export (Excel), Save and Share work as they do for the other models.

---

## 2. Input data

### Shape

- One row per **report month** for the whole book, not one row per loan.
- Each month may appear only once. The run stops if a month is repeated.
- Rows blank in every mapped column, such as trailing summary rows, are ignored.
- A row that has data but no readable month stops the run.
- Months should be consecutive. A gap is reported as a warning, because roll rates across it pair months that aren't next to each other.

### Required fields: the Outputs columns

These 11 fields are the template's Outputs header (A1:K1). The letters are used in the formulas below.

| Col | Field | Meaning |
|---|---|---|
| A | `report_month` | Report month (any date in the month) |
| B | `number_of_customers` | Borrowers on book in the month |
| C | `total_credit_line` | Sum of approved credit limits |
| D | `interest_collected` | Cash interest collected in the month |
| E | `principal_collected` | Cash principal repaid in the month |
| F | `principal_issued` | New principal drawn in the month |
| G | `principal_outstanding` | Closing principal outstanding, **including** defaulted stock |
| H | `principal_outstanding-active` | Balance not past due (New + Current) |
| I | `principal_outstanding-par30-60` | Balance 1–60 days past due |
| J | `principal_outstanding-par60-90` | Balance 61–90 days past due |
| K | `principal_written-off` | Principal written off in the month (a flow) |

### Optional fields

| Fields | Effect |
|---|---|
| `BAL_NEW`, `BAL_CURRENT`, `BAL_PAR_1_30`, `BAL_PAR_31_60`, `BAL_PAR_61_90`, `BAL_PAR_91_120`, `BAL_DEFAULT` | When **all seven** are mapped, the **full roll-rate model** runs (it matches `rcl_rollrate_ecl_3.xlsx` exactly). Otherwise the **3-link approximation** runs (section 5.2). They also enable the performing-book metrics. |
| `BAL_PAR_121_150`, `BAL_PAR_151_180`, `BAL_PAR_180_PLUS` | Balances past the end of the chain. Treated as 0 if not mapped. |
| `DUP_LOAN_MONTHS`, `MONTHS_FILLED_BY_CARRYFWD`, `SOURCE_ROWS` | The provider's own data-quality counts, shown in the checks. |
| `UNEXPLAINED` | The provider's own unexplained balance movement. When mapped, the app checks that its own roll-forward agrees with it. |
| `recoveries` | Cash recovered in the month on written-off balances. Lets LGD be derived instead of entered. |

There is **no currency conversion**: figures stay in the source currency, and the loss-rate sample threshold is in that currency too.

---

## 3. General Inputs

Defaults are in `config.toml` → `[revolving_defaults]`.

### Roll-rate & ECL

| Input | Default | What it does |
|---|---|---|
| As-of month | last month in the data | Every tab is calculated at this month, using data up to it only. |
| Roll-rate averaging window | `T12` | How many months of roll rates are averaged into the SELECTED rate. The choices come from `[revolving_defaults.roll_rate_windows]` (default T3 = 3, T6 = 6, T12 = 12, ALL = every month); add a window there to offer it. |
| LGD | 1.0 | Share of defaulted balance that is lost (1.0 = no recovery). |
| Outputs ratio window | 2 months | Window, ending at the as-of month, for the averages in the Outputs ratios. The template uses 2 rows. |

### Loss rate (feeds the Outputs Loss Rate and 95th %ile)

| Input | Default | What it does |
|---|---|---|
| Loss-rate basis | `Blended` | Naive / Blended / Lagged (section 5.3). |
| Stress percentile | 0.95 | Percentile reported as "95th %ile". |
| Minimum outstanding to be in sample | 1,000,000,000 | Leaves out the ramp-up months. **Deal-specific, in the source currency.** |
| Denominator lag | 5 months | Lag applied to cumulative issuance for the Lagged basis. |
| Blend weight on the lagged rate | 0.5 | 0 = Naive only, 1 = Lagged only. |

### LGD & unit economics

| Input | Default | What it does |
|---|---|---|
| LGD basis | `Input` | `Recoveries` derives LGD as 1 − Σ recoveries ÷ Σ written off (needs the `recoveries` column). |
| Cost of funds | 13% p.a. | Annual rate on outstanding. Same default as the Rental model. |
| Operating costs | 2% p.a. | Annual rate on outstanding. |
| Advance-rate haircut | 0 | Extra haircut on the advance rate. |

### Backtest & early warning

| Input | Default | What it does |
|---|---|---|
| Backtest history | 12 months | Months of history needed before the first backtest month. |
| Early-warning lookback | 3 months | Window the as-of value is compared against. |
| Early-warning threshold | 0.2 | Flags a metric that is more than 20% worse (relative) than its lookback average. |

### Settings in `config.toml` only

These aren't on the General Inputs screen. Change them in `config.toml` → `[revolving_defaults]`.

| Setting | Default | What it does |
|---|---|---|
| `approx_writeoff_lag_months` | 2 | Approximation only: months from PAR 61-90 to write-off. |
| `stress_roll_rate_cap` | 1.0 | Ceiling on a stressed roll rate (unless the observed rate is already above it). |
| `balance_tolerance` | 1.0 | How closely the balances must tie to G before the ECL is shown. |
| `unexplained_warning_pct` | 0.05 | Warn when unexplained movement exceeds this share of outstanding. |

Everything else, such as the template's column names and formulas, the full model's chain and the metric definitions, is part of the model's method and stays in code.

### Stress scenarios and covenants

- **Scenarios:** an editable table of name, roll-rate multiplier and LGD multiplier. Defaults: Base 1.0 / 1.0, Stress 1.25 / 1.1, Severe 1.5 / 1.25.
- **Covenants:** an editable table of metric, test (≤ or ≥) and limit. The defaults are **example limits only**:
  - PAR 91+ ≤ 15%
  - write-off rate ≤ 3% a month
  - ECL coverage (performing) ≤ 25%

  Set them to the deal's facility terms.

---

## 4. The results tabs

### Outputs

1. **Warnings.** For example: approximation in use, balances not tying, unexplained movement above 5% of outstanding, months missing.
2. **UE / LTV ratio block.** The template's rows 9–24, calculated at the as-of month with the **template's own formulas and denominators** (section 5.1).
3. **Monthly fact block.** Columns A–K for every month up to the as-of month.
4. **Point-in-time loss rate.** The distribution of the in-sample monthly loss rates: mean, median, percentiles and stress percentile. It feeds Loss Rate and 95th %ile.
5. **Standard-definition metrics.** Proper credit definitions shown **next to** the template's ratios, which stay as defined (section 5.4).
6. **Unit economics after costs.** Interest yield − write-offs − cost of funds − operating costs, per dollar outstanding: monthly, annualised, and over the principal-turnover tenor.
7. **Early warnings.** Each metric at the as-of month against its lookback average, with a flag where it is worse than the threshold.
8. **Metrics over time.** Every metric, month by month.

### Transition Matrices

1. **Method and headline figures:** full or approximation, as-of month, window, total ECL, performing and total coverage.
2. **Balances by bucket**, month by month.
3. **Roll rates:** each month's rate, then T3 / T6 / T12 / ALL / SELECTED.
4. **Cumulative loss factor:** the SELECTED links multiplied from each bucket down to write-off.
5. **Expected credit loss:** balance × loss factor × LGD for each bucket at the as-of month.
6. **Data quality:**
   - the balance identity (OK / CHECK)
   - the **roll-forward check** for each month: closing − (opening + issued − collected − written off), in currency and as a % of outstanding, plus agreement with `UNEXPLAINED` when mapped
   - the provider's own data-quality counts

### Backtest & Stress

1. **Backtest:** for every past month with enough history, the chain is recalculated from data up to that month only. Its forecast of write-offs is compared with what was actually written off afterwards (section 5.5). Summary figures:
   - months tested
   - actual ÷ forecast, pooled
   - median and range of the monthly ratios
   - mean absolute error

   **Below 1 means the forecast was conservative.**
2. **Stress scenarios:** for each scenario, the stressed LGD, total ECL, coverage, the as-of loss rate and the implied advance rate. **Base (1.0 / 1.0) reproduces the unstressed figures.**

### Facility

1. **Advance rate** = MAX(0, 1 − stress loss rate − haircut), using the Outputs 95th %ile as the stress loss rate. This is `rcl_rollrate_ecl_3.xlsx`, 'Loss Rate LTV' row 65.
2. **Covenant tests:** value, limit, headroom (negative means breached) and PASS / BREACH / N/A. N/A means the metric needs the bucket balances.

---

## 5. Methodology

### 5.1 Outputs ratio block (template rows 9–24)

"Window" means the ratio window ending at the as-of month; "as-of" means the as-of month's row.

| Row | Metric | Formula |
|---|---|---|
| 9 | Expected Tenor (m) | 1 ÷ ((E + D − F) at as-of ÷ average G over the window) |
| 10 | Interest Rate (m) | Σ D ÷ Σ G over the window |
| 11 | Interest Rate (overall) | row 10 × row 9 |
| 12 / 13 | Loss Rate / 95th %ile | Point-in-time loss rate: mean / stress percentile, on the chosen basis |
| 15 | Active | H ÷ G |
| 16 | PAR30 | I ÷ G (this is 1–60 days past due) |
| 17 | PAR60 | J ÷ H (61–90, divided by *active*) |
| 18 | PAR90 | J ÷ G (this is 61–90 days past due, **not** 91+) |
| 19 | Write offs | K ÷ average G |
| 20 | ECL | Total ECL at as-of |
| 21 | Loss Rate | ECL ÷ G |
| 22 / 23 | Outstanding / number of customers | G / B at as-of |
| 24 | % of credit line utilised | G ÷ C |

These follow the template exactly, including its non-standard definitions. Use the standard-definition block (section 5.4) for true PAR30/90. The template's tenor formula (row 9) **turns negative** in growth months, when issuance exceeds collections plus interest; the principal-turnover tenor does not.

### 5.2 Roll rates, loss factors and ECL

**Method:** aggregate bucket-to-bucket. A roll rate for month *t* = the next bucket's balance at *t* + lag ÷ this bucket's balance at *t*. A zero denominator gives a blank, not an error.

**Full model** (all seven core bucket balances mapped). The five links, exactly as `rcl_rollrate_ecl_3.xlsx` rows 29–33:

1. Current + New → PAR 1-30
2. PAR 1-30 → 31-60
3. 31-60 → 61-90
4. 61-90 → 91-120
5. 91-120 → Written off (the written-off *flow*)

**Approximation** (the 11 columns only). Three links:

1. Active → PAR 1-60
2. PAR 1-60 → 61-90
3. 61-90 → Written off, two months later (there is no 91-120 bucket)

G − H − I − J (91+ days past due plus defaulted stock) is carried at a loss factor of 1. Because the 1-60 bucket hides the roll from 1-30 to 31-60, this **reads lower** than the full model. On the reference data: ECL 1,432.5M vs 1,663.1M.

**Windows:** SELECTED = the average of the last *n* observable monthly rates (T3 = 3, T6 = 6, T12 = 12, ALL = all).

**Loss factor** for a bucket = the product of the SELECTED links from that bucket down to write-off.

**ECL:**
- **Performing ECL** = Σ (balance × loss factor × LGD) over the chain buckets.
- **Total ECL** = performing ECL + defaulted stock × LGD.
- **Coverage** = ECL ÷ the matching balance.

**ECL is withheld** when the balances don't tie to G at the as-of month: in the full model, the buckets don't sum to G; in the approximation, H + I + J exceeds G.

### 5.3 Point-in-time loss rate ('Loss Rate LTV')

For each month, using only roll rates observable by that month:

- **Loss rate** = (performing ECL before LGD + cumulative written off) × LGD ÷ cumulative issued.
- **Naive** divides by everything issued to date; **Lagged** divides by cumulative issuance as at the lag date; **Blended** is the weighted mix of the two.
- Only months with outstanding at or above the sample threshold count.
- The percentiles use Excel's `PERCENTILE`, which is linear interpolation.

### 5.4 Standard-definition metrics

| Metric | Formula | Needs buckets |
|---|---|---|
| PAR 61+ incl. defaulted | (G − H − I) ÷ G | no |
| PAR 91+ incl. defaulted | (G − H − I − J) ÷ G | no |
| Principal payment rate (m) | E ÷ average G | no |
| Tenor from principal turnover | 1 ÷ payment rate | no |
| Write-off rate, annualised | K ÷ average G × 12 | no |
| Defaulted stock share | BAL_DEFAULT ÷ G | yes |
| PAR 31+ incl. defaulted | (31-60 … 180+ + defaulted) ÷ G | yes |
| PAR 31+ / 91+ of performing book | (31-60 … 180+) / (91-120 … 180+) ÷ (G − defaulted) | yes |
| Active share / yield / utilisation of performing book | H, Σ D, (G − defaulted) over (G − defaulted) or C | yes |

The same catalogue feeds the early warnings, the metrics over time and the covenant tests. Each metric therefore has one definition everywhere. The code is in `metrics.py`.

### 5.5 Backtest

At each month *t*, the chain's performing loss before LGD is a forecast of write-offs from the book on balance sheet at *t*. Those arrive between the last link's lag and the sum of all the lags:
- **Full model:** months *t*+1 to *t*+5.
- **Approximation:** months *t*+2 to *t*+4.

Money issued after *t* can't reach write-off inside that window, so the comparison isolates the forecast. On `rcl_rollrate_ecl_3.xlsx` with T12:
- **Full model:** 19 months tested, actual ÷ forecast 0.913 (range 0.80–1.00).
- **Approximation:** 20 months tested, 0.904.

### 5.6 Stress scenarios

- Each SELECTED link is multiplied by the scenario's roll-rate multiplier and capped at 100%. A link already above 100% (aggregate rates can exceed 1) is left as it is.
- LGD is multiplied and capped at 1.
- The loss factors, ECL, coverage and the as-of loss rate are then recalculated.
- The advance rate = 1 − stressed loss rate − haircut.

### 5.7 Roll-forward check

Unexplained movement = G − (G of the previous month + F − E − K). This reproduces the provider's `UNEXPLAINED` column exactly on the reference data. A large value means the tape doesn't reconcile, and the ECL should be read with care.

---

## 6. Limits and caveats

- **ECL horizon.** The roll-rate ECL is effectively a roughly 5-month forecast of write-offs from today's balances. It is not a 12-month or lifetime ECL, because it doesn't model current balances going bad later.
- **Revenue mix.** `interest_collected` may include fees and penalties if the provider combines them.
- **Written-off stock in G.** `principal_outstanding` includes written-off stock where the provider keeps it on book. The template's ratios use G as provided; the performing-book metrics exclude that stock.
- **Out of scope.** There are no vintage curves or concentration views: both need loan-level data, which a monthly report doesn't have.
- **Example settings.** The stress multipliers and covenant limits are examples. Set them per deal.

---

## 7. Code and tests

| File | Contents |
|---|---|
| `monthly_input.py` | Input fields, validation, month handling |
| `transition_matrices.py` | Full and approximate chains, roll rates, loss factors, ECL, data-quality checks |
| `loss_rate.py` | Point-in-time loss rate and its distribution |
| `outputs.py` | The template's fact block and ratio block |
| `metrics.py` | Standard metrics, over time, unit economics, early warnings |
| `backtest_stress.py` | Backtest and stress scenarios |
| `facility.py` | Advance rate and covenant tests |
| `settings.py` | Loads `config.toml` → `[revolving_defaults]`; every tunable in the package reads from here |
| `engine.py` | `RevolvingInputs` and `run()`: truncates to the as-of month, picks the method, assembles every section |

- **API:** `web_api/main.py`: `POST /api/analyse/revolving`, `POST /api/export/revolving`, `GET /api/revolving/defaults` (every General Inputs default), `GET /api/revolving/options` (window names and covenant metrics).
- **UI:** `web/src/screens/RevolvingTabs.tsx`, plus the revolving section of `GeneralInputsScreen.tsx`.
- **Tests:** `scripts/tests/test_revolving_analysis.py`. These include round-trips against `rcl_rollrate_ecl_3.xlsx`: roll rates, loss factors, ECL, loss-rate statistics, the roll-forward and the backtest. The round-trips are skipped when the workbook isn't available (for example on CI). Set `RCL_REFERENCE_WORKBOOK` to point at a copy.
