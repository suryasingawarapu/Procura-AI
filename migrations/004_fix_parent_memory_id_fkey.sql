-- =====================================================================
-- 004_fix_parent_memory_id_fkey.sql
-- Correct the delete action of memory_provenance.parent_memory_id.
-- =====================================================================
--
-- The live constraint was created with ON DELETE NO ACTION, but the
-- provenance chain needs CASCADE: deleting a memory must also delete the
-- provenance rows that point at it as their parent. 001 could not fix
-- this because it only adds a foreign key when the name is absent.
--
-- This migration recreates that one constraint only. The column, the
-- referenced table/column and every other constraint stay untouched.
-- Re-run safe: it does nothing when the action is already CASCADE.
-- =====================================================================

BEGIN;

DO $fix_parent_fkey$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'memory_provenance_parent_memory_id_fkey'
           AND conrelid = 'public.memory_provenance'::regclass
           AND contype = 'f'
           AND confdeltype = 'c'
    ) THEN
        RAISE NOTICE 'memory_provenance_parent_memory_id_fkey already cascades';
        RETURN;
    END IF;

    ALTER TABLE public.memory_provenance
        DROP CONSTRAINT IF EXISTS memory_provenance_parent_memory_id_fkey;

    ALTER TABLE public.memory_provenance
        ADD CONSTRAINT memory_provenance_parent_memory_id_fkey
        FOREIGN KEY (parent_memory_id)
        REFERENCES public.agent_memories (id)
        ON DELETE CASCADE;
END
$fix_parent_fkey$;

COMMIT;
