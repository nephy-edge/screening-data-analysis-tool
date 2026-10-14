import { useEffect, useState } from 'react';
import { Button, Input } from './ui';
import { browseGdrive, type GDriveFile } from '../api/engine';

const FOLDER_MIME = 'application/vnd.google-apps.folder';

interface DriveBrowserProps {
  /** Import the clicked file; the modal stays open (showing busy) until the
   * caller closes it. */
  onPick: (file: GDriveFile) => void;
  onClose: () => void;
  busy: boolean;
}

/** Modal Drive folder browser: Shared Drives / "Shared with me" at the top,
 * click a folder to open it, breadcrumb to go back up, search by name. */
export function DriveBrowser({ onPick, onClose, busy }: DriveBrowserProps) {
  const [path, setPath] = useState<{ id: string; name: string }[]>([]);
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<GDriveFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const folder = path.length ? path[path.length - 1].id : '';

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    browseGdrive(query ? { q: query } : { folder })
      .then((r) => live && setItems(r))
      .catch((e) => live && setError(e instanceof Error ? e.message : 'Could not open that folder.'))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [folder, query]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && !busy && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [busy, onClose]);

  const crumbs = [{ id: '', name: 'Google Drive' }, ...path];

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Choose a file from Google Drive"
      onClick={() => !busy && onClose()}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(22, 49, 46, 0.35)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 16,
        zIndex: 1000,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--surface)',
          borderRadius: 'var(--radius)',
          border: '1px solid var(--border)',
          width: 'min(720px, 100%)',
          height: 'min(560px, 90vh)',
          display: 'flex',
          flexDirection: 'column',
          padding: 'var(--card-pad)',
          gap: 12,
        }}
      >
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <form
            style={{ flex: 1 }}
            onSubmit={(e) => {
              e.preventDefault();
              setQuery(search.trim());
            }}
          >
            <Input
              aria-label="Search Drive"
              placeholder="Search file names… (Enter)"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                if (!e.target.value) setQuery('');
              }}
            />
          </form>
          <Button variant="secondary" disabled={busy} onClick={onClose}>
            Close
          </Button>
        </div>

        <nav aria-label="Folder path" style={{ display: 'flex', flexWrap: 'wrap', gap: 4, fontSize: 13 }}>
          {query ? (
            <span className="sd-muted">Search results for “{query}”</span>
          ) : (
            crumbs.map((c, i) => (
              <span key={c.id || 'top'}>
                {i > 0 && <span className="sd-muted"> › </span>}
                {i < crumbs.length - 1 ? (
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault();
                      setPath(path.slice(0, i));
                    }}
                  >
                    {c.name}
                  </a>
                ) : (
                  <strong>{c.name}</strong>
                )}
              </span>
            ))
          )}
        </nav>

        <div style={{ flex: 1, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
          {loading && <div className="sd-muted" style={{ padding: 12 }}>Loading…</div>}
          {error && <div style={{ padding: 12, color: 'var(--danger)' }}>{error}</div>}
          {!loading && !error && items.length === 0 && (
            <div className="sd-muted" style={{ padding: 12 }}>No folders, spreadsheets, CSV or Excel files here.</div>
          )}
          {!loading &&
            !error &&
            items.map((f) => {
              const isFolder = f.mimeType === FOLDER_MIME;
              return (
                <button
                  key={f.id}
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    if (isFolder) {
                      setQuery('');
                      setSearch('');
                      setPath([...(query ? [] : path), { id: f.id, name: f.name }]);
                    } else onPick(f);
                  }}
                  style={{
                    display: 'flex',
                    width: '100%',
                    gap: 10,
                    alignItems: 'center',
                    padding: '9px 12px',
                    border: 0,
                    borderBottom: '1px solid var(--border)',
                    background: 'transparent',
                    textAlign: 'left',
                    cursor: busy ? 'wait' : 'pointer',
                    color: 'var(--text)',
                    font: 'inherit',
                  }}
                >
                  <span aria-hidden="true">{isFolder ? '📁' : '📄'}</span>
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {f.name}
                  </span>
                  <span className="sd-muted" style={{ fontSize: 12 }}>
                    {isFolder ? '' : mimeLabel(f.mimeType)}
                    {f.modifiedTime ? ` · ${f.modifiedTime.slice(0, 10)}` : ''}
                  </span>
                </button>
              );
            })}
        </div>
        {busy && <div className="sd-muted">Importing…</div>}
      </div>
    </div>
  );
}

function mimeLabel(mime: string): string {
  if (mime === 'application/vnd.google-apps.spreadsheet') return 'Google Sheet';
  if (mime === 'text/csv') return 'CSV';
  return 'Excel';
}
