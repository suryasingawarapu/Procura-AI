# Database migrations

The Supabase project is the single source of truth for the schema. These files
are plain SQL and are applied by hand in the Supabase SQL editor, because the
project must not run migrations without an explicit approval.

| File | Purpose | Destructive |
| --- | --- | --- |
| `001_harden_schema.sql` | Agent registry, sharing policy, new columns, foreign keys, constraints, one-active-version index, `updated_at` trigger | no |
| `002_memory_functions.sql` | The six atomic SQL functions + a self-test | no |
| `003_verify_after_apply.sql` | Read-only verification queries | no |
| `004_fix_parent_memory_id_fkey.sql` | Recreates `memory_provenance.parent_memory_id` with `ON DELETE CASCADE`; apply before `003` | no |
| `ROLLBACK.sql` | Undoes 001 and 002 | **yes** |

## How to apply

1. Supabase dashboard -> **SQL Editor** -> new query.
2. Paste the **whole** file and run it. It is one transaction: if any
   statement fails, nothing is applied.
3. Repeat for the next file, in order: `001`, `002`, then `004`.
4. Run `003_verify_after_apply.sql`. It returns one row per check
   (`check_name | expected | actual | comparison | status`); every row has to
   say `OK`.
5. Reload the PostgREST schema cache (Supabase does this automatically after
   DDL; if `PGRST202` persists, run `NOTIFY pgrst, 'reload schema';`).

Nothing here is applied automatically and the Python layer fails with
`MemorySchemaMissing` (and a pointer to these files) until the functions
exist.

## What the migrations buy

**Before** (three separate REST calls, no transaction):

```
UPDATE agent_memories SET status='superseded' WHERE id = ...
INSERT INTO agent_memories (...) VALUES (...)          -- version + 1
INSERT INTO memory_provenance (...) VALUES (...)       -- 'supersedes' edge
```

A failure between the calls left two active rows for one key, or an active
row without provenance.

**After** (one call, one transaction):

```python
update_memory("researcher", "price", "The price is 58000.")
```

`save_memory_atomic` and `supersede_memory_atomic` perform the version bump,
the status change and the provenance edge in a single transaction, guarded by
an advisory lock on `(agent_id, memory_key)` so two concurrent updates cannot
produce two active versions.

## Self-test

`002_memory_functions.sql` ends with a `DO` block that exercises every
function (idempotent save, version bump, sharing, policy denial, self-share
denial, multi-hop ancestry) and deletes its own rows again. If the self-test
fails, the entire migration is rolled back and the error is reported.

## Safety

* `001` is additive: no table or column is dropped and no row is deleted.
* `004` only replaces one foreign key definition; it drops no rows or columns.
* Duplicated *active* rows (if any exist) are superseded, never deleted, and
  the supersession is recorded in `memory_provenance`.
* `001` aborts with a descriptive message if the current data would violate
  a constraint that is about to be added (orphan provenance, unknown status
  value, empty `agent_id`, ...).
* `003` only runs `SELECT` statements.
* `ROLLBACK.sql` is destructive and is documented as such.

## Verifying from Python

```powershell
# the migration-aware checks pass before and after the migration
.\.venv\Scripts\python.exe -m pytest tests\integration\test_migration_state.py -v

# the full acceptance test for the write path (needs MEMORY_ALLOW_WRITE=1)
$env:MEMORY_ALLOW_WRITE = "1"
.\.venv\Scripts\python.exe -m pytest -m dbwrite -v
```
