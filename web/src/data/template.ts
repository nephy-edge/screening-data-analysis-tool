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
