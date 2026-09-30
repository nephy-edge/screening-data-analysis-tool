import { useAppStore } from '../state/AppStore';
import { Alert, Table, type TableColumn } from '../components/ui';
import type { BaasMarketResult } from '../api/engine';

type Row = Record<string, unknown>;

const isNum = (v: unknown) => typeof v === 'number' && Number.isFinite(v);
const fpct = (v: unknown) => (isNum(v) ? `${((v as number) * 100).toFixed(1)}%` : '—');
const fusd = (v: unknown, digits = 2) =>
  isNum(v) ? (v as number).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
const fint = (v: unknown) => (isNum(v) ? Math.round(v as number).toLocaleString() : '—');

const metric = (key: string, header: string, format: (v: unknown) => string): TableColumn<BaasMarketResult> => ({
  key,
  header,
  align: 'right',
  render: (r) => <span className="sd-mono">{format(r.metrics[key])}</span>,
});

/** One row per market: the UE sheet's headline lines (per swap-unit, USD). */
export function BaasResultsTab() {
  const { state } = useAppStore();
  const res = state.baasResponse;
  if (!res) return <Alert tone="info">Run analysis to see the results.</Alert>;
  const withIssues = res.markets.filter((m) => m.issues.length);
  const cols: TableColumn<BaasMarketResult>[] = [
    { key: 'market', header: 'Market' },
    metric('total_capex', 'Capex (USD)', (v) => fusd(v)),
    metric('total_revenue', 'Revenue / day', (v) => fusd(v)),
    metric('total_opex', 'Opex / day', (v) => fusd(v)),
    metric('cm1', 'CM1 / day', (v) => fusd(v)),
    metric('cm1_pct', 'CM1 %', fpct),
    metric('gross_irr', 'Gross IRR', fpct),
    metric('net_irr', 'Net IRR before financing', fpct),
    metric('financing_cost', 'Financing cost', fpct),
    metric('net_ue', 'Net UE after financing', fpct),
    {
      key: 'breakEven',
      header: 'Vehicles to break even',
      align: 'right',
      render: (r) =>
        r.issues.length ? (
          <span className="sd-muted">not run</span>
        ) : r.breakEven ? (
          <span className="sd-mono">{fint(r.breakEven.vehicles_to_break_even)}</span>
        ) : (
          <span className="sd-muted">n/a — fixed opex not provided</span>
        ),
    },
  ];
  return (
    <>
      {withIssues.map((m) => (
        <Alert key={m.market} tone="warning">
          {m.market} was not run: {m.issues.join(' ')}
        </Alert>
      ))}
      <h2 className="sd-h2">Unit economics per swap-unit (bike) — valued at {String(res.generalInputs.valuationDate)}</h2>
      <Table columns={cols} rows={res.markets} />
      <p className="sd-muted">
        IRRs are XIRR on the monthly cash flows (month 0 = capex). Net UE = net IRR before financing − interest (incl.
        WHT) − facility fee − hedging. Break-even = annual fixed opex ÷ (capex × net UE).
      </p>
    </>
  );
}

/** Monthly net cash flow per swap-unit, one column per market (USD). */
export function BaasCashFlowsTab() {
  const { state } = useAppStore();
  const res = state.baasResponse;
  if (!res) return <Alert tone="info">Run analysis to see the cash flows.</Alert>;
  if (!res.cashflows.length) return <Alert tone="warning">No market could be run — see the Results tab.</Alert>;
  const keys = Object.keys(res.cashflows[0]);
  const cols: TableColumn<Row>[] = keys.map((key, i) => ({
    key,
    header: key,
    align: i < 2 ? 'left' : 'right',
    render: (r) => <span className={i < 2 ? undefined : 'sd-mono'}>{i < 2 ? String(r[key]) : fusd(r[key])}</span>,
  }));
  return (
    <>
      <p className="sd-muted">Net cash flow before financing = revenue − operating costs; month 0 is the capex.</p>
      <Table columns={cols} rows={res.cashflows} />
    </>
  );
}
