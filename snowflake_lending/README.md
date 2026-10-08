# Lending analysis on Snowflake (Snowpark)

(See also [../README-MCP-SNOWFLAKE.md](../README-MCP-SNOWFLAKE.md) for the
separate Kilo/MCP read-only query wiring - a different integration, with its
own PAT, independent of this one.)

An isolated first cut of running the Lending LTV/UE engine
(`scripts/template_analysis`, unmodified) *inside* Snowflake via a Snowpark
Python stored procedure, instead of in this app's own process. The app never
pulls raw rows - only the finished summary/cohort tables cross the wire.

This folder is intentionally separate from `app.py` / `web_api/`: nothing
here is wired into the existing Streamlit or FastAPI app yet, so it can't
break either. Once validated it can be called from `web_api/main.py` the
same way `_compute_lending` is today.

## How it works

1. `deploy.py` registers `proc_handler.run_lending_analysis` as a permanent
   Snowpark Python stored procedure called `RUN_LENDING_ANALYSIS`, bundling
   `scripts/template_analysis/` and `column_mapping.py` as its imports so the
   *exact same, already-verified* engine code runs inside Snowflake.
2. The procedure pulls your input table into pandas with
   `session.table(...).to_pandas()`, renames columns per `COLUMN_MAP`, and
   runs `GeneralInputs` -> `process_data_input` -> `build_cohorts` ->
   `filter_cohorts` -> `UeAnalysis` -> `LtvAnalysis` - identical to
   `web_api/main.py`'s `_compute_lending`.
3. The procedure returns the summary and cohorts as JSON directly (not via
   `session.write_pandas(...)` into intermediate tables) - MAESTRO_DEV_ANA's
   clone-refresh cycle would otherwise own/reset any table this wrote to.
4. `fetch_results.py` calls the procedure and gets that JSON back as plain
   dicts/records - never the raw input rows.

## Setup

```
.venv\Scripts\pip.exe install -r snowflake_lending\requirements.txt
```

Add to `.env` (same file `config.py`/`google_oauth.py` already read from):

```
SNOWFLAKE_ACCOUNT=a0725922314871-lendable
SNOWFLAKE_USER=<your Snowflake username>
SNOWFLAKE_PAT=<a Programmatic Access Token for that user>
SNOWFLAKE_WAREHOUSE=<warehouse>
SNOWFLAKE_DATABASE=<database>
SNOWFLAKE_SCHEMA=<schema>
SNOWFLAKE_ROLE=<optional role>
SNOWFLAKE_STAGE=LENDING_ANALYSIS_STAGE   # optional, this is the default
```

Generate the PAT yourself in Snowsight (Profile -> Programmatic Access
Tokens) - this is deliberately a **separate token from Kilo's**, read only
from this project's own `.env`, never from `kilo.json`. Keep `.env` out of
git (it already is).

**Required edit before deploying**: open `column_mapping.py` and fill in
`COLUMN_MAP` with your actual Snowflake input table's column names -> the
canonical engine column names (e.g. `"Disbursement Date"`,
`"Principal Value"`, `"Total Due"`, `"Total Paid"`). Snowflake normally
returns upper-cased, underscored column names from `to_pandas()`; the engine
needs the exact mixed-case names it already uses. See that file's docstring
for the required set.

## Deploy

```
.venv\Scripts\python.exe -m snowflake_lending.deploy
```

Re-run this any time `scripts/template_analysis`, `column_mapping.py`, or
`proc_handler.py` change - it replaces the procedure in place
(`replace=True`).

**numpy_financial caveat**: `deploy.py` requests `pandas`, `numpy`, and
`numpy_financial` (the engine's `apr.py` uses it) from Snowflake's Anaconda
channel. If your account's channel doesn't carry `numpy_financial`,
registration will fail with a clear package-not-found error - the fallback
(bundle its pure-Python source as an import instead of a channel package) is
documented inline in `deploy.py`'s `except` block.

**Auth caveat**: `connection.py` uses
`authenticator="PROGRAMMATIC_ACCESS_TOKEN"` with the PAT as `token`. This
requires a reasonably recent `snowflake-connector-python`. If your installed
version predates that authenticator, the documented fallback is passing the
same PAT as `password` with the default authenticator instead - see the
comment in `connection.py`.

**Corporate TLS proxy (Cisco Umbrella) caveat**: this network intercepts and
re-signs outbound HTTPS with Cisco Umbrella's own CA, which Windows trusts
but Python doesn't by default - every connection attempt fails with
`certificate verify failed` until Python is told to trust it too.
`connection.py` handles this automatically via
`snowflake_lending/_corporate_ca_bundle.pem` (gitignored, machine-specific -
certifi's public bundle + the Umbrella root pulled from the Windows
certificate store). If you're on a different machine/network and hit the
same error, regenerate it:

```powershell
$cert = Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -like "*Umbrella*" }
$b64 = [System.Convert]::ToBase64String($cert.Export("Cert"), "InsertLineBreaks")
"-----BEGIN CERTIFICATE-----`r`n$b64`r`n-----END CERTIFICATE-----`r`n" | Set-Content snowflake_lending\_corporate_ca_bundle.pem -Encoding ascii -NoNewline
Get-Content .venv\Lib\site-packages\certifi\cacert.pem | Add-Content snowflake_lending\_corporate_ca_bundle.pem
```

If your network doesn't use Cisco Umbrella, delete the `.pem` file - the
`REQUESTS_CA_BUNDLE` override only kicks in when it's present.

## Run

```
.venv\Scripts\python.exe -m snowflake_lending.fetch_results <input_table>
```

Prints the summary dict and cohort rows as JSON. To call it from Python
elsewhere in the app later:

```python
from snowflake_lending.fetch_results import run_and_fetch

result = run_and_fetch("MY_INPUT_TABLE", days_after_term=90, min_loans_per_cohort=10)
result["summary"]   # GeneralInputs + UE + LTV scalars, one row
result["cohorts"]   # full cohorts table
```

## Scope of this v1

Covers the same core pipeline `_compute_lending` runs: general inputs, UE
analysis, LTV analysis, cohorts. It does **not** yet reproduce every payload
shape `web_api/main.py`'s `/api/analyse/lending` returns (chart data,
`_lending_data_quality_checks`, the UE Model sub-tab payload,
`summaryMetrics`) - those are UI-shaping helpers layered on top of the same
core objects and can be added to `proc_handler.py` the same way once this
pattern is confirmed to work against a real deal.
