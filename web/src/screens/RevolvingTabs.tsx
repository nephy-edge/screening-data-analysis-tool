import {
  Area,
  AreaChart,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useEffect, useState } from 'react';
import { useAppStore } from '../state/AppStore';
import { Alert, HelpTip, Input, Metric, Select, Table, Tabs, type TableColumn } from '../components/ui';
import { computeLtvCalculator, type LtvCalculatorResponse, type MetricKind, type RevolvingRatio } from '../api/engine';
import { FxChart, LtvMatrix, SnapshotTable, fmtPct } from './LtvTabs';

type Row = Record<string, unknown>;

const isNum = (v: unknown) => typeof v === 'number' && Number.isFinite(v);
// `+ 0` drops the sign from a value that rounds to zero (no "-0.00%").
const fpct = (v: unknown, digits = 2) =>
  isNum(v) ? `${(Number(((v as number) * 100).toFixed(digits)) + 0).toFixed(digits)}%` : '—';
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
  'Interest yield': 'Interest (accrued when mapped, else collected) per dollar outstanding, averaged over the LTV / UE window.',
  'Write-offs': 'Principal written off per dollar outstanding (a deduction), averaged over the LTV / UE window like the interest yield.',
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
  'Over the average term': 'The monthly figure multiplied by the average term: the mean of 1 / net collection ratio over the LTV / UE window.',
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
// Delinquency severity, green (performing) to dark red (defaulted). Ordered
// by lightness as well as hue so the order survives colour-blindness
// (validated with the dataviz validate_palette script; adjacent CVD passes).
const SEVERITY: Array<[RegExp, string]> = [
  [/default|written|91\+/i, '#4a0a0e'],
  [/121|151|180/, '#7c1418'],
  [/91-120|91_120/, '#b52222'],
  [/61-90|61_90/, '#e2582a'],
  [/31-60|31_60|1-60/, '#f39a2e'],
  [/1-30|1_30/, '#f5c84c'],
  [/^new/i, '#7cc68b'],
  [/current|active/i, '#1f9d55'],
];
/** Colour for a bucket (or a roll from a bucket: the part before the arrow). */
const severityColor = (name: string, i: number) => {
  const from = name.split('→')[0];
  return SEVERITY.find(([re]) => re.test(from))?.[1] ?? SERIES[i % SERIES.length];
};
// Loss lines are bad-is-up, so they wear the critical red.
const LOSS_RED = '#b52222';
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
            legendType="square"
            stroke={severityColor(name, i)}
            strokeWidth={1}
            fill={severityColor(name, i)}
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
            stroke={severityColor(name, i)}
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
          { key: 'overTenor', header: hdr('Over the average term'), align: 'right', render: (r: RevolvingUe) => <span className="sd-mono">{fpct(r.overTenor)}</span> },
        ]}
        rows={res.unitEconomics}
      />

      <h2 className="sd-h2">Principal tenor (reference only)</h2>
      <p className="sd-muted">How fast principal turns over at the as-of month. Shown for reference: the &quot;Over the average term&quot; column above multiplies by the average term (1 / net collection ratio) instead.</p>
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


/** rcl_rollrate_ecl_3.xlsx's 'Roll Rate ECL' tab: the ECL summary, balances
 * by bucket and the roll rates with their T3/T6/T12/ALL averages. The ECL
 * table by bucket sits on Data Checks (RevolvingEclTable). */
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
    </>
  );
}

/** Data Checks: the ECL build at the as-of month - balance, loss factor and
 * LGD per bucket. */
export function RevolvingEclTable() {
  const { state } = useAppStore();
  const res = state.revolvingResponse;
  if (!res) return null;
  return (
    <div className="sd-card">
      <h2 className="sd-h2">Expected credit loss — as of {res.eclSummary.asOfMonth}</h2>
      <WideTable
        rows={res.ecl}
        format={(v, col) => (col === 'Loss factor' || col === 'LGD' ? fpct(v) : fcell(v))}
      />
    </div>
  );
}

/** The values of a chart's dashed reference lines, as a key above the plot
 * rather than labels on it, so they never sit on top of bars, lines or dots. */
function RefKey({ items }: { items: Array<[string, string | null]> }) {
  const shown = items.filter((i): i is [string, string] => i[1] !== null);
  if (shown.length === 0) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, fontSize: 12, marginBottom: 4 }}>
      {shown.map(([color, text]) => (
        <span key={text} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: '#333' }}>
          <svg width="22" height="6" aria-hidden="true">
            <line x1="0" y1="3" x2="22" y2="3" stroke={color} strokeWidth="2" strokeDasharray="5 3" />
          </svg>
          {text}
        </span>
      ))}
    </div>
  );
}

/** Summary chart, mirroring Lending's "Cohort Loss Rates": grey bars =
 * principal issued per month, orange line = the in-sample monthly loss rate on
 * the selected basis, with the average and 95th %ile as reference lines. */
function MonthlyLossRateChart({ res, avgLoss, pctile95 }: { res: RevolvingResult; avgLoss: number | null; pctile95: number | null }) {
  const basis = res.performance.lossRateBasis;
  const issued = res.performance.table.find((r) => r.key === 'principal_issued');
  const loss = new Map(
    res.lossRates.filter((r) => isNum(r[basis])).map((r) => [String(r.Month), r[basis] as number]),
  );
  const data = Object.keys(issued ?? {})
    .filter((k) => MONTH_KEY.test(k))
    .map((m) => ({ month: m, principal: isNum(issued![m]) ? (issued![m] as number) : null, lossRate: loss.get(m) ?? null }));
  if (data.length === 0) return null;
  return (
    <div className="sd-card">
      <h2 className="sd-h2">Monthly Loss Rates</h2>
      <RefKey items={[['#6B7280', avgLoss === null ? null : `Avg: ${fpct(avgLoss)}`], ['#1f2937', pctile95 === null ? null : `95th %ile: ${fpct(pctile95)}`]]} />
      <ResponsiveContainer width="100%" height={340}>
        <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="#e7ecee" vertical={false} />
          <XAxis dataKey="month" tick={{ fontSize: 11, fill: '#666' }} />
          <YAxis yAxisId="left" tick={{ fontSize: 11, fill: '#666' }} tickFormatter={compact} />
          <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11, fill: '#666' }} tickFormatter={pctTick} />
          <Tooltip formatter={(v, name) => (name === 'Loss Rate' ? fpct(v) : fmoney(v))} />
          <Legend />
          <Bar yAxisId="left" dataKey="principal" fill="#B8BEC6" name="Principal Issued" />
          {avgLoss !== null && (
            <ReferenceLine
              yAxisId="right"
              y={avgLoss}
              stroke="#6B7280"
              strokeDasharray="6 6"
              ifOverflow="extendDomain"
            />
          )}
          {pctile95 !== null && (
            <ReferenceLine
              yAxisId="right"
              y={pctile95}
              stroke="#1f2937"
              strokeDasharray="6 6"
              ifOverflow="extendDomain"
            />
          )}
          <Line yAxisId="right" dataKey="lossRate" stroke={LOSS_RED} strokeWidth={2.5} dot={{ r: 3 }} name="Loss Rate" connectNulls />
        </ComposedChart>
      </ResponsiveContainer>
      <p className="sd-muted">
        Grey bars: principal issued per month. Red line: the {basis} loss rate for every month; the average and 95th
        %ile use only the {res.performance.lossRateMonths} in-sample months.
      </p>
    </div>
  );
}

type SnapshotRow = { label: string; value: string; help: string };

/** The Lending Summary layout: five headline cards, then a Portfolio
 * Snapshot table (every other key stat, including the credit-quality,
 * covenant and data-check counts) beside the monthly loss-rate chart. */
export function RevolvingSummaryTab() {
  const { state } = useAppStore();
  const res = state.revolvingResponse;
  if (!res) return <Alert tone="info">Run analysis to see the portfolio summary.</Alert>;
  if (!res.performance?.lossRateBasis) return <StaleResult />;
  const p = res.performance;
  const s = res.eclSummary;
  const ratio = (row: number) => res.ratios.find((r) => r.row === row)?.value ?? null;
  const perfRow = (key: string) => p.table.find((r) => r.key === key);
  const months = Object.keys(p.table[0] ?? {}).filter((k) => MONTH_KEY.test(k));
  const window = Number(res.generalInputs.ltvWindowMonths) || 12;
  const series = (key: string, last?: number) =>
    (last ? months.slice(-last) : months).map((m) => perfRow(key)?.[m]).filter(isNum) as number[];
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
  const ncr = series('ncr', window);
  const avgNcr = ncr.length ? sum(ncr) / ncr.length : null;
  const collected = sum(series('principal_paid')) + sum(series('interest_paid'));
  const lines = series('avg_credit_line');
  const lastCreditLine = lines.length ? lines[lines.length - 1] : null;
  const netYield = res.unitEconomics.find((u) => u.item === 'Net yield')?.monthly ?? null;
  const lossRate = ratio(12);
  const rows: SnapshotRow[] = [
    { label: 'Date Range', value: `${months[0] ?? '—'} to ${months[months.length - 1] ?? '—'}`, help: 'First to last report month in the data (up to the as-of month).' },
    { label: 'Customers', value: fcount(ratio(23)), help: 'Borrowers on book at the as-of month.' },
    { label: 'Total Principal Issued', value: fmoney(sum(series('principal_issued'))), help: 'Principal drawn across all months to date.' },
    { label: 'Total Collected', value: fmoney(collected), help: 'Principal plus interest collected across all months to date.' },
    { label: 'Credit Line Utilisation', value: fpct(ratio(24)), help: 'Principal outstanding divided by total credit line at the as-of month.' },
    { label: 'Avg Credit Line per Customer', value: `$${fmoney(lastCreditLine)}`, help: 'A currency amount, not a percentage: total credit line divided by customers at the as-of month, in the source currency.' },
    {
      label: 'Highest Borrowing Customer %',
      value: res.largestCustomerShare == null ? 'n/a' : fpct(res.largestCustomerShare),
      help:
        res.largestCustomerShare == null
          ? "Map largest_customer_outstanding (the largest single borrower's balance each month) to show borrower concentration."
          : "The largest single borrower's balance as a share of principal outstanding at the as-of month.",
    },
    { label: 'Interest Yield (m)', value: fpct(p.interestYield), help: `Monthly ${p.interestBasis} interest over average outstanding, averaged over the last ${p.interestYieldMonths} months.` },
    { label: 'APR Proxy', value: fpct(p.interestYield === null ? null : p.interestYield * 12), help: 'Interest yield (m) times 12 - a simple flat annualisation.' },
    { label: 'Write-off Rate (m)', value: fpct(p.writeOffRate), help: `Principal written off over average outstanding, averaged over the last ${p.interestYieldMonths} months (the Unit Economics write-off line).` },
    { label: 'Net Yield (m)', value: fpct(netYield), help: 'Interest yield less write-offs, cost of funds and operating costs (Unit Economics).' },
    { label: 'Avg Net Collection Ratio', value: fpct(avgNcr), help: `Principal plus interest collected over last month's outstanding, averaged over the last ${ncr.length} months.` },
    { label: 'Avg Term (m)', value: fdec(p.avgTerm), help: `Average of 1 / net collection ratio over the last ${p.termMonths} months - the LTV term.` },
  ];
  return (
    <>
      <Warnings warnings={res.warnings} />
      <div className="sd-grid sd-grid--metrics">
        <Metric label="Data up to" value={s.asOfMonth} hint="The as-of report month (the last month analysed)." />
        <Metric label="Principal Outstanding" value={fmoney(ratio(22))} hint="Closing principal outstanding at the as-of month, incl. defaulted stock." />
        <Metric label="Loss Rate" value={fpct(lossRate)} hint={`Mean of the in-sample monthly ${p.lossRateBasis} loss rates (Outputs).`} />
        <Metric label="95th %ile Loss Rate" value={fpct(p.lossRate95)} hint={`Stress percentile of the in-sample monthly ${p.lossRateBasis} loss rates - the figure the LTV Calculator uses.`} />
        <Metric label="Product Interest Rate" value={fpct(res.ltvInterest)} hint="Interest yield (m) multiplied by the average term - the LTV Calculator's interest input." />
      </div>
      <div className="sd-grid sd-grid--2">
        <div>
          <h2 className="sd-h2">Portfolio Snapshot</h2>
          <Table
            columns={[
              { key: 'label', header: 'Metric', render: (r: SnapshotRow) => <RowLabel label={r.label} help={r.help} /> },
              { key: 'value', header: 'Value', align: 'right', render: (r: SnapshotRow) => <span className="sd-mono">{r.value}</span> },
            ]}
            rows={rows}
          />
        </div>
        <MonthlyLossRateChart res={res} avgLoss={isNum(lossRate) ? (lossRate as number) : null} pctile95={p.lossRate95} />
      </div>
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
              legendType="square"
              stroke={severityColor(name, i)}
              strokeWidth={1}
              fill={severityColor(name, i)}
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
  const shown = table.map((r) => (asPct && r.group === 'breakdown' ? shareOfTotal(r, total) : r));
  // Labelled sections, each in one unit: New and Total Balance stay values,
  // the bucket breakdown follows the toggle, the rest keep their own unit.
  const sections: Array<[string, (r: PerfRow) => boolean]> = [
    ['Balances (amount)', (r) => r.group === 'balance'],
    [asPct ? 'Balance by bucket (% of Total Balance)' : 'Balance by bucket (amount)', (r) => r.group === 'breakdown'],
    ['Rates (%)', (r) => r.group === 'metric' && r.kind === 'pct'],
    ['Term (months)', (r) => r.group === 'metric' && r.kind === 'months'],
    ['Amounts (currency)', (r) => r.group === 'metric' && r.kind === 'money'],
    ['Counts', (r) => r.group === 'metric' && r.kind === 'count'],
  ];
  const rows = sections.flatMap(([title, keep]) => {
    const part = shown.filter(keep);
    return part.length ? [{ key: `section:${title}`, Metric: title } as PerfRow, ...part] : [];
  });
  const isSection = (r: PerfRow) => r.key.startsWith('section:');
  const months = Object.keys(table[0] ?? {}).filter((k) => MONTH_KEY.test(k));
  const cols: TableColumn<PerfRow>[] = [
    {
      key: 'Metric',
      header: 'Monthly',
      render: (r) => (isSection(r) ? <strong>{r.Metric}</strong> : <RowLabel label={r.Metric} help={PERF_HELP[r.Metric]} />),
    },
    ...months.map((m) => ({
      key: m,
      header: m,
      align: 'right' as const,
      render: (r: PerfRow) => (isSection(r) ? null : <span className="sd-mono">{fperf(r[m], r.kind)}</span>),
    })),
  ];
  return (
    <>
      <Warnings warnings={res.warnings} />
      <h2 className="sd-h2">Roll rates and ECL</h2>
      <RevolvingTransitionTab />
      <RevolvingEclTable />

      <h2 className="sd-h2">Monthly performance</h2>
      <div className="sd-actions">
        <span className="sd-muted">Balances as</span>
        <Select
          aria-label="Balances as"
          value={asPct ? 'pct' : 'value'}
          onChange={(e) => setAsPct(e.target.value === 'pct')}
        >
          <option value="value">Value</option>
          <option value="pct">% of Total Balance</option>
        </Select>
      </div>
      <p className="sd-muted">The toggle switches the balance-by-bucket rows only; New Balance and Total Balance always show as values. The other sections keep the unit in their title. New Balance needs BAL_NEW mapped.</p>
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
    </>
  );
}

/** The in-sample monthly loss rates on the selected basis with the 95th %ile
 * line - the revolving counterpart of Lending's "Loss per Cohort" chart, same
 * styling, limited (like Lending's) to what the 95th %ile uses. */
function LossByMonthChart({ res, green }: { res: RevolvingResult; green: number | null }) {
  const basis = res.performance.lossRateBasis;
  const data = res.lossRates
    .filter((r) => r['In sample'] && isNum(r[basis]))
    .map((r) => ({ month: String(r.Month), lossRate: r[basis] as number }));
  if (data.length === 0) return null;
  return (
    <div className="sd-card">
      <h2 className="sd-h2">{`Loss Rate by Month (${basis})`}</h2>
      <RefKey items={[['#1f2937', green === null ? null : `95th %ile: ${(green * 100).toFixed(2)}%`]]} />
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data} margin={{ top: 8, right: 24, bottom: 8, left: 0 }}>
          <CartesianGrid stroke="#e7ecee" vertical={false} />
          <XAxis
            dataKey="month"
            tick={{ fontSize: 11, fill: '#666' }}
            label={{ value: 'Month', position: 'insideBottom', offset: -5, fontSize: 11 }}
          />
          <YAxis
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
            tick={{ fontSize: 11, fill: '#666' }}
            label={{ value: 'Loss Rate', angle: -90, position: 'insideLeft', fontSize: 11 }}
          />
          <Tooltip formatter={(v) => (typeof v === 'number' ? `${(v * 100).toFixed(2)}%` : v)} />
          <Line dataKey="lossRate" name="Loss Rate" stroke={LOSS_RED} strokeWidth={2} dot={{ r: 3 }} />
          {green !== null ? (
            <ReferenceLine
              y={green}
              stroke="#1f2937"
              strokeDasharray="4 4"
              ifOverflow="extendDomain"
            />
          ) : null}
        </LineChart>
      </ResponsiveContainer>
      <p className="sd-muted">
        In-sample months behind the 95th %ile: {res.performance.lossRateMonths} (principal outstanding at least{' '}
        {fmoney(res.performance.inSampleFloor)}, the General Inputs minimum).
      </p>
    </div>
  );
}

/** LTV Calculator: the same layout as Lending's tab (LendingLtvTab) on the
 * same engine (/ltv-calculator, 1.7x credit stress). Only the green inputs
 * differ - interest = trailing interest yield x avg term, term = trailing
 * average of 1 / net collection ratio, loss = the Outputs stress percentile
 * loss rate on the selected basis. */
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
    if (!res?.performance?.lossRateBasis) return;
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
  if (!res.performance?.lossRateBasis) return <StaleResult />;
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

      <p className="sd-h2" style={{ fontSize: '0.95rem', fontWeight: 700 }}>
        Inputs
      </p>
      {options ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, alignItems: 'end' }}>
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
                help: `Average of 1 / net collection ratio over the last ${res.performance.termMonths} months (${fdec(avgTerm)} months), snapped to the nearest tenor bucket the workbook's dropdown allows.`,
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
              { label: 'Product Interest Rate', value: fmtPct(response.greenInterest), help: `Monthly interest yield (${res.performance.interestBasis} interest over average outstanding, averaged over the last ${res.performance.interestYieldMonths} months) multiplied by the average term, pulled from this analysis.` },
              {
                label: '95th %ile Loss',
                value: fmtPct(response.greenLoss),
                help: `Stress percentile of the ${res.performance.lossRateBasis} monthly loss rate over ${res.performance.lossRateMonths} in-sample months (the Outputs 95th %ile), pulled from this analysis - or, when that's n/a, the Min Sector Loss Rate used as a fallback (see the note above).`,
              },
              { label: 'Stress Loss', value: fmtPct(result.stress_loss_rate), help: `Loss rate multiplied by the credit stress factor (${result.credit_stress_factor}x).` },
              { label: 'Min Sector Loss Rate', value: fmtPct(result.minimum_stress_loss), help: 'Sector-minimum stress loss floor for the selected segmentation and data source.' },
              { label: 'Selected Stress Loss', value: fmtPct(result.selected_stress_loss), help: 'Max(stress loss rate, minimum stress loss floor).' },
            ]}
          />
          <LossByMonthChart res={res} green={response.greenLoss} />
        </>
      ) : null}
    </div>
  );
}

/** Unit Economics: Overview / UE Model sub-tabs, laid out like Lending's. */
export function RevolvingUnitEconomicsTab() {
  const [subTab, setSubTab] = useState<'overview' | 'model'>('overview');
  const { state } = useAppStore();
  const res = state.revolvingResponse;
  if (!res) return <Alert tone="info">Run analysis to see unit economics.</Alert>;
  if (!res.performance?.series) return <StaleResult />;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Tabs
        items={[
          { id: 'overview', label: 'Overview' },
          { id: 'model', label: 'UE Model' },
        ]}
        value={subTab}
        onChange={(v) => setSubTab(v as 'overview' | 'model')}
      />
      {subTab === 'overview' ? <RevolvingUeOverview res={res} /> : <RevolvingUeModel res={res} />}
    </div>
  );
}

/** One monthly line with a dashed average line (the mean of the months
 * plotted) - the revolving version of Lending's per-cohort UE charts. */
function UeMonthChart({ data, title, fmt, color }: {
  data: Array<{ month: string; value: number | null }>;
  title: string;
  fmt: (v: number) => string;
  color: string;
}) {
  const vals = data.map((d) => d.value).filter((v): v is number => v !== null);
  if (vals.length === 0) return null;
  const avg = vals.reduce((a, v) => a + v, 0) / vals.length;
  return (
    <div className="sd-card">
      <h2 className="sd-h2">{title}</h2>
      <RefKey items={[[color, `Avg: ${fmt(avg)}`]]} />
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="#e7ecee" vertical={false} />
          <XAxis dataKey="month" tick={{ fontSize: 11, fill: '#666' }} />
          <YAxis tick={{ fontSize: 11, fill: '#666' }} tickFormatter={(v) => (typeof v === 'number' ? fmt(v) : v)} />
          <Tooltip formatter={(v) => (typeof v === 'number' ? fmt(v) : v)} />
          <Line dataKey="value" stroke={color} strokeWidth={2} dot={{ r: 3 }} name={title} connectNulls />
          <ReferenceLine y={avg} stroke={color} strokeDasharray="6 6" ifOverflow="extendDomain" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function RevolvingUeOverview({ res }: { res: RevolvingResult }) {
  const p = res.performance;
  const months = res.availableMonths;
  const perfRow = (key: string) => {
    const row = p.table.find((r) => r.key === key);
    return months.map((m) => ({ month: m, value: row && isNum(row[m]) ? (row[m] as number) : null }));
  };
  const seriesOf = (k: 'interestYield' | 'writeOffRate') => p.series.map((s) => ({ month: s.month, value: s[k] }));
  const net = isNum(p.interestYield) && isNum(p.writeOffRate) ? p.interestYield! - p.writeOffRate! : null;
  const pct1 = (v: number) => `${(v * 100).toFixed(1)}%`;
  const pct2 = (v: number) => `${(v * 100).toFixed(2)}%`;
  const rows: Array<Array<[string, string, string]>> = [
    [
      ['Average Term (m)', fdec(p.avgTerm, 1), `Average of 1 / net collection ratio over the last ${p.termMonths} months.`],
      ['Loss Rate (as-of)', fpct(p.lossRateNow, 1), PERF_HELP['Estimated Loss Rate']],
      ['Loss Rate (95th %ile)', fpct(p.lossRate95, 1), `Stress percentile of the ${p.lossRateBasis} monthly loss rate over ${p.lossRateMonths} in-sample months - the figure the LTV Calculator uses.`],
    ],
    [
      ['Interest Yield (m)', fpct(p.interestYield), `Monthly ${p.interestBasis} interest over average outstanding, averaged over the last ${p.interestYieldMonths} months.`],
      ['Write-off Rate (m)', fpct(p.writeOffRate), 'Monthly write-offs over average outstanding, averaged over the same window.'],
      ['Product Interest Rate', fpct(res.ltvInterest, 1), 'Interest yield (m) x average term - the interest earned on a dollar over its life.'],
    ],
    [['Sense-check Margin (m)', fpct(net), 'Interest yield (m) - write-off rate (m), before costs.']],
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
      <div className="sd-grid sd-grid--2">
        <UeMonthChart data={perfRow('loss_rate')} title="Loss Rate per Month" fmt={pct1} color="#2ca02c" />
        <UeMonthChart data={perfRow('term')} title="Average Term per Month (m)" fmt={(v) => v.toFixed(1)} color="#2ca02c" />
        <UeMonthChart data={seriesOf('interestYield')} title="Interest Yield per Month" fmt={pct2} color="#ff7f0e" />
        <UeMonthChart data={seriesOf('writeOffRate')} title="Write-off Rate per Month" fmt={pct2} color="#1f77b4" />
      </div>
    </>
  );
}

type UeLoss = 'Now' | 'P95' | 'WriteOff' | 'Input';

/** UE Model: Lending's layout (manual costs, loss picker, two headline cards,
 * two tables), on a per-dollar-outstanding basis over the average term. A
 * revolving book has no single loan cash flow, so the headline is annualised
 * net yield rather than an IRR. */
function RevolvingUeModel({ res }: { res: RevolvingResult }) {
  const p = res.performance;
  const gi = res.generalInputs;
  const [ongoing, setOngoing] = useState(Number(gi.opexPct) || 0);
  const [financing, setFinancing] = useState(Number(gi.costOfFundsPct) || 0);
  const [facility, setFacility] = useState(0);
  const [hedge, setHedge] = useState(0);
  const [lossChoice, setLossChoice] = useState<UeLoss>('Now');
  const [lossInput, setLossInput] = useState((p.lossRateNow ?? 0) * 100);

  const term = p.avgTerm;
  const y = p.interestYield;
  // Loss over the term: the cumulative loss rates already are; the write-off
  // rate is monthly, so it scales by the term like the yield.
  const loss =
    lossChoice === 'Now' ? p.lossRateNow
    : lossChoice === 'P95' ? p.lossRate95
    : lossChoice === 'WriteOff' ? (isNum(p.writeOffRate) && isNum(term) ? p.writeOffRate! * term! : null)
    : lossInput / 100;
  const ok = (...v: Array<number | null | undefined>) => v.every((x) => x != null && Number.isFinite(x));
  const income = ok(y, term) ? y! * term! : null;
  const incomeNet = ok(income, loss) ? income! - loss! : null;
  const variable = ok(term) ? (ongoing * term!) / 12 : null;
  const contribution = ok(incomeNet, variable) ? incomeNet! - variable! : null;
  const cof = financing + facility + hedge;
  const cofOverTerm = ok(term) ? (cof * term!) / 12 : null;
  const gross = ok(contribution, term) && term! > 0 ? (contribution! / term!) * 12 : null;
  const netY = gross !== null ? gross - cof : null;

  const f2 = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? 'n/a' : `${(v * 100).toFixed(2)}%`);
  const f1 = (v: number | null) => (v == null || !Number.isFinite(v) ? 'n/a' : `${(v * 100).toFixed(1)}%`);
  const costInput = (label: string, value: number, onChange: (v: number) => void) => (
    <label>
      <div className="sd-muted">{label}</div>
      <Input type="number" min={0} step={0.1} value={value * 100} onChange={(e) => onChange(Math.max(0, Number(e.target.value) || 0) / 100)} />
    </label>
  );
  const snap = (rows: Array<[string, string, string]>) => rows.map(([label, value, help]) => ({ label, value, help }));
  const headline = (label: string, v: number | null, text: string) => (
    <div className="sd-card">
      <div className="sd-muted">{label}</div>
      <div className="sd-metric__value" style={{ color: v != null && v < 0 ? '#d62728' : '#2ca02c' }}>{f1(v)}</div>
      <p className="sd-muted" style={{ marginTop: 6 }}>{text}</p>
    </div>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <h2 className="sd-h2">UE Model</h2>
      <div className="sd-card">
        <span className="sd-muted" style={{ display: 'inline-block', background: '#f5d90a', color: '#16312E', padding: '2px 8px', borderRadius: 4, fontSize: '.8rem', fontWeight: 600 }}>
          Manual inputs (not in the monthly data)
        </span>
        <p className="sd-muted" style={{ marginTop: 8 }}>
          Cost and financing assumptions, all % p.a. of principal outstanding; 0 if not applicable. Ongoing costs and
          financing default to the General Inputs opex and cost of funds. A revolving line has no single origination,
          so there is no upfront cost.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 10, marginTop: 8 }}>
          {costInput('Ongoing variable costs (% p.a.)', ongoing, setOngoing)}
          {costInput('Financing cost (% p.a.)', financing, setFinancing)}
          {costInput('Facility fee - annualised (% p.a.)', facility, setFacility)}
          {costInput('Hedge cost - annualised (% p.a.)', hedge, setHedge)}
        </div>
      </div>

      <div className="sd-card">
        <span className="sd-muted" style={{ display: 'inline-block', marginBottom: 8 }}><strong>Loss Rate</strong></span>
        <label>
          <div className="sd-muted">Loss Rate used below</div>
          <Select value={lossChoice} onChange={(e) => setLossChoice(e.target.value as UeLoss)}>
            <option value="Now">Loss Rate (as-of)</option>
            <option value="P95">Loss Rate (95th %ile)</option>
            <option value="WriteOff">Write-off rate x term</option>
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
        {headline('Gross Annualised Yield', gross, 'Product contribution spread over the average term and annualised: what a dollar outstanding earns per year after losses and variable costs, before cost of finance. Not an IRR - a revolving book has no single loan cash flow.')}
        {headline('Net Annualised Yield', netY, 'Gross annualised yield minus Cost of Finance (Financing cost + Facility fee + Hedge cost, % p.a.).')}
      </div>

      <SnapshotTable
        rows={snap([
          ['Product Interest Rate', f2(income), 'Interest yield (m) x average term - the interest earned on a dollar over its life.'],
          ['Interest Yield (m)', f2(y), `Monthly ${p.interestBasis} interest over average outstanding, averaged over the last ${p.interestYieldMonths} months.`],
          ['APR Proxy', f2(ok(y) ? y! * 12 : null), 'Interest yield (m) x 12.'],
          ['Loss %', f2(loss), 'The loss rate chosen above, over the life of a dollar lent.'],
          ['Term', ok(term) ? `${term!.toFixed(1)} months` : 'n/a', `Average of 1 / net collection ratio over the last ${p.termMonths} months.`],
        ])}
      />

      <SnapshotTable
        rows={snap([
          ['Income (net losses)', f2(incomeNet), 'Product Interest Rate - Loss %, per dollar lent over the average term.'],
          ['Variable costs (over term)', f2(variable), 'Ongoing variable costs (% p.a.) x term / 12.'],
          ['Product contribution', f2(contribution), 'Income (net losses) - Variable costs, over the average term.'],
          ['Cost of Finance (% p.a.)', f2(cof), 'Financing cost + Facility fee + Hedge cost, as entered.'],
          ['Cost of Finance (over term)', f2(cofOverTerm), 'Cost of Finance (% p.a.) x term / 12, comparable to Product contribution.'],
        ])}
      />
    </div>
  );
}

type AgeingRow = RevolvingResult['ageingMix'][number];

/** Data Checks: principal outstanding by days past due at the as-of month,
 * in standard buckets that add to 100% - unlike the template's Outputs ratios
 * ('PAR30' is 1-60 days, 'PAR60' divides by the active balance and 'PAR90'
 * repeats the 61-90 bucket), which are not meant to sum. */
export function RevolvingAgeingMix() {
  const { state } = useAppStore();
  const res = state.revolvingResponse;
  if (!res) return null;
  const rows = res.ageingMix;
  const total: AgeingRow = {
    bucket: 'Total',
    balance: rows.reduce((a, r) => a + r.balance, 0),
    share: rows.reduce((a, r) => a + (r.share ?? 0), 0),
  };
  return (
    <div className="sd-card">
      <h2 className="sd-h2">Ageing mix — as of {res.eclSummary.asOfMonth}</h2>
      {rows.length === 0 ? (
        <Alert tone="info">Map the bucket balances (BAL_NEW … BAL_DEFAULT) to see the ageing mix.</Alert>
      ) : (
        <>
          <p className="sd-muted">
            Principal outstanding by days past due. The shares should add to 100%; the template&apos;s PAR ratios on the
            Data Input sub-tab use different denominators and overlapping buckets, so they do not.
          </p>
          <Table
            columns={[
              { key: 'bucket', header: 'Bucket', render: (r: AgeingRow) => (r.bucket === 'Total' ? <strong>Total</strong> : r.bucket) },
              { key: 'balance', header: 'Balance', align: 'right', render: (r: AgeingRow) => <span className="sd-mono">{fmoney(r.balance)}</span> },
              { key: 'share', header: 'Share of outstanding', align: 'right', render: (r: AgeingRow) => <span className="sd-mono">{fpct(r.share)}</span> },
            ]}
            rows={[...rows, total]}
          />
        </>
      )}
    </div>
  );
}
