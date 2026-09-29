import { useEffect, useRef, useState } from 'react';
import { useAppStore } from '../state/AppStore';
import { Button, Input, Select, Card, Alert, Table } from '../components/ui';
import { CURRENCY_CODES, CURRENCY_NAMES } from '../data/currencies';
import { getBaasDefaults, getRevolvingDefaults, getStatusValues, type StatusValuesResponse } from '../api/engine';
import type { ModelKey, RevolvingGeneralInputs } from '../state/types';

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

/** Revolving model inputs ('Roll Rate ECL' B5:B8, 'Loss Rate LTV' B9:B12 and
 * the Outputs ratio window). Starts from the server's config.toml defaults. */
function RevolvingInputsSection() {
  const { state, dispatch } = useAppStore();
  const ri = state.generalInputs.revolving;
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (ri) return;
    getRevolvingDefaults()
      .then((d) => dispatch({ type: 'setGeneralInputs', inputs: { revolving: d } }))
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load the default inputs.'));
  }, [ri, dispatch]);

  if (error) return <Alert tone="error">{error}</Alert>;
  if (!ri) return <p className="sd-muted">Loading default inputs…</p>;

  const set = (patch: Partial<RevolvingGeneralInputs>) =>
    dispatch({ type: 'setGeneralInputs', inputs: { revolving: { ...ri, ...patch } } });
  const numberField = (label: string, key: keyof RevolvingGeneralInputs, step = 'any') => (
    <div className="sd-field">
      <span>{label}</span>
      <Input
        type="number"
        step={step}
        value={String(ri[key] ?? '')}
        onChange={(e) => set({ [key]: Number(e.target.value) } as Partial<RevolvingGeneralInputs>)}
      />
    </div>
  );

  return (
    <>
      <h2 className="sd-h2">Roll-rate &amp; ECL</h2>
      <Card className="sd-grid sd-grid--2">
        <div className="sd-field">
          <span>As-of month (blank = last month in the data)</span>
          <Input type="month" value={ri.asOfMonth ?? ''} onChange={(e) => set({ asOfMonth: e.target.value || null })} />
        </div>
        <div className="sd-field">
          <span>Roll-rate averaging window</span>
          <Select
            value={ri.rollRateWindow}
            onChange={(e) => set({ rollRateWindow: e.target.value as RevolvingGeneralInputs['rollRateWindow'] })}
          >
            {['T3', 'T6', 'T12', 'ALL'].map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </Select>
        </div>
        {numberField('LGD (loss given default)', 'lgd')}
        {numberField('Outputs ratio window (months, ending at as-of)', 'ratioWindowMonths', '1')}
      </Card>
      <h2 className="sd-h2">Loss rate (feeds Outputs Loss Rate / 95th %ile)</h2>
      <Card className="sd-grid sd-grid--2">
        <div className="sd-field">
          <span>Loss-rate basis</span>
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
        {numberField('Stress percentile (0-1)', 'stressPercentile')}
        {numberField('Minimum outstanding to be in sample', 'minOutstandingInSample')}
        {numberField('Denominator lag (months)', 'denominatorLagMonths', '1')}
        {numberField('Blend weight on the lagged rate (0-1)', 'laggedBlendWeight')}
      </Card>
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

      <div className="sd-actions" style={{ marginTop: 16 }}>
        <Button variant="ghost" onClick={onNext}>
          ← Back to mapping
        </Button>
        <Button
          onClick={run}
          loading={state.analysisRunning}
          disabled={
            unmappedStatuses.length > 0 ||
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

