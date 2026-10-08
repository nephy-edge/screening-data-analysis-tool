import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useEffect, useState } from 'react';
import { useAppStore } from '../state/AppStore';
import { Alert, HelpTip, Input, Metric, Select, Table, type TableColumn } from '../components/ui';
import { computeLtvCalculator, type LtvCalculatorResponse, type MetricKind, type RevolvingRatio } from '../api/engine';
import { LtvMatrix } from './LtvTabs';

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
  9: 'Expected life of a dollar lent, in months: one divided by the as-of month’s net cash flow as a share of average outstanding over the window.',
  10: 'Monthly interest yield: interest collected divided by principal outstanding, both totalled over the ratio window.',
  11: 'Interest earned over the expected life of a dollar lent: the monthly interest rate multiplied by the expected tenor. Feeds the LTV product interest rate.',
  12: 'Mean point-in-time loss rate over the in-sample months, on the loss-rate basis chosen in General Inputs.',
  13: 'Stressed loss rate: the chosen percentile of the in-sample monthly loss rates. Sets the maximum advance rate.',
  15: 'Share of outstanding that is not past due (New + Current).',
  16: 'Template PAR30: balance 1-60 days past due divided by total outstanding.',
  17: 'Template PAR60: balance 61-90 days past due divided by the active balance (the template uses active, not total, as the denominator).',
  18: 'Template PAR90: balance 61-90 days past due divided by total outstanding.',
  19: 'Principal written off in the as-of month divided by average outstanding over the window.',
  20: 'Total expected credit loss at the as-of month, from the Transition Matrices tab.',
  21: 'ECL as a share of outstanding: total ECL divided by principal outstanding.',
  22: 'Principal outstanding at the as-of month, including defaulted stock.',
  23: 'Borrowers on book at the as-of month.',
  24: 'Principal outstanding divided by the total of approved credit lines.',
};

const ROW_HELP: Record<string, string> = {
  // Standard metrics / early warnings / covenants (scripts/revolving_analysis/metrics.py)
  'Expected Tenor (m) - template': 'Expected life of a dollar lent, in months (template definition).',
  'Interest Rate (m)': 'Monthly interest yield: interest collected divided by principal outstanding over the ratio window.',
  'Active - template': 'Share of outstanding that is not past due (New + Current).',
  'PAR30 - template (1-60 DPD)': 'Balance 1-60 days past due divided by total outstanding.',
  'PAR60 - template (61-90 as a share of active)': 'Balance 61-90 days past due divided by the active balance.',
  'PAR90 - template (61-90 DPD)': 'Balance 61-90 days past due divided by total outstanding.',
  'Write-off rate (m)': 'Principal written off in the month divided by average outstanding over the window.',
  '% of credit line utilised': 'Principal outstanding divided by the total of approved credit lines.',
  'PAR 61+ incl. defaulted stock': 'Everything 61+ days past due, including defaulted stock still on book, divided by total outstanding.',
  'PAR 91+ incl. defaulted stock': 'Everything 91+ days past due, including defaulted stock still on book, divided by total outstanding.',
  'Principal payment rate (m)': 'Principal collected in the month divided by average outstanding. Higher means the book pays down faster.',
  'Tenor from principal turnover (m)': 'Months to repay the book at the current payment rate: one divided by the principal payment rate.',
  'Write-off rate (annualised)': 'The monthly write-off rate multiplied by 12.',
  'Defaulted stock share': 'Defaulted balance still held on book divided by total outstanding. Needs the bucket balances mapped.',
  'PAR 31+ incl. defaulted stock': 'Balance 31+ days past due plus defaulted stock, divided by total outstanding. Needs the bucket balances mapped.',
  'PAR 31+ of performing book': 'Balance 31 or more days past due divided by the performing book (outstanding less defaulted). Needs the bucket balances mapped.',
  'PAR 91+ of performing book': 'Balance 91 or more days past due divided by the performing book. Needs the bucket balances mapped.',
  'Active share of performing book': 'Active balance divided by the performing book. Needs the bucket balances mapped.',
  'Interest yield on performing book (m)': 'Interest collected divided by the performing book over the window. Needs the bucket balances mapped.',
  'Credit line utilised by performing book': 'Performing book divided by the total of approved credit lines. Needs the bucket balances mapped.',
  'ECL coverage (performing)': 'ECL on the performing book divided by the performing balance.',
  'ECL coverage (total)': 'Total ECL divided by the total balance, including defaulted stock.',
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
  '91+ and defaulted stock': 'Outstanding not in the buckets above: 91+ days past due and defaulted stock still on book.',
  'Total principal outstanding': 'Closing principal outstanding, including defaulted stock.',
  'Principal written off (flow in month)': 'Principal written off during the month.',
  'Active → PAR 1-60': 'Share of this month’s active balance that is 1-60 days past due next month.',
  'PAR 1-60 → PAR 61-90': 'Share of this month’s 1-60 balance that is 61-90 days past due next month.',
  'PAR 61-90 → Written off (2 months on)': 'Share of this month’s 61-90 balance written off two months later.',
  'Active + PAR 1-90 within total outstanding': 'Check that the active and PAR buckets do not add up to more than total outstanding.',
  'Roll-forward: unexplained movement': 'The opening balance, plus new drawings, less collections and write-offs, compared with the closing balance. The gap is unexplained.',
  'Unexplained as % of outstanding': 'Unexplained movement divided by outstanding. A warning is raised above the configured threshold.',
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

/** Column-header help for tables whose rows are months or scenarios, so the
 * figure is explained on its column instead of its row. */
const COL_HELP: Record<string, string> = {
  // Stress scenarios
  'Roll-rate multiplier': 'How many times the selected roll rates are scaled up in this scenario.',
  'LGD multiplier': 'How many times LGD is scaled up in this scenario.',
  LGD: 'LGD used in this scenario: the base LGD scaled by the multiplier, capped at 100%.',
  'Total ECL': 'ECL on the performing book, plus defaulted stock multiplied by LGD.',
  'Coverage (performing)': 'ECL on the performing book divided by the performing balance.',
  'Coverage (total)': 'Total ECL divided by the total balance, including defaulted stock.',
  'Loss rate': 'Point-in-time loss rate at the as-of month, on the chosen loss-rate basis.',
  'Advance rate': 'Maximum advance rate: 100% less the loss rate, less the haircut.',
  // Early warnings
  'Lookback average': 'Average of the metric over the lookback months before the as-of month.',
  Change: 'Relative change of the as-of value against the lookback average.',
  Flag: 'Flagged when the metric is worse than its lookback average by more than the threshold in General Inputs.',
  // Covenants
  Test: 'The covenant limit: the metric must stay at or below it (at most) or at or above it (at least).',
  Headroom: 'Distance between the value and the limit. Negative means breached.',
  // Unit economics
  Monthly: 'Per dollar outstanding, per month.',
  Annualised: 'The monthly figure multiplied by 12.',
  'Over the principal tenor': 'The monthly figure multiplied by the term: the average of 1 / net collection ratio.',
  // Backtest
  'Forecast write-offs': 'Write-offs the ECL chain forecast from the book at that month, using only data up to it.',
  'Actual write-offs (2 to 4 months later)': 'Write-offs actually booked two to four months later.',
  'Actual write-offs (1 to 5 months later)': 'Write-offs actually booked one to five months later.',
  'Actual vs forecast': 'Actual write-offs divided by forecast write-offs. Below 1 means the forecast was conservative.',
  Gap: 'How far actual write-offs were from the forecast: actual vs forecast less 1. Positive means losses beat the forecast.',
  'Principal tenor (m)': 'Principal tenor at that month: one divided by principal collected as a share of average outstanding.',
  'Outcome observed': 'Whether the full outcome window has happened yet.',
  // Loss-rate distribution / ECL
  Naive: 'Loss rate on all principal issued to date (a lower bound).',
  Lagged: 'Loss rate on principal issued as at the denominator lag.',
  Blended: 'Weighted mix of Naive and Lagged (blend weight in General Inputs).',
  'Balance at as-of': 'Bucket balance at the as-of month.',
  'Loss factor': 'Share of the bucket expected to be written off, from the chained roll rates.',
  ECL: 'Balance multiplied by the loss factor and by LGD.',
  Denominator: 'The bucket whose balance each roll rate is divided by.',
  T3: 'Average roll rate over the last 3 months.',
  T6: 'Average roll rate over the last 6 months.',
  T12: 'Average roll rate over the last 12 months.',
  ALL: 'Average roll rate over every month.',
  SELECTED: 'The window chosen in General Inputs; it drives the ECL.',
};

/** Column header with a "?" when COL_HELP explains it. */
const hdr = (text: string) => (COL_HELP[text] ? <RowLabel label={text} help={COL_HELP[text]} /> : text);

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

/* Charts. Categorical hues in fixed order (dataviz default palette, validated
 * light: adjacent CVD ΔE ≥ 9.1); each chart sits above its table, which is
 * the table view the palette's contrast WARN requires. */
const SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'];
const MONTH_KEY = /^\d{4}-\d{2}$/;
const axisTick = { fontSize: 11, fill: '#666' };
const compact = (v: unknown) => (typeof v === 'number' ? v.toLocaleString(undefined, { notation: 'compact' }) : String(v));
/** Legend text stays in ink; the swatch carries the series colour. */
const legendText = (v: string) => <span style={{ color: '#16312E' }}>{v}</span>;
const pctTick = (v: unknown) => (typeof v === 'number' ? `${(v * 100).toFixed(0)}%` : String(v));

/** Months-across rows (label column + one key per "YYYY-MM") -> one point per month. */
function byMonth(rows: Row[], labelKey: string, include: (label: string) => boolean) {
  const kept = rows.filter((r) => include(String(r[labelKey])));
  const months = Object.keys(rows[0] ?? {}).filter((k) => MONTH_KEY.test(k));
  const data = months.map((m) => {
    const point: Record<string, unknown> = { month: m };
    for (const r of kept) point[String(r[labelKey])] = isNum(r[m]) ? r[m] : null;
    return point;
  });
  return { series: kept.map((r) => String(r[labelKey])), data };
}

function ChartCard({ title, children }: { title: string; children: React.ReactElement }) {
  return (
    <div className="sd-card">
      <p className="sd-muted" style={{ margin: '0 0 8px' }}>{title}</p>
      <ResponsiveContainer width="100%" height={300}>
        {children}
      </ResponsiveContainer>
    </div>
  );
}

/** Stacked balances by bucket; totals and the write-off flow are not stock
 * buckets, so they stay out of the stack. */
function BalancesChart({ rows }: { rows: Row[] }) {
  const { series, data } = byMonth(rows, 'Bucket', (l) => !l.startsWith('Total') && !l.startsWith('Principal written off'));
  return (
    <ChartCard title="Principal outstanding by bucket (stacked)">
      <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="#e7ecee" vertical={false} />
        <XAxis dataKey="month" tick={axisTick} />
        <YAxis tick={axisTick} tickFormatter={compact} />
        <Tooltip formatter={(v) => fmoney(v)} />
        <Legend formatter={legendText} />
        {series.map((name, i) => (
          <Area
            key={name}
            type="monotone"
            dataKey={name}
            stackId="b"
            stroke={SERIES[i % SERIES.length]}
            strokeWidth={1}
            fill={SERIES[i % SERIES.length]}
            fillOpacity={0.9}
            isAnimationActive={false}
          />
        ))}
      </AreaChart>
    </ChartCard>
  );
}

function RollRatesChart({ rows }: { rows: Row[] }) {
  const { series, data } = byMonth(rows, 'Transition', () => true);
  return (
    <ChartCard title="Roll rates by month">
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="#e7ecee" vertical={false} />
        <XAxis dataKey="month" tick={axisTick} />
        <YAxis tick={axisTick} tickFormatter={pctTick} />
        <Tooltip formatter={(v) => fpct(v)} />
        <Legend formatter={legendText} />
        {series.map((name, i) => (
          <Line
            key={name}
            type="monotone"
            dataKey={name}
            stroke={SERIES[i % SERIES.length]}
            strokeWidth={2}
            dot={{ r: 3 }}
            connectNulls={false}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ChartCard>
  );
}

/** Forecast vs actual write-offs per tested month (same unit, one axis). */
function BacktestChart({ rows }: { rows: Row[] }) {
  const actualKey = Object.keys(rows[0] ?? {}).find((k) => k.startsWith('Actual write-offs')) ?? '';
  const data = rows.filter((r) => r['Outcome observed']);
  return (
    <ChartCard title="Forecast vs actual write-offs (months with the outcome observed)">
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="#e7ecee" vertical={false} />
        <XAxis dataKey="As-of" tick={axisTick} />
        <YAxis tick={axisTick} tickFormatter={compact} />
        <Tooltip formatter={(v) => fmoney(v)} />
        <Legend formatter={legendText} />
        <Line dataKey="Forecast write-offs" stroke={SERIES[0]} strokeWidth={2} dot={{ r: 4 }} isAnimationActive={false} />
        <Line dataKey={actualKey} stroke={SERIES[1]} strokeWidth={2} dot={{ r: 4 }} isAnimationActive={false} />
      </LineChart>
    </ChartCard>
  );
}

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
    header: hdr(key),
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
          { key: 'annualised', header: hdr('Annualised'), align: 'right', render: (r: RevolvingUe) => <span className="sd-mono">{fpct(r.annualised)}</span> },
          { key: 'overTenor', header: hdr('Over the principal tenor'), align: 'right', render: (r: RevolvingUe) => <span className="sd-mono">{fpct(r.overTenor)}</span> },
        ]}
        rows={res.unitEconomics}
      />

      <h2 className="sd-h2">Tenor inputs</h2>
      <p className="sd-muted">The figures behind the principal tenor that the &quot;Over the principal tenor&quot; column multiplies by.</p>
      <Table
        columns={[
          { key: 'label', header: 'Input', render: (r: TenorRow) => <RowLabel label={r.label} help={r.help} /> },
          { key: 'value', header: 'Value', align: 'right', render: (r: TenorRow) => <span className="sd-mono">{r.value}</span> },
        ]}
        rows={[
          { label: 'Principal collected (as-of month)', help: 'Principal repaid in the as-of month.', value: fmoney(res.tenorInputs.principalCollected) },
          { label: `Average outstanding (${res.tenorInputs.windowMonths} months)`, help: 'Average principal outstanding over the Outputs ratio window, ending at the as-of month.', value: fmoney(res.tenorInputs.averageOutstanding) },
          { label: 'Principal payment rate (m)', help: 'Principal collected divided by average outstanding.', value: fpct(res.tenorInputs.paymentRate) },
          { label: 'Principal tenor (months)', help: 'One divided by the principal payment rate: how long a dollar lent stays out at this repayment pace.', value: fdec(res.tenorInputs.principalTenor, 2) },
        ]}
      />

      <h2 className="sd-h2">Interest yield — whole book vs performing book</h2>
      <p className="sd-muted">
        The same interest collected, divided by the whole book and by the performing book (defaulted stock left out).
        The gap shows how much defaulted stock that earns nothing dilutes the yield.
      </p>
      <Table
        columns={[
          { key: 'label', header: 'Basis', render: (r: InterestBasis) => <RowLabel label={r.label} help={r.help} /> },
          { key: 'monthly', header: 'Monthly', align: 'right', render: (r: InterestBasis) => <span className="sd-mono">{fpct(r.monthly)}</span> },
          { key: 'annualised', header: hdr('Annualised'), align: 'right', render: (r: InterestBasis) => <span className="sd-mono">{fpct(r.monthly === null ? null : r.monthly * 12)}</span> },
        ]}
        rows={[
          {
            label: 'Whole book',
            help: 'Interest collected over the ratio window divided by principal outstanding, including defaulted stock.',
            monthly: res.standardMetrics.find((m) => m.key === 'rate_m')?.value ?? null,
          },
          {
            label: 'Performing book',
            help: 'Interest collected over the ratio window divided by the performing book (outstanding less defaulted stock). Needs the bucket columns mapped.',
            monthly: res.standardMetrics.find((m) => m.key === 'yield_perf_m')?.value ?? null,
          },
        ]}
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
          { key: 'prior', header: hdr('Lookback average'), align: 'right', render: (r: RevolvingEw) => <span className="sd-mono">{fkind(r.prior, r.kind)}</span> },
          { key: 'change', header: hdr('Change'), align: 'right', render: (r: RevolvingEw) => <span className="sd-mono">{fpct(r.change, 1)}</span> },
          { key: 'flag', header: hdr('Flag'), render: (r: RevolvingEw) => (r.flag ? '⚠ Worse' : '') },
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
type InterestBasis = { label: string; help: string; monthly: number | null };
type TenorRow = { label: string; help: string; value: string };
type AgeingRow = RevolvingResult['ageingMix'][number];

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
        book then on balance sheet is compared with the write-offs booked {b.window}. Below 1 means the forecast was
        conservative.
      </p>
      <div className="sd-grid sd-grid--metrics">
        <Metric label="Months tested" value={String(b.months)} hint="Months whose full outcome window is observed." />
        <Metric label="Actual vs forecast (pooled)" value={fdec(b.pooledRatio, 3)} hint="Total actual write-offs divided by total forecast write-offs over the tested months." />
        <Metric label="Median ratio" value={fdec(b.medianRatio, 3)} hint="The middle monthly actual-vs-forecast ratio over the tested months." />
        <Metric label="Range" value={`${fdec(b.minRatio, 2)} – ${fdec(b.maxRatio, 2)}`} hint="Lowest and highest monthly ratio." />
        <Metric label="Mean absolute error" value={fpct(b.meanAbsError, 1)} hint="On average, how far each monthly ratio is from 1, in either direction." />
      </div>
      {b.months === 0 ? (
        <Alert tone="info">Not enough history yet for a backtest (see General Inputs → Backtest).</Alert>
      ) : null}
      {b.months > 0 ? <BacktestChart rows={res.backtest} /> : null}
      <WideTable
        rows={res.backtest}
        format={(v, col) =>
          col === 'Actual vs forecast'
            ? fdec(v, 3)
            : col === 'Principal tenor (m)'
              ? fdec(v, 2)
              : col === 'Coverage (performing)' || col === 'Gap'
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
        Loss rate is the as-of point-in-time rate on the chosen basis; the advance rate is 100% less the loss rate, less the haircut.
      </p>
      <Table
        columns={[
          { key: 'name', header: 'Scenario' },
          { key: 'roll', header: hdr('Roll-rate multiplier'), align: 'right', render: (r: Stress) => fdec(r.rollRateMultiplier, 2) },
          { key: 'lgdm', header: hdr('LGD multiplier'), align: 'right', render: (r: Stress) => fdec(r.lgdMultiplier, 2) },
          { key: 'lgd', header: hdr('LGD'), align: 'right', render: (r: Stress) => fpct(r.lgd) },
          { key: 'ecl', header: hdr('Total ECL'), align: 'right', render: (r: Stress) => <span className="sd-mono">{fmoney(r.totalEcl)}</span> },
          { key: 'cp', header: hdr('Coverage (performing)'), align: 'right', render: (r: Stress) => fpct(r.coveragePerforming) },
          { key: 'ct', header: hdr('Coverage (total)'), align: 'right', render: (r: Stress) => fpct(r.coverageTotal) },
          { key: 'lr', header: hdr('Loss rate'), align: 'right', render: (r: Stress) => fpct(r.lossRate) },
          { key: 'adv', header: hdr('Advance rate'), align: 'right', render: (r: Stress) => fpct(r.advanceRate) },
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
        <Metric label="Maximum advance rate" value={fpct(a.advanceRate)} hint="100% less the stress loss rate, less the haircut, and never below zero ('Loss Rate LTV' row 65)." />
      </div>

      <h2 className="sd-h2">2. Covenant tests — as of {res.eclSummary.asOfMonth}</h2>
      {res.covenants.length === 0 ? (
        <Alert tone="info">No covenants set. Add the deal's covenant terms in General Inputs → Covenant tests to test them here.</Alert>
      ) : breaches ? (
        <Alert tone="error">{breaches} covenant(s) breached.</Alert>
      ) : (
        <Alert tone="info">No covenant breached. Limits are set in General Inputs.</Alert>
      )}
      {res.covenants.length === 0 ? null : <Table
        columns={[
          { key: 'label', header: 'Metric', render: (r: { label: string }) => <RowLabel label={r.label} help={ROW_HELP[r.label]} /> },
          { key: 'test', header: 'Test', render: (c: Covenant) => `${c.operator === '<=' ? 'at most' : 'at least'} ${fkind(c.limit, c.kind)}` },
          { key: 'value', header: 'Value', align: 'right', render: (c: Covenant) => <span className="sd-mono">{fkind(c.value, c.kind)}</span> },
          { key: 'headroom', header: hdr('Headroom'), align: 'right', render: (c: Covenant) => <span className="sd-mono">{fkind(c.headroom, c.kind)}</span> },
          {
            key: 'status',
            header: 'Result',
            render: (c: Covenant) => (c.status === 'BREACH' ? '✖ BREACH' : c.status === 'PASS' ? '✔ PASS' : 'N/A (map bucket balances)'),
          },
        ]}
        rows={res.covenants}
      />}
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
        <Metric label="Total ECL" value={fmoney(s.totalEcl)} hint="ECL on the performing book, plus defaulted stock multiplied by LGD." />
        <Metric label="Coverage — performing" value={fpct(s.coveragePerforming)} hint="ECL on the performing book divided by the performing balance." />
        <Metric label="Coverage — total" value={fpct(s.coverageTotal)} hint="Total ECL divided by the total balance, including defaulted stock." />
      </div>
      <h2 className="sd-h2">1. Balances by bucket</h2>
      <BalancesChart rows={res.balances} />
      <WideTable rows={res.balances} format={(v) => fcell(v)} />
      <h2 className="sd-h2">2. Roll rates</h2>
      <p className="sd-muted">
        % of this month&apos;s bucket balance that appears in the next bucket next month; the last month has no next
        month.
      </p>
      <RollRatesChart rows={res.rollRates} />
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
        <Metric label="Outstanding" value={fmoney(ratio('Outstanding')?.value)} hint={RATIO_HELP[22]} />
        <Metric label="Customers" value={isNum(ratio('number of customers')?.value) ? (ratio('number of customers')!.value as number).toLocaleString() : '—'} hint={RATIO_HELP[23]} />
        <Metric label="Credit line utilised" value={pct('% of credit line utilized')} hint={RATIO_HELP[24]} />
        <Metric label="Interest rate (m)" value={pct('Interest Rate (m)')} hint={RATIO_HELP[10]} />
        <Metric label="Write-offs (m)" value={pct('Write offs')} hint={RATIO_HELP[19]} />
        <Metric label="Total ECL" value={fmoney(s.totalEcl)} hint={s.method === 'full' ? 'Full buckets' : 'Approximation from the Outputs columns'} />
        <Metric label="ECL coverage (total)" value={fpct(s.coverageTotal)} hint={ROW_HELP['ECL coverage (total)']} />
      </div>

      <h2 className="sd-h2">Credit quality</h2>
      <Table
        columns={[
          { key: 'label', header: 'Metric', render: (r: { label: string }) => <RowLabel label={r.label} help={ROW_HELP[r.label]} /> },
          { key: 'now', header: 'As-of', align: 'right', render: (r: RevolvingEw) => <span className="sd-mono">{fkind(r.now, r.kind)}</span> },
          { key: 'prior', header: hdr('Lookback average'), align: 'right', render: (r: RevolvingEw) => <span className="sd-mono">{fkind(r.prior, r.kind)}</span> },
          { key: 'change', header: hdr('Change'), align: 'right', render: (r: RevolvingEw) => <span className="sd-mono">{fpct(r.change, 1)}</span> },
          { key: 'flag', header: hdr('Flag'), render: (r: RevolvingEw) => (r.flag ? '⚠ Worse' : '') },
        ]}
        rows={quality}
      />

      {res.ageingMix.length ? (
        <>
          <h2 className="sd-h2">Ageing mix — as of {s.asOfMonth}</h2>
          <p className="sd-muted">Principal outstanding by days past due, standard buckets. Shares add to 100%.</p>
          <Table
            columns={[
              { key: 'bucket', header: 'Bucket' },
              { key: 'balance', header: 'Balance', align: 'right', render: (r: AgeingRow) => <span className="sd-mono">{fmoney(r.balance)}</span> },
              { key: 'share', header: 'Share of outstanding', align: 'right', render: (r: AgeingRow) => <span className="sd-mono">{fpct(r.share)}</span> },
            ]}
            rows={res.ageingMix}
          />
        </>
      ) : null}

      <h2 className="sd-h2">Unit economics</h2>
      <div className="sd-grid sd-grid--metrics">
        <Metric label="Net yield (m)" value={fpct(netYield?.monthly)} hint={ROW_HELP['Net yield']} />
        <Metric label="Net yield (annualised)" value={fpct(netYield?.annualised)} hint={ROW_HELP['Net yield (annualised)']} />
        <Metric label="Net yield (over tenor)" value={fpct(netYield?.overTenor)} hint="Net yield earned over the tenor implied by principal turnover." />
      </div>

      <h2 className="sd-h2">Stress</h2>
      <Table
        columns={[
          { key: 'name', header: 'Scenario' },
          { key: 'lr', header: hdr('Loss rate'), align: 'right', render: (r: Stress) => fpct(r.lossRate) },
          { key: 'adv', header: hdr('Advance rate'), align: 'right', render: (r: Stress) => fpct(r.advanceRate) },
        ]}
        rows={res.stress}
      />

      <h2 className="sd-h2">Facility</h2>
      {res.covenants.length === 0 ? (
        <Alert tone="info">No covenants set. Add the deal's covenant terms in General Inputs → Covenant tests to test them here.</Alert>
      ) : breaches ? (
        <Alert tone="error">{breaches} covenant(s) breached.</Alert>
      ) : (
        <Alert tone="info">No covenant breached.</Alert>
      )}
      {tightest ? (
        <p className="sd-muted">
          Tightest headroom: {tightest.label} at {fkind(tightest.value, tightest.kind)} against{' '}
          {tightest.operator === '<=' ? 'at most' : 'at least'} {fkind(tightest.limit, tightest.kind)} (headroom{' '}
          {fkind(tightest.headroom, tightest.kind)}).
        </p>
      ) : null}
      {res.covenants.length === 0 ? null : <Table
        columns={[
          { key: 'label', header: 'Covenant', render: (c: Covenant) => <RowLabel label={c.label} help={ROW_HELP[c.label]} /> },
          { key: 'value', header: 'Value', align: 'right', render: (c: Covenant) => <span className="sd-mono">{fkind(c.value, c.kind)}</span> },
          { key: 'status', header: 'Result', render: (c: Covenant) => (c.status === 'BREACH' ? '✖ BREACH' : c.status === 'PASS' ? '✔ PASS' : 'N/A') },
        ]}
        rows={res.covenants}
      />}
    </>
  );
}

/** Results computed before the Performance figures existed (an old server
 * or session) - re-running fills them in. */
function StaleResult() {
  return <Alert tone="warning">These results predate the Performance figures. Run analysis again to see this tab.</Alert>;
}

type PerfRow = RevolvingResult['performance']['table'][number];

const fcount = (v: unknown) => (isNum(v) ? (v as number).toLocaleString() : '—');
const fperf = (v: unknown, kind: string) =>
  kind === 'pct' ? fpct(v) : kind === 'months' ? fdec(v) : kind === 'count' ? fcount(v) : fmoney(v);

/** The Performance tab's chart picker: each option plots Performance-table
 * rows by key (or the roll rates / late-bucket stack). */
const PERF_CHARTS: Array<{ id: string; label: string; keys?: string[] }> = [
  { id: 'buckets', label: 'Balances by Late Bucket' },
  { id: 'ncr', label: 'Net Collection Ratio', keys: ['ncr'] },
  { id: 'term', label: 'Estimated Avg Term (1 / net collection ratio)', keys: ['term'] },
  { id: 'roll', label: 'Roll Rates' },
  { id: 'ecl', label: 'ECL by Month', keys: ['ecl'] },
  { id: 'loss', label: 'Estimated Loss Rate', keys: ['loss_rate'] },
  { id: 'line', label: 'Avg Credit Line over Time', keys: ['avg_credit_line'] },
  { id: 'interest', label: 'Total Interest Paid', keys: ['interest_paid'] },
  { id: 'principal', label: 'Total Principal Paid', keys: ['principal_paid'] },
  { id: 'issued', label: 'Total Principal Issued', keys: ['principal_issued'] },
  { id: 'cumwo', label: 'Total Write Off', keys: ['cum_write_off'] },
  { id: 'wo', label: 'Write Off in Period', keys: ['write_off'] },
  { id: 'customers', label: 'Number of Customers', keys: ['customers'] },
  { id: 'perCustomer', label: 'Avg Principal Issued per Customer', keys: ['issued_per_customer'] },
];

const PERF_HELP: Record<string, string> = {
  'New Balance': 'Balance in the New bucket (BAL_NEW). Needs the bucket balances mapped.',
  'Total Balance': 'Principal outstanding at month-end, including defaulted stock.',
  'Net Collection Ratio': 'Principal plus interest collected in the month, divided by last month’s principal outstanding.',
  'Estimated Avg Term (m)': 'One divided by the net collection ratio.',
  ECL: 'ECL on the performing book at month-end (roll rates observable by that month) multiplied by LGD.',
  'Estimated Loss Rate': 'Cumulative write-off to the month plus that month’s ECL, divided by cumulative principal issued to the month.',
  'Avg Credit Line': 'Total credit line divided by number of customers.',
  'Total Interest Paid': 'Interest collected in the month.',
  'Total Principal Paid': 'Principal collected in the month.',
  'Total Principal Issued': 'Principal issued in the month.',
  'Total Write Off': 'Cumulative principal written off up to the month.',
  'Write Off in Period': 'Principal written off in the month.',
  'Number of Customers': 'Borrowers on book at month-end.',
  'Avg Principal Issued per Customer': 'Principal issued in the month divided by number of customers.',
};

/** A breakdown row re-expressed as a share of Total Balance, month by month. */
function shareOfTotal(row: PerfRow, total: PerfRow): PerfRow {
  const out: Row = { ...row, kind: 'pct' };
  for (const k of Object.keys(row)) {
    if (MONTH_KEY.test(k)) {
      const v = row[k];
      const t = total[k];
      out[k] = isNum(v) && isNum(t) && t ? (v as number) / (t as number) : null;
    }
  }
  return out as PerfRow;
}

function PerformanceChart({ res, chart, asPct }: { res: RevolvingResult; chart: string; asPct: boolean }) {
  const table = res.performance.table;
  if (chart === 'roll') return <RollRatesChart rows={res.rollRates} />;
  if (chart === 'buckets') {
    const total = table.find((r) => r.key === 'total_balance')!;
    const rows = table.filter((r) => r.group === 'breakdown').map((r) => (asPct ? shareOfTotal(r, total) : r));
    const { series, data } = byMonth(rows, 'Metric', () => true);
    return (
      <ChartCard title={`Balances by late bucket (stacked${asPct ? ', % of total balance' : ''})`}>
        <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#e7ecee" vertical={false} />
          <XAxis dataKey="month" tick={axisTick} />
          <YAxis tick={axisTick} tickFormatter={asPct ? pctTick : compact} />
          <Tooltip formatter={(v) => (asPct ? fpct(v) : fmoney(v))} />
          <Legend formatter={legendText} />
          {series.map((name, i) => (
            <Area
              key={name}
              type="monotone"
              dataKey={name}
              stackId="b"
              stroke={SERIES[i % SERIES.length]}
              strokeWidth={1}
              fill={SERIES[i % SERIES.length]}
              fillOpacity={0.9}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      </ChartCard>
    );
  }
  const spec = PERF_CHARTS.find((c) => c.id === chart)!;
  const rows = table.filter((r) => spec.keys!.includes(r.key));
  const kind = rows[0]?.kind ?? 'money';
  const { series, data } = byMonth(rows, 'Metric', () => true);
  return (
    <ChartCard title={spec.label}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="#e7ecee" vertical={false} />
        <XAxis dataKey="month" tick={axisTick} />
        <YAxis tick={axisTick} tickFormatter={kind === 'pct' ? pctTick : compact} />
        <Tooltip formatter={(v) => fperf(v, kind)} />
        {series.map((name, i) => (
          <Line
            key={name}
            type="monotone"
            dataKey={name}
            stroke={SERIES[i % SERIES.length]}
            strokeWidth={2}
            dot={{ r: 3 }}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ChartCard>
  );
}

/** Month-by-month performance: balances (breakdown as value or %), the
 * collection, loss and customer figures, a chart picker, then the roll rates
 * and ECL build. */
export function RevolvingPerformanceTab() {
  const { state } = useAppStore();
  const res = state.revolvingResponse;
  const [asPct, setAsPct] = useState(false);
  const [chart, setChart] = useState('buckets');
  if (!res) return <Alert tone="info">Run analysis to see performance.</Alert>;
  if (!res.performance) return <StaleResult />;
  const table = res.performance.table;
  const total = table.find((r) => r.key === 'total_balance')!;
  const rows = table.map((r) => (asPct && r.group === 'breakdown' ? shareOfTotal(r, total) : r));
  const months = Object.keys(table[0] ?? {}).filter((k) => MONTH_KEY.test(k));
  const cols: TableColumn<PerfRow>[] = [
    { key: 'Metric', header: 'Monthly', render: (r) => <RowLabel label={r.Metric} help={PERF_HELP[r.Metric]} /> },
    ...months.map((m) => ({
      key: m,
      header: m,
      align: 'right' as const,
      render: (r: PerfRow) => <span className="sd-mono">{fperf(r[m], r.kind)}</span>,
    })),
  ];
  return (
    <>
      <Warnings warnings={res.warnings} />
      <h2 className="sd-h2">Monthly performance</h2>
      <div className="sd-actions">
        <span className="sd-muted">Broken-down balance as</span>
        <Select
          aria-label="Broken-down balance as"
          value={asPct ? 'pct' : 'value'}
          onChange={(e) => setAsPct(e.target.value === 'pct')}
        >
          <option value="value">Value</option>
          <option value="pct">% of Total Balance</option>
        </Select>
      </div>
      <p className="sd-muted">New Balance and Total Balance always show as values.</p>
      <Table columns={cols} rows={rows} />

      <h2 className="sd-h2">Charts</h2>
      <div className="sd-actions">
        <Select aria-label="Chart" value={chart} onChange={(e) => setChart(e.target.value)}>
          {PERF_CHARTS.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </Select>
      </div>
      <PerformanceChart res={res} chart={chart} asPct={asPct} />

      <h2 className="sd-h2">Roll rates and ECL</h2>
      <RevolvingTransitionTab />
    </>
  );
}

/** LTV Calculator: the Lending calculator's engine (/ltv-calculator, which
 * applies the 1.7x credit stress) fed with this book's interest, average
 * term and 95th %ile estimated loss rate. */
export function RevolvingLtvTab() {
  const { state, dispatch } = useAppStore();
  const res = state.revolvingResponse;
  const { dataSource, country, macroFx, segmentation, hedgeRate, rolledHedge } = state.ltvInputs;
  const set = (inputs: Partial<typeof state.ltvInputs>) => dispatch({ type: 'setLtvInputs', inputs });
  const [response, setResponse] = useState<LtvCalculatorResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const interest = res?.ltvInterest ?? null;
  const avgTerm = res?.performance?.avgTerm ?? null;
  const termDays = avgTerm != null ? avgTerm * 30.4375 : null;
  const loss = res?.performance?.lossRate95 ?? null;

  useEffect(() => {
    if (!res?.performance) return;
    let cancelled = false;
    computeLtvCalculator({
      dataSource,
      country,
      macroFxRisk: macroFx,
      segmentation,
      hedgeRate: parseFloat(hedgeRate) || 0,
      rolledHedge,
      averageInterest: interest,
      termDays,
      lossRate: loss,
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
  }, [res, dataSource, country, macroFx, segmentation, hedgeRate, rolledHedge, interest, termDays, loss]);

  if (!res) return <Alert tone="info">Run analysis to see the LTV calculator.</Alert>;
  if (!res.performance) return <StaleResult />;
  const options = response?.options;
  const result = response?.ok ? response.result : null;
  const pick = (label: string, value: string, choices: string[], onChange: (v: string) => void) => (
    <div className="sd-field">
      <span>{label}</span>
      <Select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
        {choices.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </Select>
    </div>
  );
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <h2 className="sd-h2">LTV Calculator</h2>
      {options ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
          {pick('Data Input Source', dataSource, options.dataInputSources, (v) => set({ dataSource: v }))}
          {pick('Alt Lender Country', country, options.countries, (v) => set({ country: v }))}
          {pick('Macro FX Risk Rating', macroFx, options.fxRiskRatings, (v) => set({ macroFx: v }))}
          {pick('Segmentation', segmentation, options.segmentations, (v) => set({ segmentation: v }))}
          <div className="sd-field">
            <span>Hedge Rate (0 if unhedged, else % OTM as a decimal)</span>
            <Input
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={hedgeRate}
              onChange={(e) => set({ hedgeRate: e.target.value })}
            />
          </div>
          {pick('Rolled hedge?', rolledHedge, options.hedgeTypes, (v) => set({ rolledHedge: v }))}
        </div>
      ) : null}
      {error ? <Alert tone="error">{error}</Alert> : null}
      {response?.lossRateFallbackNotes.map((n) => (
        <Alert key={n} tone="info">
          {n}
        </Alert>
      ))}
      {response && !response.ok ? <Alert tone="warning">{response.warning}</Alert> : null}
      {response && result ? (
        <>
          <h3 className="sd-h2" style={{ fontSize: '1rem' }}>Outputs - Suggested LTV</h3>
          <LtvMatrix result={result} />
          <h3 className="sd-h2" style={{ fontSize: '1rem' }}>Credit Stress Backup</h3>
          <Table
            columns={[
              { key: 'label', header: 'Input', render: (r: TenorRow) => <RowLabel label={r.label} help={r.help} /> },
              { key: 'value', header: 'Value', align: 'right', render: (r: TenorRow) => <span className="sd-mono">{r.value}</span> },
            ]}
            rows={[
              {
                label: 'Product Interest Rate',
                help: 'Interest rate (m) multiplied by the average estimated term.',
                value: fpct(response.greenInterest),
              },
              {
                label: 'Avg Term (m)',
                help: `Average of 1 / net collection ratio over ${res.performance.termMonths} months; snapped to ${response.greenTermBucket ?? 'n/a'} months for the FX tables.`,
                value: fdec(avgTerm),
              },
              {
                label: '95th %ile Loss',
                help: `95th percentile of the monthly estimated loss rate over ${res.performance.lossRateMonths} in-sample months (or the sector minimum when n/a).`,
                value: fpct(response.greenLoss),
              },
              {
                label: 'Stress Loss',
                help: `95th %ile loss multiplied by the credit stress factor (${result.credit_stress_factor}x).`,
                value: fpct(result.stress_loss_rate),
              },
              { label: 'Min Sector Loss Rate', help: 'Sector-minimum stress loss floor.', value: fpct(result.minimum_stress_loss) },
              { label: 'Selected Stress Loss', help: 'Max(stress loss, sector minimum).', value: fpct(result.selected_stress_loss) },
            ]}
          />
        </>
      ) : null}
    </div>
  );
}

/** Unit Economics: the term (average of 1 / net collection ratio) and loss
 * rate up top, then the ratio, cost and metric tables. */
export function RevolvingUnitEconomicsTab() {
  const { state } = useAppStore();
  const res = state.revolvingResponse;
  if (!res) return <Alert tone="info">Run analysis to see unit economics.</Alert>;
  if (!res.performance) return <StaleResult />;
  const p = res.performance;
  return (
    <>
      <div className="sd-grid sd-grid--metrics">
        <Metric
          label="Term (m)"
          value={fdec(p.avgTerm)}
          hint={`Average of 1 / net collection ratio over ${p.termMonths} months. The "Over the principal tenor" column multiplies by this.`}
        />
        <Metric label="Loss rate (as-of)" value={fpct(p.lossRateNow)} hint={PERF_HELP['Estimated Loss Rate']} />
        <Metric
          label="Loss rate (95th %ile)"
          value={fpct(p.lossRate95)}
          hint={`95th percentile of the monthly estimated loss rate over ${p.lossRateMonths} in-sample months.`}
        />
      </div>
      <RevolvingOutputsTab />
    </>
  );
}
