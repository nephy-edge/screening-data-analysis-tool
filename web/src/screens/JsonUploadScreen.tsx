import { useMemo, useRef, useState } from 'react';
import { Button, Alert, Input, Metric, Select, Table, type TableColumn } from '../components/ui';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

/** Shape produced by snowflake_lending/proc_handler.py's RUN_LENDING_ANALYSIS
 * stored procedure - a flat summary object (GeneralInputs + UE + LTV scalars)
 * plus the cohorts table, both JSON-serialised straight out of Snowflake. Not
 * the same shape as /api/analyse/lending's richer AnalysisResponse (no chart
 * data, quality checks, UE model payload) - this is a simpler, read-only view
 * for a pre-computed result rather than a live analysis run. */
interface AnalysisJson {
  summary: Record<string, unknown>;
  cohorts: Array<Record<string, unknown>>;
}

function isAnalysisJson(v: unknown): v is AnalysisJson {
  if (typeof v !== 'object' || v === null) return false;
  const obj = v as Record<string, unknown>;
  return (
    typeof obj.summary === 'object' &&
    obj.summary !== null &&
    Array.isArray(obj.cohorts)
  );
}

function num(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function str(v: unknown): string {
  return v === null || v === undefined ? '' : String(v);
}
const fnum = (n: number | null) =>
  n === null ? '—' : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
const fpct = (n: number | null) => (n === null ? '—' : `${(n * 100).toFixed(1)}%`);
const fint = (n: number | null) =>
  n === null ? '—' : n.toLocaleString(undefined, { maximumFractionDigits: 0 });
/** "2026-08-29 00:00:00" -> "2026-08-29". Snowflake serialises the general
 * inputs' dates with a midnight time component that carries no information. */
const fdate = (v: unknown) => str(v).slice(0, 10) || '—';

/** The headline UE/LTV block, read straight from `summary` rather than
 * recomputed from `cohorts`. That is deliberate: build_cohorts() aggregates
 * matured loans only (it mirrors the Excel pivot's "Reached T+3? = TRUE" page
 * filter), so the cohorts table covers a strict subset of the tape and cannot
 * reproduce any portfolio-wide figure. Only Average Loss and 95th Percentile
 * Losses are matured-only to begin with and would reconstruct exactly; every
 * other metric here would be materially wrong if derived from the cohort rows.
 * Order mirrors app.py's UE-then-LTV metric rows. */
const HEADLINE: Array<{ key: string; label: string; fmt: (v: unknown) => string; hint?: string }> = [
  { key: 'Average Loss', label: 'Average Loss', fmt: (v) => fpct(num(v)), hint: 'Matured loans only' },
  { key: 'Loss Rate Proxy (1-PvD)', label: 'Loss Rate Proxy (1-PvD)', fmt: (v) => fpct(num(v)) },
  {
    key: '95th Percentile Losses',
    label: '95th Percentile Losses',
    fmt: (v) => fpct(num(v)),
    hint: 'Across cohorts',
  },
  { key: 'Average Interest %', label: 'Average Product Interest Rate', fmt: (v) => fpct(num(v)) },
  { key: 'Average Fee %', label: 'Average Fee %', fmt: (v) => fpct(num(v)) },
  { key: 'Average Total Revenue %', label: 'Average Total Revenue %', fmt: (v) => fpct(num(v)) },
  { key: 'Sense-check Margin', label: 'Sense-check Margin', fmt: (v) => fpct(num(v)) },
  { key: 'Average Principal Amount', label: 'Average Principal Amount', fmt: (v) => fnum(num(v)) },
  {
    key: 'Average Expected Term',
    label: 'Average Expected Term',
    fmt: (v) => `${fint(num(v))} days`,
    hint: 'Principal-weighted',
  },
];

/** The general-inputs / run-provenance fields, shown as a separate strip. */
const RUN_FIELDS: Array<{ key: string; label: string; fmt: (v: unknown) => string }> = [
  { key: 'Date of extraction', label: 'Date of extraction', fmt: fdate },
  { key: 'Days after term', label: 'Days after term', fmt: (v) => fint(num(v)) },
  { key: 'Minimum loans per cohort', label: 'Min loans per cohort', fmt: (v) => fint(num(v)) },
  { key: 'rows', label: 'Loans in tape', fmt: (v) => fint(num(v)) },
  { key: 'cohorts', label: 'Cohorts', fmt: (v) => fint(num(v)) },
  { key: 'rows_dropped_missing_dates', label: 'Dropped (missing dates)', fmt: (v) => fint(num(v)) },
];

/** Per-cohort series the chart can plot, with the formatting each one needs.
 * Keys match build_cohorts()'s output columns. */
const CHART_METRICS: Array<{ key: string; label: string; pct: boolean; kind: 'line' | 'bar' }> = [
  { key: 'Loss Rate', label: 'Loss Rate', pct: true, kind: 'line' },
  { key: 'PvD Ratio', label: 'PvD Ratio', pct: true, kind: 'line' },
  { key: 'Avg Loan Fee % per cohort', label: 'Avg Loan Fee %', pct: true, kind: 'line' },
  {
    key: 'Avg Annualized Flat Interest Rate',
    label: 'Avg Annualized Flat Interest Rate',
    pct: true,
    kind: 'line',
  },
  { key: 'Avg Loan Principal Size', label: 'Avg Loan Principal Size', pct: false, kind: 'line' },
  { key: 'Weighted Avg Term', label: 'Weighted Avg Term (days)', pct: false, kind: 'line' },
  { key: 'Loan Count', label: 'Loan Count', pct: false, kind: 'bar' },
  { key: 'Total Principal', label: 'Total Principal', pct: false, kind: 'bar' },
];

function MetricTable({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (entries.length === 0) return <Alert tone="info">No summary fields in this file.</Alert>;
  const cols: TableColumn<[string, unknown]>[] = [
    { key: 'k', header: 'Metric', render: (r) => r[0] },
    {
      key: 'v',
      header: 'Value',
      align: 'right',
      render: (r) => (num(r[1]) != null ? fnum(num(r[1])) : str(r[1])),
    },
  ];
  return <Table columns={cols} rows={entries} />;
}

/** Cohort columns that hold a 0-1 ratio. Serialised as bare floats, they read
 * as "0.6" next to a column of millions - render them as percentages, the way
 * app.py's Cohorts tab formats the same columns. */
const RATIO_COLUMNS = new Set([
  'Loss Rate',
  'PvD Ratio',
  'Avg Loan Fee % per cohort',
  'Avg Annualized Flat Interest Rate',
]);

function RecordTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  if (rows.length === 0) return <Alert tone="info">No cohort rows match that filter.</Alert>;
  const cols: TableColumn<Record<string, unknown>>[] = Object.keys(rows[0]).map((key) => ({
    key,
    header: key,
    // The cohort label is the one non-numeric column; left-aligning it stops
    // the date from wrapping against the numbers that follow.
    align: key === 'Cohort' ? ('left' as const) : ('right' as const),
    render: (r) => {
      if (key === 'Cohort') return fdate(r[key]);
      const n = num(r[key]);
      if (n === null) return <span>{str(r[key])}</span>;
      return <span className="sd-mono">{RATIO_COLUMNS.has(key) ? fpct(n) : fnum(n)}</span>;
    },
  }));
  return <Table columns={cols} rows={rows} />;
}

/** One cohort series over time. Mirrors LtvTabs' chart idiom (same grid, axis
 * and tick styling) so the two screens read as one system. */
function CohortChart({ rows, metricKey }: { rows: Array<Record<string, unknown>>; metricKey: string }) {
  const metric = CHART_METRICS.find((m) => m.key === metricKey) ?? CHART_METRICS[0];
  const data = rows.map((r) => ({
    cohort: str(r['Cohort']).slice(0, 7),
    value: num(r[metric.key]),
  }));
  if (!data.some((d) => d.value !== null)) {
    return <Alert tone="info">No values for that metric in this file.</Alert>;
  }
  const tickFmt = (v: number) => (metric.pct ? `${(v * 100).toFixed(0)}%` : fint(v));
  const tipFmt = (v: unknown) => (typeof v === 'number' ? (metric.pct ? fpct(v) : fnum(v)) : str(v));
  const axes = (
    <>
      <CartesianGrid stroke="#e7ecee" vertical={false} />
      <XAxis dataKey="cohort" tick={{ fontSize: 11, fill: '#666' }} interval="preserveStartEnd" />
      <YAxis
        tickFormatter={tickFmt}
        domain={['auto', 'auto']}
        tick={{ fontSize: 11, fill: '#666' }}
        width={70}
      />
      <Tooltip formatter={tipFmt} />
    </>
  );
  return (
    <ResponsiveContainer width="100%" height={320}>
      {metric.kind === 'bar' ? (
        <BarChart data={data} margin={{ top: 8, right: 24, bottom: 8, left: 0 }}>
          {axes}
          <Bar dataKey="value" name={metric.label} fill="#2f6f8f" />
        </BarChart>
      ) : (
        <LineChart data={data} margin={{ top: 8, right: 24, bottom: 8, left: 0 }}>
          {axes}
          <Line
            type="monotone"
            dataKey="value"
            name={metric.label}
            stroke="#2f6f8f"
            dot={false}
            strokeWidth={2}
          />
        </LineChart>
      )}
    </ResponsiveContainer>
  );
}

function Result({
  data,
  fileName,
  onReset,
}: {
  data: AnalysisJson;
  fileName: string | null;
  onReset: () => void;
}) {
  const [minLoans, setMinLoans] = useState(0);
  const [metricKey, setMetricKey] = useState(CHART_METRICS[0].key);

  const cohorts = useMemo(
    () => data.cohorts.filter((r) => (num(r['Loan Count']) ?? 0) >= minLoans),
    [data.cohorts, minLoans]
  );

  const headline = HEADLINE.filter((m) => data.summary[m.key] !== undefined);
  const runFields = RUN_FIELDS.filter((m) => data.summary[m.key] !== undefined);
  /** The cohort rows cover matured loans only, so their loan counts sum to
   * less than `summary.rows` - surface that gap rather than leaving the two
   * numbers looking like a contradiction. */
  const maturedLoans = data.cohorts.reduce((acc, r) => acc + (num(r['Loan Count']) ?? 0), 0);
  const totalLoans = num(data.summary['rows']);

  return (
    <div>
      <div className="sd-actions" style={{ justifyContent: 'space-between' }}>
        <h2 className="sd-h2">{fileName}</h2>
        <Button variant="secondary" onClick={onReset}>
          Upload a different file
        </Button>
      </div>

      <Alert tone="info">
        Read-only view of a pre-computed result. Data-quality checks, the editable UE Model and the
        all-loans cohort view aren’t available here — each needs the loan-level tape, which this file
        doesn’t carry.
        {totalLoans !== null && maturedLoans > 0 ? (
          <>
            {' '}
            The cohort rows below aggregate the {fint(maturedLoans)} matured loans out of{' '}
            {fint(totalLoans)} in the tape.
          </>
        ) : null}
      </Alert>

      {runFields.length > 0 ? (
        <>
          <h2 className="sd-h2">Run</h2>
          <div className="sd-grid sd-grid--metrics">
            {runFields.map((m) => (
              <Metric key={m.key} label={m.label} value={m.fmt(data.summary[m.key])} />
            ))}
          </div>
        </>
      ) : null}

      {headline.length > 0 ? (
        <>
          <h2 className="sd-h2">Unit economics &amp; LTV</h2>
          <div className="sd-grid sd-grid--metrics">
            {headline.map((m) => (
              <Metric key={m.key} label={m.label} value={m.fmt(data.summary[m.key])} hint={m.hint} />
            ))}
          </div>
        </>
      ) : null}

      <h2 className="sd-h2">Cohorts ({cohorts.length})</h2>
      <div className="sd-actions">
        <label className="sd-muted" htmlFor="json-min-loans">
          Minimum loans per cohort
        </label>
        <Input
          id="json-min-loans"
          type="number"
          min={0}
          step={1}
          value={minLoans}
          style={{ maxWidth: '8rem' }}
          onChange={(e) => setMinLoans(Math.max(0, Number(e.target.value) || 0))}
        />
        <label className="sd-muted" htmlFor="json-chart-metric">
          Chart
        </label>
        <Select
          id="json-chart-metric"
          value={metricKey}
          style={{ maxWidth: '20rem' }}
          onChange={(e) => setMetricKey(e.target.value)}
        >
          {CHART_METRICS.map((m) => (
            <option key={m.key} value={m.key}>
              {m.label}
            </option>
          ))}
        </Select>
      </div>

      {cohorts.length > 0 ? <CohortChart rows={cohorts} metricKey={metricKey} /> : null}
      <RecordTable rows={cohorts} />

      <h2 className="sd-h2">All summary fields</h2>
      <MetricTable data={data.summary} />
    </div>
  );
}

export function JsonUploadScreen({ onBack }: { onBack: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [data, setData] = useState<AnalysisJson | null>(null);

  async function readFile(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      if (!isAnalysisJson(parsed)) {
        throw new Error(
          'That file doesn\'t look like an analysis result - expected a "summary" object and a "cohorts" array.'
        );
      }
      setData(parsed);
      setFileName(file.name);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read that file as JSON.');
      setData(null);
      setFileName(null);
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  }

  if (data) {
    return (
      <Result
        // Remount on a new file so the filter and chart selection reset with it.
        key={fileName ?? 'result'}
        data={data}
        fileName={fileName}
        onReset={() => {
          setData(null);
          setFileName(null);
        }}
      />
    );
  }

  return (
    <div>
      <div className="sd-actions" style={{ justifyContent: 'space-between' }}>
        <h2 className="sd-h2">Upload analysis JSON</h2>
        <Button variant="ghost" onClick={onBack}>
          ← Back
        </Button>
      </div>
      <p className="sd-muted">
        Upload a pre-computed analysis result (e.g. exported from the Snowflake pipeline) to view it
        here, without re-running the engine.
      </p>

      {error ? <Alert tone="error">{error}</Alert> : null}

      <div
        className={`sd-dropzone${dragOver ? ' sd-dropzone--over' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          readFile(e.dataTransfer.files);
        }}
      >
        <input
          ref={inputRef}
          type="file"
          hidden
          accept=".json,application/json"
          onChange={(e) => readFile(e.target.files)}
        />
        <div className="sd-dropzone__icon" aria-hidden />
        <p>Drag &amp; drop an analysis .json file, or</p>
        <Button variant="secondary" onClick={() => inputRef.current?.click()} disabled={busy}>
          {busy ? 'Reading file…' : 'Browse files'}
        </Button>
      </div>
    </div>
  );
}
