# SC Analysis — React Web App

React + TypeScript re-implementation of the Streamlit **LTV & Unit Economics**
analysis tool, rebuilt with the **Lendable** design system (from the
`streamlit_design` skill) and **Emil Kowalski's** design-engineering / motion
principles.

## Stack

- **Vite 5 + React 18 + TypeScript**
- **Framer Motion** — motion primitives (press feedback, staggered entrances)
- **Recharts** — cohort/chart visualisations
- **react-router-dom** — available for routing (currently a single-session flow)

## Run

```powershell
cd web
npm install
npm run dev
```

Open `http://localhost:5173`.

## Commands

| Command | Description |
| --- | --- |
| `npm run dev` | Start Vite dev server (port 5173) |
| `npm run build` | Typecheck + production build to `dist/` |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | ESLint |

## Design system

The Lendable look-and-feel is implemented as a real component library in
`src/components/ui/*` driven by CSS token variables in
`src/styles/tokens.css` (palette, radii, spacing) and motion tokens
(easing curves, durations) in `src/styles/components.css`.

Key components: `Masthead`, `Cover`, `Button`, `Metric`, `Card`, `Table`,
`Tabs`, `Select`, `Input`, `Alert`.

## Engine integration (FastAPI — LIVE)

The Python analysis engine runs behind a **FastAPI** backend at `web_api/main.py`
(repo root), which wraps the verified `scripts/template_analysis` engine with zero
re-implementation. The Vite dev proxy forwards `/api/**` → `http://localhost:8000`.

**Start the backend** (repo root, project venv):

```powershell
.venv\Scripts\python.exe -m uvicorn web_api.main:app --port 8000
```

**Start the front end** (`web/`):

```powershell
npm run dev
```

The "Run analysis" step posts canonical-format sample rows
(`web/src/data/sample.ts`) to `POST /api/analyse/lending` and renders the real
engine results (cohorts, loss rate, LTV, UE). This path is verified live with a
real engine computation — not a stub.

## Status

This is a scoped first build: the full ingest → map → input → analyse flow is
navigable and styled. SSO, Google Drive/Redshift ingestion, AI chat, and Excel
export are stubbed UI states awaiting backend wiring.
