import { useEffect, useState } from 'react';
import { useAppStore } from '../state/AppStore';
import { Button, Alert, HelpTip, Select, Table, type TableColumn } from '../components/ui';
import { suggestMapping, deriveColumn, fetchFieldAliases, type DerivedColumn } from '../api/engine';
import { TEMPLATE_FIELDS, RENTAL_TEMPLATE_FIELDS, REVOLVING_TEMPLATE_FIELDS } from '../data/template';
import type { ModelKey } from '../state/types';

interface MappingProps {
  onNext: () => void;
  modelKey: ModelKey;
}

function norm(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, '');
}

// Worked examples for the derive-column box. Deliberately phrased as plain
// English rather than formulas: the point of the feature is that you describe
// what you want. Each was checked end to end - AI draft through to computed
// values - not just eyeballed.
//
// Chosen to span what derive_columns.py actually supports: plain arithmetic,
// a row-wise condition (IF), an aggregate (SUM over the whole column), a
// conditional aggregate (SUMIFS), and a function the grammar has to provide
// because it cannot be faked (MAX - the model used to approximate it with an
// algebraic identity that was silently wrong whenever the second argument
// was larger).
//
// The last two cover dates (EDATE, which adds MONTHS rather than pretending a
// month is 30 days) and IFERROR, the companion to division by zero yielding a
// blank rather than failing the column.
const DERIVE_EXAMPLES = [
  'expected fee is fees accrued plus penalties accrued',
  'flag loans over 90 days past due as 1, otherwise 0',
  'each loan’s share of total principal issued',
  'total principal of loans more than 90 dpd',
  'the larger of fees accrued and penalties accrued',
  'expected completion date is origination date plus the original term in months',
  'recovery rate, but show zero when nothing was written off',
  'total due is total when expected completion date is before the latest disbursement date, otherwise 0',
];

const DERIVE_HELP =
  'If a template field isn’t in your file, describe how to compute it in plain English - e.g. ' +
  '“Expected Interest = Gross Loan Value − Principal Value”. AI writes a safe formula; you review it, ' +
  'then add it as a column to map below. Name the columns as they appear in your file. Understands ' +
  'arithmetic, comparisons, IF, IFERROR, MIN, MAX, ABS, ROUND per loan; SUM, COUNT, AVERAGE, SUMIF(S), ' +
  'COUNTIF(S), AVERAGEIF(S) across the whole column (one number repeated on every row); and EDATE, ' +
  'EOMONTH, YEAR, MONTH, DAY, DATEDIF, TODAY for dates. Subtracting two dates gives a span in days.';

/** Suggest a source column for a template field from the real file's columns
 * by normalised name matching (e.g. map "Expected Interest" to "expected
 * interest" or "EXP Interest"). Returns the best match or undefined. */
function suggestSource(fieldLabel: string, columns: string[], aliases: string[] = []): string | undefined {
  const target = norm(fieldLabel);
  for (const col of columns) {
    if (norm(col) === target) return col;
  }
  // Known header aliases from config.toml [field_aliases], e.g. "maturity date".
  for (const alias of aliases) {
    const col = columns.find((c) => norm(c) === norm(alias));
    if (col) return col;
  }
  // tolerate small punctuation/word-order differences for common ones
  const compact = target.replace(/ /g, '');
  for (const col of columns) {
    const c = norm(col).replace(/ /g, '');
    if (c === compact) return col;
  }
  return undefined;
}

export function ColumnMappingScreen({ onNext, modelKey }: MappingProps) {
  const { state, dispatch } = useAppStore();
  const columns = state.file?.columns ?? [];
  const mapped = state.mappings;
  const [aiRunning, setAiRunning] = useState(false);
  const [aiNote, setAiNote] = useState<string | null>(null);

  // "Ask AI to derive a column" - a natural-language request becomes a safe
  // formula over the file's own columns (AI writes the formula, the backend
  // computes it deterministically; see web_api/derive_columns.py).
  const derived = state.derivedColumns;
  const [deriveRequest, setDeriveRequest] = useState('');
  const [deriveBusy, setDeriveBusy] = useState(false);
  const [aiDerive, setAiDerive] = useState<DerivedColumn | null>(null);
  const [deriveNote, setDeriveNote] = useState<string | null>(null);

  const isRental = modelKey === 'rental';
  const isRevolving = modelKey === 'revolving';
  const fields = isRevolving ? REVOLVING_TEMPLATE_FIELDS : isRental ? RENTAL_TEMPLATE_FIELDS : TEMPLATE_FIELDS;

  // Instant, offline baseline: normalised-name matching against the real
  // file's columns (see suggestSource above). Always applied first so the
  // form fills in even without an AI call.
  const [aliases, setAliases] = useState<Record<string, string[]>>({});
  const applyHeuristic = (known = aliases) => {
    let count = 0;
    for (const field of fields) {
      // Revolving source headers are the field keys themselves (e.g. BAL_NEW).
      const src =
        suggestSource(field.label, columns, known[field.key]) ??
        (isRevolving ? suggestSource(field.key, columns) : undefined);
      if (src) {
        dispatch({ type: 'setMapping', fieldKey: field.key, sourceColumn: src });
        count += 1;
      }
    }
    return count;
  };

  // Open at the top (the previous screen may have been scrolled down) and
  // pre-fill every field a name or known alias matches - no AI call needed.
  useEffect(() => {
    window.scrollTo(0, 0);
    let live = true;
    void fetchFieldAliases(modelKey)
      .catch(() => ({}))
      .then((known) => {
        if (!live) return;
        setAliases(known);
        if (!mapped.some((m) => m.sourceColumn)) applyHeuristic(known);
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelKey]);

  const applySuggestions = async () => {
    const heuristicCount = applyHeuristic();
    setAiRunning(true);
    setAiNote(null);
    try {
      const result = await suggestMapping({
        modelKey,
        columns,
        context: state.analysisContext,
        documents: state.contextDocuments,
      });
      const aiMapped = Object.entries(result.mapping).filter(([, v]) => v);
      for (const [fieldKey, sourceColumn] of aiMapped) {
        dispatch({ type: 'setMapping', fieldKey, sourceColumn: sourceColumn as string });
      }
      setAiNote(
        aiMapped.length > 0
          ? `AI suggested ${aiMapped.length} field(s) (name-matching filled ${heuristicCount}).`
          : `AI mapping unavailable (no DEEPINFRA_API_KEY configured) — used name-matching only (${heuristicCount} field(s)).`,
      );
    } catch (err) {
      setAiNote(
        `AI mapping request failed (used name-matching only, ${heuristicCount} field(s)): ` +
          (err instanceof Error ? err.message : 'unknown error'),
      );
    } finally {
      setAiRunning(false);
    }
  };

  const setSource = (fieldKey: string, sourceColumn: string) =>
    dispatch({ type: 'setMapping', fieldKey, sourceColumn: sourceColumn || null });

  const askAiDerive = async () => {
    const req = deriveRequest.trim();
    if (!req) return;
    setDeriveBusy(true);
    setDeriveNote(null);
    setAiDerive(null);
    try {
      const r = await deriveColumn({ modelKey, columns, request: req });
      if (r.name && r.formula) {
        setAiDerive({ name: r.name, formula: r.formula });
        setDeriveNote('AI drafted a formula — review it (or edit below), then “Add column”.');
      } else if (r.configured === false) {
        setDeriveNote('AI isn’t configured on the server — type the formula manually below.');
      } else {
        // A key is present, so this is the model declining, not a missing
        // key. Saying "not configured" here sent people to check server
        // config when the real fix was to name the columns explicitly.
        setDeriveNote(
          'AI couldn’t turn that into a formula — try naming the exact columns, e.g. “Expected Fee = FEES_ACCRUED + PENALTIES_ACCRUED”, or type it manually below.',
        );
      }
    } catch (err) {
      setDeriveNote(err instanceof Error ? `Couldn’t ask AI: ${err.message}` : 'Couldn’t ask AI.');
    } finally {
      setDeriveBusy(false);
    }
  };

  const addDerived = () => {
    const name = aiDerive?.name?.trim();
    const formula = aiDerive?.formula?.trim();
    if (!name || !formula) {
      setDeriveNote('Give the column a name and a formula first.');
      return;
    }
    if (derived.some((d) => d.name === name) || columns.some((c) => c === name)) {
      setDeriveNote('That column name already exists — pick a different name.');
      return;
    }
    dispatch({ type: 'setDerivedColumns', columns: [...derived, { name, formula }] });
    setAiDerive(null);
    setDeriveRequest('');
    setDeriveNote(`Added "${name}". It now appears as a source column you can map below.`);
  };

  const removeDerived = (name: string) =>
    dispatch({ type: 'setDerivedColumns', columns: derived.filter((d) => d.name !== name) });

  const previewCols: TableColumn<string[]>[] = columns.map((c, i) => ({
    key: c,
    header: c,
    mono: true,
    render: (row) => row[i] ?? '',
  }));

  const allSources = [...columns, ...derived.map((d) => d.name)];

  const mappedCount = mapped.filter((m) => m.sourceColumn).length;
  const requiredMissing = fields.filter(
    (f) => f.required && !mapped.some((m) => m.fieldKey === f.key && m.sourceColumn),
  ).length;

  const rows = fields.map((field) => {
    const current = mapped.find((m) => m.fieldKey === field.key)?.sourceColumn ?? '';
    return { ...field, current };
  });

  const cols: TableColumn<(typeof rows)[number]>[] = [
    {
      key: 'label',
      header: 'Template field',
      render: (r) => (
        <div>
          <strong>{r.label}</strong>
          {r.required ? <span className="sd-req"> required</span> : null}
          <div className="sd-muted">{r.description}</div>
        </div>
      ),
    },
    {
      key: 'current',
      header: 'Source column',
      render: (r) => (
        <Select
          value={r.current}
          onChange={(e) => setSource(r.key, e.target.value)}
          aria-label={`Map ${r.label}`}
        >
          <option value="">— none —</option>
          {allSources.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
      ),
    },
  ];

  return (
    <div>
      <Alert tone="info">
        Template fields drive the LTV / Unit Economics calculations. Required fields must be
        mapped before analysis. Use <strong>Auto-fill with AI</strong> to suggest mappings from
        column names.
      </Alert>
      <div className="sd-actions" style={{ margin: '12px 0' }}>
        <Button variant="secondary" onClick={() => void applySuggestions()} disabled={aiRunning}>
          {aiRunning ? 'Asking AI…' : 'Auto-fill with AI'}
        </Button>
        <span className="sd-muted">
          {mappedCount}/{fields.length} fields mapped
        </span>
      </div>
      {aiNote ? <Alert tone="info">{aiNote}</Alert> : null}

      <div
        className="sd-derive"
        style={{
          margin: '14px 0',
          padding: '12px',
          border: '1px solid #e5e7eb',
          borderRadius: 8,
        }}
      >
        <h3 className="sd-h2" style={{ marginTop: 0 }}>
          Ask AI to derive a column <HelpTip text={DERIVE_HELP} />
        </h3>
        <div className="sd-field">
          <input
            className="sd-input"
            value={deriveRequest}
            placeholder='e.g. "Expected Interest = Gross Loan Value - Principal Value"'
            onChange={(e) => setDeriveRequest(e.target.value)}
          />
        </div>
        <div className="sd-actions" style={{ marginTop: 8 }}>
          <Button
            variant="secondary"
            onClick={() => void askAiDerive()}
            disabled={deriveBusy || !deriveRequest.trim()}
          >
            {deriveBusy ? 'Asking AI…' : 'Ask AI to derive'}
          </Button>
          <Select
            value=""
            onChange={(e) => e.target.value && setDeriveRequest(e.target.value)}
            aria-label="Try an example"
            style={{ maxWidth: 320 }}
          >
            <option value="">Try an example…</option>
            {DERIVE_EXAMPLES.map((ex) => (
              <option key={ex} value={ex}>
                {ex}
              </option>
            ))}
          </Select>
        </div>

        {state.file?.preview?.length ? (
          <div style={{ marginTop: 12 }}>
            <span className="sd-muted">
              Your data (first {state.file.preview.length} rows) - use these column names in your request:
            </span>
            <div style={{ overflowX: 'auto', maxHeight: 220, marginTop: 6 }}>
              <Table columns={previewCols} rows={state.file.preview} />
            </div>
          </div>
        ) : null}

        {aiDerive ? (
          <div className="sd-derive__result" style={{ marginTop: 12 }}>
            <div className="sd-field">
              <span>Column name</span>
              <input
                className="sd-input"
                value={aiDerive.name}
                onChange={(e) => setAiDerive({ ...aiDerive, name: e.target.value })}
              />
            </div>
            <div className="sd-field">
              <span>Formula (over your file’s columns)</span>
              <input
                className="sd-input"
                value={aiDerive.formula}
                onChange={(e) => setAiDerive({ ...aiDerive, formula: e.target.value })}
              />
            </div>
            <div className="sd-actions" style={{ marginTop: 8 }}>
              <Button onClick={addDerived}>Add column</Button>
              <Button variant="ghost" onClick={() => setAiDerive(null)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : null}

        {derived.length > 0 ? (
          <div className="sd-derive__list" style={{ marginTop: 12 }}>
            <span className="sd-muted">Derived columns (now available as source options above):</span>
            <ul style={{ margin: 8, paddingLeft: 20 }}>
              {derived.map((d) => (
                <li key={d.name}>
                  <strong>{d.name}</strong> = {d.formula}{' '}
                  <button
                    type="button"
                    onClick={() => removeDerived(d.name)}
                    style={{ marginLeft: 8, cursor: 'pointer' }}
                  >
                    remove
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {deriveNote ? <Alert tone="info">{deriveNote}</Alert> : null}
      </div>

      <Table columns={cols} rows={rows} />
      <div className="sd-actions" style={{ marginTop: 16 }}>
        <Button variant="ghost" onClick={() => dispatch({ type: 'setFile', file: null })}>
          ← Back
        </Button>
        <Button onClick={onNext} disabled={requiredMissing > 0}>
          Continue to inputs ({requiredMissing} required missing)
        </Button>
      </div>
    </div>
  );
}
