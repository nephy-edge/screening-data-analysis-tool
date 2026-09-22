import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useState } from 'react';
import { useAppStore } from '../state/AppStore';
import { Button } from './ui';
import { listProfiles, logout, type SavedProfileMeta } from '../api/engine';

interface SidebarProps {
  open: boolean;
  onClose: () => void;
  /** Called after a run is successfully resumed, so the caller can jump to
   * the mapping step (or straight to results, when the saved run had a
   * cached analysis result attached - see resumeProfile) - mirrors
   * DataInputScreen's own resume flow, since the sidebar can be opened from
   * any screen, not just before a file is loaded. */
  onResumed: (hasCachedResult: boolean) => void;
}

export function Sidebar({ open, onClose, onResumed }: SidebarProps) {
  const { state, dispatch, resumeProfile } = useAppStore();
  const [profiles, setProfiles] = useState<SavedProfileMeta[]>([]);
  const [profilesError, setProfilesError] = useState<string | null>(null);
  const [resumingId, setResumingId] = useState<string | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    if (!open || !state.auth.user) return;
    listProfiles(state.auth.user)
      .then(setProfiles)
      .catch(() => setProfilesError('Could not load your saved runs.'));
  }, [open, state.auth.user]);

  const resume = async (sessionId: string) => {
    setResumingId(sessionId);
    setProfilesError(null);
    try {
      const hasCachedResult = await resumeProfile(sessionId);
      onResumed(hasCachedResult);
      onClose();
    } catch (err) {
      setProfilesError(err instanceof Error ? err.message : 'Could not load that saved run.');
    } finally {
      setResumingId(null);
    }
  };

  const signOut = async () => {
    setLoggingOut(true);
    try {
      await logout();
    } catch {
      // Best-effort: even if the request fails, drop the client-side
      // identity so the login screen reappears - a stale cookie just means
      // the next getMe() re-signs the user back in.
    } finally {
      dispatch({ type: 'signOut' });
      setLoggingOut(false);
      onClose();
    }
  };

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="sd-sidebar__backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onClose}
          />
          <motion.aside
            className="sd-sidebar"
            role="dialog"
            aria-label="Profile and saved runs"
            initial={{ x: '-100%' }}
            animate={{ x: 0 }}
            exit={{ x: '-100%' }}
            transition={{ duration: 0.26, ease: [0.32, 0.72, 0, 1] }}
          >
            <div className="sd-sidebar__header">
              <span className="sd-sidebar__title">Menu</span>
              <button className="sd-sidebar__close" aria-label="Close menu" onClick={onClose}>
                ×
              </button>
            </div>

            <div className="sd-sidebar__section">
              <h3 className="sd-sidebar__heading">Profile</h3>
              {state.auth.user ? (
                <>
                  <p className="sd-sidebar__user">{state.auth.user}</p>
                  <p className="sd-muted">
                    {state.auth.gdrive ? 'Signed in via Google' : 'Temporary sign-in, not yet real SSO'}
                  </p>
                  <Button variant="secondary" onClick={() => void signOut()} disabled={loggingOut}>
                    {loggingOut ? 'Signing out…' : 'Log out'}
                  </Button>
                </>
              ) : (
                <p className="sd-muted">Not signed in.</p>
              )}
            </div>

            <div className="sd-sidebar__section">
              <h3 className="sd-sidebar__heading">Runs</h3>
              {profilesError ? <p className="sd-sidebar__error">{profilesError}</p> : null}
              {profiles.length === 0 ? (
                <p className="sd-muted">No saved runs yet.</p>
              ) : (
                <ul className="sd-sidebar__runs">
                  {profiles.map((p) => (
                    <li key={p.session_id}>
                      <button
                        className="sd-sidebar__run"
                        disabled={resumingId !== null}
                        onClick={() => void resume(p.session_id)}
                      >
                        <span className="sd-sidebar__run-label">{p.label}</span>
                        <span className="sd-sidebar__run-date">
                          {resumingId === p.session_id
                            ? 'Loading…'
                            : new Date(p.saved_at * 1000).toLocaleDateString()}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
