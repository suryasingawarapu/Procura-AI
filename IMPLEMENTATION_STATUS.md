# Procurement Platform — Implementation Status

## What actually works end to end right now

**One instruction → durable, resumable task → real agent pipeline → approval gate.**
`POST /procurement/tasks/start` with free text (e.g. *"Purchase 10 laptops with 16 GB RAM and 512 GB SSD, within ₹60 lakh, delivered to our Mumbai office within 30 days."*) deterministically parses quantity, item description, budget, currency, delivery location, and deadline; creates a linked, versioned Purchase Request; and actually runs the real, unmodified `run_coordinator` (Vendra → Procura → Finora, all 18 specialists reachable) against it. Verified live through the real HTTP API in both demo mode (reaches `Waiting For Approval` with a genuine recommendation) and live mode (reaches `Blocked` honestly, since the real demo suppliers are still missing real compliance documents — never fabricated).

Frontend entry point: **Start Procurement** tab (now the default/first tab) at `http://127.0.0.1:8000/app/`.

## Architecture (by layer)

| Layer | Module | Status |
|---|---|---|
| Agents/specialists | `app/procurement/systems/*.py`, `subagents/*.py` | All 18 implemented, connected to Vendra/Procura/Finora, unchanged this session |
| Cross-system coordinator | `app/procurement/coordinator.py` | Unchanged, unmodified — reused, not duplicated |
| Purchase Request + approval | `app/procurement/workflow.py` | Stale-approval rejection, `identity_verified` flag (honest — no real SSO exists) |
| Vendor Master | `app/procurement/vendor_master.py` | CRUD, search, CSV-row import w/ preview, idempotent 30–40 demo seed, demo-only reset |
| **Task Orchestrator (new this turn)** | `app/procurement/task_orchestrator.py` | One instruction → parse → linked PR → real pipeline → persisted state; resumable; idempotency-key dedup |
| Demo fixtures | `app/procurement/demo_fixtures.py` | 2-vendor "eligible/ineligible" scenario — see **known disconnect** below |

## Files changed this turn
New: `app/procurement/task_orchestrator.py`, `tests/integration/test_dbwrite_task_orchestrator.py`, `tests/unit/test_task_orchestrator_parsing.py`, `tests/unit/test_api_task_orchestrator.py`.
Modified: `app/procurement/workflow.py` (new `PurchaseRequest` fields: `mode`, `item_code`, `workflow_state`, `blockers`, `coordinator_task_id`, `coordinator_result`, `idempotency_key`; new `update_purchase_request`), `app/api/procurement_routes.py` / `procurement_schemas.py` (`POST /procurement/tasks/start`, `POST /procurement/tasks/{pr_id}/resume`, `PATCH /procurement/requests/{pr_id}`), `frontend/{index.html,app.js}` (new default **Start Procurement** tab).
No database migration. No new dependency.

## Tests actually run, actual results
- `./.venv/Scripts/python.exe -m pytest tests/unit -q` → **390 passed, 0 failed**.
- `MEMORY_ALLOW_WRITE=1 ./.venv/Scripts/python.exe -m pytest -m dbwrite -v` (real Supabase writes, real per-test cleanup, confirmed zero residue each time) → task orchestrator: **7 passed**; workflow hardening: **8 passed**; vendor master: **12 passed**.
- Live, through the running HTTP server (not just tests): demo-mode start → `Waiting For Approval`, recommendation DemoTech/₹5,20,000; live-mode start with missing item code → `Waiting For Information`; filled in + resumed → `Blocked` (real, current ERPNext state); idempotency-key retry → same task, no duplicate; simulated coordinator exception → `Failed`, not a crash.

## Demo mode vs. live mode
**Demo**: fully isolated — synthetic `SeededERPNextClient` (2 vendors, real compliance evidence that's clearly labeled SIMULATED), memory writes stubbed, never reaches live ERPNext or real Supabase except the task's own PR record.
**Live**: real `get_configured_client()` (official Frappe MCP), real memory writes, real compliance state — currently always reaches `Blocked` honestly, since the real demo suppliers still have no real PAN/GST documents.

## Real blockers (need you, not more code)
1. **Real compliance documents** for at least one live supplier — the only thing preventing a real `Waiting For Approval` outcome in live mode.
2. **Real SSO/identity** — `approver_role` is still self-declared (now visibly flagged `identity_verified: false` on every decision, not hidden).
3. **Real company approval-limit policy** — current manager/director/cfo figures are placeholders.
4. **Email credentials** — not started; no provider configured.

## Known disconnect, not hidden
`vendor_master.py`'s 35 demo vendors and `demo_fixtures.py`'s 2-vendor scenario are **separate, unconnected systems** — the task orchestrator's demo mode uses the latter (it has real synthetic quotations/pricing; the vendor master doesn't yet). Unifying them (generating synthetic quotations per demo vendor) is real, scoped follow-on work, not done this turn.

## Next concrete step
Extend the task orchestrator's RFQ stage to actually send (or, in demo mode, simulate sending) invitations to the eligible vendor list and track response state — the next piece explicitly called for in the brief (section 3, steps 5–7) that doesn't yet exist in any form.
