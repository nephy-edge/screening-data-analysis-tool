import { useEffect, useRef, useState } from 'react';
import { useAppStore } from '../state/AppStore';
import { Button, Alert, Input, Select, Table, type TableColumn } from '../components/ui';
import {
  extractContextDocs,
  getRedshiftTableStatus,
  startRedshiftTableFetch,
  suggestRedshiftFilter,
  getAuthConfig,
  getGoogleLoginUrl,
  getRedshiftConfig,
  ingestFile,
  importGdriveFile,
  importGdriveLink,
  listGdriveFiles,
  listProfiles,
  listRedshiftSchemas,
  listRedshiftTables,
  mergeIngest,
  selectIngestSheet,
  type GDriveFile,
  type IngestResponse,
  type SavedProfileMeta,
} from '../api/engine';
import type { ModelKey } from '../state/types';

interface IngestProps {
  onNext: () => void;
  modelKey: ModelKey;
  /** Navigates to the standalone JSON-upload view (a pre-computed analysis
   * result, e.g. from the Snowflake pipeline) instead of the normal
   * ingest/mapping/inputs flow this screen otherwise drives. */
  onJsonUpload: () => void;
  /** Called after resuming a saved run - jumps straight to the results
   * screen when that run had a cached analysis result attached (see
   * resumeProfile), otherwise behaves like onNext (goes to mapping). */
  onResumed: (hasCachedResult: boolean) => void;
}

type SourceKind = 'file' | 'link' | 'drive' | 'resume' | 'redshift' | 'json';

// The warehouse API itself has no row cap; this is just the number sent when
// "Fetch all rows" is checked, since the request still needs a limit value.
const REDSHIFT_ALL_ROWS_LIMIT = 1_000_000_000;

export function DataInputScreen({ onNext, onJsonUpload, onResumed }: Omit<IngestProps, 'modelKey'>) {
  const { state, dispatch, resumeProfile } = useAppStore();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<SavedProfileMeta[]>([]);
  const [resuming, setResuming] = useState(false);
  const [gsUrl, setGsUrl] = useState('');
  const [gsBusy, setGsBusy] = useState(false);
  const [driveOpen, setDriveOpen] = useState(false);
  const [driveFiles, setDriveFiles] = useState<GDriveFile[]>([]);
  const [driveBusy, setDriveBusy] = useState(false);
  const [driveSel, setDriveSel] = useState('');
  const [googleEnabled, setGoogleEnabled] = useState(false);
  const [source, setSource] = useState<SourceKind>('file');
  const [redshiftAvailable, setRedshiftAvailable] = useState(false);
  const [redshiftLimit, setRedshiftLimit] = useState(100_000);
  const [redshiftAllRows, setRedshiftAllRows] = useState(false);
  // Filter - matters most once a table has more real rows than the fetch
  // cap, so "fetch all rows" lands on a chosen slice (a date range, a
  // status) instead of whatever rows Snowflake scans first. A plain-English
  // description is AI-translated into the actual SQL WHERE condition, shown
  // here so it can be reviewed/edited before the fetch ever runs.
  const [redshiftFilterRequest, setRedshiftFilterRequest] = useState('');
  const [redshiftFilterBusy, setRedshiftFilterBusy] = useState(false);
  const [redshiftWhere, setRedshiftWhere] = useState('');
  const [redshiftBusy, setRedshiftBusy] = useState(false);
  const [redshiftProgress, setRedshiftProgress] = useState<{ done: number; total: number } | null>(null);
  const [redshiftError, setRedshiftError] = useState<string | null>(null);
  const [redshiftSchemas, setRedshiftSchemas] = useState<string[]>([]);
  const [redshiftSchema, setRedshiftSchema] = useState('');
  const [redshiftSchemasBusy, setRedshiftSchemasBusy] = useState(false);
  const [redshiftTables, setRedshiftTables] = useState<string[]>([]);
  const [redshiftTable, setRedshiftTable] = useState('');
  const [redshiftTablesBusy, setRedshiftTablesBusy] = useState(false);
  const docsInputRef = useRef<HTMLInputElement>(null);
  const [docsBusy, setDocsBusy] = useState(false);
  const [docsError, setDocsError] = useState<string | null>(null);

  // "+ Merge another file" - combine a second file into the primary one
  // before mapping, either stacked as more rows (two exports of the same
  // shape) or joined as more columns matched by a key (e.g. a loan tape + a
  // raw payments file). See mergeIngest's docstring.
  const mergeInputRef = useRef<HTMLInputElement>(null);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mergeMode, setMergeMode] = useState<'join' | 'stack'>('stack');
  // Multi-select: pick several secondary files at once (e.g. numbered export
  // parts) instead of doing "choose file -> merge -> repeat" one at a time.
  // They're folded into the primary file one after another, all using the
  // same mode/keys - realistic since a batch like this shares one schema.
  const [secondaryFiles, setSecondaryFiles] = useState<IngestResponse[]>([]);
  const [secondaryBusy, setSecondaryBusy] = useState(false);
  const [primaryKey, setPrimaryKey] = useState('');
  const [secondaryKey, setSecondaryKey] = useState('');
  const [aggregation, setAggregation] = useState<'sum' | 'last'>('sum');
  const [mergeBusy, setMergeBusy] = useState(false);
  const [mergeError, setMergeError] = useState<string | null>(null);
  const [mergeProgress, setMergeProgress] = useState<{ done: number; total: number; rows: number } | null>(
    null,
  );

  useEffect(() => {
    if (state.auth.gdrive) return;
    getAuthConfig()
      .then((c) => setGoogleEnabled(c.googleEnabled))
      .catch(() => setGoogleEnabled(false));
  }, [state.auth.gdrive]);

  useEffect(() => {
    if (!state.auth.user) return;
    listProfiles(state.auth.user).then(setProfiles).catch(() => setProfiles([]));
  }, [state.auth.user]);

  // Lending only - the warehouse API exposes lending loan tapes, not Rental
  // & Subscription contract tapes (mirrors app.py's own is_lending gate).
  useEffect(() => {
    if (state.modelKey !== 'lending') {
      setRedshiftAvailable(false);
      return;
    }
    getRedshiftConfig()
      .then((c) => setRedshiftAvailable(c.available))
      .catch(() => setRedshiftAvailable(false));
  }, [state.modelKey]);

  useEffect(() => {
    if (source !== 'redshift' || !redshiftAvailable || redshiftSchemas.length > 0) return;
    setRedshiftSchemasBusy(true);
    listRedshiftSchemas()
      .then(setRedshiftSchemas)
      .catch((err) => setRedshiftError(err instanceof Error ? err.message : 'Could not list schemas.'))
      .finally(() => setRedshiftSchemasBusy(false));
  }, [source, redshiftAvailable, redshiftSchemas.length]);

  useEffect(() => {
    if (!redshiftSchema) {
      setRedshiftTables([]);
      setRedshiftTable('');
      return;
    }
    setRedshiftTablesBusy(true);
    setRedshiftTable('');
    listRedshiftTables(redshiftSchema)
      .then(setRedshiftTables)
      .catch((err) => setRedshiftError(err instanceof Error ? err.message : 'Could not list tables.'))
      .finally(() => setRedshiftTablesBusy(false));
  }, [redshiftSchema]);

  const resume = async (sessionId: string) => {
    setBusy(true);
    setResuming(true);
    setError(null);
    try {
      const hasCachedResult = await resumeProfile(sessionId);
      onResumed(hasCachedResult);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load that saved session.');
    } finally {
      setBusy(false);
      setResuming(false);
    }
  };

  const applyParsed = (parsed: IngestResponse) => {
    dispatch({
      type: 'setFile',
      file: {
        name: parsed.name,
        columns: parsed.columns,
        rowCount: parsed.rowCount,
        preview: parsed.preview,
        ingestId: parsed.ingestId,
        sheets: parsed.sheets,
        sheet: parsed.sheet,
      },
    });
    setNotice(
      `Loaded ${parsed.name}${parsed.sheet ? ` (sheet "${parsed.sheet}")` : ''} — ` +
        `${parsed.rowCount.toLocaleString()} rows, ${parsed.columns.length} columns.`,
    );
  };

  const loadFile = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      applyParsed(await ingestFile(file));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read that file.');
      setNotice(null);
    } finally {
      setBusy(false);
    }
  };

  // Switching sheets re-parses the upload the server already has (no
  // re-upload) and resets the mapping, since a different sheet means
  // different columns.
  const changeSheet = async (sheet: string) => {
    if (!state.file) return;
    setBusy(true);
    setError(null);
    try {
      applyParsed(await selectIngestSheet(state.file.ingestId, sheet));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read that sheet.');
    } finally {
      setBusy(false);
    }
  };

  const onDrop = (files: FileList) => {
    const file = files[0];
    if (!file) return;
    void loadFile(file);
  };

  const addSecondaryFiles = async (files: FileList) => {
    setSecondaryBusy(true);
    setMergeError(null);
    try {
      const parsed = await Promise.all(Array.from(files).map((f) => ingestFile(f)));
      setSecondaryFiles((prev) => [...prev, ...parsed]);
      setSecondaryKey('');
    } catch (err) {
      setMergeError(err instanceof Error ? err.message : 'Could not read one of those files.');
    } finally {
      setSecondaryBusy(false);
      if (mergeInputRef.current) mergeInputRef.current.value = '';
    }
  };

  const removeSecondaryFile = (ingestId: string) =>
    setSecondaryFiles((prev) => prev.filter((f) => f.ingestId !== ingestId));

  // Folds the secondary files into the primary one at a time (each merge's
  // output becomes the next one's primary), so the same mode/keys apply
  // across the whole batch - a running row count doubles as progress
  // feedback for a large multi-part upload.
  const doMerge = async () => {
    if (!state.file || secondaryFiles.length === 0) return;
    if (mergeMode === 'join' && (!primaryKey || !secondaryKey)) return;
    setMergeBusy(true);
    setMergeError(null);
    setMergeProgress({ done: 0, total: secondaryFiles.length, rows: state.file.rowCount });
    try {
      let primaryIngestId = state.file.ingestId;
      let result: IngestResponse | null = null;
      for (let i = 0; i < secondaryFiles.length; i++) {
        result = await mergeIngest({
          primaryIngestId,
          secondaryIngestId: secondaryFiles[i].ingestId,
          mode: mergeMode,
          ...(mergeMode === 'join' ? { primaryKey, secondaryKey, aggregation } : {}),
        });
        primaryIngestId = result.ingestId;
        setMergeProgress({ done: i + 1, total: secondaryFiles.length, rows: result.rowCount });
      }
      if (result) applyParsed(result);
      setMergeOpen(false);
      setSecondaryFiles([]);
      setPrimaryKey('');
      setSecondaryKey('');
    } catch (err) {
      setMergeError(err instanceof Error ? err.message : 'Could not merge those files.');
    } finally {
      setMergeBusy(false);
      setMergeProgress(null);
    }
  };

  // "Google Sheets / Drive link" source: paste a share link, the server
  // downloads the (public) doc and registers it as a normal ingest.
  const importLink = async () => {
    const url = gsUrl.trim();
    if (!url) return;
    setGsBusy(true);
    setError(null);
    try {
      applyParsed(await importGdriveLink(url));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not import that link.');
      setNotice(null);
    } finally {
      setGsBusy(false);
    }
  };

  // "From Google Drive" source: fetch the session's Drive file list (server
  // holds the Drive-scoped token; it never reaches the browser) and let the
  // user pick one to import.
  const openDrive = async () => {
    if (driveOpen) {
      setDriveOpen(false);
      return;
    }
    setDriveOpen(true);
    setDriveBusy(true);
    setError(null);
    try {
      const files = await listGdriveFiles();
      setDriveFiles(files);
      setDriveSel(files.length ? files[0].id : '');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not list your Google Drive.');
      setDriveOpen(false);
    } finally {
      setDriveBusy(false);
    }
  };

  const importDriveFile = async () => {
    if (!driveSel) return;
    setDriveBusy(true);
    setError(null);
    try {
      applyParsed(await importGdriveFile(driveSel));
      setDriveOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not import that Drive file.');
      setNotice(null);
    } finally {
      setDriveBusy(false);
    }
  };

  // Reconnect a Google session whose Drive token was lost (e.g. the backend
  // restarted - tokens live in server memory) - same OAuth flow as the
  // login screen, now carrying the Drive scope.
  const connectDrive = async () => {
    setDriveBusy(true);
    setError(null);
    try {
      const { url } = await getGoogleLoginUrl();
      window.location.href = url;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start Google sign-in.');
      setDriveBusy(false);
    }
  };

  // AI-translates the plain-English filter description into a SQL WHERE
  // condition, populating (never auto-running) redshiftWhere so it's always
  // reviewed/edited before a fetch actually uses it.
  const suggestFilter = async () => {
    if (!redshiftSchema || !redshiftTable || !redshiftFilterRequest.trim()) return;
    setRedshiftFilterBusy(true);
    setRedshiftError(null);
    try {
      const { where } = await suggestRedshiftFilter({
        schema: redshiftSchema,
        table: redshiftTable,
        request: redshiftFilterRequest.trim(),
      });
      if (where) {
        setRedshiftWhere(where);
      } else {
        setRedshiftError("Couldn't translate that into a filter - try rephrasing, or type the SQL condition directly below.");
      }
    } catch (err) {
      setRedshiftError(err instanceof Error ? err.message : "Couldn't suggest a filter.");
    } finally {
      setRedshiftFilterBusy(false);
    }
  };

  // "From Snowflake" source: fetch any table under the warehouse's schema
  // allowlist straight from the warehouse via the Redshift Query API
  // (app.py's identical data source) - server-side, registers as a normal
  // ingest.
  const fetchRedshift = async () => {
    if (!redshiftSchema || !redshiftTable) return;
    setRedshiftBusy(true);
    setRedshiftError(null);
    setRedshiftProgress({ done: 0, total: 1 });
    const limit = redshiftAllRows ? REDSHIFT_ALL_ROWS_LIMIT : redshiftLimit;
    try {
      const { jobId } = await startRedshiftTableFetch({
        schema: redshiftSchema,
        table: redshiftTable,
        limit,
        where: redshiftWhere,
      });
      // Poll for real progress (partitions fetched so far) instead of one
      // blocking request - a big "fetch all rows" table can take minutes to
      // transfer, and a single request that long used to silently time out
      // (see startRedshiftTableFetch's docstring).
      for (;;) {
        const poll = await getRedshiftTableStatus(jobId, redshiftSchema, redshiftTable);
        setRedshiftProgress({ done: poll.partitionsDone, total: poll.partitionsTotal });
        if (poll.status === 'error') throw new Error(poll.error || "Couldn't fetch that table.");
        if (poll.status === 'done') {
          applyParsed(poll as unknown as IngestResponse);
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 1200));
      }
    } catch (err) {
      setRedshiftError(err instanceof Error ? err.message : "Couldn't fetch that table.");
    } finally {
      setRedshiftBusy(false);
      setRedshiftProgress(null);
    }
  };

  // Supporting documents for "Additional context for the AI" - extracted
  // server-side (scripts/document_text.py, shared with app.py's uploader)
  // and appended to the running list; a bad file surfaces as a per-doc
  // error (see the list rendering below) rather than failing the batch.
  const addContextDocs = async (files: FileList) => {
    const list = Array.from(files);
    if (list.length === 0) return;
    setDocsBusy(true);
    setDocsError(null);
    try {
      const extracted = await extractContextDocs(list);
      dispatch({
        type: 'setContextDocuments',
        documents: [...state.contextDocuments, ...extracted],
      });
    } catch (err) {
      setDocsError(err instanceof Error ? err.message : 'Could not extract those documents.');
    } finally {
      setDocsBusy(false);
      if (docsInputRef.current) docsInputRef.current.value = '';
    }
  };

  const removeContextDoc = (name: string) =>
    dispatch({
      type: 'setContextDocuments',
      documents: state.contextDocuments.filter((d) => d.name !== name),
    });

  const previewCols: TableColumn<string[]>[] = (state.file?.columns ?? []).map((c) => ({
    key: c,
    header: c,
    mono: true,
    render: (row) => row[state.file!.columns.indexOf(c)] ?? '',
  }));

  return (
    <div>
      {!state.file ? (
        <>
          <h2 className="sd-h2">Data source</h2>

          <div className="sd-field" style={{ maxWidth: 320 }}>
            <span>Where is the data?</span>
            <Select
              aria-label="Where is the data?"
              value={source}
              onChange={(e) => {
                const next = e.target.value as SourceKind;
                // "Pre-computed analysis (JSON)" is a navigation shortcut, not
                // a real ingest source this screen handles itself - jump
                // straight to JsonUploadScreen instead of setting `source`
                // (which would otherwise render a matching content block
                // below, same as every other option does).
                if (next === 'json') {
                  onJsonUpload();
                  return;
                }
                setSource(next);
              }}
            >
              <option value="file">Upload a file (Excel/CSV)</option>
              <option value="link">Google Sheets / Drive link</option>
              <option value="drive">Google Drive</option>
              {profiles.length > 0 && <option value="resume">Resume a saved session</option>}
              <option value="redshift" disabled={!redshiftAvailable}>
                From Snowflake {redshiftAvailable ? '' : '— not configured on this server'}
              </option>
              <option value="json">Pre-computed analysis (JSON)</option>
            </Select>
          </div>

          {error ? <Alert tone="error">{error}</Alert> : null}

          {source === 'file' && (
            <div
              className={`sd-dropzone${dragOver ? ' sd-dropzone--over' : ''}`}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                onDrop(e.dataTransfer.files);
              }}
            >
              <input
                ref={inputRef}
                type="file"
                hidden
                accept=".xlsx,.xls,.csv"
                onChange={(e) => e.target.files && onDrop(e.target.files)}
              />
              <div className="sd-dropzone__icon" aria-hidden />
              <p>Drag &amp; drop an Excel or CSV loan/lease tape, or</p>
              <Button variant="secondary" onClick={() => inputRef.current?.click()} disabled={busy}>
                {busy ? 'Reading file…' : 'Browse files'}
              </Button>
            </div>
          )}

          {source === 'link' && (
            <div className="sd-field" style={{ maxWidth: 560 }}>
              <div className="sd-actions" style={{ gap: 8 }}>
                <Input
                  type="text"
                  value={gsUrl}
                  placeholder="https://docs.google.com/spreadsheets/d/…/edit#gid=0"
                  onChange={(e) => setGsUrl(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void importLink();
                  }}
                />
                <Button
                  variant="secondary"
                  disabled={!gsUrl.trim() || gsBusy}
                  onClick={() => void importLink()}
                >
                  {gsBusy ? 'Importing…' : 'Import link'}
                </Button>
              </div>
              <span className="sd-muted">
                Must be shared with “Anyone with the link can view”.
              </span>
            </div>
          )}

          {source === 'drive' && (
            <div className="sd-field" style={{ maxWidth: 560 }}>
              {state.auth.gdrive ? (
                <>
                  {!driveOpen && (
                    <div className="sd-actions" style={{ gap: 8 }}>
                      <Button variant="secondary" disabled={driveBusy} onClick={() => void openDrive()}>
                        {driveBusy ? 'Connecting…' : 'Browse Drive files'}
                      </Button>
                    </div>
                  )}
                  {driveOpen && (
                    <div className="sd-actions" style={{ gap: 8 }}>
                      <Select
                        aria-label="Drive file"
                        value={driveSel}
                        onChange={(e) => setDriveSel(e.target.value)}
                      >
                        {driveFiles.length === 0 && (
                          <option value="">No spreadsheets, CSV or Excel files found</option>
                        )}
                        {driveFiles.map((f) => (
                          <option key={f.id} value={f.id}>
                            {f.name} ({driveMimeLabel(f.mimeType)})
                          </option>
                        ))}
                      </Select>
                      <Button
                        variant="secondary"
                        disabled={!driveSel || driveBusy}
                        onClick={() => void importDriveFile()}
                      >
                        {driveBusy ? 'Importing…' : 'Import'}
                      </Button>
                    </div>
                  )}
                </>
              ) : googleEnabled ? (
                <div className="sd-actions" style={{ gap: 8 }}>
                  <Button variant="secondary" disabled={driveBusy} onClick={() => void connectDrive()}>
                    {driveBusy ? 'Connecting…' : 'Connect Google Drive'}
                  </Button>
                </div>
              ) : (
                <span className="sd-muted">Google sign-in isn't configured on the server.</span>
              )}
            </div>
          )}

          {source === 'resume' && profiles.length > 0 && (
            <div className="sd-field" style={{ maxWidth: 480 }}>
              <Select
                aria-label="Resume a saved session"
                disabled={resuming}
                value=""
                onChange={(e) => e.target.value && void resume(e.target.value)}
              >
                <option value="">{resuming ? 'Loading…' : 'Choose a saved session…'}</option>
                {profiles.map((p) => (
                  <option key={p.session_id} value={p.session_id}>
                    {p.label} ({new Date(p.saved_at * 1000).toLocaleDateString()})
                  </option>
                ))}
              </Select>
            </div>
          )}

          {source === 'redshift' && (
            <div className="sd-field" style={{ maxWidth: 480 }}>
              {redshiftError ? <Alert tone="error">{redshiftError}</Alert> : null}
              <span>Schema</span>
              <Select
                aria-label="Schema"
                disabled={redshiftSchemasBusy || redshiftSchemas.length === 0}
                value={redshiftSchema}
                onChange={(e) => setRedshiftSchema(e.target.value)}
              >
                <option value="">
                  {redshiftSchemasBusy ? 'Listing schemas…' : redshiftSchemas.length === 0 ? 'No schemas available' : '— choose a schema —'}
                </option>
                {redshiftSchemas.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </Select>
              <span>Table</span>
              <Select
                aria-label="Table"
                disabled={!redshiftSchema || redshiftTablesBusy || redshiftTables.length === 0}
                value={redshiftTable}
                onChange={(e) => setRedshiftTable(e.target.value)}
              >
                <option value="">
                  {!redshiftSchema
                    ? 'Choose a schema first'
                    : redshiftTablesBusy
                      ? 'Listing tables…'
                      : redshiftTables.length === 0
                        ? 'No tables found'
                        : '— choose a table —'}
                </option>
                {redshiftTables.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </Select>
              <span className="sd-muted">
                Reads MAESTRO_SRC.{redshiftSchema || '<schema>'}.{redshiftTable || '<table>'} - any table under
                the warehouse's approved schemas (MAESTRO_SRC*, MAESTRO_*_ANA, MAESTRO_*_MRT).
              </span>

              <span>Filter (optional)</span>
              <div className="sd-actions" style={{ gap: 8 }}>
                <Input
                  type="text"
                  value={redshiftFilterRequest}
                  placeholder="Describe it in plain English, e.g. active loans from 2024 onwards"
                  style={{ flex: 1 }}
                  onChange={(e) => setRedshiftFilterRequest(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void suggestFilter();
                  }}
                />
                <Button
                  variant="secondary"
                  disabled={!redshiftSchema || !redshiftTable || !redshiftFilterRequest.trim() || redshiftFilterBusy}
                  onClick={() => void suggestFilter()}
                >
                  {redshiftFilterBusy ? 'Translating…' : 'Suggest filter'}
                </Button>
              </div>
              <Input
                type="text"
                value={redshiftWhere}
                placeholder="STATUS = 'ACTIVE' AND ORIGINATION_DATE >= '2024-01-01'"
                onChange={(e) => setRedshiftWhere(e.target.value)}
              />
              <span className="sd-muted">
                The SQL WHERE condition that will actually run - "Suggest filter" fills this in from the
                description above, but check/edit it before fetching. Matters most when you're capping the
                row count below - without a filter, a partial fetch means whatever rows the warehouse
                happens to scan first, not a chosen slice.
              </span>

              <span>Max rows to fetch</span>
              <Input
                type="number"
                min={100}
                step={1000}
                disabled={redshiftAllRows}
                value={redshiftLimit}
                onChange={(e) => setRedshiftLimit(Number(e.target.value) || 100_000)}
              />
              <label className="sd-muted" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <input
                  type="checkbox"
                  checked={redshiftAllRows}
                  onChange={(e) => setRedshiftAllRows(e.target.checked)}
                />
                Fetch all rows (no cap - can take a while on very large tables)
              </label>
              <span className="sd-muted">Tables can be very large - raise this if you need the full dataset.</span>
              <div className="sd-actions" style={{ marginTop: 4 }}>
                <Button disabled={!redshiftSchema || !redshiftTable || redshiftBusy} onClick={() => void fetchRedshift()}>
                  {redshiftBusy ? 'Fetching…' : 'Fetch table'}
                </Button>
              </div>
              {redshiftProgress ? (
                <>
                  <progress
                    className="sd-progress-bar"
                    value={redshiftProgress.done}
                    max={redshiftProgress.total}
                  />
                  <span className="sd-muted">
                    {redshiftProgress.done} of {redshiftProgress.total} part
                    {redshiftProgress.total === 1 ? '' : 's'} fetched. Large tables can take a while;
                    this isn't stuck, it's still working.
                  </span>
                </>
              ) : null}
            </div>
          )}
        </>
      ) : (
        <>
          <Alert tone={notice ? 'success' : 'info'}>{notice ?? `Loaded ${state.file.name}.`}</Alert>
          {error ? <Alert tone="error">{error}</Alert> : null}
          {state.file.sheets.length > 1 && (
            <div className="sd-field" style={{ maxWidth: 480 }}>
              <span>This file has multiple sheets — which one has your data?</span>
              <Select
                value={state.file.sheet ?? ''}
                disabled={busy}
                aria-label="Sheet"
                onChange={(e) => void changeSheet(e.target.value)}
              >
                {state.file.sheets.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </Select>
            </div>
          )}
          <h2 className="sd-h2">Loaded file</h2>
          <div className="sd-grid sd-grid--metrics">
            <MetricStat label="File" value={state.file.name} />
            {state.file.sheet ? <MetricStat label="Sheet" value={state.file.sheet} /> : null}
            <MetricStat label="Columns" value={String(state.file.columns.length)} />
            <MetricStat label="Rows" value={state.file.rowCount.toLocaleString()} />
          </div>
          {state.file.preview && state.file.preview.length > 0 ? (
            <>
              <h2 className="sd-h2">Preview</h2>
              <Table columns={previewCols} rows={state.file.preview} />
            </>
          ) : null}

          <div className="sd-field" style={{ marginTop: 16 }}>
            {!mergeOpen ? (
              <Button variant="ghost" onClick={() => setMergeOpen(true)}>
                + Merge another file
              </Button>
            ) : (
              <>
                <span>Merge more files into this one</span>
                <div className="sd-field">
                  <span>How should the files combine?</span>
                  <Select
                    aria-label="Merge mode"
                    value={mergeMode}
                    onChange={(e) => setMergeMode(e.target.value as 'join' | 'stack')}
                  >
                    <option value="stack">Stack rows — same-shape exports, e.g. numbered parts (10 + 3 rows → 13 rows)</option>
                    <option value="join">Join by a key — add columns from another file, matched by an ID</option>
                  </Select>
                  <span className="sd-muted">
                    {mergeMode === 'stack'
                      ? 'Appends each file’s rows onto the first. Columns that don’t match up are kept, left blank on whichever file doesn’t have them.'
                      : 'E.g. a separate payments/transactions file - each one is aggregated to one row per key and joined onto the file above; row count stays this file’s row count. All selected files must share the same key column names.'}
                  </span>
                </div>
                {mergeError ? <Alert tone="error">{mergeError}</Alert> : null}

                <div className="sd-actions" style={{ gap: 8 }}>
                  <input
                    ref={mergeInputRef}
                    type="file"
                    hidden
                    multiple
                    accept=".xlsx,.xls,.csv"
                    onChange={(e) => e.target.files && e.target.files.length > 0 && void addSecondaryFiles(e.target.files)}
                  />
                  <Button
                    variant="secondary"
                    onClick={() => mergeInputRef.current?.click()}
                    disabled={secondaryBusy || mergeBusy}
                  >
                    {secondaryBusy
                      ? 'Reading files…'
                      : secondaryFiles.length > 0
                        ? '+ Add more files'
                        : 'Choose files (select several at once)'}
                  </Button>
                  {secondaryFiles.length === 0 ? (
                    <Button variant="ghost" onClick={() => setMergeOpen(false)}>
                      Cancel
                    </Button>
                  ) : null}
                </div>

                {secondaryFiles.length > 0 && (
                  <>
                    {secondaryFiles.map((f) => (
                      <div key={f.ingestId} className="sd-actions" style={{ gap: 8 }}>
                        <span className="sd-muted">
                          {f.name} — {f.rowCount.toLocaleString()} rows, {f.columns.length} columns.
                        </span>
                        <Button variant="ghost" disabled={mergeBusy} onClick={() => removeSecondaryFile(f.ingestId)}>
                          Remove
                        </Button>
                      </div>
                    ))}
                    {mergeMode === 'join' && (
                      <div className="sd-grid sd-grid--metrics">
                        <div className="sd-field">
                          <span>Join key in {state.file.name}</span>
                          <Select
                            aria-label="Primary join key"
                            value={primaryKey}
                            onChange={(e) => setPrimaryKey(e.target.value)}
                          >
                            <option value="">— choose column —</option>
                            {state.file.columns.map((c) => (
                              <option key={c} value={c}>
                                {c}
                              </option>
                            ))}
                          </Select>
                        </div>
                        <div className="sd-field">
                          <span>Join key in the other file(s)</span>
                          <Select
                            aria-label="Secondary join key"
                            value={secondaryKey}
                            onChange={(e) => setSecondaryKey(e.target.value)}
                          >
                            <option value="">— choose column —</option>
                            {secondaryFiles[0].columns.map((c) => (
                              <option key={c} value={c}>
                                {c}
                              </option>
                            ))}
                          </Select>
                        </div>
                        <div className="sd-field">
                          <span>If a key appears more than once in a file</span>
                          <Select
                            aria-label="Aggregation"
                            value={aggregation}
                            onChange={(e) => setAggregation(e.target.value as 'sum' | 'last')}
                          >
                            <option value="sum">Add up its rows (e.g. a payments file)</option>
                            <option value="last">Keep its last row (e.g. a status snapshot)</option>
                          </Select>
                        </div>
                      </div>
                    )}
                    <div className="sd-actions" style={{ gap: 8 }}>
                      <Button
                        onClick={() => void doMerge()}
                        disabled={(mergeMode === 'join' && (!primaryKey || !secondaryKey)) || mergeBusy}
                      >
                        {mergeBusy ? 'Merging…' : `Merge ${secondaryFiles.length > 1 ? `${secondaryFiles.length} files` : 'file'}`}
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={mergeBusy}
                        onClick={() => {
                          setMergeOpen(false);
                          setSecondaryFiles([]);
                          setPrimaryKey('');
                          setSecondaryKey('');
                          setMergeError(null);
                        }}
                      >
                        Cancel
                      </Button>
                    </div>
                    {mergeProgress ? (
                      <span className="sd-muted">
                        Merged {mergeProgress.done} of {mergeProgress.total} —{' '}
                        {mergeProgress.rows.toLocaleString()} rows so far. Large files can take a
                        while; this isn't stuck, it's still working.
                      </span>
                    ) : null}
                  </>
                )}
              </>
            )}
          </div>

          <div className="sd-field" style={{ marginTop: 16 }}>
            <span>Additional context for the AI</span>
            <span className="sd-muted">
              Optional notes about this dataset - the loan/lease product, currency, what fields
              mean, or how interest and fees are charged. Used to auto-fill the column mapping and
              to answer questions on the Ask AI tab.
            </span>
            <textarea
              className="sd-input"
              rows={4}
              style={{ width: '100%', resize: 'vertical' }}
              placeholder="e.g. 'Portfolio of 12-month invoice-financing loans in Indonesia (IDR). Expected Interest and Expected Fee are blank because no interest or fees are charged; Total Paid is the invoiced amount actually collected.'"
              value={state.analysisContext}
              onChange={(e) => dispatch({ type: 'setAnalysisContext', value: e.target.value })}
            />
            <input
              ref={docsInputRef}
              type="file"
              hidden
              multiple
              accept=".pdf,.xlsx,.xlsm,.docx,.txt,.csv,.md"
              onChange={(e) => e.target.files && void addContextDocs(e.target.files)}
            />
            <div className="sd-actions" style={{ gap: 8 }}>
              <Button
                variant="secondary"
                onClick={() => docsInputRef.current?.click()}
                disabled={docsBusy}
              >
                {docsBusy ? 'Extracting…' : 'Upload supporting documents'}
              </Button>
              <span className="sd-muted">PDF, Excel, Word (.docx), or text files.</span>
            </div>
            {docsError ? <Alert tone="error">{docsError}</Alert> : null}
            {state.contextDocuments.map((doc) => (
              <div key={doc.name} className="sd-actions" style={{ gap: 8 }}>
                <span className="sd-muted">
                  {doc.error
                    ? `${doc.name} — could not be used (${doc.error})`
                    : `${doc.name} — ${doc.text.length.toLocaleString()} characters extracted`}
                </span>
                <Button variant="ghost" onClick={() => removeContextDoc(doc.name)}>
                  Remove
                </Button>
              </div>
            ))}
          </div>

          <div className="sd-actions" style={{ marginTop: 16 }}>
            <Button variant="secondary" onClick={() => dispatch({ type: 'setFile', file: null })}>
              Replace file
            </Button>
            <Button onClick={onNext}>Continue to mapping →</Button>
          </div>
        </>
      )}
    </div>
  );
}

function MetricStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="sd-metric">
      <div className="sd-metric__label">{label}</div>
      <div className="sd-metric__value">{value}</div>
    </div>
  );
}

function driveMimeLabel(mime: string): string {
  if (mime === 'application/vnd.google-apps.spreadsheet') return 'Google Sheet';
  if (mime === 'text/csv') return 'CSV';
  if (mime === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet') return 'Excel';
  if (mime === 'application/vnd.ms-excel') return 'Excel';
  return mime.split('.').pop() ?? mime;
}
