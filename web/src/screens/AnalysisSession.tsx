import { useEffect, useState, type ChangeEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useAppStore, getChartDataSources, type ChartCardSpec } from '../state/AppStore';
import { Button, Input, Metric, Table, Tabs, Alert, Select, type TableColumn } from '../components/ui';
import { LendingLtvTab, RentalLtvTab, inLtvCohortRange } from './LtvTabs';
import {
  RevolvingAgeingMix,
  RevolvingEclTable,
  RevolvingLtvTab,
  RevolvingOutputsTab,
  RevolvingPerformanceTab,
  RevolvingSummaryTab,
  RevolvingUnitEconomicsTab,
} from './RevolvingTabs';
import {
  ResponsiveContainer,
  ComposedChart,
  LineChart,
  BarChart,
  AreaChart,
  ScatterChart,
  Line,
  Bar,
  Area,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
} from 'recharts';
import type { EscalateResult, IngestData, QualityCheck, SummaryChartRow, UeModelAssumptions } from '../api/engine';
import { escalateVariance, fetchIngestData } from '../api/engine';
import {
  aggregateChartData,
  allColumns,
  categoricalColumns,
  numericColumns,
  pickDefaultX,
  pickDefaultY,
  pivotWide,
} from '../lib/chartAggregate';

function num(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function str(v: unknown): string {
  return v === null || v === undefined ? '' : String(v);
}
const pct = (n: number | null, digits = 1) => (n === null ? '—' : `${(n * 100).toFixed(digits)}%`);
const fnum = (n: number | null) => (n === null ? '—' : n.toLocaleString(undefined, { maximumFractionDigits: 2 }));

function MetricTable({ title, data }: { title: string; data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (entries.length === 0) return null;
  const cols: TableColumn<[string, unknown]>[] = [
    // Table's default cell renderer does row[col.key], which only works for
    // keyed objects - these rows are [string, unknown] tuples (arrays), so
    // without an explicit render every "Metric" cell fell back to
    // row['k'] === undefined and rendered blank. Same bug already found and
    // fixed in JsonUploadScreen.tsx's own copy of this pattern.
    { key: 'k', header: 'Metric', render: (r) => r[0] },
    { key: 'v', header: 'Value', align: 'right', render: (r) => (num(r[1]) != null ? fnum(num(r[1])) : str(r[1])) },
  ];
  return (
    <>
      <h2 className="sd-h2">{title}</h2>
      <Table columns={cols} rows={entries} />
    </>
  );
}

function KeyValueCards({ data }: { data: Record<string, unknown> }) {
  const items = Object.entries(data)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => [k, num(v) != null ? fnum(num(v)) : str(v)] as [string, string]);
  return (
    <div className="sd-grid sd-grid--metrics">
      {items.map(([k, v]) => (
        <Metric key={k} label={k} value={v} />
      ))}
    </div>
  );
}

/** Formatting helpers matching scripts/html_tables.py's `fmt` (percent to 2dp,
 * "n/a" for a missing/NaN value) so a figure reads the same in both apps. */
const na = (v: unknown) => v === null || v === undefined || v === '' ? 'n/a' : null;
const fpct = (v: unknown, digits = 2) => na(v) ?? `${(Number(v) * 100).toFixed(digits)}%`;
const fmoney = (v: unknown, prefix = '$') =>
  na(v) ?? `${prefix}${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const fdec = (v: unknown, digits = 1) => na(v) ?? Number(v).toFixed(digits);
const fint = (v: unknown) => na(v) ?? Number(v).toLocaleString();

/** A small "?" marker - mirrors scripts/html_tables.py's `_render_snapshot_table`
 * question-mark helped-cell: a grey circle that reveals what the metric means on
 * hover. The description bubble is rendered through a portal to <body> (fixed
 * position) so it is never clipped by the table's `overflow: hidden`, matching
 * the Streamlit "what is this figure" tooltip that appears as soon as you hover
 * the "?". */
function MetricTitle({ label, help }: { label: string; help?: string }) {
  const [tip, setTip] = useState<{ top: number; left: number } | null>(null);

  const show = (e: React.SyntheticEvent) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setTip({ top: rect.bottom + 6, left: rect.left });
  };
  const hide = () => setTip(null);

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center' }}>
      {label}
      {help ? (
        <span
          onMouseEnter={show}
          onMouseLeave={hide}
          onFocus={show}
          onBlur={hide}
          style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 14, height: 14, marginLeft: 6, borderRadius: '50%', background: '#D8DEE5',
            color: '#16312E', fontSize: '.7rem', fontWeight: 700, cursor: 'help',
          }}
        >
          ?
        </span>
      ) : null}
      {tip && help
        ? createPortal(
            <span
              role="tooltip"
              style={{
                position: 'fixed',
                top: tip.top,
                left: tip.left,
                width: 230,
                padding: '8px 10px',
                background: '#16312E',
                color: '#EDF3F1',
                fontSize: '.72rem',
                lineHeight: 1.4,
                borderRadius: 6,
                zIndex: 10000,
                fontWeight: 400,
                textAlign: 'left',
                boxShadow: '0 2px 8px rgba(0,0,0,.2)',
                pointerEvents: 'none',
              }}
            >
              {help}
            </span>,
            document.body,
          )
        : null}
    </span>
  );
}

/** The Portfolio Snapshot / UE Model tables - label/value rows, mirroring
 * scripts/html_tables.py's _render_snapshot_table. Each row may carry an
 * optional description shown via a "?" mark on the label. */
function SnapshotTable({ rows }: { rows: Array<[string, string, string?]> }) {
  const cols: TableColumn<[string, string, string?]>[] = [
    { key: 'k', header: 'Metric', render: (r) => <MetricTitle label={r[0]} help={r[2]} /> },
    { key: 'v', header: 'Value', align: 'right', render: (r) => <span className="sd-mono">{r[1]}</span> },
  ];
  return <Table columns={cols} rows={rows} />;
}

/** Summary chart: grey bars = principal disbursed per cohort (all loans),
 * orange line = cohort loss rate (matured only), with the average and 95th
 * percentile loss rates as reference lines - the same layered Altair chart
 * app.py's Summary tab draws. */
function CohortLossRateChart({ rows, avgLoss, pctile95, range }: {
  rows: SummaryChartRow[];
  avgLoss: number | null;
  pctile95: number | null;
  /** LTV cohort range: when set, only those cohorts are plotted. */
  range?: { from: string | null; to: string | null };
}) {
  const ranged = !!(range?.from || range?.to);
  const data = rows
    .map((r) => ({
      cohort: str(r.Cohort).slice(0, 7),
      principal: num(r['Total Principal']),
      lossRate: num(r['Loss Rate']),
    }))
    .filter((r) => inLtvCohortRange(r.cohort, range));
  return (
    <div className="sd-card">
      <h2 className="sd-h2">Cohort Loss Rates</h2>
      <ResponsiveContainer width="100%" height={340}>
        <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="#e7ecee" vertical={false} />
          <XAxis dataKey="cohort" tick={{ fontSize: 11, fill: '#666' }} />
          <YAxis
            yAxisId="left"
            tick={{ fontSize: 11, fill: '#666' }}
            tickFormatter={(v) => (typeof v === 'number' ? v.toLocaleString(undefined, { notation: 'compact' }) : String(v))}
          />
          <YAxis
            yAxisId="right"
            orientation="right"
            tick={{ fontSize: 11, fill: '#666' }}
            tickFormatter={(v) => (typeof v === 'number' ? `${(v * 100).toFixed(0)}%` : String(v))}
          />
          <Tooltip
            formatter={(v, name) =>
              name === 'Loss Rate' && typeof v === 'number' ? pct(v, 2) : typeof v === 'number' ? fnum(v) : v
            }
          />
          <Legend />
          <Bar yAxisId="left" dataKey="principal" fill="#B8BEC6" name="Principal Disbursed" />
          {avgLoss !== null && (
            <ReferenceLine
              yAxisId="right"
              y={avgLoss}
              stroke="#6B7280"
              strokeDasharray="6 6"
              label={{ value: `${ranged ? 'Avg (all cohorts)' : 'Avg'}: ${pct(avgLoss, 2)}`, position: 'insideTopLeft', fill: '#6B7280', fontSize: 11 }}
            />
          )}
          {pctile95 !== null && (
            <ReferenceLine
              yAxisId="right"
              y={pctile95}
              stroke="#d62728"
              strokeDasharray="6 6"
              label={{ value: `95th %ile: ${pct(pctile95, 2)}`, position: 'insideTopRight', fill: '#d62728', fontSize: 11 }}
            />
          )}
          <Line
            yAxisId="right"
            dataKey="lossRate"
            stroke="#E8632A"
            strokeWidth={2.5}
            dot={{ r: 3 }}
            name="Loss Rate"
            connectNulls
          />
        </ComposedChart>
      </ResponsiveContainer>
      <p className="sd-muted">
        Grey bars: principal disbursed per cohort (all loans). Orange line: cohort loss rate, shown
        only for cohorts with enough matured loans.
      </p>
    </div>
  );
}

function LendingSummaryTab() {
  const { state } = useAppStore();
  const m = state.analysisResponse?.summaryMetrics;
  if (!m) return <Alert tone="info">Run analysis to see the portfolio summary.</Alert>;
  const chart = state.analysisResponse?.summaryChart ?? [];
  const fxWarning = state.analysisResponse?.fxWarning;
  // With a General Inputs LTV cohort range, the 95th %ile and interest cards
  // show the same cohort-limited figures the LTV Calculator uses; otherwise
  // the whole book.
  const resp = state.analysisResponse;
  const range = resp?.ltvCohortRange;
  const ranged = !!range && !!(range.from || range.to);
  const rangeText = ranged ? `${range!.from || 'first'} to ${range!.to || 'latest'}` : '';
  const productInterest = ranged ? resp?.ltvInterest : m.productInterestRate;
  const pctile95 = ranged ? resp?.ltvLoss : m.pctile95LossRate;
  const productInterestHint = ranged
    ? `Expected Interest as a share of Principal, for cohorts ${rangeText} only (set in General Inputs) - the same rate the LTV Calculator uses. Across the whole book it is ${fpct(m.productInterestRate)}. Not annualized.`
    : 'Expected Interest as a share of Principal, across all loans (not annualized).';
  const pctile95Hint = ranged
    ? `The loss-rate level that only 5% of matured cohorts in ${rangeText} exceed (set in General Inputs) - the same figure the LTV Calculator uses. Across all matured cohorts it is ${fpct(m.pctile95LossRate)}.`
    : 'The loss-rate level that only 5% of matured cohorts exceed.';
  return (
    <>
      {fxWarning ? <Alert tone="warning">{fxWarning}</Alert> : null}
      <div className="sd-grid sd-grid--metrics">
        <Metric label="Data up to" value={String(m.dataUpTo ?? '—')} hint="The most recent loan disbursement date in the tape (as-of point for the snapshot)." />
        <Metric label="Outstanding GBV" value={fmoney(m.outstandingGbv, '')} hint="Gross book value of loans still within term (not yet matured): Principal + Interest + Fee minus Total Paid for loans where Reached T+3? is False." />
        <Metric label="Loss Rate" value={fpct(m.lossRate)} hint="Portfolio average loss rate across matured cohorts (Reached T+3? = True)." />
        <Metric label={ranged ? `95th %ile Loss Rate (${rangeText})` : '95th %ile Loss Rate'} value={fpct(pctile95)} hint={pctile95Hint} />
        <Metric label={ranged ? `Product Interest Rate (${rangeText})` : 'Product Interest Rate'} value={fpct(productInterest)} hint={productInterestHint} />
      </div>
      <div className="sd-grid sd-grid--2">
        <div>
          <h2 className="sd-h2">Portfolio Snapshot</h2>
          <SnapshotTable
            rows={[
              ranged
                ? ['Date Range', `${range!.from || str(m.dateRangeStart).slice(0, 7)} to ${range!.to || str(m.dateRangeEnd).slice(0, 7)}`, `Cohorts ${rangeText} were selected (LTV cohort range, General Inputs) - only these feed the LTV interest and 95th %ile loss. The full tape runs ${m.dateRangeStart} to ${m.dateRangeEnd}, and the other figures in this table still cover all of it.`]
                : ['Date Range', `${m.dateRangeStart} to ${m.dateRangeEnd}`, 'From the earliest disbursement date to the tape as-of date.'],
              ['Loans', fint(m.loans), 'Total number of loan records in the tape.'],
              ['Value of Principal Disbursed', fmoney(m.principalDisbursed), 'Total principal amount lent across all loans.'],
              ['Total Collected', fmoney(m.totalCollected), 'Total cash collected from borrowers to date.'],
              ['Expected Interest %', fpct(m.expectedInterestPct), 'Total expected interest as a share of total principal across all loans.'],
              ['Expected Fee %', fpct(m.expectedFeePct), 'Total expected fees as a share of total principal across all loans.'],
              ['Avg Loan Value', fmoney(m.avgLoanValue), 'Average principal amount per loan.'],
              ['Avg Total Revenue', fpct(m.avgTotalRevenuePct), 'Expected interest + fees as a share of total principal.'],
              ['PvD', fpct(m.pvd), 'Paid-vs-Due ratio (Total Paid / Total Due) across matured loans (Reached T+3? = True) in the whole portfolio.'],
              ['Weighted Avg Term (matured)', na(m.weightedAvgTermMatured) ?? `${fdec(m.weightedAvgTermMatured)} days`, 'Principal-weighted average term (days) across matured loans only.'],
              ['APR Proxy', fpct(m.aprProxy), 'Avg Annualized Flat Interest Rate: Product Interest Rate divided by the Weighted Avg Term (matured), in months, times 12 - a simple flat-rate annualization (no compounding, no amortization solve).'],
            ]}
          />
        </div>
        {chart.length > 0 && (
          <CohortLossRateChart
            rows={chart}
            avgLoss={num(m.lossRate)}
            pctile95={num(pctile95)}
            range={ranged ? range : undefined}
          />
        )}
      </div>
    </>
  );
}

function RentalSummaryTab() {
  const { state } = useAppStore();
  const m = state.rentalResponse?.summaryMetrics;
  if (!m) return <Alert tone="info">Run analysis to see the portfolio summary.</Alert>;
  const fxWarning = state.rentalResponse?.fxWarning;
  return (
    <>
      {fxWarning ? <Alert tone="warning">{fxWarning}</Alert> : null}
      <div className="sd-grid sd-grid--metrics">
        <Metric label="Contracts" value={fint(m.contracts)} hint="Number of unique assets in the portfolio." />
        <Metric label="MRR" value={fmoney(m.mrr, '')} hint="Monthly recurring revenue from active contracts." />
        <Metric label="Avg MRR per Active Contract" value={fmoney(m.avgMrrPerActiveContract, '')} hint="Total MRR divided by the number of open (active) contracts." />
        <Metric label="Average Useful Life (m)" value={fdec(m.avgUsefulLifeM)} hint="Principal-weighted average useful asset life, in months." />
      </div>
      <div className="sd-grid sd-grid--metrics">
        <Metric label="95th %ile Churn" value={fpct(m.pctile95Churn)} hint="The churn level that only 5% of cohorts exceed." />
        <Metric label="Stressed Churn" value={fpct(m.stressedChurn)} hint="Churn under the stress assumptions (e.g. adverse FX/credit scenario)." />
        <Metric label="Contracts Churned" value={fpct(m.contractsChurned)} hint="Share of contracts that have churned (closed) relative to all contracts." />
        <Metric label="Defaulted contracts > 3mo" value={na(m.defaultedGt3m) ?? fdec(m.defaultedGt3m, 0)} hint="Number of contracts in default for more than 3 months." />
        <Metric label="% recovered" value={fpct(m.pctRecovered)} hint="Share of defaulted value that has been recovered." />
      </div>
      <div className="sd-grid sd-grid--metrics">
        <Metric label="Loss (non-recoverability)" value={fpct(m.lossNonRecoverability)} hint="Expected loss from defaulted value that is not recoverable." />
        <Metric label="MRR / average cost" value={fpct(m.mrrOverAvgCost)} hint="Monthly recurring revenue relative to average asset cost." />
        <Metric label="Average Collection Rate" value={fpct(m.avgCollectionRate)} hint="Share of expected payments actually collected." />
      </div>
      <h2 className="sd-h2">LTV &amp; UE highlights</h2>
      <div className="sd-grid sd-grid--metrics">
        <Metric label="Asset LTV (max 95%)" value={na(m.assetLtv) ?? fpct(m.assetLtv, 1)} hint="Loan-to-value against asset value, capped at 95%." />
        <Metric label="Net UE margin" value={fpct(m.netUeMargin)} hint="Net unit-economics margin after costs." />
        <Metric label="Asset DSLP30" value={fpct(m.assetDslp30)} hint="Asset-level days-sales-late over the last 30 days exposure." />
        <Metric label="Receivables Coverage (180d)" value={fpct(m.receivablesCoverage180d)} hint="Coverage of expected receivables over a 180-day horizon." />
      </div>
      <h2 className="sd-h2">Portfolio Snapshot</h2>
      <SnapshotTable
        rows={[
          ['Date Range', `${m.dateRangeStart} to ${m.dateRangeEnd}`, 'From the earliest contract start date to the tape as-of date.'],
          ['Contracts', fint(m.contractRecords), 'Total number of contract records in the tape.'],
          ['Unique Assets', fint(m.uniqueAssets), 'Total number of unique assets.'],
          ['Total Collected', fmoney(m.totalCollected), 'Total cash collected from customers to date.'],
          ['Total Cost of Assets', fmoney(m.totalCostOfAssets), 'Total acquisition cost of the assets.'],
          ['Downpayment %', fpct(m.downpaymentPct), 'Total downpayments as a share of total asset cost.'],
          ['Lease Tenor (m)', fdec(m.leaseTenorM), 'Cost-weighted average lease term in months.'],
          ['Margin', fpct(m.margin), 'Total scheduled payments over asset cost minus 1.'],
          ['PvD', fpct(m.pvd), 'Total paid over total expected to date.'],
          ['Utilisation Rate', fpct(m.utilisationRate), 'Share of assets currently on an open contract.'],
          ['Historical % Collected on Principal', fpct(m.historicalPctCollectedOnPrincipal), 'Collections (incl. recovery) over the initial asset cost for assets that reached term.'],
        ]}
      />
    </>
  );
}

function SummaryTab() {
  const { state } = useAppStore();
  return state.modelKey === 'rental' ? <RentalSummaryTab /> : <LendingSummaryTab />;
}

// The Data-Checks outer tab holds two nested tabs mirroring the Streamlit
// app's `tabs[1]`: "Data Input" (the raw uploaded data grid) and "Checks"
// (the data-quality findings).
function DataChecksTab() {
  const { state } = useAppStore();
  const [subTab, setSubTab] = useState<'input' | 'checks'>('input');
  const subTabs = [
    { id: 'input', label: 'Data Input' },
    { id: 'checks', label: 'Checks' },
  ];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Tabs items={subTabs} value={subTab} onChange={(v) => setSubTab(v as 'input' | 'checks')} />
      {subTab === 'input' ? <DataInputGridTab /> : <ChecksList />}
      {subTab === 'input' && state.modelKey === 'revolving' ? <RevolvingOutputsTab /> : null}
      {subTab === 'checks' && state.modelKey === 'revolving' ? (
        <>
          <RevolvingAgeingMix />
          <RevolvingEclTable />
        </>
      ) : null}
    </div>
  );
}

function DataInputGridTab() {
  const { state } = useAppStore();
  const [data, setData] = useState<IngestData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!state.file) {
      setData(null);
      return;
    }
    const mapping: Record<string, string> = {};
    for (const m of state.mappings) {
      if (m.sourceColumn) mapping[m.fieldKey] = m.sourceColumn;
    }
    if (Object.keys(mapping).length === 0) {
      setData(null);
      setError('No column mapping yet — go back and map your columns first.');
      return;
    }
    setLoading(true);
    setError(null);
    fetchIngestData({ ingestId: state.file.ingestId, mapping })
      .then(setData)
      .catch((err) => {
        setError(err instanceof Error ? err.message : 'Could not load the data grid.');
        setData(null);
      })
      .finally(() => setLoading(false));
  }, [state.file, state.mappings]);

  if (loading) return <Alert tone="info">Loading uploaded data…</Alert>;
  if (error) return <Alert tone="error">{error}</Alert>;
  if (!data || data.rows.length === 0) return <Alert tone="info">No uploaded rows to show.</Alert>;
  return (
    <>
      <p className="sd-muted">
        {data.rowCount.toLocaleString()} rows x {data.columns.length} columns uploaded.
        {data.truncated ? ` Showing first ${data.rows.length.toLocaleString()} rows.` : ''}
      </p>
      <RecordTable rows={data.rows} />
    </>
  );
}

function ChecksList() {
  const { state, analyse } = useAppStore();
  const isRental = state.modelKey === 'rental';
  const checks: QualityCheck[] =
    (state.modelKey === 'revolving'
      ? state.revolvingResponse?.qualityChecks
      : isRental
        ? state.rentalResponse?.checks
        : state.analysisResponse?.checks) ?? [];
  const [deduping, setDeduping] = useState(false);
  const [escalating, setEscalating] = useState<string | null>(null);
  const [escalateResults, setEscalateResults] = useState<Record<string, EscalateResult>>({});

  // "Deduplicate" checkbox (Streamlit's duplicate_loan_id handling): on
  // check, re-run the analysis dropping to the first row per duplicated ID -
  // the recomputed checks then no longer flag the duplicate.
  const onDedupe = async (e: ChangeEvent<HTMLInputElement>) => {
    if (!e.target.checked) return;
    setDeduping(true);
    try {
      await analyse(true);
    } catch {
      // analyse() already dispatches analysisFail into the store.
    } finally {
      setDeduping(false);
    }
  };

  // "Escalate flagged cohorts to analytics" (Streamlit's unexplained_variance
  // handling): AI-draft a write-up from the flagged cohort facts and send it
  // to Slack. The drafted message is always surfaced so it's useful even when
  // no Slack webhook is configured.
  const onEscalate = async (key: string, c: QualityCheck) => {
    setEscalating(key);
    try {
      const res = await escalateVariance({
        modelKey: state.modelKey,
        modelName: isRental ? 'Rental & Subscription' : 'Lending',
        metricLabel: isRental ? 'Churn Rate (%)' : 'Loss Rate (%)',
        filename: state.file?.name ?? '',
        detail: c.detail ?? [],
      });
      setEscalateResults((prev) => ({ ...prev, [key]: res }));
    } catch (err) {
      setEscalateResults((prev) => ({
        ...prev,
        [key]: {
          sent: false,
          error: err instanceof Error ? err.message : 'Could not escalate.',
          message: '',
          aiWarning: null,
          borrowerName: null,
        },
      }));
    } finally {
      setEscalating(null);
    }
  };

  if (checks.length === 0) return <Alert tone="success">No data quality issues flagged.</Alert>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <h2 className="sd-h2">Data Quality Checks</h2>
      {checks.map((c, i) => {
        const key = c.check_id ?? String(i);
        const esc = escalateResults[key];
        return (
          <div className="sd-card" key={key}>
            <Alert tone={c.level}>{c.message}</Alert>
            {c.detail && c.detail.length > 0 ? (
              c.detail_expander ? (
                <details>
                  <summary className="sd-muted">{c.detail_expander}</summary>
                  <RecordTable rows={c.detail} />
                  {c.check_id === 'duplicate_loan_id' && (
                    <div style={{ marginTop: 8 }}>
                      <label
                        className="sd-muted"
                        style={{ display: 'inline-flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}
                      >
                        <input type="checkbox" onChange={onDedupe} disabled={deduping} />
                        {deduping
                          ? 'Re-running analysis without duplicates…'
                          : 'Deduplicate: keep only the first row for each duplicated ID, then re-run analysis'}
                      </label>
                    </div>
                  )}
                </details>
              ) : (
                <>
                  <RecordTable rows={c.detail} />
                  {c.check_id === 'unexplained_variance' && (
                    <div style={{ marginTop: 8 }}>
                      <Button
                        variant="secondary"
                        disabled={escalating === key}
                        onClick={() => void onEscalate(key, c)}
                      >
                        {escalating === key ? 'Drafting…' : 'Escalate flagged cohorts to analytics'}
                      </Button>
                      {esc &&
                        (esc.sent ? (
                          <div style={{ marginTop: 8 }}>
                            {esc.aiWarning ? (
                              <Alert tone="warning">
                                AI write-up unavailable ({esc.aiWarning}); sent a plain summary instead.
                              </Alert>
                            ) : (
                              <Alert tone="success">Sent to analytics.</Alert>
                            )}
                          </div>
                        ) : (
                          <div style={{ marginTop: 8 }}>
                            <Alert tone="error">Could not escalate: {esc.error}</Alert>
                            {esc.message && (
                              <details>
                                <summary className="sd-muted">View drafted message</summary>
                                <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{esc.message}</pre>
                              </details>
                            )}
                          </div>
                        ))}
                    </div>
                  )}
                </>
              )
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/** Column-aware formatting matching app.py's _render_cohorts_grid
 * (_FMT_MONEY_JS/_FMT_INT_JS/_FMT_PCT1_JS) - Loan Count/Matured Count are
 * deliberately left as plain numbers (no thousands separator), same as
 * AgGrid's unformatted default there. */
const COHORT_MONEY_COLS = new Set([
  'Total Principal', 'Total Interest', 'Total Fee', 'Total Due', 'Total Paid', 'Avg Loan Principal Size',
]);
const COHORT_TERM_COLS = new Set(['Weighted Avg Term']);
const COHORT_PCT_COLS = new Set([
  'PvD Ratio', 'Loss Rate', 'Avg Loan Fee % per cohort', 'Avg Annualized Flat Interest Rate',
]);
const COHORT_LEAD_COLS = ['Cohort', 'Loan Count', 'Matured Count', 'Loss Rate'];

function cohortCell(col: string, v: unknown) {
  if (col === 'Cohort') return str(v).slice(0, 10);
  if (COHORT_MONEY_COLS.has(col)) return fmoney(v);
  if (COHORT_TERM_COLS.has(col)) return fint(v);
  if (COHORT_PCT_COLS.has(col)) return fpct(v, 1);
  const n = num(v);
  return n != null ? String(n) : str(v);
}

/** Equal-width histogram (10 bins by default), mirroring pandas'
 * pd.cut(series, bins=10).value_counts(sort=False) - the counts
 * app.py's HistogramHeader draws as bars. */
function histogramBins(values: number[], bins = 10): number[] {
  if (values.length === 0) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) return [];
  const width = (max - min) / bins;
  const counts = new Array(bins).fill(0);
  for (const v of values) {
    const idx = Math.min(bins - 1, Math.max(0, Math.floor((v - min) / width)));
    counts[idx] += 1;
  }
  return counts;
}

const f2 = (n: number) => n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Same stats app.py's _column_stats_tooltip shows on hover: full
 * count/nulls/sum/mean/median/std/min/max for a numeric column, or
 * count/nulls/distinct for a categorical one (Cohort here). */
interface ColStats {
  histogram: number[];
  tooltip: string;
}

function computeColumnStats(rows: Array<Record<string, unknown>>, col: string): ColStats {
  const raw = rows.map((r) => r[col]);
  const nonNull = raw.filter((v) => v !== null && v !== undefined && v !== '');
  const nulls = raw.length - nonNull.length;
  if (col === 'Cohort') {
    const distinct = new Set(nonNull.map(String)).size;
    return {
      histogram: [],
      tooltip: `Count: ${nonNull.length.toLocaleString()}\nNulls: ${nulls.toLocaleString()}\nDistinct: ${distinct.toLocaleString()}`,
    };
  }
  const nums = nonNull.map((v) => num(v)).filter((v): v is number => v != null);
  if (nums.length === 0) {
    return { histogram: [], tooltip: `Count: 0\nNulls: ${nulls.toLocaleString()}` };
  }
  const sum = nums.reduce((a, b) => a + b, 0);
  const mean = sum / nums.length;
  const sorted = [...nums].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const median = sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
  const variance =
    nums.length > 1 ? nums.reduce((a, v) => a + (v - mean) ** 2, 0) / (nums.length - 1) : 0;
  return {
    histogram: new Set(nums).size > 1 ? histogramBins(nums) : [],
    tooltip:
      `Count: ${nums.length.toLocaleString()}\n` +
      `Nulls: ${nulls.toLocaleString()}\n` +
      `Sum: ${f2(sum)}\n` +
      `Mean: ${f2(mean)}\n` +
      `Median: ${f2(median)}\n` +
      `Std Dev: ${f2(Math.sqrt(variance))}\n` +
      `Min: ${f2(sorted[0])}\n` +
      `Max: ${f2(sorted[sorted.length - 1])}`,
  };
}

/** A column header showing its value distribution as a small bar chart,
 * with the same count/nulls/sum/mean/median/std/min/max stats as a native
 * hover tooltip - mirroring app.py's HistogramHeader + headerTooltip. */
function ColumnHeader({ col, stats }: { col: string; stats: ColStats }) {
  const max = Math.max(...stats.histogram, 1);
  return (
    <div title={stats.tooltip}>
      {stats.histogram.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'flex-end', height: 24, gap: 1, padding: '0 2px' }}>
          {stats.histogram.map((v, i) => (
            <div
              key={i}
              style={{ flex: 1, height: Math.max(2, Math.round((v / max) * 22)), background: '#4C8577', borderRadius: 1 }}
            />
          ))}
        </div>
      )}
      <div style={{ marginTop: 3 }}>{col}</div>
    </div>
  );
}

/** The Cohorts tab's table - lead columns first (Cohort, Loan Count,
 * Matured Count, Loss Rate), same ordering app.py's cohort_view uses, each
 * header showing its distribution + hover stats like app.py's AgGrid does. */
function CohortsGrid({
  rows,
  selected,
  onToggle,
}: {
  rows: Array<Record<string, unknown>>;
  /** When provided, adds a leading checkbox column keyed off each row's
   * "Cohort" value - the Cohorts-tab selection that scopes the rest of the
   * analysis (see LendingCohortsTab). Omit for a plain, non-selectable grid. */
  selected?: Set<string>;
  onToggle?: (cohort: string) => void;
}) {
  const allCols = Object.keys(rows[0] ?? {});
  const lead = COHORT_LEAD_COLS.filter((c) => allCols.includes(c));
  const ordered = [...lead, ...allCols.filter((c) => !lead.includes(c))];
  const cols: TableColumn<Record<string, unknown>>[] = ordered.map((c) => ({
    key: c,
    header: <ColumnHeader col={c} stats={computeColumnStats(rows, c)} />,
    align: 'right' as const,
    render: (r) => <span className="sd-mono">{cohortCell(c, r[c])}</span>,
  }));
  if (selected && onToggle) {
    cols.unshift({
      key: '__include',
      header: 'Include',
      align: 'left',
      render: (r) => {
        const key = str(r['Cohort']);
        return (
          <input
            type="checkbox"
            checked={selected.has(key)}
            onChange={() => onToggle(key)}
            aria-label={`Include cohort ${key} in analysis`}
          />
        );
      },
    });
  }
  return <Table columns={cols} rows={rows} />;
}

const COHORT_CHART_KINDS = ['Line', 'Bar', 'Area', 'Scatter'] as const;

/** "Explore Cohort Data" - a single chart plotted by Cohort (X is fixed,
 * unlike the full Custom Visualizations tab), mirroring
 * _render_custom_visualizations_tab(simple=True, fixed_x="Cohort").
 * Deliberately not wired into the global customCharts/getChartDataSources
 * machinery: this plots the tab's own locally-filtered cohort_view, not one
 * of the app-wide chart data sources. */
function CohortsExploreChart({ rows }: { rows: Array<Record<string, unknown>> }) {
  const numeric = numericColumns(rows).filter((c) => c !== 'Cohort');
  const categorical = categoricalColumns(rows, numeric).filter((c) => c !== 'Cohort');
  const [yCol, setYCol] = useState(() => pickDefaultY(numeric));
  const [kind, setKind] = useState<(typeof COHORT_CHART_KINDS)[number]>('Line');
  const [colorCol, setColorCol] = useState<string | null>(null);

  if (numeric.length === 0) return null;
  const effectiveY = numeric.includes(yCol) ? yCol : numeric[0];
  const effectiveColor = colorCol && categorical.includes(colorCol) ? colorCol : null;

  const points = aggregateChartData(rows, 'Cohort', effectiveY, effectiveColor);
  const { data: wideData, seriesNames } = pivotWide(points, 'Cohort', effectiveY, effectiveColor);
  const card: ChartCardSpec = { id: 0, source: '', kind, xCol: 'Cohort', yCol: effectiveY, colorCol: effectiveColor };

  const isPct = COHORT_PCT_COLS.has(effectiveY);
  const chartOpts: ChartRenderOpts = {
    xLabel: 'Cohort',
    yLabel: effectiveY,
    xTickFormat: (v) => str(v).slice(0, 7),
    yTickFormat: (v) => (isPct ? `${(v * 100).toFixed(0)}%` : v.toLocaleString(undefined, { notation: 'compact' })),
    tooltipFormat: (v, name) => [cohortCell(effectiveY, v), name],
  };

  return (
    <div className="sd-card">
      <h2 className="sd-h2">
        {effectiveY} by Cohort{kind !== 'Scatter' ? ` (${kind.toLowerCase()})` : ''}
      </h2>
      <div
        style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10, marginBottom: 12 }}
      >
        <Select aria-label="Chart type" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
          {COHORT_CHART_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </Select>
        <Select aria-label="Metric" value={effectiveY} onChange={(e) => setYCol(e.target.value)}>
          {numeric.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
        {categorical.length > 0 && (
          <Select
            aria-label="Group / color by"
            value={effectiveColor ?? ''}
            onChange={(e) => setColorCol(e.target.value || null)}
          >
            <option value="">Group / color by (optional)</option>
            {categorical.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
        )}
      </div>
      <ResponsiveContainer width="100%" height={320}>
        {renderChart(card, wideData, points, seriesNames, chartOpts)}
      </ResponsiveContainer>
    </div>
  );
}

function LendingCohortsTab() {
  const { state, dispatch, analyse } = useAppStore();
  const response = state.analysisResponse;
  const [maturedOnly, setMaturedOnly] = useState(true);
  const [minLoans, setMinLoans] = useState(() => Number(response?.generalInputs?.minLoansPerCohort ?? 0));
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [applying, setApplying] = useState(false);
  // The two full, UNFILTERED cohort lists (matured-only and all-loans) -
  // captured only from a response that itself carries no cohort selection
  // (state.selectedCohorts === null). A response computed FROM a selection
  // only contains the selected cohorts, so refreshing these from every
  // response would make previously-excluded cohorts vanish from the
  // checklist with no way to re-include them - the grid below always
  // renders from these, never straight from response.cohorts/cohortsAllLoans.
  const [masterMatured, setMasterMatured] = useState<Array<Record<string, unknown>>>([]);
  const [masterAll, setMasterAll] = useState<Array<Record<string, unknown>>>([]);

  useEffect(() => {
    if (!response) return;
    setMinLoans(Number(response.generalInputs?.minLoansPerCohort ?? 0));
    if (state.selectedCohorts === null) {
      setMasterMatured(response.cohorts);
      setMasterAll(response.cohortsAllLoans);
      setSelected(new Set(response.cohorts.map((r) => str(r['Cohort']))));
    }
    // response and state.selectedCohorts change together (a filtered rerun
    // produces both at once) - only the emptiness of the selection matters
    // here, not its exact contents.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [response, state.selectedCohorts === null]);

  if (!response) return <Alert tone="info">Run analysis to see cohorts.</Alert>;

  const master = maturedOnly ? masterMatured : masterAll;
  const cohortView = master.filter((r) => (num(r['Loan Count']) ?? 0) >= minLoans);

  const toggle = (cohort: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(cohort)) next.delete(cohort);
      else next.add(cohort);
      return next;
    });
  };
  const selectAll = () => setSelected(new Set(master.map((r) => str(r['Cohort']))));
  const selectNone = () => setSelected(new Set());

  const allIncluded = selected.size === master.length;
  const isFiltered = state.selectedCohorts !== null;

  const runWith = async (cohorts: string[] | null) => {
    setApplying(true);
    dispatch({ type: 'setSelectedCohorts', cohorts });
    try {
      // Pass `cohorts` explicitly rather than letting analyse() read
      // state.selectedCohorts itself - the dispatch above hasn't been
      // applied to `state` yet at this point (dispatch schedules a
      // re-render, it doesn't update the value synchronously), so analyse()
      // would otherwise run against the PREVIOUS selection instead of this
      // one.
      await analyse(state.dedupeActive, cohorts);
    } catch {
      // analyse() already dispatches analysisFail into the store.
    } finally {
      setApplying(false);
    }
  };
  const applySelection = () => runWith(allIncluded ? null : Array.from(selected));
  const clearFilter = () => runWith(null);

  const appliedCount = state.selectedCohorts?.length ?? 0;

  return (
    <>
      {isFiltered && (
        <Alert tone="info">
          Summary, UE, LTV and Data Checks are currently scoped to {appliedCount} selected cohort
          {appliedCount === 1 ? '' : 's'} below, not the full portfolio.{' '}
          <Button variant="ghost" onClick={clearFilter} disabled={applying} style={{ padding: '0 4px' }}>
            Show all cohorts
          </Button>
        </Alert>
      )}
      <label className="sd-muted" style={{ display: 'inline-flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
        <input
          type="checkbox"
          checked={maturedOnly}
          onChange={(e) => setMaturedOnly(e.target.checked)}
        />
        Matured loans only
      </label>
      <p className="sd-muted">
        {maturedOnly
          ? `${master.length} monthly cohorts with at least one matured loan (Reached T+3? = True)`
          : `${master.length} monthly cohorts (all loans, matured or not)`}
      </p>
      <div className="sd-field" style={{ maxWidth: 260, marginBottom: 12 }}>
        <span>Minimum loans per cohort</span>
        <Input
          type="number"
          min={0}
          step={1}
          value={minLoans}
          onChange={(e) => setMinLoans(Math.max(0, Number(e.target.value) || 0))}
        />
      </div>
      <div className="sd-grid sd-grid--metrics">
        <Metric label="Total Cohorts available" value={String(master.length)} hint="Number of monthly cohorts after the maturity filter (Reached T+3? = True when 'Matured loans only' is on)." />
        <Metric label="Cohorts shown" value={String(cohortView.length)} hint="Cohorts that also meet the 'Minimum loans per cohort' threshold." />
        <Metric label="Cohorts selected for analysis" value={String(selected.size)} hint="How many of the shown cohorts are checked for the next analysis run." />
      </div>
      <div className="sd-actions" style={{ gap: 8, marginBottom: 12 }}>
        <Button variant="secondary" onClick={selectAll} disabled={applying}>
          Select all
        </Button>
        <Button variant="secondary" onClick={selectNone} disabled={applying}>
          Select none
        </Button>
        <Button
          variant="primary"
          onClick={applySelection}
          loading={applying}
          disabled={selected.size === 0}
        >
          Apply selection to analysis
        </Button>
      </div>
      {cohortView.length === 0 ? (
        <Alert tone="info">No cohorts passed the maturity/minimum filter.</Alert>
      ) : (
        <CohortsGrid rows={cohortView} selected={selected} onToggle={toggle} />
      )}
      <h2 className="sd-h2" style={{ marginTop: 16 }}>
        Explore Cohort Data
      </h2>
      {cohortView.length > 0 && <CohortsExploreChart rows={cohortView} />}
    </>
  );
}

function RentalCohortsTab() {
  const { state } = useAppStore();
  const rows = state.rentalResponse?.cohortRows ?? [];
  if (rows.length === 0) return <Alert tone="info">No lease cohorts passed the minimum filter.</Alert>;
  return <RecordTable rows={rows} />;
}

function AssetViewTab() {
  const { state } = useAppStore();
  const rows = state.rentalResponse?.assetView ?? [];
  if (rows.length === 0) return <Alert tone="info">No asset-view rows.</Alert>;
  return <RecordTable rows={rows} />;
}

function TsCovenantsTab() {
  const { state } = useAppStore();
  const ts = state.rentalResponse?.ts;
  const hasValues = ts && Object.values(ts).some((v) => v !== null && v !== undefined && v !== '');
  if (!hasValues) return <Alert tone="info">No TS covenants computed (debt service 180d not provided).</Alert>;
  return <MetricTable title="TS Covenants" data={ts!} />;
}

function GeneralAnalysisTab() {
  const { state } = useAppStore();
  const general = state.rentalResponse?.general;
  if (!general) return <Alert tone="info">No general analysis.</Alert>;
  return (
    <MetricTable title="General Analysis" data={general} />
  );
}

function RecordTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  const cols = Object.keys(rows[0] ?? {}).map((key) => ({
    key,
    header: key,
    align: 'right' as const,
    render: (r: Record<string, unknown>) => {
      const n = num(r[key]);
      return <span className={n != null ? 'sd-mono' : undefined}>{n != null ? fnum(n) : str(r[key])}</span>;
    },
  }));
  return <Table columns={cols} rows={rows} />;
}

/** Client-side IRR (internal rate of return) via Newton-Raphson, mirroring
 * numpy_financial's npf.irr used by app.py's UE Model tab. Returns null
 * (matching npf.irr raising) when it can't converge. */
function irr(cashflows: number[]): number | null {
  if (cashflows.length === 0) return null;
  let rate = 0.1;
  for (let iter = 0; iter < 200; iter++) {
    let f = 0;
    let fd = 0;
    for (let i = 0; i < cashflows.length; i++) {
      const denom = Math.pow(1 + rate, i);
      f += cashflows[i] / denom;
      fd -= (i * cashflows[i]) / Math.pow(1 + rate, i + 1);
    }
    if (Math.abs(f) < 1e-9) return rate;
    if (!isFinite(fd) || fd === 0) return null;
    rate = rate - f / fd;
    if (!isFinite(rate)) return null;
  }
  return null;
}

/** A single Cohort line chart with an average reference line - the same
 * layered Altair chart app.py's lending UE Overview tab draws for each
 * metric (loss, term, interest %, fee %) per cohort. */
function LendingUeCohortChart({ rows, title, yCol, yFmt, avg, avgLabel, color }: {
  rows: Array<Record<string, unknown>>;
  title: string;
  yCol: 'Loss Rate' | 'Weighted Avg Term' | 'Interest %' | 'Fee %';
  yFmt: (v: number) => string;
  avg: number | null;
  avgLabel: string;
  color: string;
}) {
  const chart = rows.map((r) => ({ Cohort: String(r.Cohort), value: num(r[yCol]) })).filter((r) => r.value !== null);
  if (chart.length === 0) return null;
  return (
    <div className="sd-card">
      <h2 className="sd-h2">{title}</h2>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={chart} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="#e7ecee" vertical={false} />
          <XAxis dataKey="Cohort" tick={{ fontSize: 11, fill: '#666' }} />
          <YAxis tick={{ fontSize: 11, fill: '#666' }} tickFormatter={(v) => (typeof v === 'number' ? yFmt(v) : v)} />
          <Tooltip formatter={(v) => (typeof v === 'number' ? yFmt(v) : v)} />
          <Line dataKey="value" stroke={color} strokeWidth={2} dot={{ r: 3 }} name={yCol} connectNulls />
          {avg !== null && (
            <ReferenceLine y={avg} stroke={color} strokeDasharray="6 6" label={{ value: avgLabel, position: 'insideTopLeft', fill: color, fontSize: 11 }} />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** The rental Unit Economics tab - mirrors app.py's rental tabs[3] ("Unit
 * Economics Analysis"): three grouped metric rows followed by the
 * "High level repayment curve" chart and its table. */
function RentalUnitEconomicsTab() {
  const { state } = useAppStore();
  const ue = state.rentalResponse?.ue as Record<string, unknown> | undefined;
  if (!ue) return <Alert tone="info">Run analysis to see unit economics.</Alert>;
  const curveRows = state.rentalResponse?.curveRows ?? [];

  const metricRow = (items: Array<[string, string, string?]>) => (
    <div className="sd-grid sd-grid--metrics">
      {items.map(([label, value, hint]) => (
        <Metric key={label} label={label} value={value} hint={hint} />
      ))}
    </div>
  );

  return (
    <>
      {metricRow([
        ['Downpayment %', fpct(ue['downpayment_pct'], 1), 'Share of asset cost paid upfront by the customer.'],
        ['Lease Tenor (m)', fdec(ue['lease_tenor_m']), 'Average lease term, in months.'],
        ['Margin', fpct(ue['margin'], 1), 'Unit-economics margin on the lease.'],
        ['Utilisation Rate', fpct(ue['utilisation_rate'], 1), 'Share of asset value that is being put to use.'],
      ])}
      {metricRow([
        ['PvD', fpct(ue['pvd'], 1), 'Paid-vs-Due ratio (payments received relative to amounts due).'],
        ['Historical % Collected on Principal', fpct(ue['historical_pct_collected_on_principal'], 1), 'Share of principal that has historically been collected.'],
        ['Monthly Observed Repayment', fpct(ue['monthly_observed_repayment'], 2), 'Observed monthly repayment rate on the portfolio.'],
      ])}
      {metricRow([
        ['Funding Cost (% p.a.)', fpct(ue['funding_cost_pct'], 2), 'Annualized cost of funding the portfolio.'],
        ['Direct Cost (% p.a.)', fpct(ue['direct_cost_pct'], 2), 'Annualized direct operating cost as a share of value.'],
        ['Cost of Funding over Life', fpct(ue['cost_of_funding_over_life_pct'], 2), 'Total funding cost accumulated over the asset life.'],
        ['Net UE Margin', fpct(ue['net_margin'], 1), 'Net unit-economics margin after funding and direct costs.'],
      ])}

      {curveRows.length > 0 && (
        <RentalRepaymentCurve rows={curveRows} />
      )}
    </>
  );
}

/** The rental UE tab's "High level repayment curve" - a line chart of
 * pct_avg_collection by month-on-books (dropping null rows, matching
 * app.py's `curve.dropna(subset=['pct_avg_collection'])`) plus the full
 * curve table. */
function RentalRepaymentCurve({ rows }: { rows: Array<Record<string, unknown>> }) {
  const chart = rows
    .map((r) => ({ mob: num(r['mob']), collection: num(r['pct_avg_collection']) }))
    .filter((r) => r.mob !== null && r.collection !== null) as Array<{ mob: number; collection: number }>;
  return (
    <>
      <h2 className="sd-h2" style={{ marginTop: 16 }}>High level repayment curve</h2>
      {chart.length > 0 ? (
        <div className="sd-card">
          <ResponsiveContainer width="100%" height={350}>
            <LineChart data={chart} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
              <CartesianGrid stroke="#e7ecee" vertical={false} />
              <XAxis dataKey="mob" tick={{ fontSize: 11, fill: '#666' }} label={{ value: 'Month on Books', position: 'insideBottom', offset: -5, fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11, fill: '#666' }} tickFormatter={(v) => (typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : v)} />
              <Tooltip formatter={(v) => (typeof v === 'number' ? `${(v * 100).toFixed(2)}%` : v)} />
              <Line dataKey="collection" stroke="#155b54" strokeWidth={2} dot={{ r: 3 }} name="% Avg collection" connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : null}
      <div style={{ marginTop: 12 }}>
        <RecordTable rows={rows} />
      </div>
    </>
  );
}

/** The lending Unit Economics tab - mirrors app.py's lending tabs[4] ("Unit
 * Economics Analysis") with its Overview / UE Model sub-tabs. */
function LendingUnitEconomicsTab() {
  const [subTab, setSubTab] = useState<'overview' | 'model'>('overview');
  const subTabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'model', label: 'UE Model' },
  ];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Tabs items={subTabs} value={subTab} onChange={(v) => setSubTab(v as 'overview' | 'model')} />
      {subTab === 'overview' ? <LendingUeOverviewTab /> : <LendingUeModelTab />}
    </div>
  );
}

/** The lending UE Overview sub-tab: three grouped metric rows plus four
 * per-cohort charts (loss, term, interest %, fee %) each with an average
 * reference line - mirroring app.py's lending overview_tab. */
function LendingUeOverviewTab() {
  const { state } = useAppStore();
  const response = state.analysisResponse;
  if (!response) return <Alert tone="info">Run analysis to see unit economics.</Alert>;
  const ue = response.ue as Record<string, unknown>;
  const chart = response.chart ?? [];

  const naS = (v: unknown, digits = 1) => na(v) ?? `${(num(v) ?? NaN).toFixed(digits)}`;
  const fterm = (v: unknown) => na(v) ?? Number(v).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const fprincipal = (v: unknown) => na(v) ?? (num(v) ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 });

  const rows: Array<Array<[string, string, string?]>> = [
    [
      ['Average Expected Term (days)', fterm(ue['Average Expected Term']), 'Principal-weighted average loan term, in days.'],
      ['Average Loss', fpct(ue['Average Loss'], 1), 'Average loss rate across matured cohorts (Reached T+3? = True).'],
      ['Loss Rate Proxy (1-PvD)', fpct(ue['Loss Rate Proxy (1-PvD)'], 1), '1 minus the Paid-vs-Due ratio - a paid-based proxy for the loss rate.'],
    ],
    [
      ['Average Principal Amount', fprincipal(ue['Average Principal Amount']), 'Average principal loaned per loan record.'],
      ['Average Fee %', fpct(ue['Average Fee %'], 1), 'Expected fees as a share of total principal.'],
      ['Average Interest %', fpct(ue['Average Interest %'], 1), 'Expected interest as a share of total principal (the uniform product rate).'],
    ],
    [['Sense-check Margin', fpct(ue['Sense-check Margin'], 1), 'Fee + interest margin used as a sanity check on the product economics.']],
  ];

  return (
    <>
      {rows.map((row, i) => (
        <div key={i} className="sd-grid sd-grid--metrics">
          {row.map(([label, value, hint]) => (
            <Metric key={label} label={label} value={value} hint={hint} />
          ))}
        </div>
      ))}
      {chart.length > 0 && (
        <div className="sd-grid sd-grid--2">
          <LendingUeCohortChart rows={chart} title="Loss per Cohort" yCol="Loss Rate" yFmt={(v) => `${(v * 100).toFixed(1)}%`} avg={num(ue['Average Loss'])} avgLabel={`Avg: ${pct(num(ue['Average Loss']), 1)}`} color="#2ca02c" />
          <LendingUeCohortChart rows={chart} title="Average Term per Cohort (days)" yCol="Weighted Avg Term" yFmt={(v) => v.toFixed(0)} avg={num(ue['Average Expected Term'])} avgLabel={`Avg: ${naS(ue['Average Expected Term'])} days`} color="#2ca02c" />
          <LendingUeCohortChart rows={chart} title="Average Product Interest Percent per Cohort" yCol="Interest %" yFmt={(v) => `${(v * 100).toFixed(1)}%`} avg={num(ue['Average Interest %'])} avgLabel={`Avg: ${pct(num(ue['Average Interest %']), 1)}`} color="#ff7f0e" />
          <LendingUeCohortChart rows={chart} title="Average Fee Percent per Cohort" yCol="Fee %" yFmt={(v) => `${(v * 100).toFixed(1)}%`} avg={num(ue['Average Fee %'])} avgLabel={`Avg: ${pct(num(ue['Average Fee %']), 1)}`} color="#1f77b4" />
        </div>
      )}
    </>
  );
}

/** The lending UE Model sub-tab - port of app.py's interactive
 * _render_ue_model_ai_tab. The manual cost / loss-rate assumptions are
 * React state; every dependent figure (IRR, product margin, cost of
 * finance, ...) recomputes client-side from the matured-loan aggregates the
 * backend serves in `ueModel`, matching the Streamlit recompute-on-change
 * behaviour exactly. */
function LendingUeModelTab() {
  const { state, dispatch } = useAppStore();
  const response = state.analysisResponse;
  const m = response?.ueModel;
  const defaults =
    m?.costDefaults ?? { upfront_cost_pct: 1, ongoing_cost_pct: 2, financing_cost_pct: 13, facility_fee_pct: 2, hedge_cost_pct: 3 };
  // In the store, not useState: Ask AI sends these so it can quote the
  // figures on screen (see AppState.ueModelInputs). null = the defaults.
  const inputs: UeModelAssumptions = state.ueModelInputs ?? {
    upfrontCostPct: defaults.upfront_cost_pct / 100,
    ongoingCostPct: defaults.ongoing_cost_pct / 100,
    financingCostPct: defaults.financing_cost_pct / 100,
    facilityFeePct: defaults.facility_fee_pct / 100,
    hedgeCostPct: defaults.hedge_cost_pct / 100,
    lossChoice: 'Actual',
    lossInputPct: null,
  };
  const set = (patch: Partial<UeModelAssumptions>) => dispatch({ type: 'setUeModelInputs', inputs: { ...inputs, ...patch } });
  const { upfrontCostPct, ongoingCostPct, financingCostPct, facilityFeePct, hedgeCostPct, lossChoice } = inputs;
  const lossInput = inputs.lossInputPct ?? (m?.actualLossRate ?? 0) * 100;
  const setUpfrontCostPct = (v: number) => set({ upfrontCostPct: v });
  const setOngoingCostPct = (v: number) => set({ ongoingCostPct: v });
  const setFinancingCostPct = (v: number) => set({ financingCostPct: v });
  const setFacilityFeePct = (v: number) => set({ facilityFeePct: v });
  const setHedgeCostPct = (v: number) => set({ hedgeCostPct: v });
  const setLossChoice = (v: UeModelAssumptions['lossChoice']) => set({ lossChoice: v });
  const setLossInput = (v: number) => set({ lossInputPct: v });

  if (!response || !m) return <Alert tone="info">Run analysis to see the UE Model.</Alert>;
  const ue = response.ue as Record<string, unknown>;

  // Every value below ports app.py's _render_ue_model_ai_tab arithmetic one
  // for one (see the comments there for the economic reasoning).
  const lossRate =
    lossChoice === 'Actual' ? m.actualLossRate
    : lossChoice === 'Proxy' ? m.lossRateProxy
    : lossInput / 100;

  const principal = m.principal;
  const maturedOwed = m.maturedOwed;
  const maturedPrincipal = m.maturedPrincipal;

  const lossesDollar =
    lossRate != null && typeof maturedOwed === 'number' && Number.isFinite(maturedOwed)
      ? lossRate * maturedOwed
      : null;
  const lossesPctOfGbv =
    lossesDollar != null && principal != null && principal ? lossesDollar / principal : null;

  const termM = num(ue['Average Expected Term']) != null ? (num(ue['Average Expected Term'])! / 30.4375) : null;
  const impliedInterest =
    num(ue['Average Interest %']) != null && termM ? (num(ue['Average Interest %'])! / termM) * 12 * 2 : null;
  const impliedMonthly = impliedInterest != null ? impliedInterest / 12 : null;

  const nMatured = m.nMatured;
  const maturedTermM = m.maturedTermM;
  const avgPrincipalMatured = nMatured ? (maturedPrincipal ?? 0) / nMatured : null;
  const avgFeeMatured = nMatured ? (m.maturedOriginationIncome ?? 0) / nMatured : null;
  const avgGbvMatured =
    nMatured && maturedOwed != null ? maturedOwed / nMatured : null;
  const nPeriods =
    typeof maturedTermM === 'number' && Number.isFinite(maturedTermM) && maturedTermM > 0
      ? Math.round(maturedTermM) + 3
      : null;

  let grossIrr = null;
  if (
    nPeriods != null &&
    avgPrincipalMatured != null &&
    Number.isFinite(avgPrincipalMatured) &&
    avgGbvMatured != null &&
    Number.isFinite(avgGbvMatured) &&
    lossRate != null &&
    maturedOwed != null
  ) {
    const cf0 = -avgPrincipalMatured + avgFeeMatured! - upfrontCostPct * avgPrincipalMatured;
    const monthlyCf =
      (avgGbvMatured / nPeriods) * (1 - lossRate) -
      (ongoingCostPct * avgPrincipalMatured) / nPeriods;
    const cashflows = [cf0, ...Array<number>(nPeriods).fill(monthlyCf)];
    const monthlyIrr = irr(cashflows);
    if (monthlyIrr != null) grossIrr = Math.pow(1 + monthlyIrr, 12) - 1;
  }

  const costOfFinancePct = financingCostPct + facilityFeePct + hedgeCostPct;
  const netIrr = grossIrr != null ? grossIrr - costOfFinancePct : null;

  const variableCostsPct = upfrontCostPct + ongoingCostPct;
  const costOfFinanceOverTerm =
    typeof maturedTermM === 'number' && Number.isFinite(maturedTermM)
      ? costOfFinancePct * (maturedTermM / 12)
      : null;

  const incomeNetLosses =
    typeof m.maturedRevenue === 'number' && typeof m.maturedOriginationIncome === 'number' && lossesDollar != null
      ? m.maturedRevenue + m.maturedOriginationIncome - lossesDollar
      : null;
  const incomeNetLossesMargin =
    incomeNetLosses != null && maturedPrincipal != null && maturedPrincipal
      ? incomeNetLosses / maturedPrincipal
      : null;
  const variableCostsDollar = maturedPrincipal != null ? variableCostsPct * maturedPrincipal : null;
  const productContribution =
    incomeNetLosses != null && variableCostsDollar != null ? incomeNetLosses - variableCostsDollar : null;
  const productMargin =
    productContribution != null && maturedPrincipal != null && maturedPrincipal
      ? productContribution / maturedPrincipal
      : null;

  const fmtPct = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? 'n/a' : `${(v * 100).toFixed(2)}%`);
  const fmtPct1 = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? 'n/a' : `${(v * 100).toFixed(1)}%`);
  const costInput = (
    label: string,
    value: number,
    onChange: (v: number) => void,
  ) => (
    <label>
      <div className="sd-muted">{label}</div>
      <Input
        type="number"
        min={0}
        step={0.1}
        value={value * 100}
        onChange={(e) => onChange(Math.max(0, Number(e.target.value) || 0) / 100)}
      />
    </label>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <h2 className="sd-h2">UE Model</h2>
      <div className="sd-card">
        <span className="sd-muted" style={{ display: 'inline-block', background: '#f5d90a', color: '#16312E', padding: '2px 8px', borderRadius: 4, fontSize: '.8rem', fontWeight: 600 }}>
          Manual inputs (not in a loan tape)
        </span>
        <p className="sd-muted" style={{ marginTop: 8 }}>
          The master workbook needs these to complete the Unit Economics picture, but they're cost and
          financing assumptions, not something a loan tape records - enter cost % against matured-loan
          GBV (matching the Derived section below), financing % as per annum; 0 if not applicable.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 10, marginTop: 8 }}>
          {costInput('Upfront variable costs (% of matured-loan GBV)', upfrontCostPct, setUpfrontCostPct)}
          {costInput('Ongoing variable costs (% of matured-loan GBV)', ongoingCostPct, setOngoingCostPct)}
          {costInput('Financing cost (% p.a.)', financingCostPct, setFinancingCostPct)}
          {costInput('Facility fee - annualised (% p.a.)', facilityFeePct, setFacilityFeePct)}
          {costInput('Hedge cost - annualised (% p.a.)', hedgeCostPct, setHedgeCostPct)}
        </div>
      </div>

      <div className="sd-card">
        <span className="sd-muted" style={{ display: 'inline-block', marginBottom: 8 }}><strong>Loss Rate</strong></span>
        <label>
          <div className="sd-muted">Loss Rate used below</div>
          <Select value={lossChoice} onChange={(e) => setLossChoice(e.target.value as typeof lossChoice)}>
            <option value="Actual">Actual Loss Rate</option>
            <option value="Proxy">Proxy Loss Rate (1-PvD)</option>
            <option value="Input">Input</option>
          </Select>
        </label>
        {lossChoice === 'Input' && (
          <div className="sd-field" style={{ maxWidth: 260, marginTop: 10 }}>
            <span>Loss Rate (%)</span>
            <Input type="number" min={0} step={0.1} value={lossInput} onChange={(e) => setLossInput(Math.max(0, Number(e.target.value) || 0))} />
          </div>
        )}
      </div>

      <div className="sd-grid sd-grid--2">
        <div className="sd-card">
          <div className="sd-muted">Gross IRR</div>
          <div className="sd-metric__value" style={{ color: grossIrr != null && grossIrr < 0 ? '#d62728' : '#2ca02c' }}>
            {fmtPct1(grossIrr)}
          </div>
          <p className="sd-muted" style={{ marginTop: 6 }}>
            The annualized return on an average matured loan before cost of finance: what you pay out to
            fund it versus what you collect back over its life, net of losses and variable costs.
          </p>
        </div>
        <div className="sd-card">
          <div className="sd-muted">Net IRR</div>
          <div className="sd-metric__value" style={{ color: netIrr != null && netIrr < 0 ? '#d62728' : '#2ca02c' }}>
            {fmtPct1(netIrr)}
          </div>
          <p className="sd-muted" style={{ marginTop: 6 }}>
            Gross IRR minus Cost of Finance (Financing cost + Facility fee + Hedge cost, % p.a.).
          </p>
        </div>
      </div>

      <SnapshotTable
        rows={[
          ['Product Interest Rate', fmtPct(num(ue['Average Interest %'])), 'Total expected interest income as a % of total principal disbursed, across all loans - the stated interest on the product, not the estimated APR below.'],
          ['Origination Income %', fmtPct(num(ue['Average Fee %'])), 'Total expected fee income as a % of total principal disbursed, across all loans.'],
          ['Estimated APR', fmtPct(impliedInterest), "Internal estimated APR: Product Interest Rate divided by the average weighted monthly term, times 12, times 2 - a flat-rate annualization doubled to approximate the equivalent declining-balance rate (not the Summary tab's EAR, which is solved independently via amortization)."],
          ['Implied Monthly Interest %', fmtPct(impliedMonthly), 'Implied interest divided by 12.'],
          ['Loss %', fmtPct(lossesPctOfGbv), "Losses in dollars divided by total Principal Value disbursed across the whole book - a literal % of GBV (not the Summary tab's Loss Rate, which divides by matured owed instead and is scoped to a much smaller base)."],
          ['Term', na(termM) ?? `${termM!.toFixed(1)} months`, 'Principal-weighted average term.'],
        ]}
      />
      <p className="sd-muted">
        {nMatured.toLocaleString()} of {m.nTotal.toLocaleString()} loans have matured (Reached T+3? = True),{' '}
        {m.maturedGbvPct != null ? `${(m.maturedGbvPct * 100).toFixed(1)}% of GBV` : 'n/a'}.
      </p>

      <SnapshotTable
        rows={[
          ['Income (net losses)', fmtPct(incomeNetLossesMargin), 'Matured-only Revenue + Origination Income - Losses, as a % of matured-loan GBV.'],
          ['Variable costs (% of GBV)', fmtPct(variableCostsPct), 'Sum of the two cost %s entered above.'],
          ['Product contribution', fmtPct(productMargin), "Income (net losses) - Variable costs, as a % of matured-loan GBV - the workbook's 'simple' (non-time-value-of-money) unit economics basis, computed only on the subset of the book that's actually matured."],
          ['Cost of Finance (% p.a.)', fmtPct(costOfFinancePct), 'Financing cost + Facility fee + Hedge cost, as entered (per annum).'],
          ['Cost of Finance (over loan life)', fmtPct(costOfFinanceOverTerm), "Cost of Finance (% p.a.) x matured loans' own weighted term / 12 - not the full-book Term (m), which covers a different, longer-duration population. Grossed up so it's comparable to Product contribution."],
        ]}
      />
    </div>
  );
}

function UnitEconomicsTab() {
  const { state } = useAppStore();
  return state.modelKey === 'rental' ? <RentalUnitEconomicsTab /> : <LendingUnitEconomicsTab />;
}


function LtvTab() {
  const { state } = useAppStore();
  return state.modelKey === 'rental' ? <RentalLtvTab /> : <LendingLtvTab />;
}

function ChurnTab() {
  const { state } = useAppStore();
  const churn = state.rentalResponse?.churn;
  if (!churn) return <Alert tone="info">Run analysis to see churn metrics.</Alert>;
  const all = churn as Record<string, unknown>;
  const rest = Object.fromEntries(Object.entries(all).filter(([k]) => k !== 'residual_curve'));
  return (
    <>
      <KeyValueCards data={rest} />
      <MetricTable title="Churn summary" data={rest} />
    </>
  );
}

const CHART_COLORS = ['#1f77b4', '#cb4b3a', '#2a9d5c', '#8855cc', '#d4a017', '#357a8c'];
let chartCardIdCounter = 1;

/** Long-format aggregated points -> per-series {x, y} arrays, the shape
 * recharts' <Scatter> wants (unlike Line/Bar/Area, it can't consume the
 * pivoted-wide table - each series needs its own point array). */
function scatterSeries(
  points: Array<Record<string, unknown>>,
  xCol: string,
  yCol: string,
  colorCol: string | null,
  seriesName: string,
): Array<{ x: unknown; y: unknown }> {
  const filtered = colorCol ? points.filter((p) => String(p[colorCol]) === seriesName) : points;
  return filtered.map((p) => ({ x: p[xCol], y: p[yCol] }));
}

/** Optional axis labels/tick+tooltip formatting for renderChart - omitted
 * entirely leaves the plain, unlabeled axes the Custom Visualizations tab
 * has always used (every existing call site keeps working unchanged). */
interface ChartRenderOpts {
  xLabel?: string;
  yLabel?: string;
  xTickFormat?: (v: unknown) => string;
  yTickFormat?: (v: number) => string;
  tooltipFormat?: (v: unknown, name: string) => [ReactNode, string];
}

function renderChart(
  card: ChartCardSpec,
  wideData: Array<Record<string, unknown>>,
  points: Array<Record<string, unknown>>,
  seriesNames: string[],
  opts: ChartRenderOpts = {},
) {
  const showLegend = seriesNames.length > 1;
  const xAxisLabel = opts.xLabel
    ? { value: opts.xLabel, position: 'insideBottom' as const, offset: -5, fontSize: 11 }
    : undefined;
  const yAxisLabel = opts.yLabel
    ? { value: opts.yLabel, angle: -90, position: 'insideLeft' as const, fontSize: 11 }
    : undefined;
  const margin = { top: 8, right: 16, bottom: opts.xLabel ? 22 : 0, left: opts.yLabel ? 12 : -8 };
  if (card.kind === 'Scatter') {
    return (
      <ScatterChart margin={margin}>
        <CartesianGrid stroke="#e7ecee" />
        <XAxis
          dataKey="x"
          type="category"
          name={card.xCol}
          tick={{ fontSize: 11, fill: '#666' }}
          tickFormatter={opts.xTickFormat}
          label={xAxisLabel}
        />
        <YAxis
          dataKey="y"
          name={card.yCol}
          tick={{ fontSize: 11, fill: '#666' }}
          tickFormatter={opts.yTickFormat}
          label={yAxisLabel}
        />
        <Tooltip formatter={opts.tooltipFormat} />
        {showLegend && <Legend />}
        {seriesNames.map((s, i) => (
          <Scatter
            key={s}
            name={s}
            data={scatterSeries(points, card.xCol, card.yCol, card.colorCol, s)}
            fill={CHART_COLORS[i % CHART_COLORS.length]}
          />
        ))}
      </ScatterChart>
    );
  }
  const axes = (
    <>
      <CartesianGrid stroke="#e7ecee" vertical={false} />
      <XAxis
        dataKey={card.xCol}
        tick={{ fontSize: 11, fill: '#666' }}
        tickFormatter={opts.xTickFormat}
        label={xAxisLabel}
      />
      <YAxis tick={{ fontSize: 11, fill: '#666' }} tickFormatter={opts.yTickFormat} label={yAxisLabel} />
      <Tooltip formatter={opts.tooltipFormat} />
      {showLegend && <Legend />}
    </>
  );
  if (card.kind === 'Bar') {
    return (
      <BarChart data={wideData} margin={margin}>
        {axes}
        {seriesNames.map((s, i) => <Bar key={s} dataKey={s} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
      </BarChart>
    );
  }
  if (card.kind === 'Area') {
    return (
      <AreaChart data={wideData} margin={margin}>
        {axes}
        {seriesNames.map((s, i) => (
          <Area
            key={s}
            dataKey={s}
            stroke={CHART_COLORS[i % CHART_COLORS.length]}
            fill={CHART_COLORS[i % CHART_COLORS.length]}
            fillOpacity={0.35}
          />
        ))}
      </AreaChart>
    );
  }
  return (
    <LineChart data={wideData} margin={margin}>
      {axes}
      {seriesNames.map((s, i) => (
        <Line key={s} dataKey={s} stroke={CHART_COLORS[i % CHART_COLORS.length]} strokeWidth={2} dot={{ r: 3 }} />
      ))}
    </LineChart>
  );
}

function defaultsForSource(rows: Array<Record<string, unknown>>) {
  const all = allColumns(rows);
  const numeric = numericColumns(rows);
  const yOptions = numeric.length > 0 ? numeric : all;
  return { xCol: pickDefaultX(all), yCol: pickDefaultY(yOptions) };
}

function ChartCardEditor({ card }: { card: ChartCardSpec }) {
  const { state, dispatch } = useAppStore();
  const sources = getChartDataSources(state);
  const sourceNames = Object.keys(sources);
  const rows = sources[card.source] ?? [];
  const patch = (p: Partial<ChartCardSpec>) => dispatch({ type: 'updateChartCard', id: card.id, patch: p });
  const remove = () => dispatch({ type: 'removeChartCard', id: card.id });

  if (rows.length === 0) {
    return (
      <div className="sd-card">
        <Alert tone="info">This data source has no rows to plot.</Alert>
        <Button variant="ghost" onClick={remove}>Remove chart</Button>
      </div>
    );
  }

  const all = allColumns(rows);
  const numeric = numericColumns(rows);
  const categorical = categoricalColumns(rows, numeric);
  const yOptions = numeric.length > 0 ? numeric : all;

  const points = aggregateChartData(rows, card.xCol, card.yCol, card.colorCol);
  const { data: wideData, seriesNames } = pivotWide(points, card.xCol, card.yCol, card.colorCol);

  return (
    <div className="sd-card">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10, marginBottom: 12 }}>
        {sourceNames.length > 1 && (
          <label>
            <div className="sd-muted">Data source</div>
            <Select
              value={card.source}
              onChange={(e) => {
                const src = e.target.value;
                patch({ source: src, colorCol: null, ...defaultsForSource(sources[src] ?? []) });
              }}
            >
              {sourceNames.map((s) => <option key={s} value={s}>{s}</option>)}
            </Select>
          </label>
        )}
        <label>
          <div className="sd-muted">Chart type</div>
          <Select value={card.kind} onChange={(e) => patch({ kind: e.target.value as ChartCardSpec['kind'] })}>
            {(['Line', 'Bar', 'Scatter', 'Area'] as const).map((k) => <option key={k} value={k}>{k}</option>)}
          </Select>
        </label>
        <label>
          <div className="sd-muted">X axis</div>
          <Select
            value={card.xCol}
            onChange={(e) => patch({ xCol: e.target.value, colorCol: card.colorCol === e.target.value ? null : card.colorCol })}
          >
            {all.map((c) => <option key={c} value={c}>{c}</option>)}
          </Select>
        </label>
        <label>
          <div className="sd-muted">Y axis</div>
          <Select value={card.yCol} onChange={(e) => patch({ yCol: e.target.value })}>
            {yOptions.map((c) => <option key={c} value={c}>{c}</option>)}
          </Select>
        </label>
        <label>
          <div className="sd-muted">Group / color by (optional)</div>
          <Select value={card.colorCol ?? ''} onChange={(e) => patch({ colorCol: e.target.value || null })}>
            <option value="">(none)</option>
            {categorical.filter((c) => c !== card.xCol).map((c) => <option key={c} value={c}>{c}</option>)}
          </Select>
        </label>
      </div>
      {wideData.length === 0 ? (
        <Alert tone="info">No rows with data for the selected columns.</Alert>
      ) : (
        <ResponsiveContainer width="100%" height={320}>
          {renderChart(card, wideData, points, seriesNames)}
        </ResponsiveContainer>
      )}
      <div className="sd-actions" style={{ marginTop: 8 }}>
        <Button variant="ghost" onClick={remove}>Remove chart</Button>
      </div>
    </div>
  );
}

function VizTab() {
  const { state, dispatch } = useAppStore();
  const sources = getChartDataSources(state);
  const sourceNames = Object.keys(sources);
  const hasData = sourceNames.some((s) => sources[s].length > 0);

  const addChart = () => {
    const source = sourceNames.find((s) => sources[s].length > 0) ?? sourceNames[0];
    dispatch({
      type: 'addChartCard',
      card: { id: chartCardIdCounter++, source, kind: 'Bar', colorCol: null, ...defaultsForSource(sources[source] ?? []) },
    });
  };

  if (!hasData) {
    return <Alert tone="info">Run analysis to build charts from the engine's cohort/asset output.</Alert>;
  }

  return (
    <div>
      <div className="sd-actions" style={{ marginBottom: 12 }}>
        <h2 className="sd-h2" style={{ margin: 0 }}>Custom Visualizations</h2>
        <Button variant="secondary" onClick={addChart}>+ Add chart</Button>
      </div>
      <p className="sd-muted">
        Build your own charts from the engine's cohort/asset-level output. Add as many as you like -
        every chart here is also embedded in the Excel export.
      </p>
      {state.customCharts.length === 0 ? (
        <Alert tone="info">No charts yet — add one to plot cohort/asset metrics.</Alert>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {state.customCharts.map((card) => <ChartCardEditor key={card.id} card={card} />)}
        </div>
      )}
    </div>
  );
}

function AiTab() {
  const { state, askAiQuestion } = useAppStore();
  const [question, setQuestion] = useState('');

  const send = async () => {
    const q = question.trim();
    if (!q || state.chatRunning) return;
    setQuestion('');
    await askAiQuestion(q);
  };

  return (
    <div className="sd-card">
      <h2 className="sd-h2">Ask AI</h2>
      <Alert tone="info">
        Ask questions about this portfolio using its summary statistics —{' '}
        {state.analysis.rows.toLocaleString()} rows across {state.analysis.cohorts} cohorts. No
        raw loan-level data leaves the app for this feature.
      </Alert>
      {state.chatHistory.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, margin: '12px 0' }}>
          {state.chatHistory.map((m, i) => (
            <div key={i} className={m.role === 'user' ? 'sd-chat-msg sd-chat-msg--user' : 'sd-chat-msg'}>
              <strong>{m.role === 'user' ? 'You' : 'AI'}:</strong> {m.content}
            </div>
          ))}
        </div>
      )}
      {state.chatError ? <Alert tone="error">{state.chatError}</Alert> : null}
      <div className="sd-actions" style={{ marginTop: 12 }}>
        <Input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void send();
          }}
          placeholder="Ask about loss rates, cohorts, LTV..."
          disabled={state.chatRunning}
          style={{ flex: 1 }}
        />
        <Button onClick={() => void send()} disabled={state.chatRunning || !question.trim()}>
          {state.chatRunning ? 'Asking…' : 'Send'}
        </Button>
      </div>
    </div>
  );
}

export function AnalysisSession({
  onNewSession,
  onEditMapping,
}: {
  onNewSession: () => void;
  onEditMapping: () => void;
}) {
  const { state, exportWorkbook, saveSession, shareHtml } = useAppStore();
  const isRental = state.modelKey === 'rental';
  const isRevolving = state.modelKey === 'revolving';
  const [exporting, setExporting] = useState(false);
  const [label, setLabel] = useState(state.dealLabel ?? '');

  const onExport = async () => {
    setExporting(true);
    try {
      await exportWorkbook();
    } finally {
      setExporting(false);
    }
  };

  const lendingTabs = [
    { id: 'summary', label: 'Summary' },
    { id: 'checks', label: 'Data Checks' },
    { id: 'cohorts', label: 'Cohorts' },
    { id: 'ltv', label: 'LTV Analysis' },
    { id: 'ue', label: 'Unit Economics' },
    { id: 'viz', label: 'Custom Visualizations' },
    { id: 'ai', label: 'Ask AI' },
  ];
  const rentalTabs = [
    { id: 'summary', label: 'Summary' },
    { id: 'checks', label: 'Data Checks' },
    { id: 'asset', label: 'Asset View' },
    { id: 'ue', label: 'Unit Economics' },
    { id: 'churn', label: 'Churn Analysis' },
    { id: 'ltv', label: 'LTV Analysis' },
    { id: 'ts', label: 'TS Covenants' },
    { id: 'general', label: 'General Analysis' },
    { id: 'viz', label: 'Custom Visualizations' },
    { id: 'ai', label: 'Ask AI' },
  ];
  const revolvingTabs = [
    { id: 'revSummary', label: 'Summary' },
    { id: 'checks', label: 'Data Checks' },
    { id: 'revPerformance', label: 'Performance' },
    { id: 'revLtv', label: 'LTV Calculator' },
    { id: 'revUe', label: 'Unit Economics' },
    { id: 'viz', label: 'Custom Viz' },
    { id: 'ai', label: 'Ask AI' },
  ];
  const tabs = isRevolving ? revolvingTabs : isRental ? rentalTabs : lendingTabs;
  const [tab, setTab] = useState<string>(tabs[0].id);

  const error = state.analysisError;

  return (
    <div>
      {error ? <Alert tone="error">{error}</Alert> : null}

      {!state.analysisResponse && !state.rentalResponse && !state.revolvingResponse ? (
        <Alert tone="info">
          Run analysis to load live engine results for the {isRevolving ? 'revolving' : isRental ? 'rental' : 'lending'} model.
        </Alert>
      ) : null}

      <Tabs items={tabs} value={tab} onChange={setTab} />

      {tab === 'summary' && <SummaryTab />}
      {tab === 'checks' && <DataChecksTab />}
      {tab === 'cohorts' && (isRental ? <RentalCohortsTab /> : <LendingCohortsTab />)}
      {tab === 'asset' && <AssetViewTab />}
      {tab === 'ue' && <UnitEconomicsTab />}
      {tab === 'churn' && <ChurnTab />}
      {tab === 'ltv' && <LtvTab />}
      {tab === 'ts' && <TsCovenantsTab />}
      {tab === 'general' && <GeneralAnalysisTab />}
      {tab === 'viz' && <VizTab />}
      {tab === 'ai' && <AiTab />}
      {tab === 'revSummary' && <RevolvingSummaryTab />}
      {tab === 'revPerformance' && <RevolvingPerformanceTab />}
      {tab === 'revLtv' && <RevolvingLtvTab />}
      {tab === 'revUe' && <RevolvingUnitEconomicsTab />}

      <div className="sd-actions" style={{ marginTop: 24, flexWrap: 'wrap' }}>
        <Button variant="ghost" onClick={onEditMapping}>
          ← Edit column mapping
        </Button>
        <Button variant="ghost" onClick={onNewSession}>
          + New analysis
        </Button>
        <Button variant="secondary" onClick={onExport} disabled={exporting}>
          {exporting ? 'Exporting…' : 'Export (Excel)'}
        </Button>
        <Input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Deal label"
          style={{ width: 160 }}
          aria-label="Deal label"
        />
        <Button
          variant="secondary"
          onClick={() => void saveSession(label.trim() || state.file?.name || 'Untitled deal')}
          disabled={state.saveRunning}
        >
          {state.saveRunning ? 'Saving…' : 'Save'}
        </Button>
        <Button variant="secondary" onClick={() => void shareHtml()} disabled={state.shareRunning} title="Download a read-only HTML report anyone can open in a browser">
          {state.shareRunning ? 'Sharing…' : 'Share'}
        </Button>
        {state.savedAt ? <span className="sd-muted">Saved.</span> : null}
      </div>
      {state.saveError ? <Alert tone="error">{state.saveError}</Alert> : null}
      {state.shareError ? <Alert tone="error">{state.shareError}</Alert> : null}
      {state.shareUrl ? (
        <div className="sd-actions" style={{ marginTop: 8 }}>
          <Input readOnly value={state.shareUrl} style={{ flex: 1 }} onFocus={(e) => e.target.select()} />
          <Button
            variant="ghost"
            onClick={() => void navigator.clipboard.writeText(state.shareUrl ?? '')}
          >
            Copy link
          </Button>
        </div>
      ) : null}
    </div>
  );
}
