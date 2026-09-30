import { useAppStore } from '../state/AppStore';
import { Alert, HelpTip, Metric, Table, type TableColumn } from '../components/ui';
import type { MetricKind, RevolvingRatio } from '../api/engine';

type Row = Record<string, unknown>;

const isNum = (v: unknown) => typeof v === 'number' && Number.isFinite(v);
const fpct = (v: unknown, digits = 2) => (isNum(v) ? `${((v as number) * 100).toFixed(digits)}%` : '—');
// `+ 0` turns a rounding-noise -0 (e.g. G − H − I − J) into 0.
const fmoney = (v: unknown) =>
  isNum(v) ? (Math.round(v as number) + 0).toLocaleString() : '—';
const fdec = (v: unknown, digits = 2) => (isNum(v) ? (v as number).toFixed(digits) : '—');
/** Format a catalogue metric by its kind. */
const fkind = (v: unknown, kind: MetricKind | string) =>
  kind === 'months' ? fdec(v) : kind === 'money' ? fmoney(v) : fpct(v);
/** What each row means, shown via a "?" on the row label - the same pattern
 * as the Lending Summary's snapshot tables (MetricTitle). Keyed by the label
 * the engine returns; a label with no entry shows no icon. */
const RATIO_HELP: Record<number, string> = {
  9: 'Expected life of a dollar lent, in months: 1 ÷ (net cash flow at as-of ÷ average outstanding over the window).',
  10: 'Monthly interest yield: interest collected ÷ principal outstanding, summed over the ratio window.',
  11: 'Interest earned over the expected life of a dollar lent: Interest Rate (m) × Expected Tenor (m). Feeds the LTV product interest rate.',
  12: 'Mean point-in-time loss rate over the in-sample months, on the loss-rate basis chosen in General Inputs.',
  13: 'Stressed loss rate: the chosen percentile of the in-sample monthly loss rates. Sets the maximum advance rate.',
  15: 'Share of outstanding that is not past due (New + Current).',
  16: 'Template PAR30: balance 1-60 days past due ÷ total outstanding.',
  17: 'Template PAR60: balance 61-90 days past due ÷ the active balance (the template uses active, not total, as the denominator).',
  18: 'Template PAR90: balance 61-90 days past due ÷ total outstanding.',
  19: 'Principal written off in the as-of month ÷ average outstanding over the window.',
  20: 'Total expected credit loss at the as-of month, from the Transition Matrices tab.',
  21: 'ECL as a share of outstanding: total ECL ÷ principal outstanding.',
  22: 'Principal outstanding at the as-of month, including defaulted stock.',
  23: 'Borrowers on book at the as-of month.',
  24: 'Principal outstanding ÷ total approved credit lines.',
};

const ROW_HELP: Record<string, string> = {
  // Standard metrics / early warnings / covenants (scripts/revolving_analysis/metrics.py)
  'Expected Tenor (m) - template': 'Expected life of a dollar lent, in months (template definition).',
  'Interest Rate (m)': 'Monthly interest yield: interest collected ÷ principal outstanding over the ratio window.',
  'Active - template': 'Share of outstanding that is not past due (New + Current).',
  'PAR30 - template (1-60 DPD)': 'Balance 1-60 days past due ÷ total outstanding.',
  'PAR60 - template (61-90 ÷ active)': 'Balance 61-90 days past due ÷ the active balance.',
  'PAR90 - template (61-90 DPD)': 'Balance 61-90 days past due ÷ total outstanding.',
  'Write-off rate (m)': 'Principal written off in the month ÷ average outstanding over the window.',
  '% of credit line utilised': 'Principal outstanding ÷ total approved credit lines.',
  'PAR 61+ incl. defaulted stock': 'Everything 61+ days past due, including defaulted stock still on book, ÷ total outstanding.',
  'PAR 91+ incl. defaulted stock': 'Everything 91+ days past due, including defaulted stock still on book, ÷ total outstanding.',
  'Principal payment rate (m)': 'Principal collected in the month ÷ average outstanding. Higher means the book pays down faster.',
  'Tenor from principal turnover (m)': 'Months to repay the book at the current payment rate: 1 ÷ principal payment rate.',
  'Write-off rate (annualised)': 'Monthly write-off rate × 12.',
  'Defaulted stock share': 'Defaulted balance still held on book ÷ total outstanding. Needs the bucket balances mapped.',
  'PAR 31+ incl. defaulted stock': 'Balance 31+ days past due plus defaulted stock ÷ total outstanding. Needs the bucket balances mapped.',
  'PAR 31+ of performing book': 'Balance 31-180+ days past due ÷ the performing book (outstanding less defaulted). Needs the bucket balances mapped.',
  'PAR 91+ of performing book': 'Balance 91-180+ days past due ÷ the performing book. Needs the bucket balances mapped.',
  'Active share of performing book': 'Active balance ÷ the performing book. Needs the bucket balances mapped.',
  'Interest yield on performing book (m)': 'Interest collected ÷ the performing book over the window. Needs the bucket balances mapped.',
  'Credit line utilised by performing book': 'Performing book ÷ total approved credit lines. Needs the bucket balances mapped.',
  'ECL coverage (performing)': 'ECL on the performing book ÷ performing balance.',
  'ECL coverage (total)': 'Total ECL ÷ total balance including defaulted stock.',
  'Net yield (annualised)': 'Annual interest yield less write-offs, cost of funds and operating costs.',
  // Unit economics
  'Interest yield': 'Interest collected per dollar outstanding.',
  'Write-offs': 'Principal written off per dollar outstanding (a deduction).',
  'Cost of funds': 'Funding cost per dollar outstanding, from General Inputs (a deduction).',
  'Operating costs': 'Operating cost per dollar outstanding, from General Inputs (a deduction).',
  'Net yield': 'Interest yield less write-offs, cost of funds and operating costs.',
  // Transition Matrices (coarse approximation labels)
  'Active (New + Current)': 'Balance not past due.',
  Active: 'Balance not past due.',
  'PAR 1-60': 'Balance 1-60 days past due.',
  'PAR 61-90': 'Balance 61-90 days past due.',
  '91+ and defaulted stock (G − H − I − J)': 'Outstanding not in the buckets above: 91+ days past due and defaulted stock still on book.',
  'Total principal outstanding': 'Closing principal outstanding, including defaulted stock.',
  'Principal written off (flow in month)': 'Principal written off during the month.',
  'Active → PAR 1-60': 'Share of this month’s active balance that is 1-60 days past due next month.',
  'PAR 1-60 → PAR 61-90': 'Share of this month’s 1-60 balance that is 61-90 days past due next month.',
  'PAR 61-90 → Written off (2 months on)': 'Share of this month’s 61-90 balance written off two months later.',
  'Active + PAR 1-90 within total outstanding': 'Check that the active and PAR buckets do not add up to more than total outstanding.',
  'Roll-forward: unexplained movement': 'Opening balance + issued − collected − written off, compared with the closing balance. The gap is unexplained.',
  'Unexplained as % of outstanding': 'Unexplained movement ÷ outstanding. A warning is raised above the configured threshold.',
  // Loss-rate distribution
  'Months in sample': 'Months whose outstanding meets the minimum set in General Inputs.',
  'Mean': 'Average monthly loss rate over the in-sample months.',
  'Median': 'Middle monthly loss rate over the in-sample months.',
  'Standard deviation': 'Spread of the monthly loss rates.',
  'Minimum': 'Lowest in-sample monthly loss rate.',
  'Maximum': 'Highest in-sample monthly loss rate.',
  '25th percentile': 'A quarter of in-sample months had a lower loss rate.',
  '75th percentile': 'Three quarters of in-sample months had a lower loss rate.',
  'Stress percentile': 'Loss rate at the stress percentile from General Inputs. Feeds the 95th %ile and the advance rate.',
};

/** Row label with the Lending-style "?" help mark. */
function RowLabel({ label, help }: { label: string; help?: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center' }}>
      {label}
      {help ? <HelpTip text={help} /> : null}
    </span>
  );
}

const fcell = (v: unknown) => (isNum(v) ? fmoney(v) : v === null || v === undefined ? '—' : String(v));

/** Months-across table: every key of the first row becomes a column. */
function WideTable({
  rows,
  format,
  hide = [],
}: {
  rows: Row[];
  format: (v: unknown, col: string, row: Row) => string;
  /** Keys carried for formatting only, not shown as columns. */
  hide?: string[];
}) {
  const keys = Object.keys(rows[0] ?? {}).filter((k) => !hide.includes(k));
  const cols: TableColumn<Row>[] = keys.map((key, i) => ({
    key,
    header: key,
    align: i === 0 ? 'left' : 'right',
    render: (r) => <span className={i === 0 ? undefined : 'sd-mono'}>{i === 0 ? <RowLabel label={String(r[key])} help={ROW_HELP[String(r[key])]} /> : format(r[key], key, r)}</span>,
  }));
  return <Table columns={cols} rows={rows} />;
}

function Warnings({ warnings }: { warnings: string[] }) {
  return (
    <>
      {warnings.map((w) => (
        <Alert key={w} tone="warning">
          {w}
        </Alert>
      ))}
    </>
  );
}

function ratioValue(r: RevolvingRatio): string {
  if (r.row === 9) return fdec(r.value);
  if (r.row === 20 || r.row === 22) return fmoney(r.value);
  if (r.row === 23) return isNum(r.value) ? (r.value as number).toLocaleString() : '—';
  return fpct(r.value);
}

/** The Revolving Lending template's Outputs tab: the UE / LTV ratio block
 * (B9:B24) at the as-of month, the monthly fact block (A:K) behind it, and the
 * point-in-time loss-rate distribution that feeds Loss Rate / 95th %ile. */
export function RevolvingOutputsTab() {
  const { state } = useAppStore();
  const res = state.revolvingResponse;
  if (!res) return <Alert tone="info">Run analysis to see the Outputs.</Alert>;
  const ratioCols: TableColumn<RevolvingRatio>[] = [
    { key: 'label', header: 'UE / LTV', render: (r) => <RowLabel label={r.label} help={RATIO_HELP[r.row]} /> },
    { key: 'value', header: 'Value', align: 'right', render: (r) => <span className="sd-mono">{ratioValue(r)}</span> },
    { key: 'annotation', header: '', render: (r) => <span className="sd-muted">{r.annotation ?? ''}</span> },
    { key: 'formula', header: 'How calculated', render: (r) => <span className="sd-muted">{r.formula}</span> },
  ];
  return (
    <>
      <Warnings warnings={res.warnings} />
      <h2 className="sd-h2">UE / LTV — as of {res.eclSummary.asOfMonth}</h2>
      <Table columns={ratioCols} rows={res.ratios} />
      <p className="sd-muted">Rollrate analysis uses the monthly data (see the Transition Matrices tab).</p>
      <h2 className="sd-h2">Monthly fact block</h2>
      <WideTable rows={res.facts} format={(v) => fcell(v)} />
      <h2 className="sd-h2">Point-in-time loss rate — in-sample months</h2>
      <WideTable
        rows={res.lossRateDistribution}
        format={(v, _c, row) => (row.Statistic === 'Months in sample' ? fdec(v, 0) : fpct(v))}
      />

      <h2 className="sd-h2">Standard-definition metrics — as of {res.eclSummary.asOfMonth}</h2>
      <p className="sd-muted">
        Standard credit definitions next to the template&apos;s own ratios above, which stay as the template defines
        them.
      </p>
      <Table
        columns={[
          { key: 'label', header: 'Metric', render: (r: { label: string }) => <RowLabel label={r.label} help={ROW_HELP[r.label]} /> },
          {
            key: 'value',
            header: 'Value',
            align: 'right',
            render: (r: RevolvingStd) => <span className="sd-mono">{fkind(r.value, r.kind)}</span>,
          },
          {
            key: 'formula',
            header: 'How calculated',
            render: (r: RevolvingStd) => <span className="sd-muted">{r.note || r.formula}</span>,
          },
        ]}
        rows={res.standardMetrics}
      />

      <h2 className="sd-h2">Unit economics after costs (per dollar outstanding)</h2>
      <Table
        columns={[
          { key: 'item', header: '', render: (r: RevolvingUe) => <RowLabel label={r.item} help={ROW_HELP[r.item]} /> },
          { key: 'monthly', header: 'Monthly', align: 'right', render: (r: RevolvingUe) => <span className="sd-mono">{fpct(r.monthly)}</span> },
          { key: 'annualised', header: 'Annualised', align: 'right', render: (r: RevolvingUe) => <span className="sd-mono">{fpct(r.annualised)}</span> },
          { key: 'overTenor', header: 'Over the principal tenor', align: 'right', render: (r: RevolvingUe) => <span className="sd-mono">{fpct(r.overTenor)}</span> },
        ]}
        rows={res.unitEconomics}
      />

      <h2 className="sd-h2">Early warnings</h2>
      {res.earlyWarnings.some((e) => e.flag) ? (
        <Alert tone="warning">
          {res.earlyWarnings.filter((e) => e.flag).length} metric(s) deteriorated beyond the early-warning threshold.
        </Alert>
      ) : (
        <Alert tone="info">No metric deteriorated beyond the early-warning threshold.</Alert>
      )}
      <Table
        columns={[
          { key: 'label', header: 'Metric', render: (r: { label: string }) => <RowLabel label={r.label} help={ROW_HELP[r.label]} /> },
          { key: 'now', header: 'As-of', align: 'right', render: (r: RevolvingEw) => <span className="sd-mono">{fkind(r.now, r.kind)}</span> },
          { key: 'prior', header: 'Lookback average', align: 'right', render: (r: RevolvingEw) => <span className="sd-mono">{fkind(r.prior, r.kind)}</span> },
          { key: 'change', header: 'Change', align: 'right', render: (r: RevolvingEw) => <span className="sd-mono">{fpct(r.change, 1)}</span> },
          { key: 'flag', header: 'Flag', render: (r: RevolvingEw) => (r.flag ? '⚠ Worse' : '') },
        ]}
        rows={res.earlyWarnings}
      />

      <h2 className="sd-h2">Metrics over time</h2>
      <WideTable
        rows={res.overTime}
        hide={['key', 'kind']}
        format={(v, _c, row) => fkind(v, String(row.kind))}
      />
    </>
  );
}

type RevolvingResult = NonNullable<ReturnType<typeof useAppStore>['state']['revolvingResponse']>;
type RevolvingStd = RevolvingResult['standardMetrics'][number];
type RevolvingUe = RevolvingResult['unitEconomics'][number];
type RevolvingEw = RevolvingResult['earlyWarnings'][number];

/** Backtest (forecast vs actual write-offs) and stress scenarios. */
export function RevolvingBacktestStressTab() {
  const { state } = useAppStore();
  const res = state.revolvingResponse;
  if (!res) return <Alert tone="info">Run analysis to see the backtest and stress scenarios.</Alert>;
  const b = res.backtestSummary;
  return (
    <>
      <h2 className="sd-h2">1. Backtest — forecast vs actual write-offs</h2>
      <p className="sd-muted">
        At each past month the ECL chain is struck on data up to that month only; its forecast of write-offs from the
        book then on balance sheet is compared with the write-offs booked in {b.window}. Below 1 means the forecast was
        conservative.
      </p>
      <div className="sd-grid sd-grid--metrics">
        <Metric label="Months tested" value={String(b.months)} hint="Months whose full outcome window is observed." />
        <Metric label="Actual ÷ forecast (pooled)" value={fdec(b.pooledRatio, 3)} hint="Σ actual ÷ Σ forecast over the tested months." />
        <Metric label="Median ratio" value={fdec(b.medianRatio, 3)} />
        <Metric label="Range" value={`${fdec(b.minRatio, 2)} – ${fdec(b.maxRatio, 2)}`} hint="Lowest and highest monthly ratio." />
        <Metric label="Mean absolute error" value={fpct(b.meanAbsError, 1)} hint="Average |ratio − 1|." />
      </div>
      {b.months === 0 ? (
        <Alert tone="info">Not enough history yet for a backtest (see General Inputs → Backtest).</Alert>
      ) : null}
      <WideTable
        rows={res.backtest}
        format={(v, col) =>
          col === 'Actual ÷ forecast'
            ? fdec(v, 3)
            : col === 'Coverage (performing)'
              ? fpct(v)
              : col === 'Outcome observed'
                ? v
                  ? 'Yes'
                  : 'Not yet'
                : fcell(v)
        }
      />

      <h2 className="sd-h2">2. Stress scenarios</h2>
      <p className="sd-muted">
        Selected roll rates and LGD scaled per scenario (a stressed rate is capped at 100% unless already above it).
        Loss rate is the as-of point-in-time rate on the chosen basis; the advance rate is 1 − loss rate − haircut.
      </p>
      <Table
        columns={[
          { key: 'name', header: 'Scenario' },
          { key: 'roll', header: 'Roll ×', align: 'right', render: (r: Stress) => fdec(r.rollRateMultiplier, 2) },
          { key: 'lgdm', header: 'LGD ×', align: 'right', render: (r: Stress) => fdec(r.lgdMultiplier, 2) },
          { key: 'lgd', header: 'LGD', align: 'right', render: (r: Stress) => fpct(r.lgd) },
          { key: 'ecl', header: 'Total ECL', align: 'right', render: (r: Stress) => <span className="sd-mono">{fmoney(r.totalEcl)}</span> },
          { key: 'cp', header: 'Coverage (performing)', align: 'right', render: (r: Stress) => fpct(r.coveragePerforming) },
          { key: 'ct', header: 'Coverage (total)', align: 'right', render: (r: Stress) => fpct(r.coverageTotal) },
          { key: 'lr', header: 'Loss rate', align: 'right', render: (r: Stress) => fpct(r.lossRate) },
          { key: 'adv', header: 'Advance rate', align: 'right', render: (r: Stress) => fpct(r.advanceRate) },
        ]}
        rows={res.stress}
      />
      {res.stress.some((s) => !s.valid) ? (
        <Alert tone="error">Balances don&apos;t tie at the as-of month — stressed ECL withheld.</Alert>
      ) : null}
    </>
  );
}

type Stress = RevolvingResult['stress'][number];
type Covenant = RevolvingResult['covenants'][number];

/** Advance rate and covenant tests. */
export function RevolvingFacilityTab() {
  const { state } = useAppStore();
  const res = state.revolvingResponse;
  if (!res) return <Alert tone="info">Run analysis to see the facility view.</Alert>;
  const a = res.advance;
  const breaches = res.covenants.filter((c) => c.status === 'BREACH').length;
  return (
    <>
      <h2 className="sd-h2">1. Advance rate</h2>
      <div className="sd-grid sd-grid--metrics">
        <Metric label="Stress loss rate" value={fpct(a.stressLossRate)} hint="Outputs 95th %ile (point-in-time loss rate at the stress percentile)." />
        <Metric label="Haircut" value={fpct(a.haircut)} hint="Additional haircut from General Inputs." />
        <Metric label="Maximum advance rate" value={fpct(a.advanceRate)} hint="MAX(0, 1 − stress loss rate − haircut) — 'Loss Rate LTV' row 65." />
      </div>

      <h2 className="sd-h2">2. Covenant tests — as of {res.eclSummary.asOfMonth}</h2>
      {breaches ? (
        <Alert tone="error">{breaches} covenant(s) breached.</Alert>
      ) : (
        <Alert tone="info">No covenant breached. Limits are set in General Inputs.</Alert>
      )}
      <Table
        columns={[
          { key: 'label', header: 'Metric', render: (r: { label: string }) => <RowLabel label={r.label} help={ROW_HELP[r.label]} /> },
          { key: 'test', header: 'Test', render: (c: Covenant) => `${c.operator === '<=' ? '≤' : '≥'} ${fkind(c.limit, c.kind)}` },
          { key: 'value', header: 'Value', align: 'right', render: (c: Covenant) => <span className="sd-mono">{fkind(c.value, c.kind)}</span> },
          { key: 'headroom', header: 'Headroom', align: 'right', render: (c: Covenant) => <span className="sd-mono">{fkind(c.headroom, c.kind)}</span> },
          {
            key: 'status',
            header: 'Result',
            render: (c: Covenant) => (c.status === 'BREACH' ? '✖ BREACH' : c.status === 'PASS' ? '✔ PASS' : 'N/A (map bucket balances)'),
          },
        ]}
        rows={res.covenants}
      />
    </>
  );
}

/** rcl_rollrate_ecl_3.xlsx's 'Roll Rate ECL' tab: balances by bucket, the
 * roll rates with their T3/T6/T12/ALL averages, the cumulative loss factor,
 * the ECL at the as-of month, and the data-quality checks. */
export function RevolvingTransitionTab() {
  const { state } = useAppStore();
  const res = state.revolvingResponse;
  if (!res) return <Alert tone="info">Run analysis to see the transition matrices.</Alert>;
  const s = res.eclSummary;
  return (
    <>
      {s.method === 'coarse' ? (
        <Alert tone="warning">
          Approximation: only the Outputs columns are mapped, so the chain is Active → PAR 1-60 → PAR 61-90 →
          write-off (two months on). The 1-60 bucket hides the 1-30 → 31-60 roll, which understates ECL. Map the
          bucket balances (BAL_NEW … BAL_DEFAULT) to run the full model.
        </Alert>
      ) : null}
      {!s.eclValid ? (
        <Alert tone="error">
          Bucket balances don&apos;t tie to principal outstanding for {s.asOfMonth} — ECL withheld.
        </Alert>
      ) : null}
      <div className="sd-grid sd-grid--metrics">
        <Metric
          label="Method"
          value={s.method === 'full' ? 'Full buckets' : 'Approximation'}
          hint="Full = every bucket balance mapped; Approximation = 3 links from the Outputs columns."
        />
        <Metric label="As-of month" value={s.asOfMonth} hint="ECL is struck on this month's book." />
        <Metric label="Roll-rate window" value={s.window} hint="Drives the SELECTED column and the whole ECL." />
        <Metric label="Total ECL" value={fmoney(s.totalEcl)} hint="ECL on the performing book plus the defaulted stock × LGD." />
        <Metric label="Coverage — performing" value={fpct(s.coveragePerforming)} hint="ECL on the performing book ÷ performing balance." />
        <Metric label="Coverage — total" value={fpct(s.coverageTotal)} hint="Total ECL ÷ total balance incl. defaulted stock." />
      </div>
      <h2 className="sd-h2">1. Balances by bucket</h2>
      <WideTable rows={res.balances} format={(v) => fcell(v)} />
      <h2 className="sd-h2">2. Roll rates</h2>
      <p className="sd-muted">
        % of this month&apos;s bucket balance that appears in the next bucket next month; the last month has no next
        month.
      </p>
      <WideTable rows={res.rollRates} format={(v, col) => (col === 'Denominator' ? String(v) : fpct(v))} />
      <h2 className="sd-h2">3. Cumulative loss factor</h2>
      <WideTable rows={res.lossFactors} format={(v) => fpct(v)} />
      <h2 className="sd-h2">4. Expected credit loss</h2>
      <WideTable
        rows={res.ecl}
        format={(v, col) => (col === 'Loss factor' || col === 'LGD' ? fpct(v) : fcell(v))}
      />
      <h2 className="sd-h2">5. Data quality</h2>
      <WideTable
        rows={res.checks}
        format={(v, _c, row) => (String(row.Check).includes('%') ? fpct(v) : fcell(v))}
      />
    </>
  );
}

/** One-screen overview at the as-of month, reusing figures the other tabs
 * already show: headline ratios, credit quality vs the lookback, unit
 * economics, stress scenarios and covenant results. */
export function RevolvingSummaryTab() {
  const { state } = useAppStore();
  const res = state.revolvingResponse;
  if (!res) return <Alert tone="info">Run analysis to see the summary.</Alert>;
  const s = res.eclSummary;
  const ratio = (label: string) => res.ratios.find((r) => r.label === label);
  const pct = (label: string) => fpct(ratio(label)?.value);
  const netYield = res.unitEconomics.find((u) => u.item === 'Net yield');
  const quality = res.earlyWarnings.filter((e) => /template/.test(e.label));
  const breaches = res.covenants.filter((c) => c.status === 'BREACH').length;
  const tested = res.covenants.filter((c) => c.status !== 'N/A');
  const tightest = tested.length
    ? tested.reduce((a, b) => (Math.abs((b.headroom ?? Infinity) / (b.limit || 1)) < Math.abs((a.headroom ?? Infinity) / (a.limit || 1)) ? b : a))
    : null;
  return (
    <>
      <Warnings warnings={res.warnings} />
      <h2 className="sd-h2">Headline — as of {s.asOfMonth}</h2>
      <div className="sd-grid sd-grid--metrics">
        <Metric label="Outstanding" value={fmoney(ratio('Outstanding')?.value)} />
        <Metric label="Customers" value={isNum(ratio('number of customers')?.value) ? (ratio('number of customers')!.value as number).toLocaleString() : '—'} />
        <Metric label="Credit line utilised" value={pct('% of credit line utilized')} />
        <Metric label="Interest rate (m)" value={pct('Interest Rate (m)')} />
        <Metric label="Write-offs (m)" value={pct('Write offs')} />
        <Metric label="Total ECL" value={fmoney(s.totalEcl)} hint={s.method === 'full' ? 'Full buckets' : 'Approximation from the Outputs columns'} />
        <Metric label="ECL coverage (total)" value={fpct(s.coverageTotal)} />
      </div>

      <h2 className="sd-h2">Credit quality</h2>
      <Table
        columns={[
          { key: 'label', header: 'Metric', render: (r: { label: string }) => <RowLabel label={r.label} help={ROW_HELP[r.label]} /> },
          { key: 'now', header: 'As-of', align: 'right', render: (r: RevolvingEw) => <span className="sd-mono">{fkind(r.now, r.kind)}</span> },
          { key: 'prior', header: 'Lookback average', align: 'right', render: (r: RevolvingEw) => <span className="sd-mono">{fkind(r.prior, r.kind)}</span> },
          { key: 'change', header: 'Change', align: 'right', render: (r: RevolvingEw) => <span className="sd-mono">{fpct(r.change, 1)}</span> },
          { key: 'flag', header: 'Flag', render: (r: RevolvingEw) => (r.flag ? '⚠ Worse' : '') },
        ]}
        rows={quality}
      />

      <h2 className="sd-h2">Unit economics</h2>
      <div className="sd-grid sd-grid--metrics">
        <Metric label="Net yield (m)" value={fpct(netYield?.monthly)} />
        <Metric label="Net yield (annualised)" value={fpct(netYield?.annualised)} />
        <Metric label="Net yield (over tenor)" value={fpct(netYield?.overTenor)} />
      </div>

      <h2 className="sd-h2">Stress</h2>
      <Table
        columns={[
          { key: 'name', header: 'Scenario' },
          { key: 'lr', header: 'Loss rate', align: 'right', render: (r: Stress) => fpct(r.lossRate) },
          { key: 'adv', header: 'Advance rate', align: 'right', render: (r: Stress) => fpct(r.advanceRate) },
        ]}
        rows={res.stress}
      />

      <h2 className="sd-h2">Facility</h2>
      {breaches ? (
        <Alert tone="error">{breaches} covenant(s) breached.</Alert>
      ) : (
        <Alert tone="info">No covenant breached.</Alert>
      )}
      {tightest ? (
        <p className="sd-muted">
          Tightest headroom: {tightest.label} at {fkind(tightest.value, tightest.kind)} against{' '}
          {tightest.operator === '<=' ? '≤' : '≥'} {fkind(tightest.limit, tightest.kind)} (headroom{' '}
          {fkind(tightest.headroom, tightest.kind)}).
        </p>
      ) : null}
      <Table
        columns={[
          { key: 'label', header: 'Covenant', render: (c: Covenant) => <RowLabel label={c.label} help={ROW_HELP[c.label]} /> },
          { key: 'value', header: 'Value', align: 'right', render: (c: Covenant) => <span className="sd-mono">{fkind(c.value, c.kind)}</span> },
          { key: 'status', header: 'Result', render: (c: Covenant) => (c.status === 'BREACH' ? '✖ BREACH' : c.status === 'PASS' ? '✔ PASS' : 'N/A') },
        ]}
        rows={res.covenants}
      />
    </>
  );
}
