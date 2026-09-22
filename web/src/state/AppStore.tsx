import { createContext, useContext, useMemo, useReducer, type ReactNode } from 'react';
import {
  askAi,
  createShare,
  exportLendingWorkbook,
  exportRentalWorkbook,
  loadProfile,
  loadShare,
  runLendingAnalysis,
  runRentalAnalysis,
  saveProfile,
  type AnalysisResponse,
  type ChatMessage,
  type ContextDocument,
  type DerivedColumn,
  type ExportChartSpec,
  type RentalResponse,
  type RestorePayload,
} from '../api/engine';
import { aggregateChartData } from '../lib/chartAggregate';
import { friendlyError } from '../lib/friendlyError';
import type { AnalysisResult, ColumnMapping, GeneralInputs, ModelKey } from './types';

export interface RawFile {
  name: string;
  columns: string[];
  rowCount: number;
  preview: string[][];
  ingestId: string;
  /** Sheets this workbook offers (empty for a CSV) and the one currently
   * parsed - drives the "which sheet has your data?" picker. */
  sheets: string[];
  sheet: string | null;
}

/** One user-configured chart card in the Custom Visualizations tab -
 * mirrors app.py's _render_custom_visualizations_tab card state, scoped to
 * the aggregate data sources the JSON API actually returns (no loan-level
 * "Data Input" source - see AnalysisSession.tsx's VizTab for why). */
export interface ChartCardSpec {
  id: number;
  source: string;
  kind: 'Line' | 'Bar' | 'Area' | 'Scatter';
  xCol: string;
  yCol: string;
  colorCol: string | null;
}

export interface AppState {
  modelKey: ModelKey;
  auth: { user: string | null; gdrive: boolean };
  file: RawFile | null;
  /** Optional free-text notes about the dataset (product, currency, what
   * fields mean) - sent to the AI for mapping auto-fill and chat, mirroring
   * app.py's "Additional context for the AI" text area. */
  analysisContext: string;
  /** Supporting documents (PDF/Excel/Word/text) uploaded alongside the
   * notes above, already extracted to text - see DataInputScreen.tsx's
   * addContextDocs. Session-only, like app.py's context_documents: not
   * part of a saved/shared profile (scripts/snapshot_common.py doesn't
   * persist them either). */
  contextDocuments: ContextDocument[];
  mappings: ColumnMapping[];
  /** Columns derived from the file's own columns via a safe formula (the "Ask
   * AI to derive a column" feature) - computed server-side, so a derived name
   * becomes a normal source column the field mapping can point at. */
  derivedColumns: DerivedColumn[];
  generalInputs: GeneralInputs;
  analysis: AnalysisResult;
  analysisResponse: AnalysisResponse | null;
  rentalResponse: RentalResponse | null;
  analysisError: string | null;
  analysisRunning: boolean;
  chatHistory: ChatMessage[];
  chatRunning: boolean;
  chatError: string | null;
  customCharts: ChartCardSpec[];
  sessionId: string | null;
  dealLabel: string | null;
  saveRunning: boolean;
  saveError: string | null;
  savedAt: number | null;
  shareRunning: boolean;
  shareError: string | null;
  shareUrl: string | null;
  /** Data-Checks "Deduplicate" toggle - when true the analysis (and any
   * export) drop to the first row per duplicated ID, mirroring Streamlit. */
  dedupeActive: boolean;
  /** Cohorts tab's selection (Lending only) - cohort month labels to
   * restrict the analysis to. null means every cohort (the default,
   * matching today's behaviour before this existed). */
  selectedCohorts: string[] | null;
}

type Action =
  | { type: 'signIn'; name: string; gdrive?: boolean }
  | { type: 'signOut' }
  | { type: 'setFile'; file: RawFile | null }
  | { type: 'setAnalysisContext'; value: string }
  | { type: 'setContextDocuments'; documents: ContextDocument[] }
  | { type: 'setDerivedColumns'; columns: DerivedColumn[] }
  | { type: 'setMapping'; fieldKey: string; sourceColumn: string | null }
  | { type: 'setGeneralInputs'; inputs: Partial<GeneralInputs> }
  | { type: 'analysisStart' }
  | { type: 'analysisDone'; payload: AnalysisResponse }
  | { type: 'rentalDone'; payload: RentalResponse }
  | { type: 'analysisFail'; error: string }
  | { type: 'toggleModel'; modelKey: ModelKey }
  | { type: 'chatStart'; question: string }
  | { type: 'chatDone'; answer: string }
  | { type: 'chatFail'; error: string }
  | { type: 'addChartCard'; card: ChartCardSpec }
  | { type: 'updateChartCard'; id: number; patch: Partial<ChartCardSpec> }
  | { type: 'removeChartCard'; id: number }
  | { type: 'restoreProfile'; payload: RestorePayload; sessionId: string }
  | { type: 'saveStart' }
  | { type: 'saveDone' }
  | { type: 'saveFail'; error: string }
  | { type: 'shareStart' }
  | { type: 'shareDone'; url: string }
  | { type: 'shareFail'; error: string }
  | { type: 'setDedupe'; value: boolean }
  | { type: 'setSelectedCohorts'; cohorts: string[] | null };

const today = new Date().toISOString().slice(0, 10);

const initialState: AppState = {
  modelKey: 'lending',
  auth: { user: null, gdrive: false },
  file: null,
  analysisContext: '',
  contextDocuments: [],
  mappings: [],
  derivedColumns: [],
  generalInputs: {
    extractionDate: today,
    daysAfterTerm: 90,
    minLoansPerCohort: 10,
    currency: 'USD',
  },
  analysis: { rows: 0, cohorts: 0, lossRate: 0, ltv: 0, status: 'idle' },
  analysisResponse: null,
  rentalResponse: null,
  analysisError: null,
  analysisRunning: false,
  chatHistory: [],
  chatRunning: false,
  chatError: null,
  customCharts: [],
  sessionId: null,
  dealLabel: null,
  saveRunning: false,
  saveError: null,
  savedAt: null,
  shareRunning: false,
  shareError: null,
  shareUrl: null,
  dedupeActive: false,
  selectedCohorts: null,
};

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'signIn':
      return { ...state, auth: { user: action.name, gdrive: action.gdrive ?? false } };
    case 'signOut':
      return { ...state, auth: { user: null, gdrive: false } };
    case 'setFile':
      // A genuinely new upload gets its own session id (for save/resume) and
      // clears any previous deal's label/mappings/save state; restoreProfile
      // is the separate path for resuming/loading a saved deal, which keeps
      // (or reassigns, for a share) the id deliberately - see its case below.
      return {
        ...state,
        file: action.file,
        sessionId: action.file ? crypto.randomUUID() : null,
        dealLabel: action.file?.name ?? null,
        mappings: [],
        derivedColumns: [],
        savedAt: null,
        saveError: null,
        shareUrl: null,
        shareError: null,
        dedupeActive: false,
        selectedCohorts: null,
      };
    case 'setAnalysisContext':
      return { ...state, analysisContext: action.value };
    case 'setContextDocuments':
      return { ...state, contextDocuments: action.documents };
    case 'setDerivedColumns':
      return { ...state, derivedColumns: action.columns };
    case 'restoreProfile': {
      const { payload } = action;
      const mappings: ColumnMapping[] = Object.entries(payload.mapping).map(
        ([fieldKey, sourceColumn]) => ({ fieldKey, sourceColumn }),
      );
      // A profile saved with its computed result attached can render
      // straight away instead of re-running the engine (see saveSession) -
      // this is the fix for "why does loading a saved run take long": the
      // upload still gets re-ingested above (ingestId needs to stay valid
      // for any later export/chat/re-analysis), but the expensive engine
      // run itself is skipped when a cached result is available.
      const cached = payload.analysisResult;
      const isRental = payload.modelKey === 'rental';
      const analysisResponse = cached && !isRental ? (cached as AnalysisResponse) : null;
      const rentalResponse = cached && isRental ? (cached as RentalResponse) : null;
      const summary = cached?.summary as
        | { rows?: number; cohorts?: number; lossRate?: number }
        | undefined;
      return {
        ...state,
        file: {
          name: payload.name,
          columns: payload.columns,
          rowCount: payload.rowCount,
          preview: payload.preview,
          ingestId: payload.ingestId,
          sheets: payload.sheets,
          sheet: payload.sheet,
        },
        modelKey: payload.modelKey,
        analysisContext: payload.analysisContext ?? '',
        contextDocuments: [],
        mappings,
        derivedColumns: [],
        generalInputs: { ...state.generalInputs, ...(payload.generalInputs as Partial<GeneralInputs>) },
        chatHistory: payload.chatHistory,
        analysisResponse,
        rentalResponse,
        analysis: cached
          ? {
              rows: summary?.rows ?? 0,
              cohorts: summary?.cohorts ?? 0,
              lossRate: summary?.lossRate ?? 0,
              ltv: !isRental
                ? Number((analysisResponse as AnalysisResponse | null)?.ltv?.['Average Total Revenue %'] ?? 0) || 0
                : 0,
              status: 'done',
            }
          : { rows: 0, cohorts: 0, lossRate: 0, ltv: 0, status: 'idle' },
        customCharts: [],
        sessionId: action.sessionId,
        dealLabel: payload.label,
        savedAt: null,
        saveError: null,
        shareUrl: null,
        shareError: null,
        dedupeActive: false,
        selectedCohorts: null,
      };
    }
    case 'saveStart':
      return { ...state, saveRunning: true, saveError: null };
    case 'saveDone':
      return { ...state, saveRunning: false, savedAt: Date.now() };
    case 'saveFail':
      return { ...state, saveRunning: false, saveError: action.error };
    case 'shareStart':
      return { ...state, shareRunning: true, shareError: null };
    case 'shareDone':
      return { ...state, shareRunning: false, shareUrl: action.url };
    case 'shareFail':
      return { ...state, shareRunning: false, shareError: action.error };
    case 'setDedupe':
      return { ...state, dedupeActive: action.value };
    case 'setSelectedCohorts':
      return { ...state, selectedCohorts: action.cohorts };
    case 'setMapping':
      return {
        ...state,
        mappings: state.mappings.some((m) => m.fieldKey === action.fieldKey)
          ? state.mappings.map((m) =>
              m.fieldKey === action.fieldKey ? { ...m, sourceColumn: action.sourceColumn } : m,
            )
          : [...state.mappings, { fieldKey: action.fieldKey, sourceColumn: action.sourceColumn }],
      };
    case 'setGeneralInputs':
      return { ...state, generalInputs: { ...state.generalInputs, ...action.inputs } };
    case 'analysisStart':
      return { ...state, analysisRunning: true, analysisError: null };
    case 'analysisDone': {
      const { summary } = action.payload;
      return {
        ...state,
        analysisRunning: false,
        analysisError: null,
        analysisResponse: action.payload,
        analysis: {
          rows: summary.rows,
          cohorts: summary.cohorts,
          lossRate: summary.lossRate ?? 0,
          ltv: Number(action.payload.ltv?.['Average Total Revenue %'] ?? 0) || 0,
          status: 'done',
        },
      };
    }
    case 'rentalDone': {
      const { summary } = action.payload;
      return {
        ...state,
        analysisRunning: false,
        analysisError: null,
        rentalResponse: action.payload,
        analysis: {
          rows: summary.rows,
          cohorts: summary.cohorts,
          lossRate: 0,
          ltv: 0,
          status: 'done',
        },
      };
    }
    case 'analysisFail':
      return { ...state, analysisRunning: false, analysisError: action.error };
    case 'toggleModel':
      // Model switch resets analysis/mapping/chat halves, mirroring Streamlit.
      return {
        ...state,
        modelKey: action.modelKey,
        analysisResponse: null,
        rentalResponse: null,
        analysis: { rows: 0, cohorts: 0, lossRate: 0, ltv: 0, status: 'idle' },
        generalInputs: {
          ...state.generalInputs,
          daysAfterTerm: action.modelKey === 'rental' ? 0 : 90,
          minLoansPerCohort: action.modelKey === 'rental' ? 20 : 10,
        },
        chatHistory: [],
        chatError: null,
        customCharts: [],
      };
    case 'addChartCard':
      return { ...state, customCharts: [...state.customCharts, action.card] };
    case 'updateChartCard':
      return {
        ...state,
        customCharts: state.customCharts.map((c) =>
          c.id === action.id ? { ...c, ...action.patch } : c,
        ),
      };
    case 'removeChartCard':
      return { ...state, customCharts: state.customCharts.filter((c) => c.id !== action.id) };
    case 'chatStart':
      return {
        ...state,
        chatHistory: [...state.chatHistory, { role: 'user', content: action.question }],
        chatRunning: true,
        chatError: null,
      };
    case 'chatDone':
      return {
        ...state,
        chatHistory: [...state.chatHistory, { role: 'assistant', content: action.answer }],
        chatRunning: false,
      };
    case 'chatFail':
      return { ...state, chatRunning: false, chatError: action.error };
    default:
      return state;
  }
}

interface AppStoreValue {
  state: AppState;
  dispatch: React.Dispatch<Action>;
  analyse: (dedupeById?: boolean, selectedCohortsOverride?: string[] | null) => Promise<void>;
  exportWorkbook: () => Promise<void>;
  askAiQuestion: (question: string) => Promise<void>;
  saveSession: (label: string) => Promise<void>;
  /** Resolves to true when the resumed profile had a cached analysis
   * result attached, so the caller can jump straight to the results screen
   * instead of the mapping screen. */
  resumeProfile: (sessionId: string) => Promise<boolean>;
  resumeShare: (token: string) => Promise<boolean>;
  createShareLink: () => Promise<void>;
}

const AppContext = createContext<AppStoreValue | null>(null);

/** The named row-arrays available to the Custom Visualizations chart
 * builder for the active model - only what the analyse JSON response
 * already returns (no loan-level "Data Input" source; see VizTab in
 * AnalysisSession.tsx for why). Shared between the builder UI (rendering)
 * and exportWorkbook (building the export payload) so a source name always
 * means the same rows in both places. */
export function getChartDataSources(state: AppState): Record<string, Array<Record<string, unknown>>> {
  if (state.modelKey === 'rental') {
    return {
      Cohorts: state.rentalResponse?.cohortRows ?? [],
      'Asset View': state.rentalResponse?.assetView ?? [],
    };
  }
  return {
    Cohorts: state.analysisResponse?.cohorts ?? [],
    'Chart Data': state.analysisResponse?.chart ?? [],
  };
}

/** Trigger a browser "Save as" for a fetched Blob - no library needed, just
 * a transient object URL and a synthetic click on a hidden <a download>. */
function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);

  const value = useMemo<AppStoreValue>(() => {
    // Template field -> source column, from the mapping screen. Backend
    // rejects a missing required field, so no client-side gate here beyond
    // what ColumnMappingScreen already enforces before "Continue" unlocks.
    // Returns null when there's nothing to run against (no file loaded).
    const buildIngest = () => {
      if (!state.file) return null;
      const mapping: Record<string, string> = {};
      for (const m of state.mappings) {
        if (m.sourceColumn) mapping[m.fieldKey] = m.sourceColumn;
      }
      return {
        ingestId: state.file.ingestId,
        mapping,
        derivedColumns: state.derivedColumns.length ? state.derivedColumns : undefined,
      };
    };

    // Rental inputs the UI doesn't yet expose a control for - same defaults
    // RentalGeneralInputsBody uses server-side - kept in one place so
    // analyse() and exportWorkbook() send an identical payload for the same
    // run (the export must match what was just analysed).
    const buildRentalGeneralInputs = () => {
      const gi = state.generalInputs;
      return {
        extractionDate: gi.extractionDate || null,
        daysAfterTerm: gi.daysAfterTerm,
        monthsSinceDefault: 3,
        minLoansPerCohort: gi.minLoansPerCohort,
        usefulLifeYears: 3,
        churnStressMultiplier: 1.7,
        fundingCostPct: 0.13,
        directCostPct: 0.02,
        fxDevalPct: null,
        fxDiscount50pct: true,
        debtService180d: null,
      };
    };

    // Same DRY purpose as buildRentalGeneralInputs, kept for the lending
    // side so analyse()/exportWorkbook()/askAiQuestion() all send an
    // identical generalInputs payload for the same run.
    const buildLendingGeneralInputs = () => ({
      extractionDate: state.generalInputs.extractionDate,
      daysAfterTerm: state.generalInputs.daysAfterTerm,
      minLoansPerCohort: state.generalInputs.minLoansPerCohort,
    });

    // Model-aware version of the two builders above, so callers that don't
    // care which model is active (export/save/AI/share) don't each repeat
    // the `modelKey === 'rental' ? ... : ...` ternary.
    const buildGeneralInputs = () =>
      state.modelKey === 'rental' ? buildRentalGeneralInputs() : buildLendingGeneralInputs();

    // selectedCohortsOverride, not state.selectedCohorts: a caller that just
    // dispatched 'setSelectedCohorts' and immediately awaits analyse() (the
    // Cohorts tab's "Apply selection" button) would otherwise read a STALE
    // value here - dispatch schedules the state update, it doesn't apply it
    // synchronously, so this closure's own `state` is still the pre-dispatch
    // snapshot until React re-renders. Passing the value explicitly (same
    // reason dedupeById is a parameter, not read from state.dedupeActive)
    // sidesteps that entirely. undefined (the default) falls back to
    // state.selectedCohorts, for callers that aren't racing a fresh dispatch.
    const analyse = async (dedupeById = false, selectedCohortsOverride?: string[] | null) => {
      const ingest = buildIngest();
      if (!ingest) {
        dispatch({ type: 'analysisFail', error: 'No file loaded — go back and upload one.' });
        return;
      }
      const selectedCohorts =
        selectedCohortsOverride !== undefined ? selectedCohortsOverride : state.selectedCohorts;

      dispatch({ type: 'analysisStart' });
      dispatch({ type: 'setDedupe', value: dedupeById });
      try {
        if (state.modelKey === 'rental') {
          const payload = await runRentalAnalysis({
            ingest,
            generalInputs: buildRentalGeneralInputs(),
            dedupeById,
          });
          dispatch({ type: 'rentalDone', payload });
        } else {
          const payload = await runLendingAnalysis({
            ingest,
            generalInputs: buildLendingGeneralInputs(),
            dedupeById,
            selectedCohorts: selectedCohorts ?? undefined,
          });
          dispatch({ type: 'analysisDone', payload });
        }
      } catch (err) {
        dispatch({
          type: 'analysisFail',
          error: friendlyError(err instanceof Error ? err.message : 'Engine request failed'),
        });
      }
    };

    // Every visible chart card is included in the export (no separate "add
    // to export" toggle, unlike app.py's per-card queue) - re-runs the same
    // aggregation the card renders with, keyed to the real x/y/color column
    // names _write_custom_charts_sheet expects.
    const buildExportCharts = (): ExportChartSpec[] => {
      const sources = getChartDataSources(state);
      return state.customCharts
        .filter((c) => c.xCol && c.yCol)
        .map((c) => ({
          title: `${c.yCol} by ${c.xCol}${c.colorCol ? ` (${c.colorCol})` : ''}`,
          kind: c.kind,
          xCol: c.xCol,
          yCol: c.yCol,
          colorCol: c.colorCol,
          data: aggregateChartData(sources[c.source] ?? [], c.xCol, c.yCol, c.colorCol),
        }));
    };

    const exportWorkbook = async () => {
      const ingest = buildIngest();
      if (!ingest) {
        dispatch({ type: 'analysisFail', error: 'No file loaded — go back and upload one.' });
        return;
      }
      try {
        const exportCharts = buildExportCharts();
        const dedupeById = state.dedupeActive;
        if (state.modelKey === 'rental') {
          const blob = await exportRentalWorkbook({
            ingest,
            generalInputs: buildRentalGeneralInputs(),
            exportCharts,
            dedupeById,
          });
          downloadBlob(blob, 'SC_Analysis_Rental.xlsx');
        } else {
          const blob = await exportLendingWorkbook({
            ingest,
            generalInputs: buildLendingGeneralInputs(),
            exportCharts,
            dedupeById,
            selectedCohorts: state.selectedCohorts ?? undefined,
          });
          downloadBlob(blob, 'SC_Analysis_Lending.xlsx');
        }
      } catch (err) {
        dispatch({
          type: 'analysisFail',
          error: err instanceof Error ? err.message : 'Export failed',
        });
      }
    };

    const askAiQuestion = async (question: string) => {
      const ingest = buildIngest();
      if (!ingest) {
        dispatch({ type: 'chatFail', error: 'No file loaded — go back and upload one.' });
        return;
      }
      const history = state.chatHistory;
      dispatch({ type: 'chatStart', question });
      try {
        const { answer } = await askAi(state.modelKey, {
          ingest,
          generalInputs:
            buildGeneralInputs(),
          question,
          history,
          userContext: state.analysisContext,
          documents: state.contextDocuments,
        });
        dispatch({ type: 'chatDone', answer });
      } catch (err) {
        dispatch({
          type: 'chatFail',
          error: err instanceof Error ? err.message : 'AI request failed',
        });
      }
    };

    const saveSession = async (label: string) => {
      const ingest = buildIngest();
      if (!ingest || !state.sessionId || !state.auth.user) {
        dispatch({ type: 'saveFail', error: 'No file loaded — go back and upload one.' });
        return;
      }
      dispatch({ type: 'saveStart' });
      try {
        await saveProfile({
          sessionId: state.sessionId,
          label,
          ingestId: ingest.ingestId,
          mapping: ingest.mapping,
          generalInputs:
            buildGeneralInputs(),
          chatHistory: state.chatHistory,
          analysisContext: state.analysisContext,
          analysisResult: state.modelKey === 'rental' ? state.rentalResponse : state.analysisResponse,
        });
        dispatch({ type: 'saveDone' });
      } catch (err) {
        dispatch({ type: 'saveFail', error: err instanceof Error ? err.message : 'Save failed' });
      }
    };

    // Resuming a deal you saved yourself keeps the same session id - a
    // later Save updates that record in place instead of creating a
    // duplicate (mirrors app.py's "Resume my saved session" picker).
    const resumeProfile = async (sessionId: string) => {
      if (!state.auth.user) throw new Error('Not signed in.');
      const payload = await loadProfile(sessionId);
      dispatch({ type: 'restoreProfile', payload, sessionId });
      return payload.analysisResult != null;
    };

    // A shared link is someone else's deal: resuming it gets a fresh
    // session id, so if the viewer later saves, it becomes their own new
    // deal rather than overwriting the sharer's link (see app.py's
    // "saved to your own account only, never back into the shared link").
    const resumeShare = async (token: string) => {
      const payload = await loadShare(token);
      dispatch({ type: 'restoreProfile', payload, sessionId: crypto.randomUUID() });
      return payload.analysisResult != null;
    };

    const createShareLink = async () => {
      const ingest = buildIngest();
      if (!ingest || !state.auth.user) {
        dispatch({ type: 'shareFail', error: 'No file loaded — go back and upload one.' });
        return;
      }
      dispatch({ type: 'shareStart' });
      try {
        const { token } = await createShare({
          ingestId: ingest.ingestId,
          mapping: ingest.mapping,
          generalInputs:
            buildGeneralInputs(),
          chatHistory: state.chatHistory,
          analysisContext: state.analysisContext,
          analysisResult: state.modelKey === 'rental' ? state.rentalResponse : state.analysisResponse,
        });
        const url = `${window.location.origin}${window.location.pathname}?share=${token}`;
        dispatch({ type: 'shareDone', url });
      } catch (err) {
        dispatch({ type: 'shareFail', error: err instanceof Error ? err.message : 'Share failed' });
      }
    };

    return {
      state,
      dispatch,
      analyse,
      exportWorkbook,
      askAiQuestion,
      saveSession,
      resumeProfile,
      resumeShare,
      createShareLink,
    };
  }, [state]);

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useAppStore() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useAppStore must be used within AppProvider');
  return ctx;
}
