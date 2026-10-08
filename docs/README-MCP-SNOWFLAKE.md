# Connecting Snowflake to Kilo via an MCP server

This document is a self-contained handoff for a fresh Kilo instance. Its only
job is to wire **Snowflake** into **Kilo** through a **Model Context Protocol
(MCP)** server so the agent can query the Lendable warehouse with read-only
governed SQL.

We use Snowflake's **managed (Snowflake-hosted) MCP server**, not a
self-hosted bridge. No extra infrastructure is required: the endpoint lives
inside the Snowflake account and is hit over HTTPS with a Programmatic Access
Token (PAT). This is exactly how the machine already has it configured.

(See also [snowflake_lending/README.md](snowflake_lending/README.md) for the
separate Snowpark stored-procedure pipeline that runs the Lending engine
inside Snowflake - a different integration, with its own PAT, independent of
this MCP wiring.)

> **Secret hygiene**: credentials in this repository are real and live. Do NOT
> paste a real PAT, API key, or connection string into this file, into code, or
> into git. Use the `<PLACEHOLDER>` tokens below and substitute at runtime from
> the environment / config store. This file intentionally contains no live
> secrets.

---

## 1. What already exists on this machine

This project is already wired to Snowflake through Kilo. Before you create
anything, validate against the existing, **known-good** entry rather than
reinventing it:

- **Kilo global config**: `C:\Users\NephyNdwiga\.config\kilo\kilo.json`
  - It has an MCP server entry named `snowflake` that currently points at the
    Snowflake-managed SQL endpoint and authenticates with a Bearer PAT.
  - It has a parallel entry, `redshift-mcp-server`, for the older Redshift
    path (read-only role `ai_readonly`). Snowflake is the migration target.

Your job is not to guess a new connection format; re-use the shape that is
already proved to work, refresh/rotate credentials as needed, and document it.

---

## 2. How a Snowflake-managed MCP server works (the shape)

Snowflake hosts an MCP endpoint per account. You `CREATE MCP SERVER` on the
Snowflake side (the SQL `SYSTEM_EXECUTE_SQL` tool ships by default), then
configure **Kilo** as an MCP *client* with a **remote** server that points at:

```
https://<ORG>-<ACCOUNT>.snowflakecomputing.com/api/v2/databases/<DB>/schemas/<SCHEMA>/mcp-servers/<MCP_SERVER_NAME>
```

and authenticates with:

```
Authorization: Bearer <PAT>
```

The standard Snowflake managed MCP server exposes these tools (this is what a
Kilo will see after connecting):

| Tool | Purpose |
|------|---------|
| `SQL Execution Tool` (`SYSTEM_EXECUTE_SQL`) | Run a single SQL query against the connected database |
| Cortex Analyst (semantic views) | Natural-language -> governed SQL over a semantic model |
| Cortex Search (services) | RAG-style search over unstructured text |
| (custom `GENERIC` tools) | User-defined procedures, e.g. send email |

The connector we depend on is the **SQL Execution Tool**. Everything below is
scoped to read-only SELECT access.

---

## 3. Prerequisites (Snowflake side)

These are one-time setup steps you or an admin run in Snowsight / a SQL
worksheet. If the managed MCP server already exists on the account, skip to
step 4.

1. **Confirm the target objects** exist:
   - Database/schema with the data (e.g. `maestro_*` warehouse databases and
     per-originator `ana`/`mrt`/`cln` schemas).
   - A virtual warehouse the executing role can use.
2. **Create (or confirm) the MCP server**, e.g.:

   ```sql
   CREATE OR REPLACE MCP SERVER <MCP_SERVER_NAME> FROM SPECIFICATION $${%
   tools:
   - name: "SQL_Execution_Tool"
     type: "SYSTEM_EXECUTE_SQL"
     description: "Tool to execute a SQL query against the connected Snowflake database."
     title: "SQL Execution Tool"
   config:
   - name: "maestro_wh"
     warehouse: "<WAREHOUSE>"
   $$;
   ```

   (Exact syntax/fields are Snowflake-version-specific — confirm against your
   account's `CREATE MCP SERVER` docs rather than assuming this block is
   current.)
3. **Create a Programmatic Access Token (PAT) for the executing role** in
   Snowsight (**Profile → Programmatic Access Tokens**). Copy it once; it is
   shown only at creation. This PAT is the `Bearer` credential Kilo will send.
4. **Verify with `curl`** before touching Kilo:

   ```powershell
   curl.exe -X POST "https://<ORG>-<ACCOUNT>.snowflakecomputing.com/api/v2/databases/<DB>/schemas/<SCHEMA>/mcp-servers/<MCP_SERVER_NAME>" `
     --header "Content-Type: application/json" `
     --header "Accept: application/json" `
     --header "Authorization: Bearer <PAT>" `
     --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
   ```

   A `200` with a `result.tools` array (including `SQL Execution Tool`) means
   the endpoint + PAT are correct. Do not move on until this returns success —
   an "unconfirmed until verified" path must not be wired live.

---

## 4. Configure Kilo (add the remote MCP server)

Kilo reads MCP servers from its config JSON. There are two layers:

- **Global** (all projects): `C:\Users\NephyNdwiga\.config\kilo\kilo.json`
- **Project** (this repo only): `kilo.json` at the repo root (currently absent
  — the global file is what carries the Snowflake entry).

Add an entry under the top-level `"mcp"` object:

```jsonc
{
  "mcp": {
    "snowflake": {
      "type": "remote",
      "url": "https://<ORG>-<ACCOUNT>.snowflakecomputing.com/api/v2/databases/<DB>/schemas/<SCHEMA>/mcp-servers/<MCP_SERVER_NAME>",
      "headers": {
        "Authorization": "Bearer <PAT>"
      },
      "oauth": false,
      "enabled": true,
      "timeout": 60000
    }
  }
}
```

Field-by-field:

| Field | Meaning |
|-------|---------|
| `snowflake` | Server key shown in the MCP list; any unique name works |
| `type` | `"remote"` — Kilo dials the URL; no local process |
| `url` | Your account's managed MCP endpoint (shape in §2) |
| `headers.Authorization` | `Bearer <PAT>` from §3 |
| `oauth` | `false` — we authenticate by header, not OAuth flow |
| `enabled` | `true` |
| `timeout` | ms; 60000 is a sane default for warehouse queries |

`<ORG>-<ACCOUNT>` / `<DB>` / `<SCHEMA>` / `<MCP_SERVER_NAME>` must match
exactly what the Snowflake side exposes (case-sensitive).

---

## 5. Tool permissions in Kilo

After connecting, Kilo needs permission to call the Snowflake MCP tools. Add
entries under the `"permission"` object in the same `kilo.json`:

```jsonc
"permission": {
  "snowflake_execute_sql": "allow"
}
```

If the tool surfaces a different name (e.g. `<key>_<tool>`), list tools first
with `tools/list`, then grant by exact name. Keep anything outside read-only
querying at `"ask"` until you've verified it.

Kilo's own permission confirmation is separate from database-level read-only
enforcement — configure both (§6).

---

## 6. Read-only guardrails (do not skip)

The warehouse connector must stay **read-only**. Enforce it at both layers:

1. **Database / Snowflake**: the executing role (and its PAT) must be granted
   **SELECT only** on approved schemas, with **no** write/DML/DDL grants.
   Approved scope is the same as the Redshift path:
   - `maestro_<env>_ana` and `maestro_<env>_mrt` analytics/reporting layers
     (the validated, business-logic-applied layers dashboards report from).
   - `maestro_<env>_cln` / `_src` only when a user explicitly asks for raw
     source data — otherwise do not touch src/cln.
   - Prefer a single originator's own `ana` schema over the whole-portfolio
     union views (they are slow).
2. **Agent behavior** (this is how a Kilo should be instructed):
   - Never issue `INSERT / UPDATE / DELETE / DROP / CREATE / ALTER / TRUNCATE`.
   - **Row-count first**: run `SELECT COUNT(*)` (or an estimate) before pulling
     any unknown/large table; aggregate or sample rather than raw dump.
   - Always append a `LIMIT` when returning rows (`LIMIT 1000` default,
     `LIMIT 100` for inspection) and state that output was capped.
   - Query history is logged and reviewable — do not attempt to hide or batch
     activity.

Add/adjust the equivalents in `AGENTS.md` so this is enforced per session.

---

## 7. Verifying the connection end-to-end

After wiring, confirm with the actual tools, not the `curl` smoke test alone:

1. Kilo starts and lists the `snowflake` server with the `SQL Execution Tool`
   available.
2. Run the smallest real query against an approved schema:
   ```sql
   SELECT current_version() AS snowflake_version;
   ```
3. Run `SELECT COUNT(*) FROM <db>.<schema>.<table>` on a known small/approved
   table and confirm a sane count returns within the timeout.
4. Confirm a **write is rejected**: a `CREATE TEMP TABLE ...` / `INSERT` attempt
   must fail (permission or SQL error) rather than succeed.
5. Confirm output-capping behavior: a `.describe` / column lookup and a large
   query both behave as the guardrails describe (row count stated, limited).

Only when 1–5 pass is the live path considered verified. Until then it stays
unconfirmed and must not be relied on for analyst-facing queries.

---

## 8. Troubleshooting quick map

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `401` on `tools/list` | PAT invalid/expired, wrong role, or rotated | Create a fresh PAT for the executing role; update header |
| `404` on URL | Wrong `<ORG>-<ACCOUNT>`, `<DB>`, `<SCHEMA>`, or server name (case) | Validate with `curl` (§3 step 4) before Kilo |
| Server shows, but no tools / hangs | `timeout` too low, wrong tool name, or MCP server not created | Raise `timeout`, confirm `CREATE MCP SERVER` ran, re-check tool names |
| Write request succeeds | Read-only role not actually applied | Fix grants at the DB layer (this is the critical failure — escalate) |
| Slow portfolio-wide queries | Querying the whole-portfolio union views | Scope to the originator's own `ana` schema, add `LIMIT`, aggregate |

---

## 9. Do / Don't

**Do**
- Reuse the existing `snowflake` entry in `C:\Users\NephyNdwiga\.config\kilo\kilo.json` as the source of truth.
- Verify with `curl` before wiring, then verify with the live tools end-to-end.
- Keep everything read-only and capped (§6).

**Don't**
- Paste real secrets (PAT, API keys, connection strings) into this repo or git.
- Create a parallel server by guessing a URL/PAT from memory — copy fields from
  the known-good config and the Snowflake-side `describe MCP SERVER`.
- Query `prd_*`/mart/source tables, or issue any DML/DDL, without explicit
  user authorisation.
