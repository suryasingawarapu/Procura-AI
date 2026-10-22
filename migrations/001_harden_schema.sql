-- =====================================================================
-- 001_harden_schema.sql
-- Provenance-Aware Shared Memory -- schema hardening
-- =====================================================================
--
-- WHAT THIS DOES
--   1. Registers the known agents and the controlled-sharing policy.
--   2. Adds the columns the provenance model needs
--      (observed_at, hop_depth, source_version, subject_key, ...).
--   3. Adds referential integrity (foreign keys) and value constraints.
--   4. Guarantees one *active* memory per (agent_id, memory_key).
--   5. Makes updated_at actually update.
--
-- SAFETY
--   * Additive only. No table or column is dropped, no row is deleted.
--   * Existing rows are kept. Duplicated *active* rows (if any) are
--     superseded -- never removed -- and the supersession is recorded in
--     memory_provenance.
--   * The migration aborts with a descriptive error if the current data
--     would violate a constraint that is about to be added.
--   * Wrap it in a transaction so a failure leaves nothing behind.
--
-- HOW TO APPLY
--   Supabase dashboard -> SQL Editor -> paste -> Run. The whole file runs
--   as a single transaction; if any statement fails, nothing is applied.
--
-- RE-RUN SAFE
--   Yes. Every object uses IF NOT EXISTS / ON CONFLICT DO NOTHING and the
--   constraints are added only when they are missing.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 0. Pre-flight: refuse to continue if the current data would violate
--    the constraints that are about to be added.
-- ---------------------------------------------------------------------
DO $preflight$
DECLARE
    v_count bigint;
BEGIN
    SELECT count(*) INTO v_count FROM agent_memories
     WHERE status NOT IN ('active', 'superseded', 'archived', 'stale');
    IF v_count > 0 THEN
        RAISE EXCEPTION
            'agent_memories contains % row(s) with an unsupported status; normalise them before running 001_harden_schema.sql',
            v_count;
    END IF;

    SELECT count(*) INTO v_count FROM agent_memories WHERE version < 1;
    IF v_count > 0 THEN
        RAISE EXCEPTION
            'agent_memories contains % row(s) with version < 1', v_count;
    END IF;

    SELECT count(*) INTO v_count FROM memory_provenance
     WHERE relationship NOT IN ('supersedes', 'derived_from', 'conflicts_with', 'resolved_by');
    IF v_count > 0 THEN
        RAISE EXCEPTION
            'memory_provenance contains % row(s) with an unsupported relationship', v_count;
    END IF;

    SELECT count(*) INTO v_count FROM memory_provenance
     WHERE memory_id NOT IN (SELECT id FROM agent_memories);
    IF v_count > 0 THEN
        RAISE EXCEPTION
            'memory_provenance contains % row(s) pointing at a memory that does not exist', v_count;
    END IF;

    SELECT count(*) INTO v_count FROM memory_provenance
     WHERE parent_memory_id IS NOT NULL
       AND parent_memory_id NOT IN (SELECT id FROM agent_memories);
    IF v_count > 0 THEN
        RAISE EXCEPTION
            'memory_provenance contains % row(s) with a dangling parent_memory_id', v_count;
    END IF;

    SELECT count(*) INTO v_count FROM memory_provenance
     WHERE parent_memory_id = memory_id;
    IF v_count > 0 THEN
        RAISE EXCEPTION
            'memory_provenance contains % self referencing row(s)', v_count;
    END IF;

    SELECT count(*) INTO v_count FROM agent_memories
     WHERE agent_id IS NULL OR btrim(agent_id) = '';
    IF v_count > 0 THEN
        RAISE EXCEPTION
            'agent_memories contains % row(s) with an empty agent_id', v_count;
    END IF;

    RAISE NOTICE 'pre-flight checks passed';
END
$preflight$;

-- ---------------------------------------------------------------------
-- 1. Agent registry
--    A closed set of known agents. agent_id columns reference it, so an
--    unknown agent can no longer create memories by accident.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_agents (
    agent_id     text PRIMARY KEY,
    role         text NOT NULL DEFAULT 'agent',
    display_name text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT memory_agents_role_check
        CHECK (role IN ('supervisor', 'planner', 'researcher', 'executor', 'validator', 'agent'))
);

INSERT INTO memory_agents (agent_id, role) VALUES
    ('supervisor', 'supervisor'),
    ('planner',    'planner'),
    ('researcher', 'researcher'),
    ('executor',   'executor'),
    ('validator',  'validator')
ON CONFLICT (agent_id) DO NOTHING;

-- Backfill every agent that already appears in the data.
INSERT INTO memory_agents (agent_id, role)
SELECT DISTINCT agent_id, 'agent' FROM agent_memories
ON CONFLICT (agent_id) DO NOTHING;

INSERT INTO memory_agents (agent_id, role)
SELECT DISTINCT source_agent_id, 'agent' FROM agent_memories
WHERE source_agent_id IS NOT NULL
ON CONFLICT (agent_id) DO NOTHING;

INSERT INTO memory_agents (agent_id, role)
SELECT DISTINCT from_agent_id, 'agent' FROM memory_provenance
WHERE from_agent_id IS NOT NULL
ON CONFLICT (agent_id) DO NOTHING;

INSERT INTO memory_agents (agent_id, role)
SELECT DISTINCT to_agent_id, 'agent' FROM memory_provenance
WHERE to_agent_id IS NOT NULL
ON CONFLICT (agent_id) DO NOTHING;

INSERT INTO memory_agents (agent_id, role)
SELECT DISTINCT agent_id, 'agent' FROM agent_memory_summaries
ON CONFLICT (agent_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- 2. Controlled sharing policy
--    A pair that is not listed is NOT allowed (deny by default).
--    max_hops bounds how many times a memory may travel.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_share_policy (
    from_agent_id text NOT NULL REFERENCES memory_agents (agent_id) ON DELETE CASCADE,
    to_agent_id   text NOT NULL REFERENCES memory_agents (agent_id) ON DELETE CASCADE,
    allowed       boolean NOT NULL DEFAULT true,
    max_hops      integer NOT NULL DEFAULT 3,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (from_agent_id, to_agent_id),
    CONSTRAINT memory_share_policy_hops_check CHECK (max_hops >= 0)
);

INSERT INTO memory_share_policy (from_agent_id, to_agent_id, allowed, max_hops) VALUES
    ('researcher', 'executor',   true, 3),
    ('researcher', 'validator',  true, 3),
    ('researcher', 'supervisor', true, 3),
    ('planner',    'executor',   true, 3),
    ('planner',    'validator',  true, 2),
    ('planner',    'supervisor', true, 3),
    ('executor',   'validator',  true, 2),
    ('executor',   'supervisor', true, 2),
    ('validator',  'supervisor', true, 2),
    ('supervisor', 'planner',    true, 2),
    ('supervisor', 'researcher', true, 2)
ON CONFLICT (from_agent_id, to_agent_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- 3. New columns
-- ---------------------------------------------------------------------
ALTER TABLE agent_memories
    ADD COLUMN IF NOT EXISTS observed_at    timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS valid_until    timestamptz,
    ADD COLUMN IF NOT EXISTS hop_depth      integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS source_version integer,
    ADD COLUMN IF NOT EXISTS subject_key    text,
    ADD COLUMN IF NOT EXISTS superseded_at  timestamptz;

ALTER TABLE memory_provenance
    ADD COLUMN IF NOT EXISTS note        text,
    ADD COLUMN IF NOT EXISTS resolved_at timestamptz;

ALTER TABLE agent_memory_summaries
    ADD COLUMN IF NOT EXISTS updated_at         timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS status             text NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS covered_observed_at timestamptz;

-- Existing rows were observed when they were created.
UPDATE agent_memories
   SET observed_at = created_at
 WHERE observed_at > created_at;

-- ---------------------------------------------------------------------
-- 4. Collapse duplicated *active* rows
--    Only relevant if a memory key was written twice without superseding.
--    The newest version wins, the others are superseded (never deleted)
--    and every supersession is recorded in memory_provenance.
-- ---------------------------------------------------------------------
WITH duplicate_groups AS (
    SELECT agent_id, memory_key
      FROM agent_memories
     WHERE status = 'active'
     GROUP BY agent_id, memory_key
    HAVING count(*) > 1
),
ranked AS (
    SELECT m.id, m.agent_id, m.memory_key,
           row_number() OVER (
               PARTITION BY m.agent_id, m.memory_key
               ORDER BY m.version DESC, m.created_at DESC, m.id DESC
           ) AS rn
      FROM agent_memories m
      JOIN duplicate_groups g
        ON g.agent_id = m.agent_id AND g.memory_key = m.memory_key
     WHERE m.status = 'active'
),
keeper AS (
    SELECT id, agent_id, memory_key FROM ranked WHERE rn = 1
),
losers AS (
    SELECT id, agent_id, memory_key FROM ranked WHERE rn > 1
),
superseded AS (
    UPDATE agent_memories m
       SET status = 'superseded', superseded_at = now(), updated_at = now()
      FROM losers l
     WHERE m.id = l.id
    RETURNING m.id
)
INSERT INTO memory_provenance (
    memory_id, parent_memory_id, from_agent_id, to_agent_id, relationship, note
)
SELECT k.id, l.id, k.agent_id, k.agent_id, 'supersedes',
       'duplicate active rows collapsed by migration 001'
  FROM keeper k
  JOIN losers l ON l.agent_id = k.agent_id AND l.memory_key = k.memory_key;

-- ---------------------------------------------------------------------
-- 5. Value constraints
-- ---------------------------------------------------------------------
DO $constraints$
DECLARE
    c record;
BEGIN
    FOR c IN
        SELECT * FROM (VALUES
            ('agent_memories', 'agent_memories_status_check',
             'status IN (''active'', ''superseded'', ''archived'', ''stale'')'),
            ('agent_memories', 'agent_memories_version_check',
             'version >= 1'),
            ('agent_memories', 'agent_memories_hop_depth_check',
             'hop_depth >= 0'),
            ('memory_provenance', 'memory_provenance_relationship_check',
             'relationship IN (''supersedes'', ''derived_from'', ''conflicts_with'', ''resolved_by'')'),
            ('memory_provenance', 'memory_provenance_no_self_parent_check',
             'parent_memory_id IS NULL OR parent_memory_id <> memory_id')
        ) AS t(table_name, constraint_name, expression)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conname = c.constraint_name
               AND conrelid = c.table_name::regclass
        ) THEN
            EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I CHECK (%s)',
                           c.table_name, c.constraint_name, c.expression);
        END IF;
    END LOOP;

    RAISE NOTICE 'value constraints verified';
END
$constraints$;

-- ---------------------------------------------------------------------
-- 6. Foreign keys
--    provenance always points at a real memory, agents are always known.
-- ---------------------------------------------------------------------
DO $fkeys$
DECLARE
    c record;
BEGIN
    FOR c IN
        SELECT * FROM (VALUES
            ('memory_provenance', 'memory_provenance_memory_id_fkey',
             'memory_id', 'agent_memories', 'id', 'CASCADE'),
            ('memory_provenance', 'memory_provenance_parent_memory_id_fkey',
             'parent_memory_id', 'agent_memories', 'id', 'CASCADE'),
            ('memory_provenance', 'memory_provenance_from_agent_id_fkey',
             'from_agent_id', 'memory_agents', 'agent_id', 'SET NULL'),
            ('memory_provenance', 'memory_provenance_to_agent_id_fkey',
             'to_agent_id', 'memory_agents', 'agent_id', 'RESTRICT'),
            ('agent_memories', 'agent_memories_agent_id_fkey',
             'agent_id', 'memory_agents', 'agent_id', 'RESTRICT'),
            ('agent_memories', 'agent_memories_source_agent_id_fkey',
             'source_agent_id', 'memory_agents', 'agent_id', 'SET NULL'),
            ('agent_memory_summaries', 'agent_memory_summaries_agent_id_fkey',
             'agent_id', 'memory_agents', 'agent_id', 'RESTRICT')
        ) AS t(table_name, constraint_name, column_name, ref_table, ref_column, on_delete)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conname = c.constraint_name
               AND conrelid = c.table_name::regclass
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I (%I) ON DELETE %s',
                c.table_name, c.constraint_name, c.column_name,
                c.ref_table, c.ref_column, c.on_delete);
        END IF;
    END LOOP;

    RAISE NOTICE 'foreign keys verified';
END
$fkeys$;

-- ---------------------------------------------------------------------
-- 7. One active version per (agent_id, memory_key)
--    This is the invariant that makes the read path unambiguous.
-- ---------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uniq_agent_memories_active_key
    ON agent_memories (agent_id, memory_key)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_agent_memories_agent_status
    ON agent_memories (agent_id, status);

CREATE INDEX IF NOT EXISTS idx_agent_memories_created_at
    ON agent_memories (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_memories_subject_key
    ON agent_memories (subject_key)
    WHERE subject_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memory_provenance_memory
    ON memory_provenance (memory_id);

CREATE INDEX IF NOT EXISTS idx_memory_provenance_parent
    ON memory_provenance (parent_memory_id);

CREATE INDEX IF NOT EXISTS idx_memory_provenance_to_agent
    ON memory_provenance (to_agent_id);

CREATE INDEX IF NOT EXISTS idx_agent_memory_summaries_agent
    ON agent_memory_summaries (agent_id, status);

-- ---------------------------------------------------------------------
-- 8. updated_at now actually updates
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION memory_set_updated_at() RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS trg_agent_memories_set_updated_at ON agent_memories;
CREATE TRIGGER trg_agent_memories_set_updated_at
    BEFORE UPDATE ON agent_memories
    FOR EACH ROW EXECUTE FUNCTION memory_set_updated_at();

DROP TRIGGER IF EXISTS trg_agent_memory_summaries_set_updated_at ON agent_memory_summaries;
CREATE TRIGGER trg_agent_memory_summaries_set_updated_at
    BEFORE UPDATE ON agent_memory_summaries
    FOR EACH ROW EXECUTE FUNCTION memory_set_updated_at();

COMMIT;

-- =====================================================================
-- After running this file, run 003_verify_after_apply.sql to confirm
-- that everything landed as expected.
-- =====================================================================
