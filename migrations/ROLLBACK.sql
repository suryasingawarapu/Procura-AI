-- =====================================================================
-- ROLLBACK.sql
-- Undo milestones M1 (001 + 002).
-- =====================================================================
--
--   ***  DESTRUCTIVE -- READ BEFORE RUNNING  ***
--
-- This script removes everything that migrations 001 and 002 added:
--   * the six memory functions
--   * the memory_agents and memory_share_policy tables
--   * the new columns, constraints, indexes and triggers
--
-- It CANNOT undo these data changes:
--   * rows that 001 marked as 'superseded' (duplicate active rows)
--   * the 'supersedes' edges that 001 recorded for those rows
--
-- Run it in the Supabase SQL editor. The whole file is one transaction:
-- if any statement fails, nothing is removed.
--
-- Before running it, take a backup (Supabase dashboard -> Table Editor ->
-- export, or `pg_dump`).
-- =====================================================================

BEGIN;

-- 1. the memory functions ---------------------------------------------
DROP FUNCTION IF EXISTS match_agent_memories(text, text, double precision, integer);
DROP FUNCTION IF EXISTS get_descendants(uuid, integer);
DROP FUNCTION IF EXISTS get_ancestry(uuid, integer);
DROP FUNCTION IF EXISTS share_memory_atomic(uuid, text, text, integer);
DROP FUNCTION IF EXISTS supersede_memory_atomic(text, text, text, text, text, timestamptz, text, timestamptz);
DROP FUNCTION IF EXISTS save_memory_atomic(text, text, text, text, text, text, timestamptz, text, timestamptz);

-- 2. the updated_at trigger ------------------------------------------
DROP TRIGGER IF EXISTS trg_agent_memories_set_updated_at ON agent_memories;
DROP TRIGGER IF EXISTS trg_agent_memory_summaries_set_updated_at ON agent_memory_summaries;
DROP FUNCTION IF EXISTS memory_set_updated_at();

-- 3. the foreign keys that point at memory_agents ---------------------
ALTER TABLE memory_provenance DROP CONSTRAINT IF EXISTS memory_provenance_to_agent_id_fkey;
ALTER TABLE memory_provenance DROP CONSTRAINT IF EXISTS memory_provenance_from_agent_id_fkey;
ALTER TABLE memory_provenance DROP CONSTRAINT IF EXISTS memory_provenance_parent_memory_id_fkey;
ALTER TABLE memory_provenance DROP CONSTRAINT IF EXISTS memory_provenance_memory_id_fkey;
ALTER TABLE agent_memories DROP CONSTRAINT IF EXISTS agent_memories_source_agent_id_fkey;
ALTER TABLE agent_memories DROP CONSTRAINT IF EXISTS agent_memories_agent_id_fkey;
ALTER TABLE agent_memory_summaries DROP CONSTRAINT IF EXISTS agent_memory_summaries_agent_id_fkey;

-- 4. the new tables ---------------------------------------------------
DROP TABLE IF EXISTS memory_share_policy;
DROP TABLE IF EXISTS memory_agents;

-- 5. the new indexes --------------------------------------------------
DROP INDEX IF EXISTS uniq_agent_memories_active_key;
DROP INDEX IF EXISTS idx_agent_memories_agent_status;
DROP INDEX IF EXISTS idx_agent_memories_created_at;
DROP INDEX IF EXISTS idx_agent_memories_subject_key;
DROP INDEX IF EXISTS idx_memory_provenance_memory;
DROP INDEX IF EXISTS idx_memory_provenance_parent;
DROP INDEX IF EXISTS idx_memory_provenance_to_agent;
DROP INDEX IF EXISTS idx_agent_memory_summaries_agent;

-- 6. the new check constraints ----------------------------------------
ALTER TABLE agent_memories DROP CONSTRAINT IF EXISTS agent_memories_status_check;
ALTER TABLE agent_memories DROP CONSTRAINT IF EXISTS agent_memories_version_check;
ALTER TABLE agent_memories DROP CONSTRAINT IF EXISTS agent_memories_hop_depth_check;
ALTER TABLE memory_provenance DROP CONSTRAINT IF EXISTS memory_provenance_relationship_check;
ALTER TABLE memory_provenance DROP CONSTRAINT IF EXISTS memory_provenance_no_self_parent_check;

-- 7. the new columns --------------------------------------------------
ALTER TABLE agent_memory_summaries
    DROP COLUMN IF EXISTS covered_observed_at,
    DROP COLUMN IF EXISTS status,
    DROP COLUMN IF EXISTS updated_at;

ALTER TABLE memory_provenance
    DROP COLUMN IF EXISTS resolved_at,
    DROP COLUMN IF EXISTS note;

ALTER TABLE agent_memories
    DROP COLUMN IF EXISTS superseded_at,
    DROP COLUMN IF EXISTS subject_key,
    DROP COLUMN IF EXISTS source_version,
    DROP COLUMN IF EXISTS hop_depth,
    DROP COLUMN IF EXISTS valid_until,
    DROP COLUMN IF EXISTS observed_at;

COMMIT;
