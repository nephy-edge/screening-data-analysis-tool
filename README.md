# Rental & Subscription — LTV / Unit Economics Analysis

Streamlit re-implementation of the Lendable **SC Analysis for LTV and UE** workbook
(Rental & Subscription / asset-lease variant), verified against the workbook
calculation engine.

## Run locally

```powershell
.venv\Scripts\python.exe -m streamlit run app.py
```

App opens at `http://localhost:8501` (unless overridden in `.streamlit/config.toml`).

## Deploy to Streamlit Community Cloud

Free hosting from a GitHub repo — no server to manage.

1. **Push this repo to GitHub** (if not already):
   ```powershell
   git remote add origin <repo-url>
   git push -u origin main
   ```
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** and select the repo.
3. Set these values:
   - **Branch:** `main`
   - **Main file path:** `app.py`
4. Click **Deploy**. Streamlit auto-installs `requirements.txt` and reads
   `.streamlit/config.toml` for the theme. App URL will be
   `https://<app-name>.streamlit.app`.

### Optional — AI-suggest feature secret

The AI-suggest feature needs a `DEEPINFRA_API_KEY`. Without it the rest of the app
works normally. To enable it: **App → Settings → Secrets** and add:

```toml
DEEPINFRA_API_KEY = "your-key"
```

Never commit the key; it is stored server-side in `.streamlit/secrets.toml` (gitignored).

## Privacy note

Community Cloud is public — any file the app processes stays in memory and is not
stored, but anyone with the URL can open the app. For a restricted/internal deployment
(e.g. for Lendable), a private VM with an auth proxy is recommended.
