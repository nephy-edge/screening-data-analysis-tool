# Revolving Lending model: app guide

This guide explains how the Revolving Lending model works in the app: every screen, every results tab, and how each figure is calculated. For the code layout and the reference-workbook tests, see [README.md](README.md).

Every figure is calculated in deterministic Python in `scripts/revolving_analysis/`. No AI produces or changes any number. The only AI features are the optional **Auto-fill with AI** and **Ask AI to derive** helpers on the mapping screen.

---

## 1. What the model does

The model analyses a revolving credit-line book from a **monthly report**: one row per month for the whole book, not one row per loan. From that report it produces:

- the Revolving Lending template's **Outputs**: the fact block and the UE / LTV ratios;
- a **roll-rate transition model** with **expected credit loss (ECL)**, following `rcl_rollrate_ecl_3.xlsx`;
- a **backtest** of that ECL model and **stress scenarios** on it;
- a **facility view**: the maximum advance rate and covenant tests;
- **data-quality checks** on the report itself.

Everything is calculated **as of** one month (by default the last month in the data), using only data up to that month. Nothing looks ahead.

### Notation

The formulas below use the template's column letters:

| Letter | Field | Meaning |
|---|---|---|
| A | `report_month` | Report month |
| B | `number_of_customers` | Borrowers on book |
| C | `total_credit_line` | Sum of approved credit limits |
| D | `interest_collected` | Cash interest collected in the month |
| E | `principal_collected` | Cash principal repaid in the month |
| F | `principal_issued` | New principal drawn in the month |
| G | `principal_outstanding` | Closing principal outstanding, **including** defaulted stock |
| H | `principal_outstanding-active` | Balance not past due (New + Current) |
| I | `principal_outstanding-par30-60` | Balance 1–60 days past due |
| J | `principal_outstanding-par60-90` | Balance 61–90 days past due |
| K | `principal_written-off` | Principal written off in the month (a flow, not a stock) |

"Average G" means the average of G over the **ratio window**: the months ending at the as-of month, 2 by default.

---

## 2. The workflow

### Step 1: Data Input

1. Choose **Revolving Lending** in the **Model** picker.
2. Load the monthly report as a CSV or Excel upload, from Google Sheets or Drive, or from Snowflake. For a workbook with several sheets, pick the sheet.
3. Click **Continue to mapping →**.

The run stops with a clear message when:
- a month appears more than once;
- a row has data but no readable month.

Rows that are blank in every mapped column, such as summary rows at the bottom of a tab, are ignored.

### Step 2: Column mapping

Each template field is matched to a column in your file.

- **Required fields.** The 11 columns A–K listed above come first. **Continue to inputs** stays disabled until all 11 are mapped; the button shows how many are still missing.
- **Optional inputs.** These sit in a collapsible section straight after the required fields. Its heading shows how many are mapped, e.g. "Optional inputs (0 of 15 mapped)".

| Optional field(s) | What mapping it does |
|---|---|
| **Balance: New, Current, PAR 1-30, PAR 31-60, PAR 61-90, PAR 91-120, Defaulted** | When all seven are mapped, the **full roll-rate model** runs instead of the approximation (section 5.2). They also enable the performing-book metrics. |
| **Balance: PAR 121-150, 151-180, 180+** | Balances past the end of the chain. Treated as 0 if not mapped. |
| **Duplicate Loan-Months, Source Rows, Months Filled by Carry-Forward** | The provider's own data-quality counts. Shown on Data Checks and in the Transition Matrices data-quality table. |
| **Unexplained Movement** | The provider's own unexplained balance movement. The app checks its roll-forward agrees with it. |
| **recoveries** | Cash recovered on written-off balances. Lets LGD be derived from the data (General Inputs → LGD basis). |

**Helpers on this screen:**
- **Auto-fill with AI** suggests mappings from the column names.
- **Ask AI to derive** turns a plain-English description into a formula column you can map. You review the formula before it is added; the formula, not the AI, computes the values.

Figures stay in the source currency. There is no currency conversion.

### Step 3: General Inputs

These are the assumptions the model uses. Every default comes from `config.toml` → `[revolving_defaults]`. Hover the **?** next to any input for a one-line explanation.

**Roll-rate & ECL**

| Input | Default | What it does |
|---|---|---|
| As-of month | last month in the data | The month everything is calculated at, using data up to it only. |
| Roll-rate averaging window | T12 | How many recent months of roll rates are averaged into the SELECTED rate: T3 / T6 / T12 = the last 3 / 6 / 12 months, ALL = every month. The SELECTED rate drives the whole ECL. |
| LGD | 1.0 | Share of a defaulted balance that is lost. 1 means no recoveries. |
| Outputs ratio window | 2 months | Months, ending at the as-of month, that the Outputs averages use. The template uses 2. |
| Write-off averaging window | 1 month | Months the write-off rate is averaged over: total written off ÷ total outstanding over those months. 1 = the as-of month only, as the template. Feeds unit economics, standard metrics, early warnings and covenants; the template's UE / LTV row stays as the template defines it. |

**Loss rate** (feeds the Outputs Loss Rate and 95th %ile)

| Input | Default | What it does |
|---|---|---|
| Loss-rate basis | Blended | Naive, Blended or Lagged (section 5.3). |
| Stress percentile | 0.95 | The percentile reported as "95th %ile". It sets the advance rate. |
| Minimum outstanding to be in sample | 1,000,000,000 | A month counts in the loss-rate sample only when G is at least this much. **Set it per deal, in the source currency.** A book smaller than this has no in-sample months, which blanks Loss Rate, 95th %ile and the advance rate. |
| Denominator lag | 5 months | For the Lagged basis: divide by cumulative issuance this many months earlier. |
| Blend weight on the lagged rate | 0.5 | For the Blended basis: 0 = Naive only, 1 = Lagged only. |

**LGD & unit economics**

| Input | Default | What it does |
|---|---|---|
| LGD basis | Input | *Input* uses the LGD above. *Recoveries* derives LGD = 1 − Σ recoveries ÷ Σ written off, to date. |
| Cost of funds | 0.13 (13% a year) | Deducted in unit economics. |
| Operating costs | 0.02 (2% a year) | Deducted in unit economics. |
| Advance-rate haircut | 0 | Extra cushion taken off the advance rate. |

**Backtest & early warning**

| Input | Default | What it does |
|---|---|---|
| Backtest history | 12 months | Months of history needed before the first backtest month. |
| Early-warning lookback | 3 months | The as-of value is compared with the average of this many preceding months. |
| Early-warning threshold | 0.2 | Flag a metric more than 20% worse (relative) than its lookback average. |

**Stress scenarios.** An editable table of name, roll-rate multiplier and LGD multiplier. The defaults are:

| Scenario | Roll-rate multiplier | LGD multiplier |
|---|---|---|
| Base | 1.0 | 1.0 |
| Stress | 1.25 | 1.1 |
| Severe | 1.5 | 1.25 |

**Covenant tests.** An editable table of metric, test (≤ or ≥) and limit. **A new run starts with none**, because limits differ from deal to deal. Click **+ Add covenant** to enter the deal's facility terms (or the limits you are considering). Until you do, the Facility & Covenants and Portfolio Summary tabs say "No covenants set". To pre-fill covenants for every run, add `[[revolving_defaults.covenants]]` blocks to `config.toml`.

**Settings in `config.toml` only**

These aren't on the General Inputs screen:

| Setting | Default | What it does |
|---|---|---|
| `approx_writeoff_lag_months` | 2 | Approximation only: months from PAR 61-90 to write-off. |
| `stress_roll_rate_cap` | 1.0 | Ceiling on a stressed roll rate. |
| `balance_tolerance` | 1.0 | How closely the balances must tie to G before the ECL is shown. |
| `unexplained_warning_pct` | 0.05 | Unexplained movement above this share of outstanding is flagged. |

### Step 4: Run analysis

Click **Run analysis →**. One run calculates every tab. The action bar under the results offers:
- **Export (Excel):** download the results as a workbook.
- **Save:** save the run to *your* account only.
- **Share:** create a link anyone signed in can open. It is a separate copy of the run.
- **Edit column mapping** and **New analysis**.

---

## 3. Warnings shown on the results

The same warning banners appear at the top of the Portfolio Summary and Outputs (Ratios) tabs. They appear when:

- **Approximation in use:** only the 11 required columns are mapped, so the 3-link chain runs and understates ECL (section 5.2).
- **Balances don't tie** at the as-of month: the ECL is withheld.
- **Months are missing** from the data: roll rates across a gap pair months that aren't consecutive.
- **Balance sits in PAR 121+** at the as-of month (full model only): it is outside the chain, so it isn't in the ECL.
- **Unexplained movement at the as-of month** is above 5% of outstanding.
- **No month is in the loss-rate sample:** Loss Rate and 95th %ile are blank. Lower the minimum outstanding.

---

## 4. The results tabs

Every figure on every tab has a **?** that explains it:
- on the **row label**, where each row is a metric;
- on the **column header**, where each row is a month or a scenario.

### 4.1 Portfolio Summary

A one-screen overview at the as-of month. It only repeats figures calculated on the other tabs.

| Section | Contents | Source |
|---|---|---|
| **Headline** | Outstanding, Customers, Credit line utilised, Interest rate (m), Write-offs (m), Total ECL, ECL coverage (total) | Outputs ratio block and Transition Matrices ECL |
| **Credit quality** | The template's Active, PAR30, PAR60 and PAR90, each with its lookback average, the change and the ⚠ flag | Early warnings (Outputs) |
| **Ageing mix** | Outstanding by standard bucket (Current, 1-30, 31-60, 61-90, 91-120, 121-180+, Defaulted), balance and share; adds to 100%. Needs the bucket columns | Bucket balances (Transition Matrices) |
| **Unit economics** | Net yield per month, annualised, and over the principal tenor | Unit economics (Outputs) |
| **Stress** | Loss rate and advance rate for each scenario | Backtest & Stress |
| **Facility** | Whether any covenant is breached, the covenant with the tightest headroom, and pass/fail for each | Facility |

### 4.2 Data Checks

Two sub-tabs, as in the Lending model.

**Data Input** shows the uploaded rows as mapped: the source data the run used.

**Checks** lists each problem found, with the affected months in a table underneath. With no problems it shows "No data quality issues flagged."

| Check | Flags a month when | Level |
|---|---|---|
| **Balance tie** | The buckets don't tie to G. Full model: the seven buckets don't sum to G within tolerance. Approximation: H + I + J is more than G. | Warning |
| **Unexplained movement** | \|unexplained ÷ G\| is above `unexplained_warning_pct` (5%), where unexplained = G − (previous G + F − E − K). Each row shows the amount and its % of outstanding. | Warning |
| **Missing months** | A calendar month between the first and last month is absent. | Warning |
| **Negative values** | Any required field is below zero. Each row shows the month, field and value. | Warning |
| **Source duplicates / carry-forward** | The provider's own counts are non-zero (only when those optional columns are mapped). | Info |

Every month is checked, not just the as-of month.

### 4.3 Outputs (Ratios)

The template's Outputs tab, plus standard-definition metrics.

**1. UE / LTV ratio block.** The template's rows 9–24 at the as-of month, using the template's own formulas and denominators. The *How calculated* column repeats the formula.

| Row | Metric | Calculation |
|---|---|---|
| 9 | Expected Tenor (m) | 1 ÷ ((E + D − F) at as-of ÷ average G) |
| 10 | Interest Rate (m) | Σ D ÷ Σ G over the window |
| 11 | Interest Rate (overall) | row 10 × row 9 |
| 12 | Loss Rate | mean of the in-sample point-in-time loss rates (section 5.3) |
| 13 | 95th %ile | the stress percentile of those rates |
| 15 | Active | H ÷ G |
| 16 | PAR30 | I ÷ G (this is 1–60 days past due) |
| 17 | PAR60 | J ÷ H (61–90 days past due, divided by *active*, not total) |
| 18 | PAR90 | J ÷ G (61–90 days past due, **not** 91+) |
| 19 | Write offs | K at as-of ÷ average G |
| 20 | ECL | Total ECL at as-of (Transition Matrices) |
| 21 | Loss Rate | ECL ÷ G |
| 22 | Outstanding | G at as-of |
| 23 | number of customers | B at as-of |
| 24 | % of credit line utilised | G ÷ C |

These follow the template exactly, including its non-standard PAR definitions. Use the standard-definition block for true PAR measures.

Expected Tenor (row 9) **turns negative, or becomes extreme**, in growth months where issuance is close to or above collections plus interest, because the denominator approaches zero. The principal-turnover tenor in the standard block has no such problem.

**2. Monthly fact block.** Columns A–K for every month up to the as-of month.

**3. Point-in-time loss rate: in-sample months.** For each basis (Naive, Blended, Lagged): the number of months in the sample, mean, median, standard deviation, minimum, maximum, 25th and 75th percentiles, and the stress percentile. These are the monthly loss rates from section 5.3, restricted to in-sample months. The percentiles use linear interpolation, as Excel's `PERCENTILE` does.

**4. Standard-definition metrics.** Conventional credit definitions, shown next to the template ratios:

| Metric | Calculation | Needs bucket balances |
|---|---|---|
| Interest Rate (m) | Σ D ÷ Σ G over the window | no |
| Write-off rate (m) | K ÷ average G | no |
| PAR 61+ incl. defaulted stock | (G − H − I) ÷ G | no |
| PAR 91+ incl. defaulted stock | (G − H − I − J) ÷ G | no |
| Principal payment rate (m) | E ÷ average G | no |
| Tenor from principal turnover (m) | 1 ÷ principal payment rate | no |
| Write-off rate (annualised) | write-off rate (m) × 12 | no |
| Defaulted stock share | Defaulted ÷ G | yes |
| PAR 31+ incl. defaulted stock | (31-60 … 180+ + defaulted) ÷ G | yes |
| PAR 31+ of performing book | (31-60 … 180+) ÷ (G − defaulted) | yes |
| PAR 91+ of performing book | (91-120 … 180+) ÷ (G − defaulted) | yes |
| Active share of performing book | H ÷ (G − defaulted) | yes |
| Interest yield on performing book (m) | Σ D ÷ Σ (G − defaulted) over the window | yes |
| Credit line utilised by performing book | (G − defaulted) ÷ C | yes |

A metric that needs the bucket balances shows "—" until those are mapped. One catalogue of definitions feeds this table, the metrics over time, the early warnings and the covenant tests, so each metric means the same thing everywhere.

**5. Unit economics after costs** (per dollar outstanding):

| Line | Monthly |
|---|---|
| Interest yield | Interest Rate (m) |
| Write-offs | − write-off rate (m) |
| Cost of funds | − cost of funds ÷ 12 |
| Operating costs | − operating costs ÷ 12 |
| **Net yield** | the sum of the four lines above |

The columns are:
- **Monthly**: the figures above.
- **Annualised**: monthly × 12.
- **Over the principal tenor**: monthly × tenor from principal turnover.

**Tenor inputs.** Principal collected in the as-of month, average outstanding over the ratio window, the principal payment rate (collected ÷ average outstanding) and the principal tenor (1 ÷ payment rate) that the over-tenor column uses.

**Interest yield — whole book vs performing book.** The same interest collected over the
ratio window, divided by principal outstanding (whole book) and by outstanding less
defaulted stock (performing book), monthly and × 12. The gap is the yield lost to
defaulted stock that earns nothing. The performing row needs the bucket columns mapped.

**6. Early warnings.** Each metric that has a "worse" direction is shown with:
- **As-of:** the value at the as-of month.
- **Lookback average:** the mean over the preceding lookback months.
- **Change:** the relative change against that average.
- **Flag (⚠ Worse):** set when the metric moved in its bad direction by more than the threshold. Higher is worse for PAR and write-offs; lower is worse for Active, interest yield and payment rate.

**7. Metrics over time.** Every catalogue metric, month by month, each calculated with only the data up to that month.

### 4.4 Transition Matrices

The roll-rate model and ECL, as in `rcl_rollrate_ecl_3.xlsx` 'Roll Rate ECL'.

**Headline cards:**
- **Method:** Full buckets or Approximation.
- **As-of month.**
- **Roll-rate window.**
- **Total ECL.**
- **Coverage — performing** and **Coverage — total.**

**1. Balances by bucket.** Each bucket's balance month by month, with a **stacked area chart** above the table. The stack holds the buckets only; the total and the write-off flow are shown in the table but not stacked. The legend names each bucket; hover the chart for exact amounts. In the approximation the buckets are:
- Active (New + Current);
- PAR 1-60;
- PAR 61-90;
- 91+ and defaulted stock (outstanding not in the three buckets above).

**2. Roll rates.** For each link and month:

> roll rate(t) = next bucket's balance at month t + lag ÷ this bucket's balance at month t

A zero denominator gives a blank, not an error. The last months have no rate, because their "next month" hasn't happened yet. After the monthly columns come the averages T3, T6, T12 and ALL, and **SELECTED**: the window chosen in General Inputs. A **line chart** above the table shows each link's monthly rate.

The links depend on the method:

| Full model (all 7 core buckets mapped) | Approximation (11 required columns only) |
|---|---|
| Current + New → PAR 1-30 | Active → PAR 1-60 |
| PAR 1-30 → 31-60 | PAR 1-60 → PAR 61-90 |
| 31-60 → 61-90 | PAR 61-90 → Written off, two months later |
| 61-90 → 91-120 | |
| 91-120 → Written off (the write-off *flow*) | |

In the approximation, G − H − I − J (91+ plus defaulted stock) is carried at a loss factor of 1. The 1-60 bucket hides the roll from 1-30 to 31-60, so the approximation **reads lower** than the full model: on the reference data, ECL 1,432.5M against 1,663.1M. Map the bucket balances for the full model.

**3. Cumulative loss factor.** For each starting bucket, the product of the SELECTED links from that bucket down to write-off. It is the share of that bucket's balance expected to be written off. For example, Active's factor = link 1 × link 2 × link 3.

**4. Expected credit loss**, at the as-of month:
- For each bucket: ECL = balance × loss factor × LGD.
- Performing ECL = the sum over the chain buckets.
- Total ECL = performing ECL + defaulted stock × LGD.
- Coverage (performing) = performing ECL ÷ performing balance.
- Coverage (total) = total ECL ÷ total balance including defaulted stock.

The ECL is **withheld** when the balances don't tie to G at the as-of month (the balance-tie check).

**5. Data quality.** A per-month table:
- the balance-tie check (OK / CHECK);
- **Roll-forward: unexplained movement** = G − (previous G + F − E − K);
- **Unexplained as % of outstanding**;
- when mapped, agreement with the provider's `UNEXPLAINED` column, and the provider's duplicate and carry-forward counts.

The Data Checks tab lists the months this table flags.

### 4.5 Backtest & Stress

**1. Backtest: forecast vs actual write-offs.**

At each past month t with enough history:
1. The ECL chain is rebuilt from data **up to t only**.
2. Its performing loss before LGD is taken as the **forecast** of write-offs from the book on balance sheet at t.
3. That forecast is compared with the write-offs **actually** booked in the window when that book would reach write-off: months t+1 to t+5 for the full model, t+2 to t+4 for the approximation.

Money issued after t can't reach write-off inside that window, so the comparison tests the forecast alone.

The summary cards are:
- **Months tested:** months whose whole outcome window has been observed.
- **Actual vs forecast (pooled):** total actual write-offs divided by total forecast write-offs over the tested months.
- **Median ratio**, and the **range** (lowest and highest monthly ratio).
- **Mean absolute error:** the average of |ratio − 1|.

**Below 1 means the forecast was conservative** (more loss forecast than happened). A **line chart** plots forecast and actual write-offs for the tested months. The table beneath lists every month; months whose outcome isn't known yet show "Not yet". **Gap** is actual vs forecast less 1 (positive = losses beat the forecast), and **Principal tenor (m)** is the tenor at that month, to see whether the gap moves with how fast the book repays.

**2. Stress scenarios.** For each scenario:
- Each SELECTED roll rate × the roll-rate multiplier, capped at `stress_roll_rate_cap` (100%). A link already above the cap is left as it is.
- LGD × the LGD multiplier, capped at 100%.
- The loss factors, **Total ECL**, both **coverage** figures and the as-of **loss rate** (on the chosen basis) are then recalculated.
- **Advance rate** = 1 − stressed loss rate − haircut.

Base (1.0 / 1.0) reproduces the unstressed figures. The table is withheld if the balances don't tie.

### 4.6 Facility & Covenants

**1. Advance rate:**

| Card | Calculation |
|---|---|
| Stress loss rate | the Outputs 95th %ile |
| Haircut | from General Inputs |
| Maximum advance rate | MAX(0, 1 − stress loss rate − haircut) |

This is 'Loss Rate LTV' row 65 of `rcl_rollrate_ecl_3.xlsx`. It is blank when no month is in the loss-rate sample.

**2. Covenant tests**, at the as-of month. Each covenant shows:
- **Test:** the limit and its direction (≤ or ≥).
- **Value:** the metric at the as-of month.
- **Headroom:** limit − value for ≤, value − limit for ≥. Negative means breached.
- **Result:** PASS, BREACH, or N/A. N/A means the metric needs the bucket balances.

Covenants can test any catalogue metric, plus ECL coverage (performing / total) and net yield (annualised).

---

## 5. Methodology details

### 5.1 The as-of month

Before any calculation, the data is cut at the as-of month. Every figure, including the backtest's "actual" windows and the early-warning lookbacks, uses only months up to it. Changing the as-of month therefore reproduces what the model would have shown at that point.

### 5.2 Full model vs approximation

The **full model** runs when the seven core bucket balances are all mapped; it matches the reference workbook exactly. Otherwise the **approximation** runs. The Method card and the warning banner say which was used.

### 5.3 Point-in-time loss rate

For each month m, using only roll rates observable by m:

> total loss(m) = performing ECL before LGD(m) + cumulative principal written off to m
>
> **Naive**(m) = total loss × LGD ÷ cumulative principal issued to m
>
> **Lagged**(m) = total loss × LGD ÷ cumulative principal issued to (m − denominator lag)
>
> **Blended**(m) = Naive × (1 − blend weight) + Lagged × blend weight

- A month is **in sample** when G ≥ the minimum outstanding.
- The Outputs **Loss Rate** is the mean of the chosen basis over the in-sample months.
- **95th %ile** is its stress percentile.
- Naive is a lower bound, because recent issuance hasn't had time to default. Lagged corrects for that.

### 5.4 LGD

With **Input**, the entered value is used. With **Recoveries**:

> LGD = 1 − Σ recoveries ÷ Σ principal written off, to date

It is clamped to between 0 and 1, and needs the recoveries column mapped and some write-offs to date.

---

## 6. Limits and caveats

- **ECL horizon.** The roll-rate ECL is effectively a roughly 5-month forecast of write-offs from today's balances. It is not a 12-month or lifetime ECL: it doesn't model current balances that go bad later.
- **The template's PAR definitions** (PAR30 = 1–60 days, PAR90 = 61–90 days, PAR60 against active) are non-standard. Prefer the standard-definition block when comparing deals.
- **Expected Tenor (template)** can be negative or extreme in growth months (section 4.3).
- **`interest_collected` may include fees and penalties** if the provider combines them.
- **G includes written-off stock** where the provider keeps it on book. The template ratios use G as given; the performing-book metrics exclude that stock.
- **No vintage curves or concentration views.** They need loan-level data, which a monthly report doesn't have.
- **Stress multipliers are examples, and there are no default covenants.** Set both per deal.
