import { useEffect, useState } from 'react';
import { AppProvider, useAppStore } from './state/AppStore';
import { Masthead } from './components/Masthead';
import { Sidebar } from './components/Sidebar';
import { Cover } from './components/Cover';
import { Alert, Card, Select } from './components/ui';
import { googleCallback, getMe } from './api/engine';
import { LoginScreen } from './screens/LoginScreen';
import { DataInputScreen } from './screens/DataInputScreen';
import { ColumnMappingScreen } from './screens/ColumnMappingScreen';
import { GeneralInputsScreen } from './screens/GeneralInputsScreen';
import { AnalysisSession } from './screens/AnalysisSession';
import { JsonUploadScreen } from './screens/JsonUploadScreen';
import type { ModelKey } from './state/types';

type Step = 'input' | 'mapping' | 'inputs' | 'analysis' | 'jsonUpload';

const MODEL_LABELS: Record<ModelKey, string> = {
  lending: 'Lending',
  rental: 'Rental & Subscription',
  revolving: 'Revolving Lending',
  baas: 'Battery as a Service',
};

function modelTitle(model: ModelKey, step: Step): { title: string; sub: string } {
  const base = MODEL_LABELS[model];
  if (step === 'input') {
    return {
      title: 'LTV & Unit Economics Analysis',
      sub:
        model === 'lending'
          ? 'Load a loan tape, map columns to the template, and review cohort-level LTV / loss-rate results.'
          : model === 'revolving'
            ? 'Load a monthly credit-line report (one row per month), map its columns, and review the Outputs ratios and roll-rate transition matrices with ECL.'
            : model === 'baas'
              ? 'Load a market assumptions table (one row per market - download the template below), map its columns, and review each market’s battery-swap unit economics, IRRs and break-even.'
              : 'Load contract-level lease/rental data, map your columns to the Data Input template, and review asset-cohort LTV, churn, unit economics and TS covenants.',
    };
  }
  if (step === 'mapping')
    return { title: 'Map your columns to the Data Input template', sub: `Assign each raw ${base} source column to its template field.` };
  if (step === 'inputs')
    return { title: 'General Inputs', sub: `Set the conventions the ${base} analysis engine uses.` };
  if (step === 'jsonUpload')
    return { title: 'Upload analysis JSON', sub: 'View a pre-computed analysis result without re-running the engine.' };
  return {
    title: `${base} analysis results`,
    sub:
      model === 'revolving'
        ? 'Outputs ratios and roll-rate transition matrices computed from the monthly report.'
        : model === 'baas'
          ? 'Per-market unit economics, IRRs and monthly cash flows computed from the assumptions table.'
          : `Cohort-level outputs computed for this ${base} portfolio.`,
  };
}

function Shell() {
  const { state, dispatch, resumeShare } = useAppStore();
  const [step, setStep] = useState<Step>('input');
  const [shareError, setShareError] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get('share');
    if (!token) return;
    // Strip the query param immediately so a refresh (or Save afterwards,
    // which never writes back into a shared link - see AppStore's
    // resumeShare) doesn't keep re-resuming the same shared snapshot.
    window.history.replaceState(null, '', window.location.pathname);
    resumeShare(token)
      .then(() => setStep('mapping'))
      .catch((err) => setShareError(err instanceof Error ? err.message : 'This share link is invalid or has expired.'));
    // Runs once per mount (Shell only mounts after sign-in, matching
    // app.py's own share-token handling running after its login gate).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const today = new Date().toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });

  const { title, sub } = modelTitle(state.modelKey, step);

  const advance = () => {
    if (step === 'input') setStep('mapping');
    else if (step === 'mapping') setStep('inputs');
    else setStep('analysis');
  };

  const newSession = () => {
    dispatch({ type: 'setFile', file: null });
    setStep('input');
  };

  return (
    <div>
      <Masthead
        docType={`${MODEL_LABELS[state.modelKey]} — ${title}`}
        date={today}
        onMenuClick={() => setMenuOpen(true)}
      />
      <Sidebar
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        onResumed={(hasCachedResult) => setStep(hasCachedResult ? 'analysis' : 'mapping')}
      />
      <div className="sd-main">
        <Cover title={title} subtitle={sub} />

        {shareError ? <Alert tone="error">{shareError}</Alert> : null}

        {step === 'input' && (
          <div className="sd-modelbar">
            <span className="sd-muted">Model</span>
            <Select
              value={state.modelKey}
              aria-label="Select model"
              onChange={(e) => dispatch({ type: 'toggleModel', modelKey: e.target.value as ModelKey })}
            >
              <option value="lending">Lending</option>
              <option value="rental">Rental &amp; Subscription</option>
              <option value="revolving">Revolving Lending</option>
              <option value="baas">Battery as a Service</option>
            </Select>
          </div>
        )}

        {step === 'input' && (
          <DataInputScreen
            onNext={advance}
            onJsonUpload={() => setStep('jsonUpload')}
            onResumed={(hasCachedResult) => setStep(hasCachedResult ? 'analysis' : 'mapping')}
          />
        )}
        {step === 'mapping' && <ColumnMappingScreen onNext={advance} modelKey={state.modelKey} />}
        {step === 'inputs' && <GeneralInputsScreen onNext={advance} modelKey={state.modelKey} />}
        {step === 'analysis' && (
          <AnalysisSession onNewSession={newSession} onEditMapping={() => setStep('mapping')} />
        )}
        {step === 'jsonUpload' && <JsonUploadScreen onBack={() => setStep('input')} />}
      </div>
    </div>
  );
}

/** Landing point for Google's redirect back to this app
 * (GOOGLE_REACT_APP_REDIRECT_URI in web_api/main.py must match this exact
 * path). Exchanges the code server-side (client secret never reaches the
 * browser) and signs in with the verified email. */
function GoogleCallbackScreen() {
  const { dispatch } = useAppStore();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const state = params.get('state');
    // Replace immediately: this URL carries a single-use code/state that
    // must not be re-submitted on a refresh or back-navigation.
    window.history.replaceState(null, '', '/');
    if (!code || !state) {
      setError('Missing code/state from Google — please try signing in again.');
      return;
    }
    googleCallback(code, state)
      .then(({ email }) => dispatch({ type: 'signIn', name: email, gdrive: true }))
      .catch((err) => setError(err instanceof Error ? err.message : 'Google sign-in failed.'));
  }, [dispatch]);

  return (
    <div className="sd-login">
      <Card className="sd-login__card">
        {error ? <Alert tone="error">{error}</Alert> : <p>Finishing Google sign-in…</p>}
      </Card>
    </div>
  );
}

function Gate() {
  const { state, dispatch } = useAppStore();
  const [checking, setChecking] = useState(true);

  // Restore a remembered sign-in: if the backend's signed session cookie is
  // still valid, /api/auth/me returns the identity and we skip the login
  // screen - so a refresh, a new tab, or a browser restart within the
  // cookie's lifetime never shows it again. Runs once per mount; the Google
  // callback route signs in itself, so it's skipped there.
  useEffect(() => {
    if (window.location.pathname === '/auth/google/callback') {
      setChecking(false);
      return;
    }
    getMe()
      .then(({ email, gdrive }) => dispatch({ type: 'signIn', name: email, gdrive }))
      .catch(() => undefined)
      .finally(() => setChecking(false));
  }, [dispatch]);

  if (window.location.pathname === '/auth/google/callback') return <GoogleCallbackScreen />;
  if (checking) {
    return (
      <div className="sd-login">
        <Card className="sd-login__card">
          <p className="sd-muted">Checking sign-in…</p>
        </Card>
      </div>
    );
  }
  if (!state.auth.user) return <LoginScreen />;
  return <Shell />;
}

export default function App() {
  return (
    <AppProvider>
      <Gate />
    </AppProvider>
  );
}
