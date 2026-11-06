-- =====================================================================
-- 006_procurement_provenance_fields.sql
-- Additive columns for the procurement system's richer provenance model
-- (source type/hash, verification/confidence, task/message linkage,
-- sharing classification, structured facts, change reason, and share-
-- event metadata on provenance edges).
--
-- *** DRAFTED ONLY -- NOT APPLIED. Review before running. ***
-- =====================================================================
--
-- WHY
--   The procurement spec (Vendra/Procura/Finora) asks memory records to
--   additionally carry: source type, source hash/revision, a reason for
--   each change, verification status, confidence, task/message-call
--   references, and a sharing classification -- plus, on each share
--   event, the destination, message/task IDs, purpose/permission and
--   whether the receiver accepted it. None of that existed before this
--   file; everything here is new, nullable or defaulted, columns.
--
-- HOW THESE COLUMNS ARE POPULATED
--   The six atomic functions in 002 are NOT modified by this migration
--   -- rewriting hundreds of lines of already-working, tested PL/pgSQL
--   from a description, with no live database to verify the result
--   against, is a correctness risk this migration does not take. The
--   core write (version bump, supersession, provenance edge) stays
--   exactly as atomic and tested as it already is.
--
--   The new columns are instead populated by the Python layer with one
--   additional UPDATE right after the atomic RPC call succeeds (see
--   app/memory/provenance_fields.py). This is NOT part of the same
--   transaction as the core write: a crash between the two calls leaves
--   the memory/version/provenance edge fully correct (the part that
--   must never be inconsistent) but the enrichment columns below unset.
--   That is an accepted, explicitly documented tradeoff, not a silent
--   gap -- these are audit/classification fields, not fields the
--   version-chain or sharing-policy correctness guarantees depend on.
--
-- SAFETY
--   * Every column is ADD COLUMN IF NOT EXISTS, nullable or defaulted.
--     No existing row, column, function or constraint is touched.
--   * Re-run safe.
--
-- HOW TO APPLY
--   Supabase dashboard -> SQL Editor -> paste -> Run, after 001, 002,
--   004 and 005 (005 is independent/optional; order relative to 006
--   does not matter). One transaction.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- agent_memories: richer provenance on the fact itself
-- ---------------------------------------------------------------------
ALTER TABLE agent_memories
    ADD COLUMN IF NOT EXISTS source_type           text,
    -- e.g. 'erpnext_doctype', 'document', 'manual', 'computed'
    ADD COLUMN IF NOT EXISTS source_hash           text,
    -- content/revision hash of the source record, when available
    ADD COLUMN IF NOT EXISTS change_reason         text,
    -- why this version was created, when the write provides one
    ADD COLUMN IF NOT EXISTS verification_status   text NOT NULL DEFAULT 'unverified',
    ADD COLUMN IF NOT EXISTS confidence            double precision,
    ADD COLUMN IF NOT EXISTS task_id               text,
    ADD COLUMN IF NOT EXISTS message_id            text,
    -- the coordinator task/message that produced this fact
    ADD COLUMN IF NOT EXISTS sharing_classification text NOT NULL DEFAULT 'internal',
    -- 'internal' | 'shareable' | 'restricted'
    ADD COLUMN IF NOT EXISTS structured_fact       jsonb;
    -- optional structured representation alongside free-text `content`

DO $add_verification_check$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'agent_memories_verification_status_check'
           AND conrelid = 'public.agent_memories'::regclass
    ) THEN
        ALTER TABLE agent_memories
            ADD CONSTRAINT agent_memories_verification_status_check
            CHECK (verification_status IN ('unverified', 'verified', 'disputed'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'agent_memories_sharing_classification_check'
           AND conrelid = 'public.agent_memories'::regclass
    ) THEN
        ALTER TABLE agent_memories
            ADD CONSTRAINT agent_memories_sharing_classification_check
            CHECK (sharing_classification IN ('internal', 'shareable', 'restricted'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'agent_memories_confidence_check'
           AND conrelid = 'public.agent_memories'::regclass
    ) THEN
        ALTER TABLE agent_memories
            ADD CONSTRAINT agent_memories_confidence_check
            CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));
    END IF;
END
$add_verification_check$;

CREATE INDEX IF NOT EXISTS idx_agent_memories_task_id
    ON agent_memories (task_id)
    WHERE task_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- memory_provenance: share-event metadata (destination/task/message/
-- purpose/acceptance), on top of the existing memory_id/parent_memory_id
-- /from_agent_id/to_agent_id/relationship/note columns from 001+002.
-- ---------------------------------------------------------------------
ALTER TABLE memory_provenance
    ADD COLUMN IF NOT EXISTS task_id    text,
    ADD COLUMN IF NOT EXISTS message_id text,
    ADD COLUMN IF NOT EXISTS purpose    text,
    ADD COLUMN IF NOT EXISTS accepted   boolean;
    -- NULL = not applicable / not a share event; true/false once the
    -- receiving system has actually accepted or rejected the shared fact

-- ---------------------------------------------------------------------
-- Register the three procurement systems as memory-owning identities.
-- Specialist task executors (Vendor Discovery, RFQ Management, Budget
-- Verification, ...) are NOT registered here and never will be -- per
-- spec they are task executors, not independent durable-memory owners;
-- only the three system orchestrators own a private memory scope.
-- 'agent' is an already-allowed role (see memory_agents_role_check in
-- 001), so this needs no constraint change.
-- ---------------------------------------------------------------------
INSERT INTO memory_agents (agent_id, role, display_name) VALUES
    ('vendra',  'agent', 'Vendra -- Vendor Intelligence & Management'),
    ('procura', 'agent', 'Procura -- Procurement Operations'),
    ('finora',  'agent', 'Finora -- Finance & Approval Management')
ON CONFLICT (agent_id) DO NOTHING;

-- Sharing policy between the three systems. Deny-by-default still
-- applies: any pair not listed here (e.g. finora -> vendra) is denied.
INSERT INTO memory_share_policy (from_agent_id, to_agent_id, allowed, max_hops) VALUES
    ('vendra',  'procura', true, 3),
    ('vendra',  'finora',  true, 2),
    ('procura', 'finora',  true, 2),
    ('finora',  'procura', true, 2)
ON CONFLICT (from_agent_id, to_agent_id) DO NOTHING;

COMMIT;
