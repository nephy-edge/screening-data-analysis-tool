import { useAppStore } from '../state/AppStore';
import { Alert, Metric, Table, type TableColumn } from '../components/ui';
import type { RevolvingRatio } from '../api/engine';

type Row = Record<string, unknown>;

const isNum = (v: unknown) => typeof v === 'number' && Number.isFinite(v);
const fpct = (v: unknown, digits = 2) => (isNum(v) ? `${((v as number) * 100).toFixed(digits)}%` : '—');
// `+ 0` turns a rounding-noise -0 (e.g. G − H − I − J) into 0.
const fmoney = (v: unknown) =>
  isNum(v) ? (Math.round(v as number) + 0).toLocaleString() : '—';
const fdec = (v: unknown, digits = 2) => (isNum(v) ? (v as number).toFixed(digits) : '—');
const fcell = (v: unknown) => (isNum(v) ? fmoney(v) : v === null || v === undefined ? '—' : String(v));

/** Months-across table: every key of the first row becomes a column. */
function WideTable({ rows, format }: { rows: Row[]; format: (v: unknown, col: string, row: Row) => string }) {
  const keys = Object.keys(rows[0] ?? {});
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
