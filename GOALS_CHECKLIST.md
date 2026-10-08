# Screening Tool — Goal-Achievement Checklist

Source spec: `..\..\Screening Stage Data Analysis_Workflow_Spec_v1.docx`
(Workflow Specification — Screening Stage data analysis self service tool, v1,
current-state baseline).

Legend: ✅ Done / live · 🟡 Built but not shipped/linked · ⛔ Not present · ⚠️ Live-blocked pending real-tenant verification

---

## A. Core Purpose — Success Measures (spec)

| # | Success Measure (from spec) | Status | Where / Evidence |
|---|------------------------------|--------|------------------|
| A1 | Requester gets **correct analytical outputs** | ✅ | Outputs validated vs real Excel workbooks (`docs/progress/RENTAL_ANALYSIS_VS_EXCEL_VALIDATION.md`); pytest green (34 passed) |
| A2 | Outputs available **quickly** (process moves at pace) | ✅ | Automated compute — no manual Excel/analysis step needed after upload+mapping |
| A3 | IO can decide to **move forward or kill a deal** | ✅ | LTV Analysis + Unit Economics tabs, loss rates, cohort grids, export workbook |
| A4 | IO can **set key covenant levels** | 🟡 | LTV Calculator (stress waterfall) present; TS Covenants only in rental export |

---

## B. Standard Template Outputs (spec Steps 0–2, 5)

| # | Output / Behaviour | Status | Where / Evidence |
|---|--------------------|--------|------------------|
| B1 | Lending template analysis (LTV, UE, loss rates, term) | ✅ | `_compute_lending_analysis`, LTV/UE tabs, export workbook |
| B2 | Unit Economics analysis | ✅ | `_render_ue_model_ai_tab`, UE sheet export |
| B3 | LTV analysis (95th %ile loss, revenue, term) | ✅ | LTV Analysis sheet/tab |
| B4 | Cohort analysis + "Cohorts for X or more loans" | ✅ | `_render_cohorts_grid`, filtered cohorts |
| B5 | Rental & Subscription (asset-lease) template | 🟡 | Built + validated in `rental_and_subscription/`, **removed from app** (commit `e81a8e2`) |
| B6 | Payments template | ⛔ | No payments model in codebase |

---

## C. Exceptions / "Where it breaks down today" — each solved?

| # | Spec breakdown point | Status | How addressed |
|---|----------------------|--------|----------------|
| C1 | **Large data sets** too big for Excel/GSheets | ✅ | Direct CSV/XLSX upload; not bounded by sheet row limits |
| C2 | **Incomplete data sets** / gaps in history | ✅ | Step 2 data-quality checks (omissions, duplicates, dedupe option) |
| C3 | **Non-standard business models** | 🟡 | Two model types exist (lending live; rental unlinked); payments absent |
| C4 | **Odd / anomalous results** — time-consuming to explain | ✅ | Step 4 diagnostics: loss-rate variance, negative-loss, interest==fee, cohort-coverage/threshold; AI escalation write-up + one-click Slack |
| C5 | **Key-person dependency** | ✅ | Ask-AI assistant, AI mapping auto-fill, canned anomaly diagnostics |

---

## D. Step-4 Anomaly Handling Detail (spec Table 7 / Step 4)

| # | Anomaly diagnostic | Status | Where / Evidence |
|---|--------------------|--------|------------------|
| D1 | Cohort loss-rate variance / swing diagnosis | ✅ | `_lending_variance_check`, `_rental_variance_check` |
| D2 | Negative loss-rate flag | ✅ | `_negative_loss_rate_check` |
| D3 | Expected-Interest == Expected-Fee flag | ✅ | `_expected_interest_equals_fee_check` |
| D4 | Cohort coverage note (thin cohorts) | ✅ | `_cohort_coverage_note` |
| D5 | Min-loans-per-cohort vs file's own count | ✅ | `_cohort_threshold_check` |
| D6 | AI-generated escalation write-up | ✅ | `_suggest_escalation_writeup` |
| D7 | One-click escalation to Slack | ✅ | `_send_slack_feedback` / escalate button |

---

## E. Data Ingestion & Workflow (spec Trigger / Steps 0–2)

| # | Capability | Status | Where / Evidence |
|---|------------|--------|------------------|
| E1 | Upload raw loan tape (Excel / CSV) | ✅ | `_read_tabular_file` |
| E2 | Google Sheets / Drive **link** ingestion (share link) | ✅ | `_download_google_sheet` (no API key needed) |
| E3 | **My Google Drive** file picker (OAuth) | ⚠️ | `_render_gdrive_picker`; unit-tested, needs real Google client verified |
| E4 | Column-mapping wizard (auto AI mapping) | ✅ | `_suggest_mapping`, mapping UI; eval harness in `scripts/prompt_evals/` |
| E5 | Row-exclusion / status-mapping rules | ✅ | `_normalize_columns`, `_active_status_values`, exclude rule |
| E6 | General Inputs (extraction date, days-after-term, min loans) | ✅ | General Inputs form |
| E7 | Multi-doc context upload (PDF/XLSX/DOCX/…) for AI | ✅ | `_extract_document_text`, `_ai_context_text` |
| E8 | Saved sessions / resume | ✅ | `profile_store` / `drive_store` (Google or local) |
| E9 | Share links (read-only, access-conscious) | ⚠️ | `_store_create_share`; Google-scoped for real accounts |

---

## F. AI Assistant & Escalation

| # | Capability | Status | Where / Evidence |
|---|------------|--------|------------------|
| F1 | Ask-AI chat assistant (lending + rental context) | ⚠️ | `_render_ai_chat_tab`; needs DeepInfra key live-confirmed |
| F2 | AI column-mapping suggestions | ⚠️ | `_suggest_mapping`; golden-set LLM eval passes vs real endpoint |
| F3 | AI-derived-column suggestion | ✅ | Added, then **removed** as unused (commit `a8454ad`) — intentional |
| F4 | Feedback → Slack | ⚠️ | `_send_slack_feedback`; needs webhook live-confirmed |

---

## G. Engineering Health / Quality Gate

| # | Check | Status | Evidence (run 2026-09-15) |
|---|-------|--------|----------------------------|
| G1 | Lint (`ruff check`) | ✅ | All checks passed |
| G2 | Tests (`pytest`) | ✅ | 34 passed, 3 skipped |
| G3 | App boots (headless) | ✅ | AppTest — 0 exceptions |
| G4 | Config externalized (thresholds/prompts) | ✅ | `config.py` / `config.toml` |
| G5 | Per-workbook correctness validated | ✅ | Lending + Rental validation docs |

---

## H. Open Items / Gaps vs Spec

| # | Item | Status | Note |
|---|------|--------|------|
| H1 | Payments template | ⛔ | Not implemented anywhere |
| H2 | Re-link Rental & Subscription in the app | 🟡 | Code + validation exist; currently archived/unlinked |
| H3 | Live-verify Google SSO / Drive / Slack / AI in a real tenant | ⚠️ | Per AGENTS.md §9 "unconfirmed until verified" |
| H4 | Spec "Open Questions" table | — | Blank in v1; nothing to reconcile |
