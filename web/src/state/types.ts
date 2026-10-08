export type ModelKey = 'lending' | 'rental';

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
  /** "YYYY-MM" or '' - limits the LTV Calculator's interest input to these cohorts (lending only). */
  ltvCohortFrom?: string;
  ltvCohortTo?: string;
}

export interface AnalysisResult {
  rows: number;
  cohorts: number;
  lossRate: number;
  ltv: number;
  status: 'idle' | 'running' | 'done';
}
