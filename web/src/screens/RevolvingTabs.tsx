import { useAppStore } from '../state/AppStore';
import { Alert, Metric, Table, type TableColumn } from '../components/ui';
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
    render: (r) => <span className={i === 0 ? undefined : 'sd-mono'}>{i === 0 ? String(r[key]) : format(r[key], key, r)}</span>,
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
    { key: 'label', header: 'UE / LTV' },
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
          { key: 'label', header: 'Metric' },
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
          { key: 'item', header: '' },
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
          { key: 'label', header: 'Metric' },
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
          { key: 'label', header: 'Metric' },
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
