import { useRef, useState } from 'react';
import { Button, Alert, Table, type TableColumn } from '../components/ui';

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
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function str(v: unknown): string {
  return v === null || v === undefined ? '' : String(v);
}
const fnum = (n: number | null) =>
  n === null ? '—' : n.toLocaleString(undefined, { maximumFractionDigits: 2 });

function MetricTable({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (entries.length === 0) return <Alert tone="info">No summary fields in this file.</Alert>;
  const cols: TableColumn<[string, unknown]>[] = [
    { key: 'k', header: 'Metric', render: (r) => r[0] },
    { key: 'v', header: 'Value', align: 'right', render: (r) => (num(r[1]) != null ? fnum(num(r[1])) : str(r[1])) },
  ];
  return <Table columns={cols} rows={entries} />;
}

function RecordTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  if (rows.length === 0) return <Alert tone="info">No cohort rows in this file.</Alert>;
  const cols: TableColumn<Record<string, unknown>>[] = Object.keys(rows[0]).map((key) => ({
    key,
    header: key,
    align: 'right' as const,
    render: (r) => {
      const n = num(r[key]);
      return <span className={n != null ? 'sd-mono' : undefined}>{n != null ? fnum(n) : str(r[key])}</span>;
    },
  }));
  return <Table columns={cols} rows={rows} />;
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
      <div>
        <div className="sd-actions" style={{ justifyContent: 'space-between' }}>
          <h2 className="sd-h2">{fileName}</h2>
          <Button
            variant="secondary"
            onClick={() => {
              setData(null);
              setFileName(null);
            }}
          >
            Upload a different file
          </Button>
        </div>

        <h2 className="sd-h2">Summary</h2>
        <MetricTable data={data.summary} />

        <h2 className="sd-h2">Cohorts ({data.cohorts.length})</h2>
        <RecordTable rows={data.cohorts} />
      </div>
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
