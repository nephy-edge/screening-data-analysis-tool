import { useEffect, useState } from 'react';
import { useAppStore } from '../state/AppStore';
import { Alert, Input, Metric, Select, Table, type TableColumn } from '../components/ui';
import { computeLtvCalculator, type LtvCalculatorResponse } from '../api/engine';
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

function num(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** app.py's html_tables.fmt(value, "{:.1%}") - percent, one decimal, n/a. */
const fmtPct = (v: unknown): string => {
  const n = num(v);
  return n === null ? 'n/a' : `${(n * 100).toFixed(1)}%`;
};
const fmt = (v: unknown, digits = 0): string => {
  const n = num(v);
  return n === null ? 'n/a' : n.toFixed(digits);
};

/** True when a "YYYY-MM..." cohort falls inside the LTV cohort range (either
 * end open when null/absent). "YYYY-MM" strings compare correctly as text. */
export function inLtvCohortRange(cohort: string, range?: { from: string | null; to: string | null }) {
  const month = cohort.slice(0, 7);
  return (!range?.from || month >= range.from) && (!range?.to || month <= range.to);
}

/** Rows for the "Loss per Cohort" chart - mirrors AnalysisSession's toChartRows,
 * limited to the LTV cohort range so the chart shows what the 95th %ile uses. */
function lossChartRows(
  records: Array<Record<string, unknown>> | undefined,
  range?: { from: string | null; to: string | null },
) {
  return (records ?? [])
    .map((r) => ({
      cohort: String(r['Cohort'] ?? '').slice(0, 7),
      lossRate: num(r['Loss Rate']),
    }))
    .filter((r) => inLtvCohortRange(r.cohort, range));
}

/** A small Metric | Value table with a muted help line under each label -
 * mirrors app.py's _render_snapshot_table (rows of label, value, tooltip). */
function SnapshotTable({ rows }: { rows: Array<{ label: string; value: string; help?: string }> }) {
  const cols: TableColumn<{ label: string; value: string; help?: string }>[] = [
    {
      key: 'label',
      header: 'Metric',
      render: (r) => (
        <span>
          {r.label}
          {r.help ? <div className="sd-muted">{r.help}</div> : null}
        </span>
      ),
    },
    { key: 'value', header: 'Value', align: 'right' },
  ];
  return <Table columns={cols} rows={rows} />;
}

function FxChart({ response, country }: { response: LtvCalculatorResponse; country: string }) {
  const fx = response.fxCountryData;
  const selected = response.result?.historical_fx_deval ?? null;
  const tenor = response.greenTermBucket;
  if (!fx) return null;
  const data = fx.tenors.map((t, i) => ({ tenor: t, deval: fx.values[i] }));
  return (
    <div className="sd-card">
      <h2 className="sd-h2">{`99th %ile Historical FX Devaluation - ${country}`}</h2>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data} margin={{ top: 8, right: 24, bottom: 8, left: 0 }}>
          <CartesianGrid stroke="#e7ecee" vertical={false} />
          <XAxis
            dataKey="tenor"
            type="category"
            tick={{ fontSize: 11, fill: '#666' }}
            label={{ value: 'Tenor (months)', position: 'insideBottom', offset: -5, fontSize: 11 }}
          />
          <YAxis
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
            domain={['auto', 'auto']}
            tick={{ fontSize: 11, fill: '#666' }}
            label={{ value: '99th %ile FX Devaluation', angle: -90, position: 'insideLeft', fontSize: 11 }}
          />
          <Tooltip formatter={(v) => (typeof v === 'number' ? `${(v * 100).toFixed(2)}%` : v)} />
          <Line dataKey="deval" name="Historical FX Devaluation" stroke="#1f77b4" strokeWidth={2} dot={{ r: 3 }} />
          {selected !== null && tenor !== null ? (
            <ReferenceLine
              y={selected}
              stroke="#d62728"
              strokeDasharray="4 4"
              label={{ value: `Selected tenor (${tenor}m): ${(selected * 100).toFixed(2)}%`, position: 'insideTopRight', fill: '#d62728', fontSize: 11 }}
            />
          ) : null}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function LossPerCohortChart({
  response,
  chartRecords,
  range,
}: {
  response: LtvCalculatorResponse;
  chartRecords?: Array<Record<string, unknown>>;
  range?: { from: string | null; to: string | null };
}) {
  const rows = lossChartRows(chartRecords, range);
  const green = response.greenLoss;
  if (rows.length === 0) return null;
  return (
    <div className="sd-card">
      <h2 className="sd-h2">Loss per Cohort</h2>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={rows} margin={{ top: 8, right: 24, bottom: 8, left: 0 }}>
          <CartesianGrid stroke="#e7ecee" vertical={false} />
          <XAxis
            dataKey="cohort"
            tick={{ fontSize: 11, fill: '#666' }}
            label={{ value: 'Cohort', position: 'insideBottom', offset: -5, fontSize: 11 }}
          />
          <YAxis
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
            tick={{ fontSize: 11, fill: '#666' }}
            label={{ value: 'Loss Rate', angle: -90, position: 'insideLeft', fontSize: 11 }}
          />
          <Tooltip formatter={(v) => (typeof v === 'number' ? `${(v * 100).toFixed(2)}%` : v)} />
          <Line dataKey="lossRate" name="Loss Rate" stroke="#1f77b4" strokeWidth={2} dot={{ r: 3 }} />
          {green !== null ? (
            <ReferenceLine
              y={green}
              stroke="#d62728"
              strokeDasharray="4 4"
              label={{ value: `95th %ile: ${(green * 100).toFixed(2)}%`, position: 'insideTopRight', fill: '#d62728', fontSize: 11 }}
            />
          ) : null}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Lending "LTV Analysis" tab - a faithful port of app.py's tabs[3]
 * "LTV Calculator": interactive inputs -> the Receivables waterfall via the
 * shared template_analysis.ltv_calculator engine -> the suggested-LTV matrix,
 * FX backup table + chart, and credit-stress backup table + Loss per Cohort
 * chart. Green inputs (avg interest, term, 95th %ile loss) come from the
 * already-computed analysis. */
export function LendingLtvTab() {
  const { state, dispatch } = useAppStore();
  const ue = state.analysisResponse?.ue as Record<string, unknown> | undefined;
  const ltv = state.analysisResponse?.ltv as Record<string, unknown> | undefined;
  // ltvInterest / ltvLoss = the interest and 95th %ile loss limited to the
  // General Inputs LTV cohort range. A key that is present but null means the
  // range held no data (n/a, not the whole book); only sessions saved before
  // these keys existed fall back to the whole-book figures.
  const resp = state.analysisResponse;
  const range = resp?.ltvCohortRange;
  const rangeNote =
    range && (range.from || range.to)
      ? ` - cohorts ${range.from || 'first'} to ${range.to || 'latest'} only (General Inputs)`
      : '';
  const greenInterest =
    resp && resp.ltvInterest !== undefined ? num(resp.ltvInterest) : ue ? num(ue['Average Interest %']) : null;
  const greenTermDays = ltv ? num(ltv['Average Term']) : null;
  // In the store, not useState: leaving the tab unmounts it, which used to
  // reset every dropdown to its default.
  const { dataSource, country, macroFx, segmentation, hedgeRate, rolledHedge, includeUnfinished } = state.ltvInputs;
  // The checkbox swaps in the loss counted over every loan in each cohort,
  // finished or not; off, it's the template's matured-only (T+3) loss.
  const allLoansAvailable = resp?.ltvLossAllLoans !== undefined;
  const useAllLoans = includeUnfinished && allLoansAvailable;
  const greenLoss = useAllLoans
    ? num(resp!.ltvLossAllLoans)
    : resp && resp.ltvLoss !== undefined
      ? num(resp.ltvLoss)
      : ltv
        ? num(ltv['95th Percentile Losses'])
        : null;
  const lossBasisNote = useAllLoans ? ', counting every loan in each cohort (finished or not)' : ', matured loans only';
  const set = (inputs: Partial<typeof state.ltvInputs>) => dispatch({ type: 'setLtvInputs', inputs });
  const setDataSource = (dataSource: string) => set({ dataSource });
  const setCountry = (country: string) => set({ country });
  const setMacroFx = (macroFx: string) => set({ macroFx });
  const setSegmentation = (segmentation: string) => set({ segmentation });
  const setHedgeRate = (hedgeRate: string) => set({ hedgeRate });
  const setRolledHedge = (rolledHedge: string) => set({ rolledHedge });
  const setIncludeUnfinished = (includeUnfinished: boolean) => set({ includeUnfinished });
  const [response, setResponse] = useState<LtvCalculatorResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    computeLtvCalculator({
      dataSource,
      country,
      macroFxRisk: macroFx,
      segmentation,
      hedgeRate: parseFloat(hedgeRate) || 0,
      rolledHedge,
      averageInterest: greenInterest,
      termDays: greenTermDays,
      lossRate: greenLoss,
    })
      .then((r) => {
        if (!cancelled) setResponse(r);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not run the LTV calculator.');
      });
    return () => {
      cancelled = true;
    };
  }, [dataSource, country, macroFx, segmentation, hedgeRate, rolledHedge, greenInterest, greenTermDays, greenLoss]);

  const result = response?.ok ? response.result : null;
  const options = response?.options;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <h2 className="sd-h2">LTV Calculator</h2>

      <p className="sd-h2" style={{ fontSize: '0.95rem', fontWeight: 700 }}>
        Inputs
      </p>
      {options ? (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            gap: 12,
          }}
        >
          <div className="sd-field">
            <span>Data Input Source</span>
            <Select aria-label="Data Input Source" value={dataSource} onChange={(e) => setDataSource(e.target.value)}>
              {options.dataInputSources.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </Select>
          </div>
          <div className="sd-field">
            <span>Alt Lender Country</span>
            <Select aria-label="Alt Lender Country" value={country} onChange={(e) => setCountry(e.target.value)}>
              {options.countries.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </Select>
          </div>
          <div className="sd-field">
            <span>Macro FX Risk Rating</span>
            <Select aria-label="Macro FX Risk Rating" value={macroFx} onChange={(e) => setMacroFx(e.target.value)}>
              {options.fxRiskRatings.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </Select>
          </div>
          <div className="sd-field">
            <span>Segmentation</span>
            <Select aria-label="Segmentation" value={segmentation} onChange={(e) => setSegmentation(e.target.value)}>
              {options.segmentations.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </Select>
          </div>
          <div className="sd-field">
            <span>Hedge Rate (0 if unhedged, else % OTM as a decimal)</span>
            <Input
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={hedgeRate}
              onChange={(e) => setHedgeRate(e.target.value)}
            />
          </div>
          <div className="sd-field">
            <span>Rolled hedge?</span>
            <Select aria-label="Rolled hedge?" value={rolledHedge} onChange={(e) => setRolledHedge(e.target.value)}>
              {options.hedgeTypes.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </Select>
          </div>
          <div className="sd-field">
            <span>Loss rate basis</span>
            <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={!!includeUnfinished}
                disabled={!allLoansAvailable}
                onChange={(e) => setIncludeUnfinished(e.target.checked)}
              />
              Include unfinished loans
            </label>
            <span className="sd-muted">
              {allLoansAvailable
                ? 'Counts every loan in each cohort, finished or not (Salmon-sheet basis). Recent cohorts will look like heavy losses unless the LTV cohort range excludes them.'
                : 'Re-run the analysis to enable this option.'}
            </span>
          </div>
        </div>
      ) : null}

      {error ? <Alert tone="error">{error}</Alert> : null}

      {response
        ? response.lossRateFallbackNotes.map((note, i) => <Alert key={`fb${i}`} tone="info">{note}</Alert>)
        : null}
      {response && !response.ok ? <Alert tone="warning">{response.warning}</Alert> : null}
      {result
        ? result.notes.map((note, i) => <Alert key={`rn${i}`} tone="info">{note}</Alert>)
        : null}

      {response && result ? (
        <>
          <hr style={{ border: 'none', borderTop: '1px solid var(--sd-border, #e7ecee)' }} />
          <h3 className="sd-h2" style={{ fontSize: '1rem' }}>Outputs - Suggested LTV</h3>
          <LtvMatrix result={result} />
          <p className="sd-muted">
            FX High is the stressed, headline scenario. Advance on Principal = Advance on GBV grossed up by
            (1 + weighted avg. gross interest). Both Advance on GBV and Advance on Principal limits must be
            complied with (source: Receivables sheet, cell C44).
          </p>

          <hr style={{ border: 'none', borderTop: '1px solid var(--sd-border, #e7ecee)' }} />
          <h3 className="sd-h2" style={{ fontSize: '1rem' }}>Back-Up</h3>

          <h3 className="sd-h2" style={{ fontSize: '1rem', marginTop: 4 }}>FX Backup</h3>
          <SnapshotTable
            rows={[
              {
                label: 'Term',
                value: response.greenTermBucket != null ? `${response.greenTermBucket} months` : 'n/a',
                help: "Weighted average receivable term, snapped to the nearest tenor bucket the workbook's dropdown allows.",
              },
              { label: 'Historical Devaluation', value: fmtPct(result.historical_fx_deval), help: '99th-percentile historical FX devaluation for the selected country and tenor.' },
              { label: 'Utilized Historical FX Deval', value: fmtPct(result.utilized_fx_deval), help: 'Historical FX devaluation actually used - the 6-month figure when the tenor is 6 months or less.' },
              { label: 'Stress FX Devaluation', value: fmtPct(result.stress_fx_deval), help: `Max(historical, utilized) FX devaluation, stressed by the FX stress factor (${result.stress_fx_factor}x).` },
              { label: 'Base FX Deval', value: fmtPct(result.base_fx_deval), help: 'Stress FX devaluation, capped at the hedge rate when the deal is hedged.' },
              { label: 'FX Slippage Stress', value: fmtPct(result.fx_slippage), help: 'FX basis-risk slippage for the selected country.' },
              { label: 'Selected FX Deval (High)', value: fmtPct(result.selected_fx_deval_high), help: 'Base FX deval + slippage + high-end roll risk.' },
              { label: 'Selected FX Deval (Low)', value: fmtPct(result.selected_fx_deval_low), help: 'Base FX deval + slippage + low-end roll risk.' },
            ]}
          />
          <FxChart response={response} country={country} />

          <h3 className="sd-h2" style={{ fontSize: '1rem', marginTop: 12 }}>Credit Stress Backup</h3>
          <SnapshotTable
            rows={[
              { label: 'Product Interest Rate', value: fmtPct(response.greenInterest), help: `Weighted average gross interest rate, pulled from this analysis${rangeNote}.` },
              {
                label: '95th %ile Loss',
                value: fmtPct(response.greenLoss),
                help: `95th percentile loss rate, pulled from this analysis${rangeNote}${lossBasisNote} - or, when that's n/a, the Min Sector Loss Rate used as a fallback (see the note above).`,
              },
              { label: 'Stress Loss', value: fmtPct(result.stress_loss_rate), help: `Loss rate multiplied by the credit stress factor (${result.credit_stress_factor}x).` },
              { label: 'Min Sector Loss Rate', value: fmtPct(result.minimum_stress_loss), help: 'Sector-minimum stress loss floor for the selected segmentation and data source.' },
              { label: 'Selected Stress Loss', value: fmtPct(result.selected_stress_loss), help: 'Max(stress loss rate, minimum stress loss floor).' },
            ]}
          />
          <LossPerCohortChart
            response={response}
            chartRecords={useAllLoans ? resp?.cohortLossesAllLoans : state.analysisResponse?.chart}
            range={range}
          />
        </>
      ) : null}
    </div>
  );
}

export function LtvMatrix({ result }: { result: NonNullable<LtvCalculatorResponse['result']> }) {
  const cols: TableColumn<{ label: string; principal: string; gbv: string; big?: boolean }>[] = [
    { key: 'label', header: '', render: (r) => (r.big ? <strong>{r.label}</strong> : r.label) },
    { key: 'principal', header: 'Advance on Principal', align: 'right', render: (r) => (r.big ? <strong>{r.principal}</strong> : r.principal) },
    { key: 'gbv', header: 'Advance on GBV', align: 'right', render: (r) => (r.big ? <strong>{r.gbv}</strong> : r.gbv) },
  ];
  // big_row=0 (FX High) is the stressed headline scenario - Streamlit's
  // _render_matrix_table emphasises it.
  const rows = [
    { label: 'FX High', principal: fmtPct(result.ltv_high), gbv: fmtPct(result.ltgbv_high), big: true },
    { label: 'No FX', principal: fmtPct(result.ltv_no_fx), gbv: fmtPct(result.ltgbv_no_fx) },
    { label: 'FX Low', principal: fmtPct(result.ltv_low), gbv: fmtPct(result.ltgbv_low) },
  ];
  return <Table columns={cols} rows={rows} />;
}

/** Rental "LTV Analysis" tab - mirrors app.py's tabs[5]: two columns of
 * Asset / Cash Flow (MRR) metrics plus the Asset-value LTV waterfall. */
export function RentalLtvTab() {
  const { state } = useAppStore();
  const ltv = (state.rentalResponse?.ltv ?? {}) as Record<string, unknown>;

  const assetCol = [
    { label: 'Average Useful Life (m)', value: fmt(ltv['avg_useful_life_m'], 1), hint: 'Principal-weighted average useful asset life, in months.' },
    { label: 'Defaulted contracts > 3mo', value: fmt(ltv['n_defaulted_gt_3m'], 0), hint: 'Number of contracts in default for more than 3 months.' },
    { label: 'Defaulted last 12m', value: fmt(ltv['n_defaulted_last_12m'], 0), hint: 'Number of contracts that defaulted in the last 12 months.' },
    { label: '% recovered', value: fmtPct(ltv['pct_recovered']), hint: 'Share of defaulted value that has been recovered.' },
    { label: 'Recovery Rate (12m)', value: fmtPct(ltv['pct_recovered_last_12m']), hint: 'Share of defaulted value recovered over the last 12 months.' },
    { label: 'Recoverability (95th %ile)', value: fmtPct(ltv['recoverability_pctile_95']), hint: '95th-percentile recovery value on defaulted assets.' },
    { label: 'Loss (non-recoverability)', value: fmtPct(ltv['loss_non_recoverability']), hint: 'Expected loss from defaulted value that is not recoverable.' },
  ];
  const mrrCol = [
    { label: 'MRR', value: num(ltv['mrr'])?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ?? 'n/a', hint: 'Monthly recurring revenue from active contracts.' },
    { label: 'MRR (active <= DSLP30)', value: num(ltv['mrr_dslp30'])?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ?? 'n/a', hint: 'MRR from active contracts that are at most 30 days past due.' },
    { label: 'Subscription margin', value: ltv['subscription_margin'] != null ? `${(num(ltv['subscription_margin'])! * 100).toFixed(2)}%` : 'n/a', hint: 'Margin on subscription revenue.' },
    { label: 'Subscription support', value: ltv['subscription_support'] != null ? `${(num(ltv['subscription_support'])! * 100).toFixed(2)}%` : 'n/a', hint: 'Subscription revenue supporting the asset value.' },
    { label: 'MRR Multiplier (3y)', value: fmt(ltv['mrr_multiplier'], 2), hint: 'MRR valued at a 3-year multiple.' },
    { label: 'MRR / average cost', value: ltv['mrr_over_avg_cost'] != null ? `${(num(ltv['mrr_over_avg_cost'])! * 100).toFixed(2)}%` : 'n/a', hint: 'Monthly recurring revenue relative to average asset cost.' },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <h2 className="sd-h2">LTV Analysis</h2>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: 12,
        }}
      >
        <div>
          <strong>Asset Analysis</strong>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 8 }}>
            {assetCol.map((m) => (
              <Metric key={m.label} label={m.label} value={m.value} hint={m.hint} />
            ))}
          </div>
        </div>
        <div>
          <strong>Cash Flow Analysis (MRR)</strong>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 8 }}>
            {mrrCol.map((m) => (
              <Metric key={m.label} label={m.label} value={m.value} hint={m.hint} />
            ))}
          </div>
        </div>
      </div>

      <hr style={{ border: 'none', borderTop: '1px solid var(--sd-border, #e7ecee)' }} />
      <h3 className="sd-h2" style={{ fontSize: '1rem' }}>Asset-value LTV waterfall (Guide)</h3>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 8 }}>
        <Metric label="Recoverability (95th %ile)" value={fmtPct(ltv['recoverability_pctile_95'])} hint="95th-percentile recovery value on defaulted assets." />
        <Metric label="Subscription support" value={fmtPct(ltv['subscription_support'])} hint="Subscription revenue supporting the asset value." />
        <Metric label="FX devaluation used" value={ltv['fx_deval_used'] != null ? `${(num(ltv['fx_deval_used'])! * 100).toFixed(2)}%` : 'n/a'} hint="Devaluation applied to the asset value in the LTV calculation." />
        <Metric label="Asset LTV (max 95%)" value={ltv['ltv_asset_value'] != null ? `${(num(ltv['ltv_asset_value'])! * 100).toFixed(1)}%` : 'n/a'} hint="Loan-to-value against asset value, capped at 95%." />
      </div>
      <p className="sd-muted">
        LTV = min(95%, (recoverability + subscription support) / (1 + FX)). Recoverability = 95th %ile of
        per-asset recovery/value on defaulted assets (falls back to the repossession rate when no recovery
        values are available).
      </p>
    </div>
  );
}
