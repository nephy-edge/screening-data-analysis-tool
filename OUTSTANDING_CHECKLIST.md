# Screening Tool — Outstanding Work Checklist

Source: `GOALS_CHECKLIST.md` (full goal-achievement matrix) + spec
`..\Screening Stage Data Analysis_Workflow_Spec_v1.docx`. This file lists
**only what is not yet done**, as of 2026-09-22.

---

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
