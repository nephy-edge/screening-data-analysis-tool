export type ModelKey = 'lending' | 'rental' | 'revolving';

export interface MappingField {
  key: string;
  label: string;
  required: boolean;
  description: string;
}

export interface ColumnMapping {
  fieldKey: string;
  sourceColumn: string | null;
}

export interface GeneralInputs {
  extractionDate: string;
  daysAfterTerm: number;
  minLoansPerCohort: number;
  currency: string;
  /** "YYYY-MM" or '' - limits the LTV Calculator's interest and 95th %ile loss to these cohorts (lending only). */
  ltvCohortFrom?: string;
  ltvCohortTo?: string;
  /** Rental only: client status value -> Lendable status (Open / Closed / Paid-off). */
  statusMap?: Record<string, string>;
  /** Revolving only - null/undefined until the server defaults load (see
   * getRevolvingDefaults); names match web_api's RevolvingGeneralInputsBody. */
  revolving?: RevolvingGeneralInputs | null;
}

export interface RevolvingGeneralInputs {
  /** "YYYY-MM" or '' / null = the last month in the data. */
  asOfMonth: string | null;
  lgd: number;
  /** A window name from config.toml [revolving_defaults.roll_rate_windows]. */
  rollRateWindow: string;
  ratioWindowMonths: number;
  minOutstandingInSample: number;
  stressPercentile: number;
  denominatorLagMonths: number;
  laggedBlendWeight: number;
  lossRateBasis: 'Naive' | 'Blended' | 'Lagged';
  lgdBasis: 'Input' | 'Recoveries';
  costOfFundsPct: number;
  opexPct: number;
  advanceHaircut: number;
  backtestMinHistoryMonths: number;
  earlyWarningLookbackMonths: number;
  earlyWarningThreshold: number;
  scenarios: RevolvingScenario[];
  covenants: RevolvingCovenant[];
}

export interface RevolvingScenario {
  name: string;
  rollRateMultiplier: number;
  lgdMultiplier: number;
}

export interface RevolvingCovenant {
  /** A metric key from scripts/revolving_analysis/metrics.py (or coverage_perf / coverage_total / net_yield_annual). */
  metric: string;
  operator: '<=' | '>=';
  limit: number;
}

export interface AnalysisResult {
  rows: number;
  cohorts: number;
  lossRate: number;
  ltv: number;
  status: 'idle' | 'running' | 'done';
}
