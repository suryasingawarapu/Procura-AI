# Provenance-Aware Shared Memory for Multi-Agent LLM Systems

Two things live in this repository:

1. **The memory platform** -- a provenance-aware, version-aware, Supabase/
   pgvector-backed memory layer, a five-agent LangGraph demo workflow
   (Supervisor/Planner/Researcher/Executor/Validator) built on top of it, a
   FastAPI service, and NVIDIA Nemotron 3.5 Lightning as the default LLM.
2. **The procurement system** (`app/procurement/`) -- three independent
   agent systems (Vendra, Procura, Finora) built on top of the same memory
   platform, coordinated by a LangGraph router, integrating with ERPNext.
   See [Procurement system](#procurement-system) below.

Stack: LangChain 1.x / LangGraph 1.x (orchestration), NVIDIA Nemotron 3.5
Lightning via `langchain-nvidia-ai-endpoints` (default LLM), Supabase
PostgreSQL + pgvector (storage and retrieval), FastAPI (API service),
Langfuse (observability).

## Layout

```
app/
  memory/        store.py, versioning.py, propagation.py, provenance_fields.py
  agents/        the five-agent demo workflow (LangGraph + NVIDIA)
  tools/         identity-scoped LangChain memory tools
  api/           FastAPI app: memory + workflow + procurement endpoints
  observability/ Langfuse tracing
  procurement/   Vendra / Procura / Finora + coordinator -- see below
frontend/        plain HTML/JS UI, served at /app (no build step)
scripts/         live_e2e_check.py, live_nvidia_persistence_check.py (opt-in, real-cost checks)
tests/
  unit/        pure logic, mocked Supabase/ERPNext, no network
  integration/ read-only checks + opt-in (`dbwrite`) write tests against real Supabase
migrations/    additive SQL, applied by hand -- see migrations/README.md
```

Legacy ad-hoc scripts (`test_memory.py`, `test_versioning.py`,
`test_propagation.py`) live in the repository root, excluded from pytest
collection, and default to a dry run.

## Setup

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

`requirements.txt` pins the direct dependencies, `requirements.lock.txt` pins
the full transitive environment.

## Environment

Create a local `.env` (never committed, see `.gitignore`) -- see
`.env.example` for the full list with placeholders. Summary:

| Variable | Required? | Purpose |
| --- | --- | --- |
| `SUPABASE_URL`, `SUPABASE_KEY` | Required | All memory reads/writes |
| `MEMORY_API_TOKEN` | Required for the API | Admin token; endpoints fail closed (503) without it |
| `MEMORY_API_TOKEN_<AGENT>` | Optional | Per-agent/system scoped token (e.g. `_RESEARCHER`, `_VENDRA`) |
| `NVIDIA_API_KEY` | Needed for the default LLM | Nemotron 3.5 Lightning (`AGENT_LLM_MODEL` default) |
| `OPENAI_API_KEY` | Optional | Embeddings (always OpenAI, regardless of chat provider); also usable as an alternate LLM |
| `LANGFUSE_PUBLIC_KEY` / `_SECRET_KEY` | Optional (both or neither) | Tracing; a no-op without both |
| `ERPNEXT_URL` / `_API_KEY` / `_API_SECRET` | Optional | Real ERPNext; without all three, the procurement system runs on seeded data |
| `ERPNEXT_ALLOW_WRITES` | Optional, default off | Even with ERPNext configured, writes stay refused until this is set |

## Running the tests

```powershell
.\scripts\run_tests.ps1                 # full suite
.\scripts\run_tests.ps1 tests\unit      # unit tests only, no database, no network
.\scripts\run_tests.ps1 -m "not integration"
```

`tests/unit` needs no database and no credentials -- Supabase and ERPNext are
mocked/seeded throughout. `tests/integration` only issues `SELECT`s by
default; a fixture compares row counts before/after every test. Anything
that writes is behind the `dbwrite` marker, opt-in, and excluded by
`pytest.ini`'s default `-m "not dbwrite"` unless explicitly overridden:

```powershell
$env:MEMORY_ALLOW_WRITE = "1"
.\.venv\Scripts\python.exe -m pytest -m dbwrite -v
```

Two standalone, double-opt-in scripts make real, costed calls and are never
run automatically: `scripts/live_e2e_check.py` (NVIDIA + Supabase) and
`scripts/live_nvidia_persistence_check.py` (proves a live agent saves a
finding and a later task retrieves it). Both print a free preflight report
and require `MEMORY_ALLOW_WRITE=1` and `RUN_LIVE_LLM_TEST=1` before doing
anything live.

## Migrations

Applied by hand in the Supabase SQL editor, in order -- see `migrations/README.md`.

| File | Purpose | Applied? |
| --- | --- | --- |
| `001_harden_schema.sql` | Agent registry, sharing policy, constraints, one-active-version index | Yes (live project) |
| `002_memory_functions.sql` | The six atomic SQL functions + self-test | Yes |
| `003_verify_after_apply.sql` | Read-only verification queries | n/a (verification script) |
| `004_fix_parent_memory_id_fkey.sql` | Corrects `parent_memory_id`'s delete action to CASCADE | Yes |
| `005_restrict_direct_table_access.sql` | Revokes direct table DML for anon/authenticated; SECURITY DEFINER + pinned search_path on the six functions | **Drafted, not applied** -- pending confirmation of which `SUPABASE_KEY` type is in use |
| `006_procurement_provenance_fields.sql` | Additive provenance columns for Vendra/Procura/Finora (source type/hash, verification, task/message linkage, sharing classification, structured facts) + registers the three systems in `memory_agents` | **Drafted, not applied** |
| `ROLLBACK.sql` | Undoes 001+002 (destructive) | n/a |

Until 006 is applied, `app.memory.provenance_fields.enrich_memory`/
`enrich_share_edge` will fail at the database level (unknown column) the
first time they're actually exercised against the real project -- they are
fully unit-tested against mocks, not yet verified against the live schema.

## Procurement system

`app/procurement/` -- three independent agent systems coordinated for
procurement tasks, built on the memory platform above. Implements the
spec in `Claude_Code_Procurement_Project_Full_Build_Instructions.md`.

```
app/procurement/
  identities.py    SYSTEM_IDS = (vendra, procura, finora)
  messages.py      SystemMessage / TaskRequest / TaskResult (typed, IDs, sources, verification/freshness)
  calculations.py  deterministic quote comparison + budget check (no LLM)
  coordinator.py   LangGraph router: classifies which system(s) a task needs, runs them in dependency order
  mcp/client.py      ERPNextClient interface + SeededERPNextClient + ERPNextRestClient (both real, REST one now live-verified)
  mcp/mcp_client.py  MCPERPNextClient -- real MCP stdio client, correct against the verified server's actual source, NOT YET BUILT/RUN (blocked -- see below)
  evaluation.py      baseline-vs-memory-enabled comparison harness (measures, doesn't assume, a result)
  systems/
    vendra.py      Vendor Discovery, Document Verification/Compliance Check
    procura.py     Purchase Request interpretation, RFQ, Quotation Processing/Comparison, PO preparation
    finora.py      Budget Verification, Invoice Matching
```

### Local ERPNext test instance -- live, running

A real ERPNext v16.52.1 instance runs locally via the official
`frappe/frappe_docker` quickstart (`pwd.yml`), cloned to
`C:\Users\surya\erpnext_test_instance` (outside this repo -- it's
infrastructure, not project code). Docker Desktop and WSL2 were already
installed on this machine; nothing needed installing.

```powershell
cd C:\Users\surya\erpnext_test_instance
docker compose -f pwd.yml up -d     # start (data persists in named volumes)
docker compose -f pwd.yml stop      # stop without deleting data
```

Reachable at **http://localhost:8082** (8080 was already taken by another
project's container on this machine, confirmed via `Test-NetConnection`
before picking 8082 -- never assumed). Seeded with clearly-labelled test
records: a Company, Fiscal Year, three suppliers (one deliberately
`on_hold=1`), an item, and two Supplier Quotations at different prices. A
restricted test API user (`Purchase User`/`Stock User`/`Accounts User`
roles, not admin) was created with its own API key/secret --
see `C:\Users\surya\erpnext_test_instance\CREDENTIALS.md` (outside this
repo, never committed; add its `ERPNEXT_*` values to your own `.env` to
point the app at it). Two real, pre-existing bugs in `bench new-site
--install-app erpnext` without the setup wizard had to be worked around
(System Settings language/time_zone were unset, breaking all document
creation; a Supplier's Cost Center tree never auto-created, so Budget
records can't be created yet) -- documented in `CREDENTIALS.md`, not
silently papered over.

**`ERPNextRestClient` verified live against it** (not mocked): real
`list_suppliers()`, `get_supplier()`, `list_items()`, and
`list_supplier_quotations()` calls against the real instance, over real
HTTP, with the real API key/secret. This found and fixed two genuine bugs
that mocked tests alone had not caught:
1. ERPNext's list endpoint returns only `name` unless `fields` is
   requested explicitly -- `on_hold`/`disabled` were silently coming back
   as `None`, which would have broken `vendra.discover_suppliers()`'s
   on_hold filter against a real instance. Fixed: explicit `fields=` on
   every list/get call.
2. ERPNext's REST filter syntax only supports top-level doctype fields;
   `item_code` lives on the `Supplier Quotation Item` child table, so
   filtering on it directly returns HTTP 417. Fixed:
   `list_supplier_quotations` now fetches full documents and flattens to
   one row per matching item line.

### ERPNext MCP server -- verified and cloned, not yet built

No official ERPNext MCP server exists. Two real candidates were checked
via the GitHub API and their READMEs, then the chosen one's actual source
was read (not just its README):

- **`frappe/mcp`** (official Frappe org, MIT, Python) -- turns the ERPNext
  site itself into an MCP server. Requires installing an app *on* the
  ERPNext instance and OAuth2 (needs a Frappe version with
  [frappe#33188](https://github.com/frappe/frappe/pull/33188) merged), and
  `pwd.yml`'s disposable quickstart explicitly doesn't support installing
  custom apps anyway. README calls it "highly experimental."
- **`rakeshgangwar/erpnext-mcp-server`** (MIT, TypeScript/Node, cloned to
  `C:\Users\surya\erpnext_mcp_server`) -- **chosen**. Standalone, calls
  ERPNext's own REST API with a plain API key/secret, built on the
  official `@modelcontextprotocol/sdk`. Source read directly
  (`src/index.ts`, ~1000 lines): confirms the exact 11 tool names/schemas
  and response shape (`content: [{type:"text", text: JSON...}]`,
  `isError` on failure) used by `mcp_client.py`. No permission scoping in
  the server itself (confirmed by reading the source) -- `delete_document`,
  `submit_document`, `cancel_document` and `call_method` are never called
  by this wrapper; that allowlist is enforced here, not by the server.

**What's implemented:** `MCPERPNextClient` (`app/procurement/mcp/mcp_client.py`),
written against the real, installed `mcp` Python SDK's actual API
(verified via `inspect.signature`, not guessed) and the server's real
tool schemas above. Unit-tested with the MCP stdio transport mocked (9
tests, `tests/unit/test_procurement_mcp_client.py`) -- this proves the
wrapper's own logic (allowlisting, write-gating, isError/JSON handling),
not the third-party server.

**Blocked, not silently skipped:** building the cloned server
(`npm install && npm run build` in `C:\Users\surya\erpnext_mcp_server`)
is refused by Claude Code's own auto-mode safety classifier ("Code from
External"), tried via both Bash and PowerShell, independent of in-chat
instructions -- this is a harness-level policy, not something resolvable
from inside the session. **To finish this:** either run those two
commands yourself in that directory, or add a Claude Code permission rule
allowing it, then ask to continue. Once built, set
`ERPNEXT_MCP_SERVER_ENTRY=C:\Users\surya\erpnext_mcp_server\build\index.js`
(alongside the three `ERPNEXT_*` vars) and `get_configured_client()`
(`app/procurement/mcp/client.py`) will select it automatically, ahead of
`ERPNextRestClient` -- no further code change needed. **Not claimed as
complete** until that actual build-and-call happens.

### Frontend -- real, running, against the real API

`frontend/` (plain HTML/CSS/vanilla JS, no build step) served by FastAPI
at **`/app`** (mounted in `app/api/main.py`). Covers the spec's screens
functionally, not as a mockup -- every section calls a real endpoint:
Integration Health (`GET /procurement/health`), Task Console
(`POST /procurement/tasks`), Execution Timeline (rendered from the task's
real `messages`, including sources/verification status), Approval
(`POST /procurement/purchase-orders`, requires an explicit click -- never
auto-triggered), Memory Explorer (`GET /agents/{id}/memories`, new this
session), Provenance & Version History (the existing versions/ancestry/
descendants endpoints). System Overview is static role descriptions,
labelled as such. The API token is entered once and kept only in the
browser's `localStorage`.

### What's done, tested, and runs now

- The full required demo workflow ("find eligible suppliers for 20 laptops,
  collect/compare quotations, check budget, prepare for approval") runs end
  to end against seeded ERPNext data: `tests/unit/test_procurement_coordinator.py`.
- Deterministic quotation comparison and budget checking (no LLM, no
  invented numbers) -- `tests/unit/test_procurement_calculations.py`.
- Vendra's vendor discovery + compliance check, Procura's RFQ/quotation/PO
  flow, Finora's budget/invoice checks -- each independently unit-tested
  against the seeded client.
- Typed inter-system messages with IDs, source references, verification/
  freshness status, purpose -- `app/procurement/messages.py`.
- Coordinator routing is a deterministic keyword classifier (not every
  task touches all three systems), a straight dependency-ordered pipeline
  (no cycles, so no circular-conversation risk by construction), with one
  retry on an ERPNext/MCP hiccup before failing cleanly.
- Purchase order submission is a separate, explicitly-gated API call
  (`POST /procurement/purchase-orders`, requires the admin token *and*
  `confirm: true`) -- the coordinator itself never creates one.
- API: `POST /procurement/tasks`, `POST /procurement/purchase-orders`,
  `GET /procurement/health` (`app/api/procurement_routes.py`).
- Migration 006 (drafted) extends the memory schema with the richer
  provenance fields the spec asks for; `app/memory/provenance_fields.py`
  sets them via a second call after each atomic write succeeds (not
  inside the same transaction -- see the migration file's docstring for
  why rewriting the untestable atomic functions was judged riskier than
  this explicitly-documented trade-off).

### Known gaps -- not claimed as done

- **MCP server not yet built/run** -- see above; blocked on a harness
  permission only you or a settings change can grant.
- **The coordinator itself still runs against `SeededERPNextClient` by
  default** (`get_configured_client()` needs `ERPNEXT_URL`/`_API_KEY`/
  `_API_SECRET` in the app's own `.env` to switch to the now-verified
  `ERPNextRestClient`, and `ERPNEXT_MCP_SERVER_ENTRY` once that's built,
  to prefer MCP). The live verification above was done by calling
  `ERPNextRestClient` directly, not yet by pointing the coordinator at it
  end to end.
- **Budget records can't be created on the test instance yet** -- the
  Company's Cost Center tree didn't auto-create (see `CREDENTIALS.md`).
  Finora's budget check against the real instance will honestly report
  "no budget data found," not a fabricated number, until that's fixed.
- **Migration 006 is drafted, not applied** (same "ask before touching the
  live database" policy as 005). `enrich_memory`/`enrich_share_edge` are
  unit-tested against mocks only.
- **Only a subset of the spec's "potential" specialists are implemented**:
  Vendor Onboarding, Vendor Profile, Vendor Performance (Vendra); Supplier
  Matching as its own step, Delivery Tracking (Procura); Payment Terms
  review, Approval Routing, Financial Records (Finora) are not built --
  no onboarding workflow, performance-history source, delivery/receipt
  data, or approval-rule configuration exists yet to build them against
  honestly.
- **Baseline-vs-memory-enabled comparison has an honest, measured
  finding, not a "win."** `app/procurement/evaluation.py` runs both modes
  against the seeded backend: for a single task, there is **no** measured
  difference in completion or provenance coverage, because this
  coordinator's control flow doesn't yet consult memory to skip ERPNext
  calls -- memory adds a small measured latency cost and a durable,
  queryable provenance record, not a same-task speedup. See the module's
  docstring. A fuller study (multi-task reuse, stale/conflict injection)
  is still open.
- **Purchase Request interpretation** (free-text "20 laptops" -> structured
  quantity/item) uses a real LLM when available, with a regex fallback --
  the regex path is a heuristic, not a parser, and is only exercised when
  the LLM path is unavailable.
- **Frontend covers the spec's screens functionally, not with every
  listed field** (e.g. no live "routing/retries/errors" feed beyond one
  task's messages; System Overview is static).

## Frontend (React + TypeScript)

The UI is a Vite + React + TypeScript app in `web/` (landing page, chat workspace, observability dashboard), served by
FastAPI at `/app` once built:

```
cd web
npm install
npm run build      # type-checks, then writes web/dist (served at http://127.0.0.1:8000/app/)
npm run dev        # optional: hot-reload dev server on :5173, proxying the API on :8000
```

If `web/dist` does not exist, the server falls back to the previous static pages in `frontend/`.

## Observability (Langfuse)

Every chat message, task run and approval is traced to Langfuse (`app/observability/langfuse_tracing.py`), following the
platform standard in `langfuse.md`: names are `Procurement Coordinator - <step>`, tags `agent:<slug>` / `env:<env>`,
`user_id` comes from the `X-User-Id` header, `session_id` is the conversation id, and failures are marked `ERROR`.
Model calls (Router, narration, extractor, summarizer), the Vendra/Procura/Finora steps and ERPNext tool calls are
child observations. Set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`, `AGENT_ID`, `ENVIRONMENT`
(see `.env.example`); `LANGFUSE_ENABLED=0` turns it off and `LANGFUSE_CAPTURE_CONTENT=0` withholds prompt/reply text.

The dashboard page is `frontend/observability.html` (linked from the landing page and the chat top bar). It reads the
Langfuse v2 `metrics` / `observations` API through admin-only `GET /observability/{status,summary,recent}`; the keys
never reach the browser.

## Memory system verification

`python scripts/verify_memory_system.py` checks every memory feature against the real
Supabase using throwaway, tagged data that it deletes afterwards: versioning and
supersession, provenance tagging (exact source document, content hash), the
Vendra -> Procura -> Finora memory path (relay, supplier selection from received
verdicts, Finora's evidence check), stale detection (shared copies across two hops,
document expiry), conflict detection and resolution between sources (an email vs an
updated PDF; equal sources needing a person's decision), summaries and summary-first
retrieval (Procura's quotation history; conversation memory), vector search, the whole
live pipeline end to end with seeded ERPNext (nothing reaches the real one), and an
audit of the real data. `--keep` leaves the rows for inspection; `--no-llm` uses
extractive summaries; `--cleanup-leftovers` removes anything an interrupted run left.
Known gaps it reports: embeddings are not generated until `OPENAI_API_KEY` is set, and
conflict *edges* need `migrations/008_allow_conflict_relationships.sql`. See
`PROJECT_OVERVIEW.md` section 3.6 for the design.

## Safety notes

* The configured Supabase project is shared and long-lived; treat every
  write as a production write.
* `migrations/005` (direct-table-write lockdown) and `006` are drafted but
  not applied -- DB-level write isolation for the procurement system's
  richer fields is not yet in place; apply 005/006 only with explicit
  review, per the migrations themselves.
* On Windows the console defaults to cp1252 and cannot print characters
  such as `₹`. `app/utils/console.enable_utf8_console()` fixes this; the
  entry points call it automatically.
* The local ERPNext test instance (`C:\Users\surya\erpnext_test_instance`)
  is a disposable, localhost-only Docker Compose stack with a default
  `admin`/`admin` login and a plaintext MariaDB root password, per the
  official quickstart's own design for local demos. Never exposed beyond
  `localhost:8082`; do not reuse these defaults for anything reachable
  from outside this machine.

## Vendor email (Composio Gmail)

After a purchase order is submitted, a PO email to the vendor is **drafted** automatically; when it is sent, the purchase order goes as a **PDF attachment** (built with reportlab, uploaded through Composio) and the body is a short cover note (never sent by chat or by the pipeline). A person then sends it, and replies and follow-ups are tracked. Every email and every vendor reply is stored as a Procura memory with `source_type="email"`.

- `POST /procurement/mail/draft` `{pr_id}`: draft the PO email for an approved request
- `POST /procurement/mail/{id}/send` `{confirm:true}`: send it (needs `MAIL_ALLOW_SEND=1`)
- `POST /procurement/mail/check-replies`: read open threads, record vendor replies, mark mails `replied`
- `POST /procurement/mail/followups` `{confirm:true}`: reminder in the same thread for mails unanswered after `MAIL_FOLLOWUP_DAYS` (max `MAIL_MAX_FOLLOWUPS`)
- `GET /procurement/mail`, `GET /procurement/mail/status`

**In the chat:** say *"give me the selected vendor information"* for the supplier's real details (ERPNext email, phone, website, quotation), and *"send the purchase order email to the vendor"* to get the email as a card with the PDF attached; it is sent only when you press **Send email**. Both are recognised by rules in `chat_router.py`, not by the LLM.

All are admin-only. Set `COMPOSIO_API_KEY` (a consumer key, sent as `x-consumer-api-key` to `connect.composio.dev/mcp`) and `COMPOSIO_GMAIL_ACCOUNT` (the connected account to send from). While testing, set `MAIL_TEST_RECIPIENT` so every mail goes to that address instead of the vendor. Vendor addresses come from the vendor master `email` field.
