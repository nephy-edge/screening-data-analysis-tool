# Revolving Lending model: how every figure is calculated

This document explains in plain language how the Revolving Lending model produces each of its figures. To learn how to use the app, see [APP_GUIDE.md](APP_GUIDE.md). For setup and the list of input fields, see [README.md](README.md).

Every number is calculated by ordinary, repeatable code. No AI is involved in any figure. Each default setting mentioned below can be changed for an individual run in General Inputs.

---

## 1. The input data

The model works from a monthly report of the whole book, with one row per month. Each row gives the following:

- the number of customers
- the total credit line granted
- the interest and principal collected that month
- the principal issued that month
- the principal outstanding at month end, split into active balances, balances 1 to 60 days past due and balances 61 to 90 days past due
- the principal written off that month

The total outstanding includes defaulted balances that have not yet left the books.

Optionally, the report can also break the outstanding balance into finer buckets: newly issued, current, 1–30 days past due, 31–60, 61–90, 91–120, 121–150, 151–180, over 180, and defaulted. When the main buckets (newly issued through 91–120, plus defaulted) are all provided, the model runs its **full** version. Otherwise it runs a **coarse** version that uses only the active, 1–60 and 61–90 splits. An optional recoveries column lets the model work out loss given default (LGD) from the data instead of using an entered figure.

Before any calculation, the model checks the data and tidies it up:

- **Checks that stop the run.** The run stops if a required field is missing, a month can't be read or a month appears twice.
- **Clean-up.** Completely empty rows are ignored, months are sorted in order, and everything else is read as numbers.
- **Cut-off.** If you choose an as-of month, every later month is removed first, so no figure can use information from after that date.

---

## 2. Roll rates and expected credit loss

### How balances move between buckets

The model follows money as it ages through the delinquency buckets.

- **Full version.** There are five steps: current and new balances roll into 1–30 days past due, then into 31–60, then 61–90, then 91–120, and finally into write-off.
- **Coarse version.** There are three steps: active balances roll into 1–60 days past due, then into 61–90, and finally into write-off. The last step is assumed to take two months, because the 91–120 stage isn't visible.

### Roll rates

A roll rate measures what share of one bucket moves into the next bucket a month later. For example, the 31–60 to 61–90 roll rate for January is February's 61–90 balance divided by January's 31–60 balance. For the coarse version's last step, the model compares write-offs two months later. The rate is left blank when the starting bucket is empty, and it can exceed 100%.

The most recent month or two have no rate yet, because the following month hasn't happened.

### Averaging the roll rates

Monthly roll rates are noisy, so the model averages them. It offers four averaging windows:

- the last 3 months
- the last 6 months
- the last 12 months
- every month available

Only rates that are fully known at the as-of month are used, and blanks are skipped. The window chosen in General Inputs (12 months by default) becomes the "selected" rate.

### Loss factors

A loss factor answers this question: of a dollar sitting in a given bucket today, how much is expected to end up written off? It is found by multiplying together the selected roll rates from that bucket all the way to write-off.

- For balances 91–120 days past due, the loss factor is simply the final roll rate into write-off.
- For current balances, it is the product of all five roll rates.

If any step in the chain has no rate, the loss factor is blank.

### Expected credit loss

The expected credit loss (ECL) for each bucket is today's balance, multiplied by its loss factor, multiplied by LGD.

- **Performing buckets.** These are current through 91–120 days past due in the full version, and active through 61–90 in the coarse version.
- **Defaulted balances.** These are treated as certain to be lost, so their loss factor is 1. In the coarse version, this group is whatever is left of the total once active, 1–60 and 61–90 balances are taken away, which means it also includes anything over 90 days past due.

The model reports the following:

- **Performing ECL:** the expected loss on the performing book.
- **Total ECL:** performing ECL plus the expected loss on defaulted balances.
- **Two coverage ratios:** performing ECL as a share of the performing balance, and total ECL as a share of the total balance.

If the as-of month's balances don't add up correctly (see the next section), the ECL is withheld and shown blank. The balances and loss factors are still shown so you can investigate.

### Data checks

Every month is checked in several ways:

- **Balance tie.** In the full version, the buckets must add up to total outstanding, within one unit of currency. In the coarse version, active, 1–60 and 61–90 balances together must not exceed the total.
- **Roll-forward.** Last month's balance, plus this month's issuance, minus principal collected, minus write-offs, should equal this month's balance. Any difference is reported as unexplained movement, both as an amount and as a share of outstanding. A warning appears when it exceeds 5% of outstanding. If the source data includes its own unexplained figure, the two are compared.
- **Other checks.** Missing calendar months, negative values, and duplicate or carried-forward records reported by the source are also listed.

---

## 3. Point-in-time loss rate

The loss rate estimates the share of money lent that will ultimately be lost. To see how this estimate would have looked over time, the model recalculates it for every month in the history. Each recalculation uses only the roll rates that were known at that point.

For each month, the model adds two figures together:

- the expected loss still to come on the performing book (before LGD)
- every write-off to date

It applies LGD to that total and then divides it by money lent, measured in three ways:

- **Naive:** all principal issued to date. This tends to understate losses, because recent lending hasn't had time to go bad.
- **Lagged:** principal issued up to five months earlier, which matches losses to lending that has had time to season.
- **Blended:** an equal mix of the naive and lagged rates by default.

Defaulted balances are left out of the expected-loss part here, because they are already counted in the write-offs to date.

### Summary statistics

The loss rates are then summarised across the months where the book was large enough to be meaningful (above the minimum-outstanding threshold). For each of the three measures, the model reports:

- the number of months used
- the average, median, lowest and highest
- the standard deviation
- the 25th and 75th percentiles
- a stress percentile (the 95th by default)

The percentile and standard-deviation methods match Excel's. The blended measure is used by default for the headline loss rate (its average) and the stressed loss rate (its 95th percentile).

---

## 4. The Outputs tab ratios

These reproduce the template's ratio block at the as-of month. Where the template averages across months, the model uses the last two months by default, as the template does.

| Figure | How it is calculated |
|---|---|
| Expected tenor (months) | One divided by the as-of month's net cash flow (principal collected plus interest collected minus principal issued) as a share of average outstanding. |
| Interest rate (monthly) | Interest collected divided by outstanding, both totalled over the averaging months. |
| Interest rate (overall) | The monthly interest rate multiplied by the expected tenor. |
| Loss rate | The average point-in-time loss rate (section 3). |
| 95th percentile | The stressed point-in-time loss rate (section 3). |
| Active | Active balances as a share of total outstanding. |
| PAR30 | Balances 1–60 days past due as a share of total outstanding. |
| PAR60 | Balances 61–90 days past due as a share of **active** balances, following the template. |
| PAR90 | Balances 61–90 days past due as a share of total outstanding. Despite the template's label, this is not 90+. |
| Write-offs | The as-of month's write-offs as a share of average outstanding. |
| ECL | Total expected credit loss (section 2). |
| ECL as a loss rate | Total ECL as a share of total outstanding. |
| Outstanding | Total principal outstanding. |
| Number of customers | As reported. |
| Credit line utilised | Total outstanding as a share of the total credit line. |

---

## 5. Standard metrics

A single list of metrics feeds the metrics table, the charts over time, the early warnings and the covenant tests, so each metric is defined once. Each metric is calculated for every month using only data up to that month.

**Available from the basic monthly columns:**

- **Over 60 days past due, including defaulted:** everything that isn't active or 1–60 days past due, as a share of total outstanding.
- **Over 90 days past due, including defaulted:** everything beyond 61–90 days past due, as a share of total outstanding.
- **Principal payment rate (monthly):** principal collected as a share of average outstanding.
- **Tenor from principal turnover:** one divided by the payment rate, meaning how many months it takes to collect the book once.
- **Write-off rate:** write-offs as a share of average outstanding, shown monthly and annualised (times 12).
- **Interest rate and credit-line utilisation:** as in section 4.

**Available only when the detailed buckets are provided.** Here, "performing book" means total outstanding minus defaulted balances.

- **Defaulted share:** defaulted balances as a share of total outstanding.
- **Over 30 days past due:** shown both including defaulted (as a share of total outstanding) and excluding it (as a share of the performing book).
- **Over 90 days past due, performing:** as a share of the performing book.
- **Active share of the performing book.**
- **Interest yield on the performing book:** interest collected divided by the performing book, both totalled over the averaging months.
- **Credit line used by the performing book.**

### Unit economics

Unit economics shows what each dollar outstanding earns. Net yield is the interest rate, minus the write-off rate, minus the cost of funds and operating costs. The cost of funds (13% a year by default) and operating costs (2% a year) are converted to monthly figures. Each line is shown per month, per year, and over the term: the average of the monthly 1 / net collection ratio (see the Performance tab).

### Early warnings

A metric is flagged when its latest value is more than 20% worse than its average over the previous three months. Whether "worse" means higher or lower depends on the metric: a rising write-off rate is bad, while a rising payment rate is good.

---

## 5a. Performance tab

- **Net collection ratio:** principal collected plus interest collected in the month, divided by last month's principal outstanding. The first month has none.
- **Estimated avg term:** one divided by the net collection ratio. The Unit Economics and LTV term is the average of these monthly values.
- **ECL by month:** the point-in-time ECL on the performing book (roll rates observable by that month) multiplied by LGD.
- **Estimated loss rate:** cumulative write-off up to the month plus that month's ECL, divided by cumulative principal issued up to the month.
- **LTV loss rate:** the 95th percentile of the estimated loss rate over the in-sample months. The LTV Calculator multiplies it by the credit stress factor (1.7, from config).
- **LTV product interest rate:** interest rate (m) multiplied by the average term.
- **Avg credit line:** total credit line divided by number of customers. **Avg principal issued per customer:** principal issued divided by number of customers.

## 6. Backtest

The backtest checks whether the model's past forecasts came true. It starts once there are 12 months of history. For each month from then on, it does four things:

1. **Rebuild.** The roll rates and loss factors are rebuilt using only data available up to that month.
2. **Forecast.** It calculates how much of the performing book was expected to be written off, before LGD.
3. **Actual.** It adds up the write-offs that actually happened over the months when that book could reach write-off: the next one to five months in the full version, and the next two to four months in the coarse version. Money lent after the forecast month is too new to be written off within that window, so the comparison is fair.
4. **Compare.** It divides actual write-offs by the forecast, once the full window has passed.

Across all months, the summary shows the following:

- the overall ratio of total actual to total forecast
- the median, lowest and highest monthly ratio
- the average size of the miss

A ratio above 1 means the model underestimated losses.

---

## 7. Stress scenarios

Each scenario makes conditions worse by scaling up the roll rates and LGD. The defaults are:

- **Base:** no change.
- **Stress:** roll rates up 25% and LGD up 10%.
- **Severe:** roll rates up 50% and LGD up 25%.

Stressed roll rates are capped at 100%, unless the observed rate was already above that, in which case it is left as it is. Stressed LGD is also capped at 100%.

With these stressed inputs, the model recalculates the following at the as-of month:

- the loss factors
- performing and total ECL
- coverage
- the loss rate
- an advance rate (one minus the loss rate minus any haircut, never below zero)

The Base scenario reproduces the unstressed figures.

---

## 8. Facility

**Advance rate.** The advance rate is how much a lender could advance against the book: one minus the stressed (95th percentile) loss rate, minus any extra haircut, never below zero.

**Covenant tests.** Each covenant compares a metric against a limit. Headroom is how far the metric sits inside the limit, and a negative headroom means a breach. Each covenant shows one of three results:

- **PASS:** the metric is inside the limit.
- **BREACH:** the metric is outside the limit.
- **N/A:** the metric can't be calculated.

The default limits are examples only and should be set for each deal.

---

## 9. Loss given default

LGD can be set in one of two ways:

- **Entered** (the default): the figure you enter, which defaults to 100%.
- **Derived from recoveries:** one minus total recoveries divided by total write-offs to date, kept between 0% and 100%. This option needs the recoveries column and at least some write-offs.

---

## 10. Known limitations

These points come from a review on 30 September 2026. None of them is a calculation error. They are modelling choices worth keeping in mind when reading the results.

1. **Past months that fail the balance check still count towards the loss rate.** The ECL is withheld only when the *as-of* month fails the check. Earlier months that fail it still feed the loss-rate statistics, and so the advance rate.
2. **The final roll rate assumes write-off at around 120 days past due.** If the lender actually writes off later, say at 180 days, the last step compares the wrong months and is distorted, not merely incomplete. The existing warning only says that balances over 120 days sit outside the ECL.
3. **Unit economics uses gross write-offs.** It ignores recoveries and LGD, so it is more cautious than the rest of the model. It also compares interest averaged over several months with a single month's write-offs.
4. **Scenario advance rates aren't directly comparable with the Facility advance rate.** The scenarios use the as-of month's loss rate, while the Facility figure uses the 95th percentile across all months.
5. **Books that predate the data look worse early on.** Lending is only counted from the first month in the data. A book that already had balances then will have their losses counted but not their original lending, which inflates early loss rates.
6. **The template's expected tenor can be unreliable.** It mixes interest into principal turnover and can turn negative or very large when the book is growing. The tenor from principal turnover is the more dependable measure.
7. **LGD derived from recoveries tends to read high.** Recent write-offs haven't had time to recover yet, so the figure is cautious.
8. **The coarse version makes assumptions about the source data.** It assumes the "30–60" column really covers 1–60 days past due. If it truly starts at 30 days, balances 1–29 days past due are counted as active and the first roll rate reads low. Merging 1–30 with 31–60 also hides one stage of ageing, so the coarse version tends to understate ECL compared with the full version.
9. **Missing bucket values count as zero.** A bucket left blank for a month is treated as zero when working out roll rates.
