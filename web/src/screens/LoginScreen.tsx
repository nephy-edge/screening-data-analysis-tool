import { useEffect, useState } from 'react';
import { useAppStore } from '../state/AppStore';
import { Button, Card, Input, Alert } from '../components/ui';
import { getAuthConfig, getGoogleLoginUrl, dummySignIn } from '../api/engine';

/** Real Google sign-in when configured server-side (web_api/main.py's
 * /api/auth/*, mirroring scripts/login_gates.py's _render_google_login_gate),
 * else the self-declared, unverified "dummy" identity
 * (_render_dummy_login_gate) - same either/or fallback app.py uses. The
 * dummy path is not a password or a security boundary: it exists only so
 * save/resume/share (scripts/profile_store.py) has a stable string to key a
 * user's saved deals by, ahead of real SSO everywhere. */
export function LoginScreen() {
  const { dispatch } = useAppStore();
  const [email, setEmail] = useState('');
  const [googleEnabled, setGoogleEnabled] = useState<boolean | null>(null);
  const [googleBusy, setGoogleBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAuthConfig()
      .then((c) => setGoogleEnabled(c.googleEnabled))
      .catch(() => setGoogleEnabled(false));
  }, []);

  const submit = async () => {
    const clean = email.trim().toLowerCase();
    if (!clean) return;
    setError(null);
    // Server-side so the backend sets the remember-me cookie - without it a
    // refresh would fall back to the login screen again.
    try {
      await dummySignIn(clean);
      dispatch({ type: 'signIn', name: clean, gdrive: false });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not sign in.');
    }
  };

  const signInWithGoogle = async () => {
    setGoogleBusy(true);
    setError(null);
    try {
      const { url } = await getGoogleLoginUrl();
      window.location.href = url;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start Google sign-in.');
      setGoogleBusy(false);
    }
  };

  return (
    <div className="sd-login">
      <div className="sd-login__brand">
        <span className="sd-masthead__dot" />
        <span className="sd-login__title">Lendable</span>
        <span className="sd-login__doctype">SC Analysis — LTV &amp; Unit Economics</span>
      </div>
      <Card className="sd-login__card">
        <h2 className="sd-h2">Sign in</h2>
        {error ? <Alert tone="error">{error}</Alert> : null}
        {googleEnabled ? (
          <>
            <p className="sd-muted">Sign in with your Lendable Google account to continue.</p>
            <div className="sd-login__actions">
              <Button onClick={() => void signInWithGoogle()} disabled={googleBusy}>
                {googleBusy ? 'Redirecting…' : 'Sign in with Google'}
              </Button>
            </div>
          </>
        ) : (
          <>
            <Alert tone="info">
              Temporary sign-in while Google sign-in is being set up — this only identifies you
              so your uploaded file, mapping, and Ask AI chat can be saved and picked back up
              later. It is not a password and does not verify who you are.
            </Alert>
            <div className="sd-field">
              <span>Your Lendable email</span>
              <Input
                type="email"
                value={email}
                placeholder="you@lendable.io"
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') submit();
                }}
              />
            </div>
            <div className="sd-login__actions">
              <Button disabled={!email.trim()} onClick={() => void submit()}>
                Continue
              </Button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
