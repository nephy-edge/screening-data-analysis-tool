import type { MappingField } from '../state/types';

// The Lending template field keys are the engine's canonical column names
// (app.py LENDING_CONFIG.input_columns), NOT UI slugs - the backend builds the
// engine DataFrame from these exact keys, so they must match 1:1.
export const TEMPLATE_FIELDS: MappingField[] = [
  { key: 'Loan ID', label: 'Loan ID', required: true, description: 'Unique loan/account identifier' },
  { key: 'Disbursement Date', label: 'Disbursement Date', required: true, description: 'Date principal was lent' },
  { key: 'Expected Completion Date', label: 'Expected Completion Date', required: true, description: 'Scheduled maturity date' },
  { key: 'Principal Value', label: 'Principal Value', required: true, description: 'Original principal amount lent' },
  { key: 'Expected Interest', label: 'Expected Interest', required: true, description: 'Expected interest over the loan term' },
  { key: 'Gross Loan Value', label: 'Gross Loan Value', required: false, description: 'Gross value of the loan' },
  { key: 'Expected Fee', label: 'Expected Fee', required: false, description: 'Fee expected at origination' },
  { key: 'Total Paid', label: 'Total Paid', required: true, description: 'Cumulative payments received' },
  { key: 'Total Due', label: 'Total Due', required: false, description: 'Total outstanding amount due' },
  { key: 'Loan Status', label: 'Loan Status', required: false, description: 'Current status of the loan' },
  { key: 'Days Late', label: 'Days Late', required: false, description: 'Days past due' },
  { key: 'Term (days)', label: 'Term (days)', required: false, description: 'Loan term in days (overrides completion-date-derived tenor)' },
];

export const RENTAL_TEMPLATE_FIELDS: MappingField[] = [
  { key: 'contract_id', label: 'Contract ID', required: true, description: 'Unique contract identifier' },
  { key: 'asset_id', label: 'Asset ID', required: true, description: 'Unique asset identifier' },
  { key: 'start_date', label: 'Start Date', required: true, description: 'Lease start date' },
  { key: 'status', label: 'Status', required: true, description: 'Contract status (Active/Closed/Repaid)' },
  { key: 'downpayment', label: 'Downpayment', required: true, description: 'Initial downpayment amount' },
  { key: 'monthly_expected_payment', label: 'Monthly Expected Payment', required: true, description: 'Contractual monthly payment' },
  { key: 'total_paid', label: 'Total Paid', required: true, description: 'Cumulative payments received' },
  { key: 'cost_of_asset', label: 'Cost of Asset', required: true, description: 'Cost of the leased asset' },
  { key: 'expected_end_date', label: 'Expected End Date', required: false, description: 'Scheduled end date' },
  { key: 'closed_date', label: 'Closed Date', required: false, description: 'Actual close/termination date' },
  { key: 'total_contract_value', label: 'Total Contract Value', required: false, description: 'Total value of the contract' },
  { key: 'amount_expected_to_date', label: 'Amount Expected to Date', required: false, description: 'Expected collections to date' },
  { key: 'current_asset_value', label: 'Current Asset Value', required: false, description: 'Current value of the asset' },
  { key: 'recovery_date', label: 'Recovery Date', required: false, description: 'Asset recovery date' },
  { key: 'recovery_amount', label: 'Recovery Amount', required: false, description: 'Recovered amount' },
];

// Revolving Lending: the required fields are the Revolving Lending template's
// Outputs columns (A1:K1), under the same names. The bucket balances are
// optional - when all seven core ones are mapped the full roll-rate model runs,
// otherwise a 3-link approximation from these columns
// (scripts/revolving_analysis/transition_matrices.py).
export const REVOLVING_TEMPLATE_FIELDS: MappingField[] = [
  { key: 'report_month', label: 'report_month', required: true, description: 'Report month (one row per month)' },
  { key: 'number_of_customers', label: 'number_of_customers', required: true, description: 'Borrowers on book in the month' },
  { key: 'total_credit_line', label: 'total_credit_line', required: true, description: 'Sum of approved credit limits' },
  { key: 'interest_collected', label: 'interest_collected', required: true, description: 'Cash interest collected in the month' },
  { key: 'principal_collected', label: 'principal_collected', required: true, description: 'Cash principal repaid in the month' },
  { key: 'principal_issued', label: 'principal_issued', required: true, description: 'New principal drawn in the month' },
  { key: 'principal_outstanding', label: 'principal_outstanding', required: true, description: 'Closing principal outstanding, incl. defaulted stock' },
  { key: 'principal_outstanding-active', label: 'principal_outstanding-active', required: true, description: 'Balance not past due (New + Current)' },
  { key: 'principal_outstanding-par30-60', label: 'principal_outstanding-par30-60', required: true, description: 'Balance 1-60 days past due' },
  { key: 'principal_outstanding-par60-90', label: 'principal_outstanding-par60-90', required: true, description: 'Balance 61-90 days past due' },
  { key: 'principal_written-off', label: 'principal_written-off', required: true, description: 'Principal written off in the month (flow)' },
  { key: 'BAL_NEW', label: 'Balance: New', required: false, description: 'Balance of lines in their origination month (optional - enables the full roll-rate model)' },
  { key: 'BAL_CURRENT', label: 'Balance: Current', required: false, description: 'Balance not past due (optional - enables the full roll-rate model)' },
  { key: 'BAL_PAR_1_30', label: 'Balance: PAR 1-30', required: false, description: 'Balance 1-30 days past due (optional - enables the full roll-rate model)' },
  { key: 'BAL_PAR_31_60', label: 'Balance: PAR 31-60', required: false, description: 'Balance 31-60 days past due (optional - enables the full roll-rate model)' },
  { key: 'BAL_PAR_61_90', label: 'Balance: PAR 61-90', required: false, description: 'Balance 61-90 days past due (optional - enables the full roll-rate model)' },
  { key: 'BAL_PAR_91_120', label: 'Balance: PAR 91-120', required: false, description: 'Balance 91-120 days past due (optional - enables the full roll-rate model)' },
  { key: 'BAL_DEFAULT', label: 'Balance: Defaulted', required: false, description: 'Defaulted / written-off balance still held as stock (optional - enables the full roll-rate model)' },
  { key: 'BAL_PAR_121_150', label: 'Balance: PAR 121-150', required: false, description: 'Balance 121-150 days past due (0 if absent)' },
  { key: 'BAL_PAR_151_180', label: 'Balance: PAR 151-180', required: false, description: 'Balance 151-180 days past due (0 if absent)' },
  { key: 'BAL_PAR_180_PLUS', label: 'Balance: PAR 180+', required: false, description: 'Balance over 180 days past due (0 if absent)' },
  { key: 'DUP_LOAN_MONTHS', label: 'Duplicate Loan-Months', required: false, description: 'Data-quality: duplicate loan-month rows in the source' },
  { key: 'SOURCE_ROWS', label: 'Source Rows', required: false, description: 'Data-quality: source rows behind the month' },
  { key: 'MONTHS_FILLED_BY_CARRYFWD', label: 'Months Filled by Carry-Forward', required: false, description: 'Data-quality: loan-months carried forward' },
  { key: 'UNEXPLAINED', label: 'Unexplained Movement', required: false, description: 'Data-quality: balance movement not explained by flows' },
  { key: 'recoveries', label: 'recoveries', required: false, description: 'Cash recovered in the month on written-off balances (optional - lets LGD be derived)' },
];

// BaaS: one row per market; field keys are the BaasMarket fields
// (scripts/baas_analysis/engine.py) and the headers of GET /api/baas/template.
// Percent fields are whole numbers (5 = 5%).
const baas = (key: string, required: boolean, description: string): MappingField => ({
  key,
  label: key,
  required,
  description,
});
export const BAAS_TEMPLATE_FIELDS: MappingField[] = [
  baas('market', true, 'Market name (one row per market)'),
  baas('currency', true, 'Local currency code (USD if priced in USD)'),
  baas('fx_rate', true, 'LCY per USD (1 for USD)'),
  baas('price_per_swap', true, 'Revenue per swap, LCY'),
  baas('pack_cost_usd', true, 'Cost of one swap pack, USD'),
  baas('pack_capacity_kwh', true, 'Pack energy, kWh'),
  baas('lifetime_months', true, 'Battery lifetime, months of revenue (1-120)'),
  baas('batteries_per_swap', true, 'Batteries swapped per swap event'),
  baas('batteries_in_bike', false, 'Batteries in the bike per swap unit (default = batteries_per_swap)'),
  baas('batteries_per_bike', true, 'Batteries per bike incl. spares'),
  baas('station_capex_usd', true, 'Station capex per bike deployed, USD'),
  baas('station_life_years', false, 'Station lifetime, years (default 7)'),
  baas('swaps_per_day', true, 'Swaps per day per user'),
  baas('electricity_lcy_per_kwh', true, 'Electricity cost, LCY per kWh'),
  baas('station_rent_usd_month', true, 'Station rent per bike per month, USD'),
  baas('interest_rate_pct', true, 'Facility interest rate, %'),
  baas('facility_tenor_months', true, 'Facility tenor, months'),
  baas('facility_grace_months', false, 'Facility grace period, months'),
  baas('upfront_fee_pct', false, 'Facility upfront fee, %'),
  baas('wht_pct', false, 'Withholding tax on interest, %'),
  baas('hedging_pct', false, 'Annual hedging cost, %'),
  baas('signup_fee', false, 'User sign-up fee, LCY'),
  baas('signup_renewal_months', false, 'Sign-up renewal period, months (1000 = never)'),
  baas('salvage_pct', false, 'Salvage value, % of pack cost'),
  baas('station_install_usd', false, 'Station installation per bike, USD'),
  baas('install_life_years', false, 'Installation lifetime, years'),
  baas('station_infra_usd', false, 'Station infrastructure per bike, USD'),
  baas('infra_life_years', false, 'Infrastructure lifetime, years'),
  baas('station_capex_upfront', false, '1 = full station capex in month 0; 0 = allocated over battery life'),
  baas('revenue_days_per_month', false, 'Revenue days per month (default 30)'),
  baas('opex_days_per_month', false, 'Opex days per month (default 30)'),
  baas('commission_pct', false, 'Commission, % of usage revenue'),
  baas('repair_maintenance_pct', false, 'Repair & maintenance, % of usage revenue'),
  baas('insurance_pct', false, 'Insurance, % of pack cost p.a.'),
  baas('battery_it_usd_month', false, 'Battery IT (cloud/SIM) per month'),
  baas('other_opex_pct', false, 'Other opex incl. staff, % of usage revenue'),
  baas('fixed_opex_usd', false, 'Annual company fixed opex, USD (blank/0 = break-even n/a)'),
  baas('active_vehicles', false, 'Active vehicles today (break-even)'),
  baas('batteries_on_road', false, 'Batteries on road today (break-even)'),
];

export const DEMO_FILE = {
  name: 'sample_loan_tape.xlsx',
  columns: [
    'account_id',
    'funded_at',
    'status',
    'outstanding_balance',
    'loan_amount',
    'rate',
    'term',
    'paid_total',
    'write_off',
    'fee',
    'region',
    'originator',
  ],
  rowCount: 12854,
  preview: [
    ['account_id', 'funded_at', 'status', 'loan_amount', 'rate', 'term'],
    ['LN-0001', '2023-02-14', 'ACTIVE', '1500.00', '22.4', '12'],
    ['LN-0002', '2023-03-02', 'PAID OFF', '900.00', '19.9', '12'],
    ['LN-0003', '2023-03-28', 'IN ARREARS', '2400.00', '27.0', '18'],
    ['LN-0004', '2023-04-11', 'ACTIVE', '600.00', '16.5', '9'],
  ],
};
