import { useAppStore } from '../state/AppStore';
import { Button, Input, Select, Card, Alert } from '../components/ui';
import { CURRENCY_CODES, CURRENCY_NAMES } from '../data/currencies';
import type { ModelKey } from '../state/types';

interface Props {
  onNext: () => void;
  modelKey: ModelKey;
}

export function GeneralInputsScreen({ onNext, modelKey }: Props) {
  const { state, dispatch, analyse } = useAppStore();
  const gi = state.generalInputs;
  const isRental = modelKey === 'rental';

  const run = async () => {
    // Stay here on failure - the error Alert below shows why.
    if (await analyse()) onNext();
  };

  return (
    <div>
      <Alert tone="info">
        {isRental
          ? 'Conventions used by the Rental & Subscription engine (useful asset life, churn stress, and funding/direct cost assumptions).'
          : 'Conventions used by the Lending LTV / Unit Economics engine.'}
      </Alert>

      <h2 className="sd-h2">Currency</h2>
      <div className="sd-field" style={{ maxWidth: 280 }}>
        <span>Portfolio currency</span>
        <Select
          value={gi.currency}
          onChange={(e) => dispatch({ type: 'setGeneralInputs', inputs: { currency: e.target.value } })}
        >
          {CURRENCY_CODES.map((code) => (
            <option key={code} value={code}>
              {code} — {CURRENCY_NAMES[code]}
            </option>
          ))}
        </Select>
      </div>

      <h2 className="sd-h2">General inputs</h2>
      <Card className="sd-grid sd-grid--2">
        <div className="sd-field">
          <span>Extraction date</span>
          <Input
            type="date"
            value={gi.extractionDate}
            onChange={(e) => dispatch({ type: 'setGeneralInputs', inputs: { extractionDate: e.target.value } })}
          />
        </div>
        {isRental ? (
          <>
            <div className="sd-field">
              <span>Days after term</span>
              <Input
                type="number"
                value={gi.daysAfterTerm}
                onChange={(e) =>
                  dispatch({ type: 'setGeneralInputs', inputs: { daysAfterTerm: Number(e.target.value) } })
                }
              />
            </div>
            <div className="sd-field">
              <span>Min contracts per cohort</span>
              <Input
                type="number"
                value={gi.minLoansPerCohort}
                onChange={(e) =>
                  dispatch({ type: 'setGeneralInputs', inputs: { minLoansPerCohort: Number(e.target.value) } })
                }
              />
            </div>
            <div className="sd-field">
              <span>Months since default</span>
              <Input type="number" defaultValue={3} disabled />
            </div>
          </>
        ) : (
          <>
            <div className="sd-field">
              <span>Days after term (maturity window)</span>
              <Input
                type="number"
                value={gi.daysAfterTerm}
                onChange={(e) =>
                  dispatch({ type: 'setGeneralInputs', inputs: { daysAfterTerm: Number(e.target.value) } })
                }
              />
            </div>
            <div className="sd-field">
              <span>Min loans per cohort</span>
              <Input
                type="number"
                value={gi.minLoansPerCohort}
                onChange={(e) =>
                  dispatch({ type: 'setGeneralInputs', inputs: { minLoansPerCohort: Number(e.target.value) } })
                }
              />
            </div>
            <div className="sd-field">
              <span>LTV interest: cohort from (optional)</span>
              <Input
                type="month"
                value={gi.ltvCohortFrom ?? ''}
                onChange={(e) => dispatch({ type: 'setGeneralInputs', inputs: { ltvCohortFrom: e.target.value } })}
              />
            </div>
            <div className="sd-field">
              <span>LTV interest: cohort to (optional)</span>
              <Input
                type="month"
                value={gi.ltvCohortTo ?? ''}
                onChange={(e) => dispatch({ type: 'setGeneralInputs', inputs: { ltvCohortTo: e.target.value } })}
              />
            </div>
          </>
        )}
      </Card>

      <div className="sd-actions" style={{ marginTop: 16 }}>
        <Button variant="ghost" onClick={onNext}>
          ← Back to mapping
        </Button>
        <Button onClick={run} loading={state.analysisRunning}>
          {state.analysisRunning ? 'Running analysis…' : 'Run analysis →'}
        </Button>
      </div>
      {state.analysisError ? <Alert tone="error">{state.analysisError}</Alert> : null}
    </div>
  );
}

