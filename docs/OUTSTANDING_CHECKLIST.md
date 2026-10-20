# Screening Tool — Outstanding Work Checklist

Source: `docs/GOALS_CHECKLIST.md` (full goal-achievement matrix) + spec
`..\Screening Stage Data Analysis_Workflow_Spec_v1.docx`. This file lists
**only what is not yet done**, as of 2026-09-22.

---

## Lending UI feedback (2026-10-21)

Done on Lending; "Also:" marks where the same change applies to other models.

- [x] **HIGH: Save keeps the inputs.** Save never sent `viewState`, so LTV
      Calculator / UE Model inputs, cohort picks and charts were lost on
      reopen (Share already sent them). Now both use one `viewState()`.
      Also: Rental + Revolving (shared code path - fixed for all three).
- [x] **Save fixes the LTV inputs** - same fix as above.
- [x] **Case-when style derived fields.** `IF(cond, a, b)` already covers
      CASE WHEN; MAX/MIN of a *date* column was broken (returned NaN), so
      "total due = case when expected completion date < max_date then total
      else 0" failed. Fixed, tested, added as an example. Also: all models.
- [x] **Metrics date range ends at "Data up to"** (Portfolio Snapshot).
      Also: Rental Summary (done).
- [x] **Outstanding GBV shows "$"** (amounts are USD after conversion).
- [x] **Map your data: data preview** in the derive box; the long help text
      moved into a (?) tip; examples are a dropdown. Also: all models.
- [x] **Mapping opens at the top** (Ask AI button visible) and **auto-maps
      by name + config aliases** with no AI call. Also: all models (Revolving
      has no alias table yet - name match only).
- [x] **Currency note**: "Select the tape's local currency - amounts are
      converted to US dollars." Also: Rental (same screen).
- [x] **Weighted Avg Term shows (days)** in the Cohorts table header.
- [x] **LTV country** guessed from the portfolio currency (only replaces the
      untouched default) and sorted A-Z. Also: Revolving LTV Calculator.
- [x] **Hedge defaults to 5%.** Also: Revolving (shared ltvInputs).
- [x] **Matured cohorts minimum** now filters on Matured Count (General
      Inputs minimum, default 10) when "Matured loans only" is on.
- [x] **Loss rate basis note** reworded as requested.
- [x] **UE Overview: "Average Interest %" -> "Average Product Interest Rate".**
- [x] Carry-overs: Rental Summary date range ends at Data up to; Revolving
      alias table added (config.toml [field_aliases.revolving]).

## Not started

- [ ] **Payments template.** Spec names three templates (lending, asset
      rental, payments); no payments model exists anywhere in the codebase.
- [ ] **Lending-side covenants.** TS Covenants tab/export exists only for
      rental (`app.py:5473`, `app.py:5856`); lending has no covenant output.

## Built but unverified / unshipped

- [ ] **Rental & Subscription re-link — confirm end-to-end in the running
      app.** Currently uncommitted: `app.py` now imports the rental module
      and wires it into a model selector (`MODEL_LABELS` at `app.py:637`,
      selectbox at `app.py:662`), reversing the earlier "removed from app"
      state. Needs a manual click-through (upload → mapping → outputs →
      export) before treating this as done, and a server restart since
      `app.py`/submodule edits don't hot-reload.
- [ ] **Live-verify Google SSO** in a real tenant (unit-tested only).
- [ ] **Live-verify Google Drive picker / save-resume/share** in a real
      tenant (`_render_gdrive_picker`, `_store_create_share`).
- [ ] **Live-verify Slack feedback webhook** (`_send_slack_feedback`).
- [ ] **Live-verify AI assistant + AI column-mapping** against the real
      DeepInfra endpoint (golden-set eval passes only so far).

## Known bugs

- [x] **Cross-model column-alias collision** in rental analysis — fixed
      2026-09-16.
- [x] **Mixed-date-format parsing drops rows to NaT** in rental analysis —
      fixed 2026-09-16.
- [ ] **SME Go churn is under-computed** in rental analysis — root cause not
      yet found.

## Quality gaps

- [ ] **Rental analysis has zero automated test coverage**, unlike
      `scripts/template_analysis` (lending) — higher priority now that
      rental is being re-linked into the live app.
- [ ] **Docker images have never been built or run.** Neither `Dockerfile`
      nor `Dockerfile.web` has been exercised; build with
      `--platform linux/amd64` (ECS Express is X86_64).

## Deployment constraints (not bugs — write these into the runbook)

- **The React app is single-task only.** `_PENDING_GOOGLE_OAUTH_STATES`,
      `INGESTS` and `_GOOGLE_TOKENS` are in-process state, so at ECS desired
      count > 1 sign-in fails intermittently (callback lands on a different
      task than issued the state) and analyse 404s (uploaded to one task,
      analysed on another). Scaling out looks harmless and is not.
- **Saved deals need S3 to be durable.** Only the Cognito (S3) and Google
      (Drive) auth paths persist off-box; the dummy fallback writes to
      container-local disk, which a redeploy discards.

## Explicitly out of scope (not a gap)

- Ad-hoc raw-data analysis by the analytics team (spec Step 3) — a manual
  analyst workflow, not something a template tool replicates.
- Borrower benchmarking against similar business models — flagged in the
  spec's review comments as a future AI-revamp idea, not part of this
  baseline.
