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
  rollRateWindow: 'T3' | 'T6' | 'T12' | 'ALL';
  ratioWindowMonths: number;
  minOutstandingInSample: number;
  stressPercentile: number;
  denominatorLagMonths: number;
  laggedBlendWeight: number;
  lossRateBasis: 'Naive' | 'Blended' | 'Lagged';
}

export interface AnalysisResult {
  rows: number;
  cohorts: number;
  lossRate: number;
  ltv: number;
  status: 'idle' | 'running' | 'done';
}
