# Provenance-Aware Shared Memory for Multi-Agent LLM Systems

Each agent owns a persistent logical memory. Agents exchange information through
controlled operations while the system keeps **provenance**, tracks **propagation**,
detects **stale** knowledge, handles **updates and conflicts**, and retrieves
relevant context efficiently.

Stack: LangChain 1.x (LLM + tools), LangGraph 1.x (orchestration), Supabase
PostgreSQL + pgvector (storage and retrieval), FastAPI (memory service),
Langfuse (observability, planned).

## Layout

```
app/
  memory/      store.py, versioning.py, propagation.py   (memory layer)
  agents/      agent definitions and graph nodes          (M5)
  tools/       LangChain tools                           (M5)
  utils/       console encoding and write guards
tests/
  unit/        pure logic, no database
  integration/ read-only checks against Supabase
scripts/       helper scripts (run_tests.ps1)
```

Legacy ad-hoc scripts (`test_memory.py`, `test_versioning.py`,
`test_propagation.py`) live in the repository root. They are **excluded from
pytest collection** and default to a dry run.

## Setup

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

`requirements.txt` pins the direct dependencies, `requirements.lock.txt` pins
the full transitive environment.

## Environment

Create a local `.env` (never committed, see `.gitignore`) containing:

| Variable | Purpose |
| --- | --- |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase API key |
| `MEMORY_ALLOW_WRITE` | Optional. Set to `1` to let the legacy scripts write. |

No other variable is required yet. An `OPENAI_API_KEY` and Langfuse keys are
required before the LLM-dependent milestones (M5/M6).

## Running the tests

```powershell
.\scripts\run_tests.ps1                 # full suite
.\scripts\run_tests.ps1 tests\unit      # unit tests only, no database
.\scripts\run_tests.ps1 -m "not integration"
```

The suite is **read-only**:

* `tests/unit` needs no database and no credentials.
* `tests/integration` only issues `SELECT` queries. A fixture compares row
  counts before and after every test, so an accidental write fails the test.
* The root `conftest.py` excludes the legacy scripts from collection, so no
  pytest invocation can modify the database.
* Anything that writes must live behind the `dbwrite` marker and is opt-in:
  ```powershell
  $env:MEMORY_ALLOW_WRITE = "1"
  .\.venv\Scripts\python.exe -m pytest -m dbwrite -v
  ```

## Migrations

The schema is owned by `migrations/` and applied by hand in the Supabase SQL
editor. See `migrations/README.md`.

| File | Purpose |
| --- | --- |
| `001_harden_schema.sql` | Agent registry, sharing policy, new columns, foreign keys, constraints, one-active-version index, `updated_at` trigger |
| `002_memory_functions.sql` | The six atomic SQL functions + self-test |
| `003_verify_after_apply.sql` | Read-only verification queries |
| `ROLLBACK.sql` | Undoes 001 and 002 (destructive) |

**Until the migrations are applied, the write path raises
`MemorySchemaMissing`.** Reads keep working.

## The memory layer

```
app/memory/
  client.py       Supabase client
  errors.py       Typed errors (MemoryNotFound, ShareNotAllowed, ...)
  formatting.py   Value serialisation for the SQL function parameters
  rpc.py          Bridge to the SQL functions + error translation
  store.py        save_memory, get_agent_memories, get_memory, ...
  versioning.py   update_memory, get_version_history
  propagation.py  share_memory, get_ancestry, get_descendants
```

Writes are executed by Postgres functions so that the mutation and its
provenance edge are committed together:

```python
from app.memory.store import save_memory
from app.memory.versioning import update_memory
from app.memory.propagation import share_memory, get_ancestry

memory = save_memory("researcher", "price", "The price is 55000.")
memory = update_memory("researcher", "price", "The price is 58000.")
copy = share_memory(memory["id"], "executor")
chain = get_ancestry(copy["id"])          # multi-hop provenance
```

Sharing is policy controlled: a pair that is not listed in
`memory_share_policy` is denied, and the hop limit is enforced.

## Safety notes

* The configured Supabase project is shared and long-lived; treat every write
  as a production write.
* The key in `.env` is a secret/service-role key, so database-level row
  security is not enforced. Agent isolation is currently application-level
  only. Closing this gap is tracked in M7.
* On Windows the console defaults to cp1252 and cannot print characters such
  as `₹`. `app/utils/console.enable_utf8_console()` fixes this; the entry
  points call it automatically.

## Milestones

| ID | Scope | Status |
| --- | --- | --- |
| M0 | Repo and test foundation | done |
| M1 | Hardened schema, constraints, atomic SQL core | code ready, **migration not applied** |
| M2 | Application layer on top of the SQL functions | pending |
| M3 | Embeddings and vector search | pending |
| M4 | FastAPI memory service | pending |
| M5 | LangChain tools and LangGraph workflow | pending |
| M6 | Langfuse observability and evaluation harness | pending |
| M7 | Database-level authorization (RLS) | pending |
