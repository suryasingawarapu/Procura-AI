-- =====================================================================
-- 008_allow_conflict_relationships.sql
-- Let memory_provenance record conflicts between memories.
-- =====================================================================
--
-- WHY
--   001_harden_schema.sql meant memory_provenance.relationship to allow four
--   values: supersedes, derived_from, conflicts_with, resolved_by. It only adds
--   its constraint when no constraint of that name exists, and the live
--   database already had a narrower one, so conflicts_with / resolved_by
--   inserts are refused ("violates check constraint
--   memory_provenance_relationship_check").
--
--   The conflict feature works without this migration (each decision is also
--   stored on the claims themselves), but the conflicts_with / resolved_by
--   edges -- the queryable provenance trail of every dispute and who/what
--   settled it -- are only recorded once this is applied.
--
-- SAFETY
--   * Only widens a CHECK constraint: no row changes, nothing deleted.
--   * Existing rows (supersedes / derived_from) satisfy the new rule; the
--     statement fails, changing nothing, if any row does not.
--   * Re-run safe.
--
-- HOW TO APPLY
--   Supabase dashboard -> SQL Editor -> paste -> Run.
--
-- ROLLBACK (only if no conflicts_with / resolved_by rows exist yet)
--   ALTER TABLE memory_provenance DROP CONSTRAINT memory_provenance_relationship_check;
--   ALTER TABLE memory_provenance ADD CONSTRAINT memory_provenance_relationship_check
--       CHECK (relationship IN ('supersedes', 'derived_from'));
-- =====================================================================

BEGIN;

ALTER TABLE memory_provenance DROP CONSTRAINT IF EXISTS memory_provenance_relationship_check;

ALTER TABLE memory_provenance
    ADD CONSTRAINT memory_provenance_relationship_check
    CHECK (relationship IN ('supersedes', 'derived_from', 'conflicts_with', 'resolved_by'));

COMMIT;
