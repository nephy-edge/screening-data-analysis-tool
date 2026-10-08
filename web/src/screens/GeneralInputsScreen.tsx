import { useEffect, useRef, useState } from 'react';
import { useAppStore } from '../state/AppStore';
import { Button, Input, Select, Card, Alert, Table, HelpTip } from '../components/ui';
import { CURRENCY_CODES, CURRENCY_NAMES } from '../data/currencies';
import {
  getBaasDefaults,
  getRevolvingOptions,
  getRevolvingDefaults,
  getStatusValues,
  type RevolvingOptions,
  type StatusValuesResponse,
} from '../api/engine';
import type { ModelKey, RevolvingCovenant, RevolvingGeneralInputs, RevolvingScenario } from '../state/types';

interface Props {
  onNext: () => void;
  modelKey: ModelKey;
}

const LENDABLE_STATUSES = ['Open', 'Closed', 'Paid-off'];

type StatusRow = StatusValuesResponse['values'][number];

/** Rental status table (app.py's "Status mapping" editor): one row per raw
 * status value in the file, each mapped to a Lendable status. Choices already
 * made (or restored from a saved run) are kept; new values start from the
 * engine's suggestion. */
function StatusMappingSection() {
  const { state, dispatch } = useAppStore();
  const statusMap = state.generalInputs.statusMap ?? {};
  const statusMapRef = useRef(statusMap);
  statusMapRef.current = statusMap;
  const ingestId = state.file?.ingestId;
  const statusCol = state.mappings.find((m) => m.fieldKey === 'status')?.sourceColumn ?? '';
  const derivedColumns = state.derivedColumns;
  const [rows, setRows] = useState<StatusRow[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ingestId || !statusCol) return;
    let cancelled = false;
    getStatusValues({
      ingestId,
      mapping: { status: statusCol },
      derivedColumns: derivedColumns.length ? derivedColumns : undefined,
    })
      .then((res) => {
        if (cancelled) return;
        setRows(res.values);
        setTruncated(res.truncated);
        setError(null);
        // Only values in this file - a map left over from another file is dropped.
        const prev = statusMapRef.current;
        const next = Object.fromEntries(res.values.map((v) => [v.value, prev[v.value] ?? v.suggested]));
        dispatch({ type: 'setGeneralInputs', inputs: { statusMap: next } });
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not read the status column.');
      });
    return () => {
      cancelled = true;
    };
  }, [ingestId, statusCol, derivedColumns, dispatch]);

  const setStatus = (value: string, lendable: string) =>
    dispatch({ type: 'setGeneralInputs', inputs: { statusMap: { ...statusMap, [value]: lendable } } });

  return (
    <>
      <h2 className="sd-h2">Status mapping</h2>
      <Alert tone="info">
        Map each raw status value in your file to a Lendable status (active / canceled / paid-off).
      </Alert>
      {error ? <Alert tone="error">{error}</Alert> : null}
      {truncated ? (
        <Alert tone="warning">
          This column has more than {rows?.length} distinct values — check that the right column is mapped to Status.
        </Alert>
      ) : null}
      {rows ? (
        <Table<StatusRow>
          rows={rows}
          columns={[
            { key: 'value', header: 'Client status', mono: true },
            { key: 'count', header: 'Contracts', align: 'right', render: (r) => r.count.toLocaleString() },
            {
              key: 'lendable',
              header: 'Lendable status',
              render: (r) => (
                <Select value={statusMap[r.value] ?? ''} onChange={(e) => setStatus(r.value, e.target.value)}>
                  <option value="">— choose —</option>
                  {LENDABLE_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </Select>
              ),
            },
          ]}
        />
      ) : null}
    </>
  );
}

// Allowed range per numeric Revolving input; the engine does not check these itself.
const REVOLVING_RANGES: Partial<Record<keyof RevolvingGeneralInputs, { min: number; max?: number; whole?: boolean }>> = {
  lgd: { min: 0, max: 1 },
  stressPercentile: { min: 0, max: 1 },
  laggedBlendWeight: { min: 0, max: 1 },
  costOfFundsPct: { min: 0, max: 1 },
  opexPct: { min: 0, max: 1 },
  advanceHaircut: { min: 0, max: 1 },
  earlyWarningThreshold: { min: 0, max: 1 },
  minOutstandingInSample: { min: 0 },
  ratioWindowMonths: { min: 1, whole: true },
  writeoffWindowMonths: { min: 1, whole: true },
  denominatorLagMonths: { min: 0, whole: true },
  backtestMinHistoryMonths: { min: 1, whole: true },
  earlyWarningLookbackMonths: { min: 1, whole: true },
};

function revolvingFieldError(key: keyof RevolvingGeneralInputs, value: unknown): string | null {
  const range = REVOLVING_RANGES[key];
  if (!range) return null;
  const v = Number(value);
  if (!Number.isFinite(v)) return 'Enter a number.';
  if (range.whole && !Number.isInteger(v)) return 'Enter a whole number of months.';
  if (v < range.min || (range.max !== undefined && v > range.max))
    return range.max !== undefined ? `Must be between ${range.min} and ${range.max}.` : `Must be at least ${range.min}.`;
  return null;
}

/** Number of Revolving inputs currently out of range - Run is blocked while any are. */
function revolvingInvalidCount(ri: RevolvingGeneralInputs): number {
  const fields = (Object.keys(REVOLVING_RANGES) as (keyof RevolvingGeneralInputs)[]).filter((k) => revolvingFieldError(k, ri[k]));
  const scenarios = ri.scenarios.filter((sc) => !(sc.rollRateMultiplier >= 0) || !(sc.lgdMultiplier >= 0));
  return fields.length + scenarios.length;
}

/** Revolving model inputs ('Roll Rate ECL' B5:B8, 'Loss Rate LTV' B9:B12 and
 * the Outputs ratio window). Starts from the server's config.toml defaults. */
function RevolvingInputsSection() {
  const { state, dispatch } = useAppStore();
  // Inputs saved before a field existed are completed from the defaults.
  const saved = state.generalInputs.revolving;
  const ri = saved && saved.scenarios !== undefined ? saved : null;
  const [error, setError] = useState<string | null>(null);
  const [options, setOptions] = useState<RevolvingOptions | null>(null);
  const metrics = options?.covenantMetrics ?? [];

  useEffect(() => {
    if (ri) return;
    getRevolvingDefaults()
      .then((d) => dispatch({ type: 'setGeneralInputs', inputs: { revolving: { ...d, ...(saved ?? {}) } } }))
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load the default inputs.'));
  }, [ri, saved, dispatch]);

  useEffect(() => {
    getRevolvingOptions()
      .then(setOptions)
      .catch(() => setOptions(null));
  }, []);

  if (error) return <Alert tone="error">{error}</Alert>;
  if (!ri) return <p className="sd-muted">Loading default inputs…</p>;

  const set = (patch: Partial<RevolvingGeneralInputs>) =>
    dispatch({ type: 'setGeneralInputs', inputs: { revolving: { ...ri, ...patch } } });
  const setScenario = (i: number, patch: Partial<RevolvingScenario>) =>
    set({ scenarios: ri.scenarios.map((sc, j) => (j === i ? { ...sc, ...patch } : sc)) });
  const setCovenant = (i: number, patch: Partial<RevolvingCovenant>) =>
    set({ covenants: ri.covenants.map((cv, j) => (j === i ? { ...cv, ...patch } : cv)) });
  const label = (text: string, help: string) => (
    <span>
      {text}
      <HelpTip text={help} />
    </span>
  );
  const numberField = (text: string, help: string, key: keyof RevolvingGeneralInputs, step = 'any') => {
    const fieldError = revolvingFieldError(key, ri[key]);
    return (
      <div className="sd-field">
        {label(text, help)}
        <Input
          type="number"
          step={step}
          value={String(ri[key] ?? '')}
          aria-invalid={fieldError ? true : undefined}
          onChange={(e) => set({ [key]: Number(e.target.value) } as Partial<RevolvingGeneralInputs>)}
        />
        {fieldError ? <span className="sd-field__error">{fieldError}</span> : null}
      </div>
    );
  };

  return (
    <>
      <h2 className="sd-h2">Roll-rate &amp; ECL</h2>
      <Card className="sd-grid sd-grid--2">
        <div className="sd-field">
          {label('As-of month (blank = last month in the data)', 'The month the analysis is struck at. Everything uses only months up to this one, with no look-ahead. Leave blank to use the last month in the data.')}
          <Input type="month" value={ri.asOfMonth ?? ''} onChange={(e) => set({ asOfMonth: e.target.value || null })} />
        </div>
        <div className="sd-field">
          {label('Roll-rate averaging window', 'How many recent months the roll rates are averaged over (T3/T6/T12 = last 3/6/12 months, ALL = every month). The selected average drives the loss factor and the whole ECL.')}
          <Select
            value={ri.rollRateWindow}
            onChange={(e) => set({ rollRateWindow: e.target.value })}
          >
            {(options?.rollRateWindows ?? [ri.rollRateWindow]).map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </Select>
        </div>
        {numberField('LGD (loss given default)', 'Share of a defaulted balance that is lost (0-1). Multiplies the roll-rate loss factor into ECL and the loss rate. 1 = no recoveries.', 'lgd')}
        {numberField('Outputs ratio window (months, ending at as-of)', 'How many months, ending at the as-of month, the Outputs ratios average over (for example, Interest Rate (m) is interest collected divided by outstanding, both totalled over the window).', 'ratioWindowMonths', '1')}
        {numberField('Write-off averaging window (months)', 'How many months the write-off rate is averaged over, ending at the as-of month: total written off divided by total outstanding over those months. 1 uses the as-of month only, as the template does. 6 or 12 smooths out bumpy months. Feeds unit economics, early warnings and covenants.', 'writeoffWindowMonths', '1')}
      </Card>
      <h2 className="sd-h2">Loss rate (feeds Outputs Loss Rate / 95th %ile)</h2>
      <Card className="sd-grid sd-grid--2">
        <div className="sd-field">
          {label('Loss-rate basis', 'Denominator for the point-in-time loss rate. Naive divides by all principal issued to date, so it is a lower bound. Lagged divides by principal issued as at the lag below. Blended mixes the two using the blend weight.')}
          <Select
            value={ri.lossRateBasis}
            onChange={(e) => set({ lossRateBasis: e.target.value as RevolvingGeneralInputs['lossRateBasis'] })}
          >
            {['Naive', 'Blended', 'Lagged'].map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </Select>
        </div>
        {numberField('Stress percentile (0-1)', 'Percentile of the in-sample monthly loss rates used as the stressed loss rate (Outputs 95th %ile). That rate sets the maximum advance rate.', 'stressPercentile')}
        {numberField('Minimum outstanding to be in sample', 'A month only counts in the loss-rate distribution when principal outstanding is at least this much. It keeps small, early months out of the sample.', 'minOutstandingInSample')}
        {numberField('Denominator lag (months)', 'For the Lagged loss rate: divide by cumulative principal issued this many months earlier, so recent lending that has not had time to default does not dilute the rate.', 'denominatorLagMonths', '1')}
        {numberField('Blend weight on the lagged rate (0-1)', 'For the Blended loss rate: weight on the Lagged rate. The rest goes on the Naive rate (0.5 = an equal mix).', 'laggedBlendWeight')}
      </Card>

      <h2 className="sd-h2">LGD &amp; unit economics</h2>
      <Card className="sd-grid sd-grid--2">
        <div className="sd-field">
          {label('LGD basis', 'Where LGD comes from. Input uses the LGD value above. Recoveries derives it as 100% less recoveries as a share of principal written off to date, which needs the recoveries column mapped.')}
          <Select
            value={ri.lgdBasis}
            onChange={(e) => set({ lgdBasis: e.target.value as RevolvingGeneralInputs['lgdBasis'] })}
          >
            <option value="Input">Input (LGD above)</option>
            <option value="Recoveries">Derived from the recoveries column</option>
          </Select>
        </div>
        {numberField('Cost of funds (% p.a., 0-1)', 'Annual funding cost as a share of outstanding (0.13 = 13%). Deducted in unit economics to get net yield.', 'costOfFundsPct')}
        {numberField('Operating costs (% p.a., 0-1)', 'Annual operating cost as a share of outstanding (0.02 = 2%). Deducted in unit economics to get net yield.', 'opexPct')}
        {numberField('Advance-rate haircut (0-1)', 'Extra cushion taken off the advance rate: the maximum advance rate is 100% less the stress loss rate, less this haircut.', 'advanceHaircut')}
      </Card>

      <h2 className="sd-h2">Backtest &amp; early warning</h2>
      <Card className="sd-grid sd-grid--2">
        {numberField('Backtest: months of history before the first test', 'The backtest forecasts losses using only data available at each month and compares them with what actually happened. This many months of history are needed before the first test month.', 'backtestMinHistoryMonths', '1')}
        {numberField('Early warning: lookback (months)', 'Each early-warning metric at the as-of month is compared with its average over this many preceding months.', 'earlyWarningLookbackMonths', '1')}
        {numberField('Early warning: flag when worse by (0-1, relative)', 'Flag a metric when it is this much worse than its lookback average, in relative terms (0.2 = 20% worse).', 'earlyWarningThreshold')}
      </Card>

      <h2 className="sd-h2">Stress scenarios</h2>
      <Table<RevolvingScenario & { i: number }>
        rows={ri.scenarios.map((sc, i) => ({ ...sc, i }))}
        columns={[
          {
            key: 'name',
            header: <>Scenario<HelpTip text="Name shown on the Backtest & Stress and Portfolio Summary tabs." /></>,
            render: (r) => <Input value={r.name} onChange={(e) => setScenario(r.i, { name: e.target.value })} />,
          },
          {
            key: 'roll',
            header: <>Roll-rate multiplier<HelpTip text="Scales the selected roll rates in this scenario (1.25 = 25% more balance rolls to the next bucket), capped at the configured stress cap (100% by default)." /></>,
            render: (r) => (
              <Input
                type="number"
                step="0.05"
                value={String(r.rollRateMultiplier)}
                onChange={(e) => setScenario(r.i, { rollRateMultiplier: Number(e.target.value) })}
              />
            ),
          },
          {
            key: 'lgd',
            header: <>LGD multiplier<HelpTip text="Scales LGD in this scenario (1.1 = 10% higher), capped at 100%." /></>,
            render: (r) => (
              <Input
                type="number"
                step="0.05"
                value={String(r.lgdMultiplier)}
                onChange={(e) => setScenario(r.i, { lgdMultiplier: Number(e.target.value) })}
              />
            ),
          },
          {
            key: 'rm',
            header: '',
            render: (r) => (
              <Button variant="ghost" onClick={() => set({ scenarios: ri.scenarios.filter((_, j) => j !== r.i) })}>
                Remove
              </Button>
            ),
          },
        ]}
      />
      <Button
        variant="secondary"
        onClick={() =>
          set({ scenarios: [...ri.scenarios, { name: 'New scenario', rollRateMultiplier: 1, lgdMultiplier: 1 }] })
        }
      >
        + Add scenario
      </Button>

      <h2 className="sd-h2">Covenant tests</h2>
      <Alert tone="info">No covenants by default — add this deal&apos;s facility terms with + Add covenant.</Alert>
      <Table<RevolvingCovenant & { i: number }>
        rows={ri.covenants.map((cv, i) => ({ ...cv, i }))}
        columns={[
          {
            key: 'metric',
            header: <>Metric<HelpTip text="The figure the covenant tests, as of the as-of month." /></>,
            render: (r) => (
              <Select value={r.metric} onChange={(e) => setCovenant(r.i, { metric: e.target.value })}>
                {(metrics.length ? metrics : [{ key: r.metric, label: r.metric }]).map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.label}
                  </option>
                ))}
              </Select>
            ),
          },
          {
            key: 'op',
            header: <>Test<HelpTip text="Whether the metric must stay at or below the limit (at most) or at or above it (at least)." /></>,
            render: (r) => (
              <Select
                value={r.operator}
                onChange={(e) => setCovenant(r.i, { operator: e.target.value as RevolvingCovenant['operator'] })}
              >
                <option value="<=">at most</option>
                <option value=">=">at least</option>
              </Select>
            ),
          },
          {
            key: 'limit',
            header: <>Limit (0-1 for %)<HelpTip text="The facility limit. Enter percentages as decimals (0.15 = 15%). A breach shows on the Facility & Covenants and Portfolio Summary tabs." /></>,
            render: (r) => (
              <Input
                type="number"
                step="any"
                value={String(r.limit)}
                onChange={(e) => setCovenant(r.i, { limit: Number(e.target.value) })}
              />
            ),
          },
          {
            key: 'rm',
            header: '',
            render: (r) => (
              <Button variant="ghost" onClick={() => set({ covenants: ri.covenants.filter((_, j) => j !== r.i) })}>
                Remove
              </Button>
            ),
          },
        ]}
      />
      <Button
        variant="secondary"
        disabled={!metrics.length}
        onClick={() => set({ covenants: [...ri.covenants, { metric: metrics[0].key, operator: '<=', limit: 0 }] })}
      >
        + Add covenant
      </Button>
    </>
  );
}

/** BaaS: the valuation date (month 0 of the cash flows). Every other
 * assumption is per market, in the uploaded table. */
function BaasInputsSection() {
  const { state, dispatch } = useAppStore();
  const bi = state.generalInputs.baas;
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (bi) return;
    getBaasDefaults()
      .then((d) => dispatch({ type: 'setGeneralInputs', inputs: { baas: d } }))
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load the default inputs.'));
  }, [bi, dispatch]);

  if (error) return <Alert tone="error">{error}</Alert>;
  if (!bi) return <p className="sd-muted">Loading default inputs…</p>;

  return (
    <>
      <h2 className="sd-h2">Valuation</h2>
      <Card className="sd-grid sd-grid--2">
        <div className="sd-field">
          <span>Valuation date (month 0 of the cash flows)</span>
          <Input
            type="date"
            value={bi.valuationDate}
            onChange={(e) =>
              e.target.value && dispatch({ type: 'setGeneralInputs', inputs: { baas: { valuationDate: e.target.value } } })
            }
          />
        </div>
      </Card>
    </>
  );
}

export function GeneralInputsScreen({ onNext, modelKey }: Props) {
  const { state, dispatch, analyse } = useAppStore();
  const gi = state.generalInputs;
  const isRental = modelKey === 'rental';
  const isRevolving = modelKey === 'revolving';
  const isBaas = modelKey === 'baas';
  const unmappedStatuses = isRental
    ? Object.entries(gi.statusMap ?? {})
        .filter(([, v]) => !v)
        .map(([k]) => k)
    : [];

  const revolvingInvalid = isRevolving && gi.revolving?.scenarios ? revolvingInvalidCount(gi.revolving) : 0;

  const run = async () => {
    // Stay here on failure - the error Alert below shows why.
    if (await analyse()) onNext();
  };

  return (
    <div>
      <Alert tone="info">
        {isBaas
          ? 'Inputs for the Battery as a Service model. Market assumptions (including facility terms) come from the uploaded table; results are in USD.'
          : isRevolving
          ? 'Inputs for the Revolving Lending roll-rate / ECL model. Figures stay in the source currency.'
          : isRental
            ? 'Conventions used by the Rental & Subscription engine (useful asset life, churn stress, and funding/direct cost assumptions).'
            : 'Conventions used by the Lending LTV / Unit Economics engine.'}
      </Alert>

      {isBaas ? (
        <BaasInputsSection />
      ) : isRevolving ? (
        <RevolvingInputsSection />
      ) : (
        <>
          <h2 className="sd-h2">Currency</h2>
          <div className="sd-field" style={{ maxWidth: 280 }}>
            <span>Portfolio currency</span>
            <Select
              value={gi.currency}
              onChange={(e) => dispatch({ type: 'setGeneralInputs', inputs: { currency: e.target.value } })}
            >
              {CURRENCY_CODES.map((code) => (
                <option key={code} value={code}>
                  {code} — {CURRENCY_NAMES[code]}
                </option>
              ))}
            </Select>
          </div>

          <h2 className="sd-h2">General inputs</h2>
          <Card className="sd-grid sd-grid--2">
            <div className="sd-field">
              <span>Extraction date</span>
              <Input
                type="date"
                value={gi.extractionDate}
                onChange={(e) => dispatch({ type: 'setGeneralInputs', inputs: { extractionDate: e.target.value } })}
              />
            </div>
            {isRental ? (
              <>
                <div className="sd-field">
                  <span>Days after term</span>
                  <Input
                    type="number"
                    value={gi.daysAfterTerm}
                    onChange={(e) =>
                      dispatch({ type: 'setGeneralInputs', inputs: { daysAfterTerm: Number(e.target.value) } })
                    }
                  />
                </div>
                <div className="sd-field">
                  <span>Min contracts per cohort</span>
                  <Input
                    type="number"
                    value={gi.minLoansPerCohort}
                    onChange={(e) =>
                      dispatch({ type: 'setGeneralInputs', inputs: { minLoansPerCohort: Number(e.target.value) } })
                    }
                  />
                </div>
                <div className="sd-field">
                  <span>Months since default</span>
                  <Input type="number" defaultValue={3} disabled />
                </div>
              </>
            ) : (
              <>
                <div className="sd-field">
                  <span>Days after term (maturity window)</span>
                  <Input
                    type="number"
                    value={gi.daysAfterTerm}
                    onChange={(e) =>
                      dispatch({ type: 'setGeneralInputs', inputs: { daysAfterTerm: Number(e.target.value) } })
                    }
                  />
                </div>
                <div className="sd-field">
                  <span>Min loans per cohort</span>
                  <Input
                    type="number"
                    value={gi.minLoansPerCohort}
                    onChange={(e) =>
                      dispatch({ type: 'setGeneralInputs', inputs: { minLoansPerCohort: Number(e.target.value) } })
                    }
                  />
                </div>
                <div className="sd-field">
                  <span>LTV cohort from (optional)</span>
                  <Input
                    type="month"
                    value={gi.ltvCohortFrom ?? ''}
                    onChange={(e) => dispatch({ type: 'setGeneralInputs', inputs: { ltvCohortFrom: e.target.value } })}
                  />
                </div>
                <div className="sd-field">
                  <span>LTV cohort to (optional)</span>
                  <Input
                    type="month"
                    value={gi.ltvCohortTo ?? ''}
                    onChange={(e) => dispatch({ type: 'setGeneralInputs', inputs: { ltvCohortTo: e.target.value } })}
                  />
                </div>
              </>
            )}
          </Card>
        </>
      )}

      {isRental ? <StatusMappingSection /> : null}
      {unmappedStatuses.length ? (
        <Alert tone="warning">
          {unmappedStatuses.length} status value(s) aren&apos;t mapped yet: {unmappedStatuses.join(', ')}. Choose a
          Lendable status for each before running.
        </Alert>
      ) : null}

      {revolvingInvalid ? (
        <Alert tone="warning">
          {revolvingInvalid} input(s) are out of range - fix the fields marked in red before running.
        </Alert>
      ) : null}

      <div className="sd-actions" style={{ marginTop: 16 }}>
        <Button variant="ghost" onClick={onNext}>
          ← Back to mapping
        </Button>
        <Button
          onClick={run}
          loading={state.analysisRunning}
          disabled={
            unmappedStatuses.length > 0 ||
            revolvingInvalid > 0 ||
            (isRevolving && !state.generalInputs.revolving) ||
            (isBaas && !state.generalInputs.baas)
          }
        >
          {state.analysisRunning ? 'Running analysis…' : 'Run analysis →'}
        </Button>
      </div>
      {state.analysisError ? <Alert tone="error">{state.analysisError}</Alert> : null}
    </div>
  );
}

