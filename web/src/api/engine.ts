/**
 * Engine API adapter — FastAPI backend.
 *
 * Talks to the FastAPI engine wrapper in web_api/main.py, which calls the
 * verified Python analysis engine (scripts/template_analysis). The dev-server
 * proxy in vite.config.ts forwards `/api/**` to `http://localhost:8000`.
 *
 * Status: LIVE. Both /api/analyse/lending and /api/analyse/rental accept
 * either a real upload (ingestFile + a column mapping) or pre-mapped
 * canonical rows directly; the UI drives the ingest path end to end.
 */

export interface AnalysisSummary {
  rows: number;
  cohorts: number;
  lossRate: number | null;
}

/** A single data-quality/variance finding from scripts/quality_checks.py -
 * `detail` (when present) is a small table of the flagged rows/cohorts. */
export interface QualityCheck {
  level: 'warning' | 'info';
  check_id?: string;
  message: string;
  detail?: Array<Record<string, unknown>>;
  detail_expander?: string;
}

/** One row of the Summary tab's "Cohort Loss Rates" chart: principal
 * disbursed across ALL loans in that cohort (the bars) plus the
 * matured-only loss rate (the line) - the same `matured_only=False`
 * cohort view app.py's Summary chart plots. */
export interface SummaryChartRow {
  Cohort: string;
  'Total Principal': number | null;
  'Loan Count': number | null;
  'Matured Count': number | null;
  'Loss Rate': number | null;
}

export interface AnalysisResponse {
  summary: AnalysisSummary;
  ue: Record<string, unknown>;
  ltv: Record<string, unknown>;
  /** Every matured-only monthly cohort - the Cohorts tab's own table, not
   * pre-filtered by min_loans_per_cohort (the tab applies its own adjustable
   * filter client-side, same as app.py's Cohorts tab). */
  cohorts: Array<Record<string, unknown>>;
  /** Same cohorts with matured_only=False - shown when the Cohorts tab's
   * "Matured loans only" toggle is unchecked. */
  cohortsAllLoans: Array<Record<string, unknown>>;
  chart: Array<Record<string, unknown>>;
  checks: QualityCheck[];
  /** Every figure on the Summary tab, from scripts/summary_metrics.py -
   * the same dict app.py's Streamlit Summary tab renders from. */
  summaryMetrics: Record<string, number | string | null>;
  summaryChart: SummaryChartRow[];
  generalInputs: Record<string, unknown>;
  /** UE Model sub-tab inputs (matured-loan aggregates + cost defaults) -
   * the browser derives every dependent figure/IRR from these when the user
   * edits the manual cost / loss-rate assumptions, mirroring app.py's
   * interactive _render_ue_model_ai_tab. */
  ueModel?: UeModelInputs;
}

/** Raw inputs served for the lending UE Model sub-tab - see
 * web_api/main.py's _ue_model_payload. Values are percentages (0..1) where
 * noted; the browser recomputes all derived figures/IRR from these. */
export interface UeModelInputs {
  averageInterestPct: number | null;
  averageFeePct: number | null;
  averageExpectedTerm: number | null;
  lossRateProxy: number | null;
  principal: number | null;
  revenue: number | null;
  originationIncome: number | null;
  actualLossRate: number | null;
  maturedPrincipal: number | null;
  maturedRevenue: number | null;
  maturedOriginationIncome: number | null;
  maturedOwed: number | null;
  maturedPaid: number | null;
  maturedTermM: number | null;
  nMatured: number;
  nTotal: number;
  maturedGbvPct: number | null;
  costDefaults: {
    upfront_cost_pct: number;
    ongoing_cost_pct: number;
    financing_cost_pct: number;
    facility_fee_pct: number;
    hedge_cost_pct: number;
  };
}

/** FastAPI's HTTPException error body shape: {"detail": "message"} - not
 * {"error": ...}. (Fixed here: every catch site below was reading a field
 * FastAPI never sends, so a real backend error - a validation message, "No
 * DEEPINFRA_API_KEY found", a missing-mapping detail - silently fell back
 * to a generic "request failed (503)" instead of reaching the user.) */
export interface AnalysisEngineError {
  detail: string;
}

// credentials: 'same-origin' (the fetch default) is explicit here because
// the session-cookie auth endpoints (/api/auth/*) rely on the browser
// sending and storing the remember-me cookie on same-origin requests.
async function request<T>(
  path: string,
  opts: { method?: 'GET' | 'POST'; body?: unknown; blob?: boolean; errorPrefix?: string } = {},
): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: opts.method ?? 'GET',
    credentials: 'same-origin',
    ...(opts.body !== undefined
      ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(opts.body) }
      : {}),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null)) as AnalysisEngineError | null;
    throw new Error(detail?.detail ?? `${opts.errorPrefix ?? 'Request'} failed (${res.status})`);
  }
  return (opts.blob ? res.blob() : res.json()) as Promise<T>;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', body, errorPrefix: 'Engine request' });
}

function get<T>(path: string): Promise<T> {
  return request<T>(path);
}

/** Upload a real CSV/XLSX file to the backend, which parses it server-side
 * (the same pandas path the Streamlit app uses) and returns its columns,
 * row count and a preview plus an ingestId used to run the engine. */
export interface IngestResponse {
  ingestId: string;
  name: string;
  columns: string[];
  rowCount: number;
  preview: string[][];
  /** Sheets the user can switch between (empty for a CSV), and which one
   * these columns/preview came from. */
  sheets: string[];
  sheet: string | null;
}

export async function ingestFile(file: File): Promise<IngestResponse> {
  const form = new FormData();
  form.append('file', file, file.name);
  const res = await fetch('/api/ingest', { method: 'POST', body: form });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null)) as AnalysisEngineError | null;
    throw new Error(detail?.detail ?? `File upload failed (${res.status})`);
  }
  return (await res.json()) as IngestResponse;
}

/** Re-parse an already-uploaded workbook at a different sheet, keeping the
 * same ingestId - the browser doesn't re-upload the file. */
export function selectIngestSheet(ingestId: string, sheet: string): Promise<IngestResponse> {
  return post<IngestResponse>(`/ingest/${encodeURIComponent(ingestId)}/sheet`, { sheet });
}

/** Combine two already-ingested files into one dataset - 'stack' appends the
 * second file's rows onto the first (two exports of the same shape), 'join'
 * adds the second file's columns matched by a key (e.g. a loan tape + a
 * separate raw payments file), left-joined so row count stays the primary
 * file's row count. See web_api/main.py's merge_ingest for the aggregation
 * rules. Returns a normal ingest response with a new ingestId; nothing
 * downstream needs to know the result came from two files. */
export function mergeIngest(input: {
  primaryIngestId: string;
  secondaryIngestId: string;
  mode: 'join' | 'stack';
  primaryKey?: string;
  secondaryKey?: string;
  aggregation?: 'sum' | 'last';
}): Promise<IngestResponse> {
  return post<IngestResponse>('/ingest/merge', input);
}

/** Tables fetched live from Snowflake via the warehouse Query API (app.py's
 * "Redshift (Warehouse)" data source, kept named "redshift" for the same
 * backward-compat reason - see handover_snowflake_api_link.md). */
export function getRedshiftConfig(): Promise<{ available: boolean }> {
  return get<{ available: boolean }>('/redshift/config');
}

/** Every schema in a database, for the schema dropdown. */
export function listRedshiftSchemas(database?: string): Promise<string[]> {
  return get<string[]>(`/redshift/schemas${database ? `?database=${encodeURIComponent(database)}` : ''}`);
}

/** Every table in database.schema, for the table dropdown once a schema is picked. */
export function listRedshiftTables(schema: string, database?: string): Promise<string[]> {
  const params = new URLSearchParams({ schema, ...(database ? { database } : {}) });
  return get<string[]>(`/redshift/tables?${params.toString()}`);
}

/** AI-translated filter: a plain-English description -> a SQL WHERE
 * condition, grounded in the target table's real column names (see
 * web_api/main.py's redshift_filter / ai_client._suggest_redshift_filter).
 * Returns `where: null` (not an error) when no AI key is configured or the
 * request was too vague - the caller falls back to a manually-typed filter. */
export function suggestRedshiftFilter(input: {
  schema: string;
  table: string;
  database?: string;
  request: string;
}): Promise<{ where: string | null }> {
  return post<{ where: string | null }>('/redshift/filter', {
    schemaName: input.schema,
    table: input.table,
    database: input.database,
    request: input.request,
  });
}

/** Start a table fetch under the Query API's schema allowlist (MAESTRO_SRC*,
 * MAESTRO_*_ANA, MAESTRO_*_MRT) - e.g. MAESTRO_SRC.SALMON.salmon_credit_lines
 * - as a background job and return its job id, so progress can be polled
 * instead of blocking on one request for as long as a big table takes to
 * transfer - that single-request wait is what silently timed out after
 * ~150s on SALMON_CREDIT_LINES with "fetch all rows" before this existed.
 * `where` (optional, e.g. "STATUS = 'ACTIVE'") matters most when `limit` is
 * set below the table's real row count - without it, a partial fetch gets
 * whatever rows Snowflake happened to scan first, not a chosen slice. */
export function startRedshiftTableFetch(input: {
  schema: string;
  table: string;
  database?: string;
  limit: number;
  where?: string;
}): Promise<{ jobId: string }> {
  return post<{ jobId: string }>('/redshift/table/start', {
    schemaName: input.schema,
    table: input.table,
    database: input.database,
    limit: input.limit,
    where: input.where?.trim() || undefined,
  });
}

export interface RedshiftFetchProgress {
  status: 'running' | 'done' | 'error';
  partitionsDone: number;
  partitionsTotal: number;
  error?: string;
}

/** Poll a job started by startRedshiftTableFetch. Once status is "done",
 * the response is also a full IngestResponse (ingestId, columns, preview,
 * ...), ready to feed into applyParsed the same as any other ingest. */
export function getRedshiftTableStatus(
  jobId: string,
  schema: string,
  table: string,
): Promise<RedshiftFetchProgress & Partial<IngestResponse>> {
  const params = new URLSearchParams({ jobId, schema, table });
  return get<RedshiftFetchProgress & Partial<IngestResponse>>(`/redshift/table/status?${params.toString()}`);
}

/** Mapped canonical rows for the current ingest + mapping - feeds the
 * Data-Checks "Data Input" nested tab (mirrors the Streamlit app's
 * `_render_data_input_grid`). */
export interface IngestData {
  columns: string[];
  rows: Array<Record<string, unknown>>;
  rowCount: number;
  truncated: boolean;
}

export function fetchIngestData(input: {
  ingestId: string;
  mapping: Record<string, string>;
  derivedColumns?: DerivedColumn[];
}): Promise<IngestData> {
  return post<IngestData>('/ingest-data', input);
}

/** A column derived server-side from other source columns via a safe
 * arithmetic formula (the "Ask AI to derive a column" feature). The formula is
 * written by the AI (or the user) and evaluated deterministically by the
 * backend - "Gross Loan Value - Principal Value", say. */
export interface DerivedColumn {
  name: string;
  formula: string;
}

export interface LendingIngest {
  ingestId: string;
  mapping: Record<string, string>;
  derivedColumns?: DerivedColumn[];
}

/** One Custom Visualizations chart card, pre-aggregated client-side (see
 * web/src/lib/chartAggregate.ts). Only consumed by the export endpoints -
 * embedded as a real Excel chart on the workbook's "Custom Charts" sheet. */
export interface ExportChartSpec {
  title: string;
  kind: 'Line' | 'Bar' | 'Area' | 'Scatter';
  xCol: string;
  yCol: string;
  colorCol: string | null;
  data: Array<Record<string, unknown>>;
}

export interface LendingAnalyseInput {
  rows?: Array<Record<string, unknown>>;
  ingest?: LendingIngest;
  generalInputs: {
    extractionDate: string | null;
    daysAfterTerm: number;
    minLoansPerCohort: number;
  };
  exportCharts?: ExportChartSpec[];
  /** Data-Checks "Deduplicate" action: drop to the first row per duplicated
   * Loan ID before analysing (mirrors the Streamlit app's checkbox). */
  dedupeById?: boolean;
  /** Cohorts tab's selection - cohort month labels (e.g. "2025-06-01") to
   * restrict the analysis to. Undefined/empty means every cohort, matching
   * today's behaviour - filters every downstream figure (Summary, UE, LTV,
   * checks, the cohorts table itself), not just what the Cohorts tab shows,
   * since the backend applies it before computing any of them. */
  selectedCohorts?: string[];
}

/** Run the lending LTV / Unit Economics analysis for a mapped dataset.
 * Pass either pre-mapped canonical `rows`, or an `ingest` reference (a real
 * uploaded file + column mapping) so the engine runs on the actual upload. */
export function runLendingAnalysis(input: LendingAnalyseInput): Promise<AnalysisResponse> {
  return post<AnalysisResponse>('/analyse/lending', input);
}

function postForBlob(path: string, body: unknown): Promise<Blob> {
  return request<Blob>(path, { method: 'POST', body, blob: true, errorPrefix: 'Export' });
}

/** Download the real Excel workbook for the current lending analysis (same
 * request shape as runLendingAnalysis) - a genuine .xlsx from the verified
 * export_workbook.py builder, not a client-side stub. */
export function exportLendingWorkbook(input: LendingAnalyseInput): Promise<Blob> {
  return postForBlob('/export/lending', input);
}

export interface RentalResponse {
  summary: { rows: number; cohorts: number };
  ue: Record<string, unknown>;
  ltv: Record<string, unknown>;
  churn: Record<string, unknown>;
  ts: Record<string, unknown>;
  general: Record<string, unknown>;
  assetView: Array<Record<string, unknown>>;
  cohortRows: Array<Record<string, unknown>>;
  /** High-level repayment curve - mob vs % avg collection (the rental UE
   * tab's curve chart + table). */
  curveRows: Array<Record<string, unknown>>;
  checks: QualityCheck[];
  summaryMetrics: Record<string, number | string | null>;
  generalInputs: Record<string, unknown>;
}

export interface RentalIngest {
  ingestId: string;
  mapping: Record<string, string>;
  derivedColumns?: DerivedColumn[];
}

export interface RentalAnalyseInput {
  rows?: Array<Record<string, unknown>>;
  ingest?: RentalIngest;
  generalInputs: {
    extractionDate: string | null;
    daysAfterTerm: number;
    monthsSinceDefault: number;
    minLoansPerCohort: number;
    usefulLifeYears: number;
    churnStressMultiplier: number;
    fundingCostPct: number;
    directCostPct: number;
    fxDevalPct: number | null;
    fxDiscount50pct: boolean;
    debtService180d: number | null;
  };
  exportCharts?: ExportChartSpec[];
  /** Data-Checks "Deduplicate" action: drop to the first row per duplicated
   * contract id before analysing (mirrors the Streamlit app's checkbox). */
  dedupeById?: boolean;
}

/** Run the Rental & Subscription analysis for a mapped dataset.
 * Pass either pre-mapped canonical `rows`, or an `ingest` reference (a real
 * uploaded file + column mapping) so the engine runs on the actual upload. */
export function runRentalAnalysis(input: RentalAnalyseInput): Promise<RentalResponse> {
  return post<RentalResponse>('/analyse/rental', input);
}

/** Download the real Excel workbook for the current rental analysis (same
 * request shape as runRentalAnalysis) - a genuine .xlsx from the verified
 * export_workbook.py builder, not a client-side stub. */
export function exportRentalWorkbook(input: RentalAnalyseInput): Promise<Blob> {
  return postForBlob('/export/rental', input);
}

export interface SuggestMappingResult {
  mapping: Record<string, string | null>;
  excludeSuggestion: { column: string; values: string[] } | null;
  activeStatusValues: string[] | null;
}

/** One supporting document's extracted text (see extractContextDocs) -
 * round-tripped back to suggestMapping/askAi so the AI sees it as context,
 * same as app.py's "Additional context for the AI" uploader. */
export interface ContextDocument {
  name: string;
  text: string;
  error: string | null;
}

/** Extract readable text from supporting documents (PDF, Excel, Word, text)
 * uploaded alongside the dataset - web_api/main.py's /api/context-docs runs
 * the same parsers as app.py's uploader (scripts/document_text.py). An
 * unsupported type or a parse failure comes back as a per-file `error`
 * rather than rejecting the whole request. */
export async function extractContextDocs(files: File[]): Promise<ContextDocument[]> {
  const form = new FormData();
  for (const file of files) form.append('files', file, file.name);
  const res = await fetch('/api/context-docs', { method: 'POST', body: form });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null)) as AnalysisEngineError | null;
    throw new Error(detail?.detail ?? `Document extraction failed (${res.status})`);
  }
  return (await res.json()) as ContextDocument[];
}

/** AI-assisted column-mapping auto-fill (ai_client._suggest_mapping via
 * DeepSeek V4 Flash). Returns an all-null mapping, not an error, when no
 * DEEPINFRA_API_KEY is configured server-side - callers should fall back to
 * a local heuristic in that case rather than treating it as a failure. */
export function suggestMapping(input: {
  modelKey: 'lending' | 'rental';
  columns: string[];
  context?: string;
  documents?: ContextDocument[];
  knownLoanStatusColumn?: string | null;
}): Promise<SuggestMappingResult> {
  return post<SuggestMappingResult>('/ai/suggest-mapping', input);
}

/** Ask AI to turn a natural-language "derive a column" request into a safe
 * formula over the file's real columns, e.g. "Expected Interest = Gross Loan
 * Value - Principal Value" -> {name, formula}. Returns nulls (not an error)
 * when no DEEPINFRA_API_KEY is configured server-side; the caller then tells
 * the user to type a formula manually. */
export function deriveColumn(input: {
  modelKey: 'lending' | 'rental';
  columns: string[];
  request: string;
}): Promise<{ name: string | null; formula: string | null }> {
  return post<{ name: string | null; formula: string | null }>('/ai/derive-column', input);
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

/** Ask AI about the current analysis. Sends only already-computed aggregate
 * statistics as context (never raw rows) - see web_api/main.py's
 * ai_chat_lending/ai_chat_rental. Rejects with a message from the backend
 * (e.g. "No DEEPINFRA_API_KEY found...") when the AI service isn't
 * configured or the request fails. */
export function askAi(
  modelKey: 'lending' | 'rental',
  input: (LendingAnalyseInput | RentalAnalyseInput) & {
    question: string;
    history: ChatMessage[];
    userContext?: string;
    documents?: ContextDocument[];
  },
): Promise<{ answer: string }> {
  return post<{ answer: string }>(`/ai/chat/${modelKey}`, input);
}

/** Result of the Data-Checks "Escalate flagged cohorts to analytics" action -
 * the drafted write-up (AI, or a plain fallback summary) plus whether it was
 * sent to Slack and any AI/send errors, mirroring app.py's escalation. */
export interface EscalateResult {
  borrowerName: string | null;
  message: string;
  aiWarning: string | null;
  sent: boolean;
  error: string | null;
}

/** Draft (AI, falling back to a plain summary) and send the escalation
 * write-up for an unexplained-variance finding to the configured Slack
 * webhook. `detail` is the check's detail rows: Cohort, <metricLabel>,
 * "Swing vs Prior Cohort (pp)", "Likely Contributing Factor(s)". */
export function escalateVariance(input: {
  modelKey: 'lending' | 'rental';
  modelName: string;
  metricLabel: string;
  filename: string;
  detail: Array<Record<string, unknown>>;
}): Promise<EscalateResult> {
  return post<EscalateResult>('/analyse/escalate', input);
}

/** Dropdown option lists for the LTV Calculator (mirrors the constants in
 * template_analysis.ltv_calculator). */
export interface LtvCalcOptions {
  dataInputSources: string[];
  countries: string[];
  fxRiskRatings: string[];
  segmentations: string[];
  hedgeTypes: string[];
  tenors: number[];
}

/** Every output of the LTV waterfall (template_analysis.ltv_calculator
 * compute_ltv), as decimals - format with fmtPct in the UI. */
export interface LtvCalcResult {
  tenor_bucket: number;
  historical_fx_deval: number;
  utilized_fx_deval: number;
  stress_fx_factor: number;
  stress_fx_deval: number;
  base_fx_deval: number;
  fx_slippage: number;
  roll_risk_high: number;
  roll_risk_low: number;
  selected_fx_deval_high: number;
  selected_fx_deval_low: number;
  credit_stress_factor: number;
  stress_loss_rate: number;
  minimum_stress_loss: number;
  selected_stress_loss: number;
  ltgbv_no_fx: number;
  ltgbv_high: number;
  ltgbv_low: number;
  ltv_no_fx: number;
  ltv_high: number;
  ltv_low: number;
  notes: string[];
}

export interface LtvCalculatorResponse {
  options: LtvCalcOptions;
  ok: boolean;
  warning: string | null;
  greenInterest: number | null;
  greenTermBucket: number | null;
  greenLoss: number | null;
  lossRateFallbackNotes: string[];
  result: LtvCalcResult | null;
  fxCountryData: { tenors: number[]; values: number[] } | null;
}

/** Run the LTV Calculator waterfall for the lending analysis - mirrors
 * app.py's "LTV Analysis" tab (tabs[3]). Green inputs (average interest,
 * weighted-avg term days, 95th %ile loss) come from the analysis response. */
export function computeLtvCalculator(input: {
  dataSource: string;
  country: string;
  macroFxRisk: string;
  segmentation: string;
  hedgeRate: number;
  rolledHedge: string;
  averageInterest: number | null;
  termDays: number | null;
  lossRate: number | null;
}): Promise<LtvCalculatorResponse> {
  return post<LtvCalculatorResponse>('/ltv-calculator', input);
}

/** Save/resume/share - backed by scripts/s3_store.py (`identity` is a
 * self-declared string for dummy sign-in, or the verified Google email, see
 * LoginScreen.tsx). */
export interface SavedProfileMeta {
  session_id: string;
  label: string;
  saved_at: number;
}

export function saveProfile(input: {
  identity: string;
  sessionId: string;
  label: string;
  ingestId: string;
  mapping: Record<string, string>;
  generalInputs: Record<string, unknown>;
  chatHistory: ChatMessage[];
  analysisContext?: string;
  /** The already-computed result for this deal (whichever of
   * AnalysisResponse/RentalResponse matches the current model), so
   * resuming later can render it straight away instead of re-running the
   * engine - see AppStore's saveSession. */
  analysisResult?: AnalysisResponse | RentalResponse | null;
}): Promise<{ ok: boolean }> {
  return post<{ ok: boolean }>('/profiles/save', input);
}

export function listProfiles(identity: string): Promise<SavedProfileMeta[]> {
  return get<SavedProfileMeta[]>(`/profiles?identity=${encodeURIComponent(identity)}`);
}

/** The restore payload for a loaded profile/share: a fresh ingestId for the
 * saved upload plus the mapping/inputs/chat history to pre-fill. */
export interface RestorePayload {
  ingestId: string;
  name: string;
  columns: string[];
  rowCount: number;
  preview: string[][];
  sheets: string[];
  sheet: string | null;
  modelKey: 'lending' | 'rental';
  mapping: Record<string, string>;
  generalInputs: Record<string, unknown>;
  chatHistory: ChatMessage[];
  label: string | null;
  analysisContext: string;
  /** Present when this deal was saved with its computed result attached -
   * lets the caller skip straight to the results screen instead of
   * re-running the engine (see AppStore's restoreProfile). */
  analysisResult: (AnalysisResponse | RentalResponse) | null;
}

export function loadProfile(sessionId: string, identity: string): Promise<RestorePayload> {
  return get<RestorePayload>(`/profiles/${encodeURIComponent(sessionId)}?identity=${encodeURIComponent(identity)}`);
}

export function createShare(input: {
  identity: string;
  ingestId: string;
  mapping: Record<string, string>;
  generalInputs: Record<string, unknown>;
  chatHistory: ChatMessage[];
  analysisContext?: string;
  analysisResult?: AnalysisResponse | RentalResponse | null;
}): Promise<{ token: string }> {
  return post<{ token: string }>('/share', input);
}

export function loadShare(token: string): Promise<RestorePayload> {
  return get<RestorePayload>(`/share/${encodeURIComponent(token)}`);
}

/** Real Google sign-in (identity only - no Drive access, see
 * web_api/main.py's GOOGLE_REACT_APP_SCOPE). Falls back to the
 * self-declared dummy identity in LoginScreen.tsx when not configured. */
export function getAuthConfig(): Promise<{ googleEnabled: boolean }> {
  return get<{ googleEnabled: boolean }>('/auth/config');
}

export function getGoogleLoginUrl(): Promise<{ url: string }> {
  return get<{ url: string }>('/auth/google/login-url');
}

export function googleCallback(code: string, state: string): Promise<{ email: string; name: string; gdrive: boolean }> {
  return post<{ email: string; name: string; gdrive: boolean }>('/auth/google/callback', { code, state });
}

/** Restore a remembered sign-in: returns the identity stored in the signed
 * session cookie the server set on the last successful sign-in, plus whether
 * this session has a live Google Drive connection (`gdrive`), so the app can
 * skip the login screen on load/refresh. Rejects (401) when there is no
 * valid cookie - the caller falls back to the normal login screen. */
export function getMe(): Promise<{ email: string; name: string; gdrive: boolean }> {
  return get<{ email: string; name: string; gdrive: boolean }>('/auth/me');
}

/** Clears the signed session cookie server-side (mirrors app.py's "Log out"
 * button). */
export function logout(): Promise<{ ok: boolean }> {
  return post<{ ok: boolean }>('/auth/logout', {});
}

/** The self-declared "temporary sign-in" fallback (shown when Google isn't
 * configured) - now server-side so the backend can set the same remember-me
 * cookie, making the dummy identity survive a refresh too. */
export function dummySignIn(email: string): Promise<{ email: string; name: string }> {
  return post<{ email: string; name: string }>('/auth/dummy', { email });
}

/** One importable file in the user's Google Drive, as listed by the backend
 * (which talks to the Drive API with the session's own token - the token
 * never reaches the browser). */
export interface GDriveFile {
  id: string;
  name: string;
  mimeType: string;
  modifiedTime: string;
}

/** List the signed-in user's Drive files that can be analysed
 * (spreadsheets / CSV / Excel), newest first - backs the "From Google
 * Drive" picker. Requires a Google sign-in (Drive scope); rejects otherwise. */
export function listGdriveFiles(): Promise<GDriveFile[]> {
  return get<GDriveFile[]>('/auth/gdrive/files');
}

/** Download the chosen Drive file (a Google Sheet is exported as a full
 * workbook) and register it as a normal ingest - same response shape as a
 * local upload, so the existing "loaded file" UI applies. */
export function importGdriveFile(fileId: string): Promise<IngestResponse> {
  return post<IngestResponse>('/auth/gdrive/import', { fileId });
}

/** Import a public Google Sheets / Google Drive share link (the doc must be
 * shared with "Anyone with the link can view" - no token needed) and register
 * it as a normal ingest. */
export function importGdriveLink(url: string): Promise<IngestResponse> {
  return post<IngestResponse>('/gdrive-link/import', { url });
}
