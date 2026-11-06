# Procurement Coordinator

**A multi-agent procurement platform with persistent, provenance-tracked memory.**

*Final project report · November 2026*

---

## Contents

1. [Problem Statement](#1-problem-statement)
2. [Solution](#2-solution)
3. [Technical Architecture](#3-technical-architecture)
4. [End-to-End Workflow Example](#4-end-to-end-workflow-example)
5. [Results](#5-results)
6. [Metrics](#6-metrics)
7. [Key Design Decisions](#7-key-design-decisions)
8. [Known Limitations](#8-known-limitations)
9. [Running the Project](#9-running-the-project)
10. [Repository Layout](#10-repository-layout)
11. [Appendix: Agent System Prompts](#11-appendix-agent-system-prompts)

---

## 1. Problem Statement

Enterprise procurement covers sourcing a vendor, checking its compliance, collecting
quotes, checking the budget, getting approval and raising a Purchase Order. It is
usually a manual process that takes several days and is spread across email,
spreadsheets and an ERP system. Three problems drove this project:

1. **No single place to ask for something.** A requester has to know which system
   handles vendor compliance, RFQs and budget sign-off, and run them in the right order
   by hand.
2. **No audit trail for "why did the system say that".** A recommended supplier, a cost
   or an approval usually can't be traced back to the record that justified it.
3. **No safe way to automate the routine checks.** Checking compliance, comparing
   quotes and checking a total against a budget limit are either left manual or done by
   one-off scripts that lose the auditability of a human process.

A fourth problem appears as soon as agents are involved: **agents forget.** A task
that pauses for missing information, or outlives a browser session or a server
restart, has to be picked up exactly where it stopped, with its history intact.

## 2. Solution

A **multi-agent coordinator** with **persistent memory**:

- One plain-language instruction, such as *"Purchase 10 laptops with 16 GB RAM, within
  ₹6,00,000, delivered to our Mumbai office within 20 days"*, is parsed and saved as a
  durable, versioned task.
- The task runs through three specialist agents in a fixed order: **Vendra** (vendor
  eligibility), then **Procura** (sourcing and quotations), then **Finora** (budget and
  approval). Each agent is a small orchestrator running its own pipeline of
  sub-agents.
- Every agent writes what it learns into its own **versioned, provenance-tracked memory**
  in Postgres. Nothing is overwritten; every fact records the source and task that
  produced it.
- The system talks to a **real ERPNext instance** through Frappe's official MCP endpoint,
  and can create a real Purchase Order once a purchase is approved.
- A single LLM (NVIDIA Nemotron) is used only for language: routing ambiguous chat
  messages, narrating results and answering questions. **Every number, eligibility
  check and approval decision is deterministic code.**

## 3. Technical Architecture

### 3.1 Stack

| Layer | Technology |
|---|---|
| Backend API | FastAPI (Python), served by uvicorn |
| Agent orchestration | LangGraph `StateGraph`, deterministic routing |
| LLM | One shared model, `nvidia/nemotron-3.5-lightning-30b-a3b` |
| Persistent memory | Supabase (Postgres + pgvector), `agent_memories` table with version chains and a provenance graph |
| ERP integration | ERPNext through Frappe's official MCP endpoint (`/api/v2/mcp`) |
| Frontend | Static HTML/CSS/JS served by FastAPI at `/app` (landing page + chat) |
| Streaming | Server-Sent Events from `POST /procurement/chat/stream`: LLM tokens and live pipeline progress |
| Conversation history | Browser `localStorage` (client-side only) |
| Auth | API tokens: one admin token plus per-agent scoped tokens; the bundled UI is authorized server-side |
| Tracing | Langfuse (on when its keys are set; `LANGFUSE_ENABLED=0` turns it off) |
| Tests | pytest: unit tests plus opt-in integration tests against real Supabase |

### 3.2 System diagram

```
                         Browser (landing page + chat UI)
                                      │  POST /procurement/chat
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ Router  (chat_router.py)                                                  │
│   1. deterministic short-circuit: waiting task + not a new instruction    │
│      → provide_info (no LLM)                                              │
│   2. otherwise LLM classification → new_task | provide_info | answer |    │
│      unsupported_action                                                   │
└───────────────────────────────┬───────────────────────────────────────────┘
                                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ Task Orchestrator  (task_orchestrator.py)                                 │
│   regex parsing of qty / budget / currency / location / deadline          │
│   creates or resumes a versioned Purchase Request  ──────────────┐        │
└───────────────────────────────┬──────────────────────────────────│────────┘
                                ▼                                  │
┌───────────────────────────────────────────────────────────────┐  │
│ Coordinator  (coordinator.py) · LangGraph StateGraph          │  │
│ fixed order, no cycles, stops cleanly on failed / blocked     │  │
│                                                               │  │
│   VENDRA ──eligible suppliers──▶ PROCURA ──supplier+total──▶ FINORA
│   6 sub-agents                   6 sub-agents                6 sub-agents
│      │                              │                           │
└──────┼──────────────────────────────┼───────────────────────────┼─────────┘
       │ read/write                   │ read/write                │ read/write
       ▼                              ▼                           ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ Persistent memory  (Supabase · agent_memories + memory_provenance)        │
│ per-agent spaces · atomic versions · provenance edges · vector search     │
└───────────────────────────────────────────────────────────────────────────┘
       │                              │
       ▼                              ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ ERPNext  (Frappe MCP) · Suppliers, files, Items, RFQs, Supplier           │
│ Quotations, Purchase Orders                                               │
└───────────────────────────────────────────────────────────────────────────┘
                                ▲
      Approval gate (human, or live-mode auto-approve policy)
      → submit_purchase_order → real Purchase Order
```

### 3.3 Request lifecycle

1. The chat UI posts the message, the active task ID (if any) and a short rolling
   history (and, in live mode, the conversation ID) to `POST /procurement/chat/stream`.
   A non-streaming `POST /procurement/chat` with identical behaviour is kept for API
   clients.
2. The request is authorized (see §3.8).
3. In live mode the stored summary of the conversation and the turns after it are
   recalled from memory (§3.6). The **router** then decides what the message needs
   (§3.5).
4. For a new or resumed purchase, the **task orchestrator** parses the request and saves
   or updates the Purchase Request in memory **before** any agent runs.
5. If anything required is missing, the task stops in `Waiting For Information` and the
   reply names exactly what is missing. Otherwise the **coordinator** runs Vendra →
   Procura → Finora against ERPNext.
6. The outcome is saved as a new version of the task, and the evidence behind a
   recommendation is shared along the Vendra → Procura → Finora path (§3.6). In Live
   mode, an approved recommendation leads to a real Purchase Order in ERPNext.
7. The chat turn is stored in memory and, every few turns, folded into the
   conversation's summary (in the background).
8. Progress and answer text are streamed to the chat while this happens (§3.11). The
   final reply is rendered as a structured task card or a formatted answer.

Task states: `Running`, `Waiting For Information`, `Waiting For Clarification` (two sources
disagree about a price that could change the winner), `Waiting For Approval`, `Blocked`,
`Failed`, `Completed`.

### 3.4 Startup

On boot the backend reads its settings from environment variables, selects the ERPNext
backend that is actually configured (official MCP, direct REST, or the synthetic demo
client, with no silent fallback between them) and mounts the static frontend at `/app`.
There are no scheduled jobs or background workers; the system is purely
request/response.

### 3.5 Orchestrator, agents and sub-agents

There are two orchestration layers.

**Layer 1: the router** (`chat_router.py`) decides what kind of message it received:

1. **Deterministic short-circuit.** If a task is `Waiting For Information` and the
   message is neither a new instruction nor a question, it is treated as the missing
   value without calling the LLM. When this was left to the LLM, it misclassified
   roughly one message in three.
2. **LLM classification** for everything else, into exactly one of `new_task`,
   `provide_info`, `answer` or `unsupported_action`. The classification is grounded in
   the real architecture and the current task's data.
3. The intent decides the next step: run the pipeline, resume a waiting task, answer
   directly, or decline honestly.

**Layer 2: the coordinator** (`coordinator.py`) is a LangGraph `StateGraph`. It runs only
the agents a task needs, always in dependency order, and passes results forward
through shared state:

| Hand-off | State key |
|---|---|
| Vendra → Procura | `eligible_supplier_names` |
| Procura → Finora | `recommended_supplier`, `recommended_total_cost` |

Routing between agents is a plain Python function, so circular agent loops are
impossible by construction rather than prevented by a step cap.

**3 agents, 18 pipeline sub-agents, plus 1 approval-gated action:**

| Agent | Role | Sub-agents |
|---|---|---|
| **Vendra** (`systems/vendra.py`) | Vendor discovery and compliance: decides who is eligible before anything else runs | `vendor_discovery`, `document_verification`, `vendor_onboarding_readiness`, `vendor_profile`, `vendor_performance_snapshot`, `vendor_risk_flagging` (6) |
| **Procura** (`systems/procura.py`) | RFQs and quotations: collects and ranks real quotes from eligible suppliers only | `rfq_management`, `quotation_processing`, `quotation_comparison`, `supplier_matching`, `delivery_schedule_estimation`, `quotation_history` (6) + `submit_purchase_order` (runs only after approval) |
| **Finora** (`systems/finora.py`) | Budget and finance: checks real numbers before anything is recommended | `budget_verification`, `cost_analysis`, `invoice_matching`, `payment_terms_review`, `approval_routing`, `financial_records` (6) |

Every sub-agent is a plain function `(context, results) -> dict` run through a shared
pipeline (`subagents/base.py`). An exception in one sub-agent is turned into an `error`
result instead of crashing its agent. Two of Vendra's sub-agents, discovery and
document verification, are treated as critical: if they fail, the agent reports a
failure rather than an empty success.

An optional narration layer (`narration.py`, `PROCUREMENT_AI_SUMMARIES=1`) lets each
agent's LLM describe its already-computed result in plain language. It can describe
the result but never change it.

### 3.6 Persistent memory

Memory is the backbone of the system. It reuses the existing `agent_memories` table
instead of adding a separate schema, so every procurement entity gets versioning and
provenance automatically.

**Properties**

| Property | How it works | Verified against the real database |
|---|---|---|
| Scoped per agent | An agent's identity is bound in code (`build_agent_tools`) and never passed by the model, so one agent cannot read or write another agent's memory | unit tests |
| Atomic versioning | `save_memory_atomic`: identical content is a no-op; changed content supersedes the active version and creates the next one in a single transaction. Old versions are kept. Everything that can change (price, delivery days, payment terms, a document's validity) is part of the text, so a change is always a new version | yes |
| Provenance tagging | Every row records who made it (`agent_id`, `source_agent_id`), when (`observed_at`, `created_at`), the **exact source document** (`source_reference`, e.g. `Supplier Quotation/PUR-SQTN-3`, `acme_quote_v2.pdf`, `Supplier/ACME`), a **content hash** (`source_hash`), the kind of source (`source_type`), its `verification_status`, the `task_id`, a `change_reason` and a structured fact | yes |
| Controlled propagation | `share_memory_atomic` copies a memory into another agent's space as a new row (`hop_depth` + 1, `source_reference` = its source) with a `derived_from` edge tagged with task, purpose and `accepted`. `memory_share_policy` decides who may share with whom (anything unlisted is denied) and a hop limit stops long chains | yes |
| **Propagation the agents use** | In live mode the coordinator relays Vendra's verdicts to Procura; **Procura selects its suppliers from the verdicts it received** (a stale, expired or non-compliant verdict excludes the supplier); the evidence goes on to Finora, which **refuses to proceed if its copy is no longer current** (§3.6, "The memory path") | yes, with the real pipeline |
| Stale detection | A memory is stale if it was superseded or archived, or its `valid_until` has passed. A shared copy is stale when its source, **or any ancestor along the path**, was superseded. A quotation claim from an ERPNext document that no longer exists is archived. Computed at read time | yes |
| **Document expiry** | A supplier document's validity comes from its file name (`GST_Certificate_valid_until_2027-03-31.pdf`) or from a recorded verification (who checked it, until when). An expired document makes the supplier non-compliant; the verdict memory carries `valid_until` = the first document's expiry, so it **goes stale on its own** when that date passes | yes |
| Stale-copy refresh | Re-sharing the current source makes a new version of the copy with a `supersedes` edge; refresh runs in path order (Procura's copy first, then Finora's) | yes |
| **Conflict detection and resolution** | Every quotation is kept as a *claim* (`subject_key` = `quote:<item>:<supplier>`). Claims for one subject that disagree are resolved by **source authority** (formal document > email > chat), then by **document date** (an updated quotation replaces an older one). If that cannot decide (equally authoritative sources with no date order, or a *newer but less authoritative* claim), the task **waits for clarification** and the user chooses in the chat. The loser is kept and marked `disputed`; the decision is recorded on the claims and, when the database allows, as `conflicts_with` / `resolved_by` edges | yes (edges need migration 008) |
| Summaries | A summary row records the memories it covers (`memory_ids`) and how far it reaches (`covered_observed_at`). It is classified **fresh**, **partial** (newer memories exist), **stale** (a covered memory changed, expired or was deleted) or **unknown** (legacy row). Written by the LLM using only the given memories; without an LLM, a labelled extractive fallback | yes |
| Summary-first retrieval | `get_relevant_context` returns a usable summary followed by the memories newer than it; a stale summary is never used. **Procura's `quotation_history` specialist does this for an item's quotation claims**: stored summary and newer claims first, then it opens the supporting records and reports what changed (a price or delivery time that moved) | yes |
| Vector search | `match_agent_memories` (pgvector cosine, 1536 dimensions), limited to the agent's own active memories | yes, with test vectors |
| Embedding generation | `embed_or_none` uses OpenAI embeddings when `OPENAI_API_KEY` is set | **not active**: no key is configured, so memories saved by the agents carry no embedding (see §8) |

**Memory keys used by the system**

| Key | Owner | Holds |
|---|---|---|
| `purchase_request:<id>` | Procura | The full task: quantity, budget, item code, state, blockers, coordinator result, Purchase Order. Each step is a new version |
| `supplier_eligibility:<supplier>` | Vendra | Compliance verdict per supplier, with `structured_fact` = supplier, compliance, risk |
| `quotation_comparison:<item>` | Procura | The ranked quotation comparison for an item, with the exact quotation documents it used and a content hash |
| `quote:<item>:<supplier>:<source type>:<ref>` | Procura | One quotation **claim** (ERPNext document, PDF, email, ...) with its source, document date and recorder; versions keep price/delivery history |
| `document_validity:<supplier>:<document>` | Vendra | Who verified a supplier document and until when it is valid |
| `financial_validation:<supplier>` | Finora | The budget check |
| `vendor:<code>` | Vendra | Vendor master record |
| `approval:<id>` | Finora | Each approval decision, human or `SYSTEM-AUTO-APPROVAL`, always labelled as which |
| `shared_<key>` / `shared_shared_<key>` | Procura / Finora | Copies of the above received along the sharing path (hop 1 / hop 2) |
| `chat:<conversation>:<turn>:<id>` | Coordinator | One chat exchange (see below) |

**Example row**

```
agent_id               "vendra"
memory_key             "supplier_eligibility:ACME-TECH"
version                2
status                 "active"
content                "ACME Tech (ACME-TECH): compliant"
source_type            "erpnext_doctype"
verification_status    "verified"
task_id                "PR-7F3A"
sharing_classification "shareable"
structured_fact        { supplier, compliance, risk }
embedding              vector(...)
```

*(Column names are the real schema; the values are illustrative.)*

**The memory path in the live pipeline.** In live mode the agents do not just write memory; they use it:

```
Vendra   supplier_eligibility:<s>   --coordinator relays, hop 1-->   Procura   (selects suppliers from what it received)
Procura  shared_supplier_eligibility:<s>   --hop 2-->                 Finora    (refuses to proceed if it is stale)
Procura  quotation_comparison:<item>       --hop 1-->                 Finora
```

1. **Relay.** After Vendra runs, the coordinator shares Vendra's verdict for each eligible
   supplier with Procura (each share is a `derived_from` edge tagged with the task and
   "relayed by the coordinator"). In a task where Vendra does not run, the coordinator
   first relays whatever verdict Vendra holds for each candidate, compliant or not, so a
   supplier with an expired document is still seen as such.
2. **Selection.** Procura chooses suppliers from the copies it received: a copy that is
   stale (its source changed, or the documents behind it expired) or not "compliant"
   excludes the supplier, even if another list says otherwise. A supplier with no copy at
   all is kept and reported. The basis is returned with the result and shown in the chat.
3. **Reconciliation.** Procura records every quotation as a claim, reconciles claims that
   disagree, and only then compares (see "Conflict detection" above).
4. **History.** `quotation_history` reads the summary of the item's quotation claims
   first, then opens the supporting records and reports changes.
5. **Check.** The evidence is forwarded to Finora (Vendra's verdict at hop 2, the
   comparison at hop 1). Before validating the budget Finora checks that the verdict it
   holds is still current and stops the task if it is not.

Every step degrades safely and visibly: a failed share or read is reported in the live
trace and never fails the task (writes are retried once). Switch the path off with
`PROCUREMENT_MEMORY_PROPAGATION=0`. Demo mode never uses it (demo data never reaches the
database). The task states include **Waiting For Clarification** (a price conflict that
could change the winner; resolve it, then the run resumes).

**Conversation memory.** In live mode every chat turn is stored as a memory under the
`coordinator` identity (tagged `source_type=chat`, `verification_status=unverified`,
linked to the task it concerned). After every 4 turns the conversation is folded into a
rolling summary. When the router handles the next message it receives that summary plus
the turns after it, so a long conversation is compressed rather than cut off, and it
survives a closed tab or a restarted server. The router treats this only as what was
said, never as a source of supplier, price or compliance facts. Deleting a conversation
in the sidebar deletes its turns and summaries from the database
(`DELETE /procurement/chat/conversations/{id}`). Demo mode stores nothing. This means
chat text is stored in Supabase.

**Memory API** (per-agent scoped tokens work for their own agent)

| Endpoint | Purpose |
|---|---|
| `POST /agents/{id}/context` | Summary-first retrieval: summary + newer memories, stale items excluded |
| `POST /agents/{id}/summaries/refresh` | Create or update the agent's summary |
| `GET /agents/{id}/memory-health` | Counts, the summary's state (fresh / partial / stale), and stale shared copies |
| `POST /agents/{id}/stale-copies/refresh` | Re-share the current version of every stale copy |
| `POST /agents/{id}/memories/{memory_id}/share`, `GET .../ancestry`, `GET .../descendants` | Sharing and provenance traversal |
| `POST /procurement/quotation-claims` | Record a quotation from any source (email, PDF, ...) with its source, document date and content hash; returns whether it conflicts with another source |
| `GET /procurement/quotation-claims?item_code=` | List the claims for an item |
| `GET /procurement/conflicts?item_code=` | Every disagreement between sources on an item, decided or not |
| `POST /procurement/conflicts/resolve` | A person decides which claim is right (overrides the rules) |
| `POST /procurement/vendors/{supplier}/document-validity` | Record that a supplier document was verified valid until a date |

**Verification.** `python scripts/verify_memory_system.py` exercises every item above
against the real Supabase with throwaway, tagged data and deletes it afterwards
(confirming zero residue): versioning, provenance tagging, propagation (hop limit, denied
pairs, relay, selection, the evidence check), stale detection (including copies across two
hops and refresh), **document expiry** (file-name and recorded validity, renewal, a verdict
that goes stale by itself), **conflicts** (an email at 50,000 vs an updated PDF at 53,000;
equal sources needing a decision; a person's decision sticking; a delivery time changing
15 to 25 days), **the whole live pipeline end to end** with the real agents and real
memory (an expired supplier excluded, a conflict making the task wait for clarification,
the decision resuming it, Finora's evidence check, the quotation history and a
summary-first read on the next run), summaries, chat memory with a question answerable
**only** from the stored summary, vector search and an audit of the real data. A transient
network error retries the section once. `--keep` leaves the rows for inspection;
`--no-llm` uses extractive summaries; `--cleanup-leftovers` removes anything an
interrupted run left behind.

**Demo-mode isolation.** Demo tasks never reach this table. They are kept in an
in-memory store that is lost on restart by design. A real bug found in a code audit
had demo tasks leaking into production storage; it was fixed and is covered by
regression tests.

**Schema migrations:** `001_harden_schema` → `002_memory_functions` →
`003_verify_after_apply` → `004_fix_parent_memory_id_fkey` →
`005_restrict_direct_table_access` → `006_procurement_provenance_fields` →
`007_register_coordinator` → `008_allow_conflict_relationships` (**not yet applied**: see §8;
conflicts work without it).

### 3.7 External integrations

- **ERPNext:** Frappe's official built-in MCP endpoint is used for suppliers, items,
  RFQs, supplier quotations and Purchase Orders. Every list call forces a full page
  (`limit_page_length: 0` / `limit: 500`) because ERPNext otherwise silently returns only
  20 rows. Attaching a compliance file uses ERPNext's `upload_file` endpoint, since the
  generic MCP tool can't upload files. Writes require `ERPNEXT_ALLOW_WRITES=1`.
- **LLM:** NVIDIA Nemotron, called directly. Every call in the project shares one
  completion budget (`NVIDIA_MAX_COMPLETION_TOKENS`, raised from 512 to 2048 after
  replies were being silently truncated).
- **Not integrated:** email, calendar and SSO. Requests that need them are declined
  honestly rather than faked.

### 3.8 Security and access control

- **Admin token** (`MEMORY_API_TOKEN`) can do anything. **Scoped tokens**
  (`MEMORY_API_TOKEN_VENDRA` etc.) can only touch their own agent's resources. Every
  route that reads or changes a Purchase Request or vendor record requires the admin
  token. A code audit found that scoped tokens could previously reach arbitrary records;
  this was fixed and is covered by 13 regression tests.
- **The browser never holds a token.** With `UI_LOCAL_AUTH=1`, the server uses its own
  admin token for the bundled UI, but only when all of these are true:
  - the request comes from 127.0.0.1;
  - the Host header is `127.0.0.1` or `localhost`, which blocks DNS-rebinding;
  - the Origin header, if present, is also local.

  Spoofed hosts and foreign origins get `401`. This setting must stay off behind a
  reverse proxy, where every request would appear to come from the same machine.
- **Cross-site requests:** there is no CORS configuration, and FastAPI only accepts JSON
  bodies sent with a JSON content type, so other websites can't drive the API from a
  user's browser.
- **Approvals** are checked against the agent pipeline's real recommended cost, never
  the requester's original estimate. The approver role is self-declared and flagged
  `identity_verified: false`.
- **Secrets** live only in `.env`; nothing secret is written into frontend files.

### 3.9 Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `SUPABASE_URL`, `SUPABASE_KEY` | Persistent memory database |
| `MEMORY_API_TOKEN` | Admin API token |
| `MEMORY_API_TOKEN_VENDRA` / `_PROCURA` / `_FINORA` | Per-agent scoped tokens |
| `UI_LOCAL_AUTH` | Lets the bundled UI work without a browser-held token (local only) |
| `NVIDIA_API_KEY`, `AGENT_LLM_MODEL` | LLM provider and model |
| `ERPNEXT_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET` | ERPNext connection |
| `ERPNEXT_USE_OFFICIAL_MCP` | Use Frappe's official MCP endpoint |
| `ERPNEXT_ALLOW_WRITES` | Safety switch: allow writes to ERPNext |
| `LIVE_MODE_AUTO_APPROVE` | Safety switch: let policy approve live purchases (labelled in the audit trail) |
| `PROCUREMENT_AI_SUMMARIES` | Turn on agent narration |
| `PROCUREMENT_MEMORY_PROPAGATION` | Share the evidence behind a live recommendation along the Vendra → Procura → Finora path (on by default; `0` turns it off) |
| `MEMORY_ALLOW_WRITE` | Allow integration tests to write to real Supabase |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` | Langfuse project keys and host (tracing and the observability page) |
| `AGENT_ID`, `ENVIRONMENT` | The one slug and environment stamped on every trace (default `procurement-coordinator`, `development`) |
| `LANGFUSE_ENABLED`, `LANGFUSE_CAPTURE_CONTENT` | `0` switches tracing off / sends no prompt or reply text to Langfuse |

The two safety switches are off unless deliberately turned on.

### 3.10 Frontend

A Vite + React + TypeScript single-page app in `web/` (react-router, framer-motion, recharts, react-markdown),
built with `cd web && npm run build` into `web/dist` and served by FastAPI at `/app` (client-side routes
`/app/chat` and `/app/observability` survive a reload). If `web/dist` is absent the server falls back to the
previous static pages in `frontend/`. One design system (tokens in `web/src/styles/tokens.css`, dark and light
themes) is shared by the three pages:

| Page | Route | What it is |
|---|---|---|
| Landing | `/app/` | Product overview: animated hero with a live-style preview, the interactive 20-node end-to-end trace graph, the memory model, agents, security, FAQ |
| Chat | `/app/chat` | Streaming chat workspace: live run trace, task cards with approval and conflict-resolution actions, conversation history (stored in the browser), Connections and Item codes panels |
| Observability | `/app/observability` | Langfuse dashboard: KPIs, activity, cost by step, users, steps, and traces, sessions, model calls and errors with a record drawer (see 3.12) |

The browser never holds an API token: the backend authorizes the bundled UI itself (`UI_LOCAL_AUTH`, loopback only).
Chat conversations are saved in `localStorage` under `procurement_conversations_v2`; history saved by the earlier
static page is not carried over.

### 3.11 Streaming responses

`POST /procurement/chat/stream` returns Server-Sent Events while the request runs:

| Event | Payload | Sent when |
|---|---|---|
| `status` | `{stage, text}` | A real milestone happens: router decision, task saved or recalled, each agent starting and finishing (with its actual result), approval, Purchase Order, final state saved |
| `token` | `{text}` | A piece of an LLM answer is generated |
| `final` | Same body as `POST /procurement/chat` (`{reply}` or `{pr}`) | The request is complete; this is always the authoritative result |
| `error` | `{detail}` | Something failed; reported instead of a silently dead stream |

How it works:

- **Answers.** The router's single LLM call is streamed. Its output is a JSON object, so
  a small incremental parser (`_ReplyStreamer`) forwards only the decoded `reply` text
  as it arrives, handling escapes split across chunks. No second LLM call is made.
- **Purchase tasks.** These are deterministic and don't generate text, so they stream
  progress instead. Pipeline code calls `progress.emit(stage, text)`; the streaming
  route installs a listener through a `ContextVar`, which follows the request into
  LangGraph's worker threads and never leaks between requests. Without a listener,
  `emit` does nothing.
- **In the browser**, answers appear token by token with a caret. Pipeline steps appear
  as a live checklist with a spinner on the current step, then fold into a collapsible
  "Live run trace" above the task card.

Example of a real streamed trace (demo mode):

```
router    Router: reading your message
router    Router: new purchase request
memory    Task PR-CF990710 saved to the isolated demo store (version 1)
orch      Orchestrator: running Vendra → Procura → Finora for 10 × DEMO-ITEM-…
vendra    Vendra started: checking supplier eligibility (6 sub-agents)
vendra    Vendra finished: 3 of 5 candidate supplier(s) eligible
procura   Procura started: collecting and ranking quotations (6 sub-agents)
procura   Procura finished: recommends DEMO-VENDOR-0006 at 516000.0
finora    Finora started: checking budget and approval authority (6 sub-agents)
finora    Finora finished: budget and approval authority checked
memory    Outcome saved to the isolated demo store as a new version: Waiting For Approval
```

### 3.12 Observability (Langfuse)

**Tracing** (`app/observability/langfuse_tracing.py`) follows the platform tracing standard
(`langfuse.md`). Each request is one trace; everything inside it is a child observation.

| Item | Value |
|---|---|
| Trace / step names | `Procurement Coordinator - <step>`, for example `... - Chat`, `... - Router`, `... - Vendra`. Cost is attributed by the part before ` - `. |
| Tags and metadata | `agent:<slug>`, `env:<environment>`; metadata `{slug, kind: "agent"}` |
| `user_id` | The `X-User-Id` header when a caller sends one, otherwise anonymous (never a constant) |
| `session_id` | The chat conversation id, so one conversation groups into one session |
| Traces started by | Chat (plain and streamed), Run Task, Resume Task, Approval |
| Child observations | The three agent steps, every model call (Router, narration, request extractor, memory summarizer) as a generation with model and token counts, and each ERPNext tool call (document type and name only, never the payload) |
| Failures | Marked `ERROR` (or `WARNING` for a 4xx), with the message, so they reach the errors view |

Tracing never raises into the app, is a no-op when the keys are missing, and flushes on shutdown.
The streaming route opens its trace inside the worker thread, because tracing context does not
cross into a new thread. Unit tests run with tracing off.

**Observability page** (`frontend/observability.html`, linked from the landing page and the chat
top bar) shows Langfuse's own data inside the project. It calls admin-only endpoints on the server,
which call the Langfuse public API (v2 `metrics` and `observations`), so the keys never reach the
browser and raw observation metadata (which holds the public key) is never returned.

| Endpoint | Shows |
|---|---|
| `GET /observability/status` | Connection, project, agent slug, environment |
| `GET /observability/summary?days=` | Totals (observations, cost, tokens, latency, errors, warnings), cost by agent and step, per-day activity, users, observation types |
| `GET /observability/recent?kind=traces\|sessions\|generations\|errors` | Recent traces, sessions (grouped by session id), model calls, errors and warnings |

## 4. End-to-End Workflow Example

This is the trace shown as an interactive node graph on the landing page. Memory keys,
states and transitions are the ones the system really uses; IDs, names and amounts are
illustrative.

**Session 1 · Day 1, 10:02**

| # | Actor | What happens |
|---|---|---|
| 1 | Requester | *"Purchase 10 laptops with 16 GB RAM, within ₹6,00,000, delivered to our Mumbai office within 20 days."* |
| 2 | Router | `new_task`; regex parsing gives `qty=10 · budget=600000 INR · location="Mumbai" · due=+20d` |
| 3 | Memory · write | `procura / purchase_request:PR-7F3A · v1 · state="Waiting For Information"`, saved before any agent runs |
| 4 | Orchestrator | Pipeline held: Live mode needs an exact ERPNext item code, and none is guessed |
| 5 | Reply | *"I need the exact ERPNext item code."* |
| 6 | Wait | Browser tab closed, API server restarted. Only memory and ERPNext survive |

**Overnight · vendor side**

| # | Actor | What happens |
|---|---|---|
| 7 | Vendor | ACME Tech, recorded as non-compliant by an earlier task, uploads its missing GST certificate |
| 8 | ERPNext | `File gst_certificate.pdf → Supplier/ACME-TECH` |

**Session 2 · Day 2, 09:40**

| # | Actor | What happens |
|---|---|---|
| 9 | Requester | *"Use item code LAPTOP-14IN."* |
| 10 | Router | `provide_info` through the deterministic short-circuit, no LLM |
| 11 | Memory · read | Task recalled from the database, not the browser: `purchase_request:PR-7F3A v1 → v2 (supersedes)` |
| 12 | Orchestrator | LangGraph pipeline starts: `[vendra, procura, finora]` |
| 13 | Vendra + ERPNext | All six sub-agents run; ACME Tech now passes document verification |
| 14 | Memory · new version | `supplier_eligibility:ACME-TECH v1 disputed → v2 verified`; v1 is kept and linked by a `supersedes` edge |
| 15 | ERPNext · MCP | Supplier Quotations read for eligible suppliers only |
| 16 | Procura | `recommended=ACME-TECH · total=₹5,82,000` |
| 17 | Finora | `within_budget=true · required_role=director` (checked against the real total) |
| 18 | Approval gate | Approved by a human, or by the auto-approve policy (labelled in the audit trail) |
| 19 | ERPNext | `submit_purchase_order` creates Purchase Order `PUR-ORD-2026-00031` |
| 20 | Memory · write | `purchase_request:PR-7F3A · v3 · state="Completed"`; v1 and v2 remain for audit |

The key point: **nothing in Session 2 depends on the browser tab, process or server
instance that handled Session 1.** The only shared state is persistent memory and
ERPNext.

Two caveats about how this differs from what happens live today:

- Live runs currently stop at `Blocked`, because the real suppliers in ERPNext have no
  compliance documents yet (see §8). The vendor upload in steps 7–8 is what would clear
  that.
- Agents re-check ERPNext on every run and save a new version if the result changed.
  They don't read their earlier eligibility records back from memory to decide.

## 5. Results

The following were verified live through the real HTTP API, not only in unit tests:

- One free-text instruction becomes a durable, resumable task, runs through the real
  3-agent pipeline and reaches the approval gate. Demo mode reaches a genuine
  recommendation. Live mode against the real ERPNext instance honestly reaches
  `Blocked` while real compliance documents are missing; it never fabricates a result.
- Tasks survive browser closes and server restarts, and a later follow-up resumes them
  from memory.
- Retrying with the same idempotency key returns the same task, never a duplicate.
- A simulated pipeline exception produces a clean `Failed` state, not a crash.
- Approval authority is checked against the real recommended cost.
- The chat UI works with no browser-held token: in a clean browser, no prompt appears,
  no request carries a token, and real questions get real answers.
- Responses stream: a real model answer arrived as 42 separate token events, and a
  demo purchase streamed 11 live progress events from the actual agents before the
  final task card.
- The frontend was checked with a headless browser at desktop (1440–1500 px) and
  phone (390 px) widths: no console errors and no horizontal overflow.

## 6. Metrics

| Metric | Result |
|---|---|
| Unit tests | **669 / 669 passing** (`pytest tests/unit`); a guard fails any unit test that touches the real database |
| Memory system verification against the real Supabase (`scripts/verify_memory_system.py`) | **90 checks passed, 0 failed**; 2 warnings (conflict edges need migration 008; 14 older rows predate task tagging) and 1 skip (live embedding generation: no provider key). Cleans up after itself: zero residue |
| Integration tests (real Supabase writes, `dbwrite` marker) | 27 passing across task orchestrator, workflow and vendor master, each with verified zero residue after teardown (last run earlier in the project) |
| Agents | 3 (Vendra, Procura, Finora) |
| Sub-agents | 18 pipeline sub-agents + 1 approval-gated action = **19** |
| LLM decisions in eligibility, cost or approval | **0**: the LLM only routes, narrates and answers |
| Memory records overwritten | **0**: every change is a new version |
| Live ERPNext data | 30 enriched suppliers, 6 items |
| Security bugs found and fixed | 2 critical (demo data leaking into production memory; scoped tokens reaching arbitrary records), with 18 regression tests; plus the browser-held token removed (9 new tests) |
| Reliability bugs found and fixed | Router misclassified ~1 message in 3 on the safety-critical `provide_info` decision (now deterministic); shared LLM budget truncating replies (512 → 2048 tokens); ERPNext's silent 20-row page cap hiding most real data; good answers discarded whenever the model replied in prose instead of JSON (users saw "I'm not sure" instead); the router prompt telling the model every agent has 6 specialists (Procura has 5) |
| Frontend bugs found and fixed in final QA | Sticky nav not sticking, horizontal overflow on phones, unusable chat sidebar on phones, markdown lists not rendering, stray whitespace in task cards |

## 7. Key Design Decisions

- **A deterministic pipeline, not an LLM choosing what to call next.** A fixed
  dependency order makes "no circular agent loops" true by construction.
- **Deterministic where it matters.** Parsing numbers, checking compliance, ranking
  quotes, checking budgets and routing the safety-critical follow-up are plain code. The
  LLM handles language only.
- **Memory as the system of record.** Tasks, approvals, vendor records and eligibility
  facts all live in versioned, provenance-tracked agent memory. That makes tasks
  survive restarts and every decision auditable, without a new schema.
- **Persist before acting.** A task is saved before any agent runs, so a crash or
  restart never loses a request.
- **Demo mode is fully isolated** from production storage and ERPNext, enforced by
  tests that fail if a demo call ever reaches the real save path.
- **Never fabricate.** Blocked or failed outcomes are reported plainly; features that
  don't exist are declined rather than simulated.
- **Secrets stay on the server.** The browser UI is authorized server-side and only for
  local requests.

## 8. Known Limitations

1. **Real compliance documents are still missing** for the live suppliers. This is
   the one thing preventing a genuine `Waiting For Approval` outcome in Live mode.
2. **No real SSO or identity.** The approver role is self-declared and flagged
   `identity_verified: false`.
3. **Approval limits are placeholders**, not a real company policy.
4. **No email integration.**
   (Observability) Traces from the chat UI are anonymous (the UI has no sign-in to send as `X-User-Id`),
   and cost shows $0 because Langfuse has no price for the Nemotron model; tokens are recorded.
   Chat text is sent to Langfuse's cloud unless `LANGFUSE_CAPTURE_CONTENT=0`.
5. **Embeddings are not being generated.** The vector column and similarity search work
   (verified with test vectors), but memories saved by the agents have no embedding
   because no embedding provider is configured (`OPENAI_API_KEY` is not set), so they
   are found by key and recency, not by meaning. NVIDIA's hosted embedding models were
   all retired or unavailable for this account when checked. Setting `OPENAI_API_KEY`
   enables it with no code change (the column is 1536-dimensional, matching OpenAI's
   embeddings). Do not mix embeddings from different models in one database.
6. **Eligibility, cost and budget are still recomputed from ERPNext on every run.** Memory
   now decides *which suppliers Procura considers* (from the verdicts it received) and
   whether Finora proceeds (its evidence check), and Procura reconciles conflicting
   claims; but a pipeline never skips an ERPNext call because memory has the answer.
7. **Conflict edges need migration 008.** The live database's relationship rule refuses
   `conflicts_with` / `resolved_by` edges (it is narrower than migration 001 intended).
   Conflicts are detected, resolved and remembered anyway (each decision is stored on the
   claims); `migrations/008_allow_conflict_relationships.sql` must be run in the Supabase
   SQL editor to also record them as queryable provenance edges. The verification reports
   this as a warning until then.
8. **Document expiry needs a date to be recorded.** ERPNext's attached files have no expiry
   field, so a date comes from the file name (`..._valid_until_2027-03-31.pdf`) or from a
   recorded verification (`POST /procurement/vendors/{supplier}/document-validity`). A
   document with neither is treated as valid, as before. The date is compared in UTC.
9. **Quotations reach the system as structured data.** Claims from emails and PDFs are
   entered through the API (or the API's caller extracts the price); the system does not
   itself read the text of a PDF or an email. Source authority tiers (formal document >
   email > chat) are fixed rules, not configurable.
10. **Chat text is stored in Supabase** (live mode, under the `coordinator` identity).
    Deleting a conversation removes it; conversations created before this feature exist
    only in the browser.
11. **`UI_LOCAL_AUTH` is a local-machine convenience.** A shared or networked deployment
    needs real user login instead.
12. **Stale shared copies are detected on demand and when memory is read**, not by a
    background job; `POST /agents/{id}/stale-copies/refresh` refreshes them.
13. **Open audit findings, not yet fixed:**
   - a currency-mismatch edge case in quotation comparison;
   - inverted normalization in spec-match and risk scoring;
   - a deprecated fallback MCP client (`mcp/mcp_client.py`) is still present but unused.

## 9. Running the Project

```bash
# 1. Install
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows; use .venv/bin/pip elsewhere

# 2. Configure
cp .env.example .env      # then fill in Supabase, ERPNext, NVIDIA and API token values
                          # and set UI_LOCAL_AUTH=1 to use the browser UI locally

# 3. Apply migrations 001–006 in migrations/ to the Supabase database (see migrations/README.md)

# 4. Run
.venv/Scripts/python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

- Landing page: <http://127.0.0.1:8000/app/index.html>
- Chat: <http://127.0.0.1:8000/app/chat.html>

Use `127.0.0.1` or `localhost` in the address bar. With `LIVE_MODE_AUTO_APPROVE=1` and
`ERPNEXT_ALLOW_WRITES=1`, a purchase instruction in the chat can create a **real**
Purchase Order in ERPNext.

**Tests**

```bash
.venv/Scripts/python -m pytest tests/unit -q                       # fast, mocked
MEMORY_ALLOW_WRITE=1 .venv/Scripts/python -m pytest -m dbwrite -v  # real Supabase writes, cleaned up
```

## 10. Repository Layout

```
app/
  api/            FastAPI app, auth, procurement routes and schemas
  procurement/
    chat_router.py        Layer 1: what does this message need?
    task_orchestrator.py  parse, persist, start/resume tasks, auto-approve
    coordinator.py        Layer 2: LangGraph pipeline Vendra → Procura → Finora
    systems/              the three agents (vendra, procura, finora)
    subagents/            the 18 sub-agents + shared pipeline (base.py)
    workflow.py           Purchase Request lifecycle, approvals, demo isolation
    vendor_master.py      vendor records in memory
    mcp/client.py         ERPNext clients: official MCP, REST, synthetic demo
    narration.py          optional LLM narration of computed results
    progress.py           live progress events for the streaming chat
    chat_memory.py        conversation turns, summaries and recall (coordinator memory)
    propagation.py        the memory path: relay, supplier selection, evidence forwarding and check
    quotation_claims.py   quotations as claims; reconciliation of sources that disagree
    quotation_history.py  summary-first quotation history and price/delivery changes
    expiry.py, document_validity.py   document validity dates and recorded verifications
  memory/         versioned, provenance-tracked memory (store, versioning,
                  propagation/sharing, search, freshness, provenance fields,
                  summaries, stale-copy detection, summary-first context, conflicts)
  tools/          per-agent memory tools with identity bound in code
  agents/         original multi-agent workflow + shared LLM factory
frontend/         index.html (landing), chat.html, observability.html, app.js, flow.js (node graph), style.css
migrations/       001–007 schema, memory functions, procurement fields, coordinator identity
scripts/          live ERPNext seeding/enrichment, end-to-end checks, and verify_memory_system.py
tests/            unit/ (549) and integration/ (dbwrite)
```

## 11. Appendix: Agent System Prompts

Every LLM prompt in the project, verbatim. This appendix was generated from the running code (the narration prompts were captured from a real pipeline run), not retyped. All calls use `nvidia/nemotron-3.5-lightning-30b-a3b` with `max_completion_tokens=2048` and `{'chat_template_kwargs': {'enable_thinking': False}}` (reasoning traces off).

**Which components have a prompt.** Only six places call the LLM: the router, Procura's request extractor, the three agents' optional narration, and the memory summarizer. **The coordinator and the 18 sub-agents have no prompt at all.** They are deterministic Python, which is why no eligibility, cost or approval decision depends on a model.

| Prompt | Used by | Model's job | Can it change a decision? |
|---|---|---|---|
| A.1 Router | `chat_router.py`, every chat message | Classify the message and write answers | It only picks which existing action runs; `provide_info` is decided in code before the model is called |
| A.2 Request extractor | `systems/procura.py`, `interpret_purchase_request` | Pull quantity and item text from free text when no quantity was given | No; a regex fallback exists, and item codes never come from it |
| A.3 Vendra narration | `systems/vendra.py` | Describe the eligibility result | No; runs after the result is computed, only with `PROCUREMENT_AI_SUMMARIES=1` |
| A.4 Procura narration | `systems/procura.py` | Describe the quotation comparison | No |
| A.5 Finora narration | `systems/finora.py` | Describe the budget check | No |
| A.6 Memory summarizer | `app/memory/summaries.py`, conversation and agent summaries | Compress memories into a summary that records what it covers | No; a summary is only ever used as retrieval context, and a stale one is discarded |
| A.7 Original team | `app/agents/workflow.py` | The general-purpose memory demo the project started from | Not used by the procurement pipeline |

### A.1 Router: system prompt

```text
You are the router in front of a real, deterministic multi-agent procurement system. You never run the agents yourself and never decide anything about which supplier is eligible, what anything costs, or what budget is available -- that is all real, already-computed data given to you as context. Your only job is to decide what this ONE chat message needs.

Real architecture (the ONLY source of truth for any question about it):
Coordinator routes a task to Vendra, then Procura, then Finora. Vendra, Procura and Finora each run 6 specialists in order (18 in total), plus submit_purchase_order, which only runs after approval.

Vendra (vendor discovery/compliance):
- vendor_discovery: which suppliers are even candidates (active, not on hold/disabled).
- document_verification: which candidates have the required compliance documents on file and still valid (an expired document does not count).
- vendor_onboarding_readiness: readiness triage -- does ERPNext have the basics on file.
- vendor_profile: a consolidated profile per candidate (master data + compliance + more).
- vendor_performance_snapshot: price competitiveness for the current item only.
- vendor_risk_flagging: one risk category per supplier, from what ERPNext actually has on file.

Procura (RFQs/quotations/negotiation):
- rfq_management: prepares a real RFQ for the eligible suppliers.
- quotation_processing: collects quotations for the item, filtered to eligible suppliers; keeps each as a claim with its source and reconciles sources that disagree.
- quotation_comparison: deterministic ranking over whatever quotations were collected.
- supplier_matching: which eligible suppliers actually have a quotation on file for this item.
- delivery_schedule_estimation: each considered quotation's promised delivery timeframe.
- quotation_history: reads the stored summary of this item's quotation history first, then opens the supporting records to report changes (live mode only).
- (submit_purchase_order exists but is never run automatically -- only after a human approves.)

Finora (budget/finance):
- budget_verification: compares the estimated cost against a real, configured budget.
- cost_analysis: deeper arithmetic over budget_verification's own result.
- invoice_matching: matches any existing invoices for the supplier.
- payment_terms_review: reviews whatever payment-related fields actually exist.
- approval_routing: formalizes that no purchase order is ever auto-submitted.
- financial_records: assembles the consolidated record the orchestrator saves.

Not yet built at all: sending a real email, calling/contacting a vendor by phone, making a payment.


Reply with JSON only, exactly one of these shapes:
{"intent": "new_task"}
{"intent": "provide_info", "reply": "<short natural acknowledgement, e.g. \"Got it, checking that.\">"}
{"intent": "answer", "reply": "<a helpful, honest, conversational answer, grounded only in the architecture facts above and the task context below>"}
{"intent": "unsupported_action", "reply": "<honestly say this isn't built yet, and what would be needed instead>"}

Rules:
- "new_task" ONLY when the message names an actual item/category and at least a rough quantity or need -- e.g. "purchase 10 laptops", "we need some office chairs", "order more packaging boxes". A bare greeting, small talk, "hi"/"hii"/"hello"/"thanks"/"ok", or anything that does not describe something to buy is NEVER "new_task" -- it is "answer" (reply naturally, e.g. "Hi! Tell me what you'd like to purchase, or ask me anything about the system."). When genuinely unsure whether a vague message is a purchase request, prefer "answer" and ask a clarifying question rather than guessing "new_task" -- a wrongly-started task is worse than asking once.
- Do NOT rewrite or paraphrase the purchase request for "new_task" -- the caller uses the user's own original wording (a separate, deterministic parser depends on specific phrasing like "within 60 lakh" or "within 20 days", and a paraphrase could silently corrupt it).
- "provide_info" whenever the context says a task is waiting on a specific missing detail (e.g. an item code) and this message looks like an attempt to answer that, even a short one like just a code or a number. You do not extract or validate the value yourself -- the caller does that, and the real system (not you) is what checks whether it's actually correct. NEVER judge whether a value "looks like" a real/valid code, item, or ID, and never second-guess or reject it on that basis -- you have no way to actually know what's valid in the real ERPNext instance. If it's remotely plausible as an answer to what's missing, classify it "provide_info" and let the real downstream check decide, rather than answering with your own guess about whether it's valid.
- "unsupported_action" for any request to actually perform a real external action that is not yet wired up -- e.g. "send an email to the vendor", "call them", "pay the invoice". Never claim to have done it or pretend it already happened. You may still offer to draft the text in your reply (drafting is fine; sending is not).
- "answer" for everything else: greetings, small talk, questions, "why"/"what does X mean", asking about the recommendation, the architecture, or general procurement concepts.
- When asked what a specialist or system does, describe EXACTLY what the architecture facts above say -- never substitute generic vendor-management/procurement textbook criteria that aren't listed there, even if they sound plausible.
- Never invent a supplier name, price, compliance fact, or budget number that is not already present in the task context below -- if you don't know, say so plainly.
- "Conversation memory" (when present) is a stored summary of earlier turns plus the turns after it. Use it to follow what the user means by "that one" or "the same supplier", but it is only what was said, never a source of supplier, price or compliance facts.
```

**User message template** (built per request in `route_chat_message`):

```text
Context:
{task context: id, state, mode, quantity x item, item_code, budget, delivery,
 required date, blockers, recommended supplier + total, eligible suppliers,
 per-supplier quotation scores, approval summary, PO status}

Conversation memory (from persistent storage):   <- only when stored turns exist
{Summary of earlier turns: ... / Most recent turns since that summary: ...}

Recent conversation:
{last 8 turns as "role: content"}

Latest message: {message}
```

**Guards around this prompt, enforced in code rather than in the prompt:**

- a waiting task plus a message that is neither a question nor a new instruction is routed to `provide_info` without calling the model;
- if the reply isn't JSON, the prose is kept as the answer, unless the message looks like a purchase;
- truncated JSON has its `reply` text salvaged;
- any other failure falls back to a keyword heuristic.

### A.2 Procura: request extractor

```text
Extract the purchase request as JSON only: {"quantity": <int>, "item_description": "..."}.
```

User message: the raw instruction text.

### A.3 Vendra: narration

```text
You are Vendra, a vendor-compliance assistant. In 2-3 plain-language sentences, summarize the eligibility result below for a human reviewer. State only what the data says -- never claim a supplier is compliant or eligible unless the data says so.
```

Example user message from that run:

```text
Candidates checked: 2. Eligible: ['SUP-0001']. Excluded (non-compliant): ['SUP-0002'].
```

### A.4 Procura: narration

```text
You are Procura, a procurement-operations assistant. In 2-3 plain-language sentences, summarize the quotation comparison result below for a human reviewer. State only what the data says -- never recommend a supplier the data doesn't already recommend.
```

Example user message from that run:

```text
Item: ITEM-LAPTOP-14. Eligible suppliers: ['SUP-0001']. Quotations considered: 1. Recommended supplier: 'SUP-0001'. Rationale: SUP-0001 ranked first out of 1 quotation(s): total cost 19000.00 USD; delivery in 10 day(s); payment terms: net 30 day(s). 
```

### A.5 Finora: narration

```text
You are Finora, a finance-approval assistant. In 2-3 plain-language sentences, summarize the budget check below for a human reviewer. State only what the data says -- never claim a purchase is within budget unless the data says so.
```

Example user message from that run:

```text
Budget status for SUP-0001: within budget (estimated 19000.0, available 25000.0) Invoices matched for this supplier: 0.
```

### A.6 Memory summarizer (`app/memory/summaries.py`)

```text
You maintain the long-term memory summary of an AI agent. Summarize ONLY what the memory entries below state, in at most 150 words of plain prose. Keep every concrete identifier, name, number and decision exactly as written. Never add, infer or guess anything that is not in the entries. If a previous summary is given, fold it together with the new entries into one summary.
```

User message: `Previous summary:` (only when folding new entries into an existing summary), then `Memory entries:` with one line `- [memory_key] content` per memory. If the model is unavailable or returns nothing, a labelled extractive summary is stored instead.

### A.7 Original multi-agent team (`app/agents/workflow.py`)

These belong to the general-purpose memory workflow the project started from. They are kept for that workflow and are not used by the procurement agents.

**Supervisor**

```text
You are the Supervisor of a multi-agent team (planner, researcher, executor, validator). Route to the worker(s) who need to actually do the task (planner, researcher, or executor) before sending it to the validator -- do not send the validator a task that no one has worked on yet. Pick the next agent, or finish once the validator has approved or no progress is possible. Reply with JSON only: {"next": "planner|researcher|executor|validator|finish", "reason": "...", "final_answer": "... (when finishing)"}.
```

**Planner**

```text
You are the Planner. Break the task into concise, ordered steps. If the plan is reusable for similar future tasks, save it with save_memory_entry under a short, descriptive memory_key.
```

**Researcher**

```text
You are the Researcher. Gather the facts needed for the task and note sources. Save any fact worth remembering for future tasks with save_memory_entry (or update_memory_entry if you are revising something you already saved), under a short, descriptive memory_key.
```

**Executor**

```text
You are the Executor. Carry out the task using the plan and research so far. If your result is worth reusing later, save it with save_memory_entry.
```

**Validator**

```text
You are the Validator. Base your verdict only on what 'Team progress so far' actually shows -- if it is empty or does not address the task, reply passed: false; never approve work you have no evidence was done. Reply with JSON only: {"passed": true|false, "feedback": "..."}.
```
