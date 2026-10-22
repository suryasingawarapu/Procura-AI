-- =====================================================================
-- 003_verify_after_apply.sql
-- Read-only verification of the M1 migrations.
-- =====================================================================
--
-- Run this AFTER 001_harden_schema.sql and 002_memory_functions.sql.
-- It is a single SELECT and changes nothing.
--
-- The result is one row per check:
--     check_name | expected | actual | comparison | status
-- Every row must say 'OK'.
-- =====================================================================

WITH checks(seq, check_name, expected, comparison, actual) AS (

    -- 1. the tables that must exist
    SELECT 1, 'required tables exist', '5', '=', count(*)::bigint
      FROM information_schema.tables
     WHERE table_schema = 'public'
       AND table_name IN ('agent_memories', 'memory_provenance', 'agent_memory_summaries',
                          'memory_agents', 'memory_share_policy')

    -- 2. the new columns
    UNION ALL
    SELECT 2, 'new columns on agent_memories', '6', '=', count(*)::bigint
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'agent_memories'
       AND column_name IN ('observed_at', 'valid_until', 'hop_depth', 'source_version',
                           'subject_key', 'superseded_at')

    UNION ALL
    SELECT 3, 'new columns on memory_provenance', '2', '=', count(*)::bigint
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'memory_provenance'
       AND column_name IN ('note', 'resolved_at')

    UNION ALL
    SELECT 4, 'new columns on agent_memory_summaries', '3', '=', count(*)::bigint
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'agent_memory_summaries'
       AND column_name IN ('updated_at', 'status', 'covered_observed_at')

    -- 3. the foreign keys
    UNION ALL
    SELECT 5, 'foreign keys installed', '7', '=', count(*)::bigint
      FROM pg_constraint
     WHERE contype = 'f'
       AND connamespace = 'public'::regnamespace
       AND conname IN ('memory_provenance_memory_id_fkey',
                       'memory_provenance_parent_memory_id_fkey',
                       'memory_provenance_from_agent_id_fkey',
                       'memory_provenance_to_agent_id_fkey',
                       'agent_memories_agent_id_fkey',
                       'agent_memories_source_agent_id_fkey',
                       'agent_memory_summaries_agent_id_fkey')

    -- 4. the value constraints
    UNION ALL
    SELECT 6, 'check constraints installed', '7', '=', count(*)::bigint
      FROM pg_constraint
     WHERE contype = 'c'
       AND connamespace = 'public'::regnamespace
       AND conname IN ('agent_memories_status_check',
                       'agent_memories_version_check',
                       'agent_memories_hop_depth_check',
                       'memory_provenance_relationship_check',
                       'memory_provenance_no_self_parent_check',
                       'memory_agents_role_check',
                       'memory_share_policy_hops_check')

    -- 5. the one-active-version-per-key index
    UNION ALL
    SELECT 7, 'unique active-key index installed', '1', '=', count(*)::bigint
      FROM pg_indexes
     WHERE schemaname = 'public' AND indexname = 'uniq_agent_memories_active_key'

    -- 6. the updated_at triggers
    UNION ALL
    SELECT 8, 'updated_at triggers installed', '2', '=', count(*)::bigint
      FROM pg_trigger
     WHERE NOT tgisinternal
       AND tgname IN ('trg_agent_memories_set_updated_at',
                      'trg_agent_memory_summaries_set_updated_at')

    -- 7. the memory functions
    UNION ALL
    SELECT 9, 'memory functions installed', '6', '=', count(*)::bigint
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND p.proname IN ('save_memory_atomic', 'supersede_memory_atomic',
                         'share_memory_atomic', 'get_ancestry', 'get_descendants',
                         'match_agent_memories')

    -- 8. the registry and the policy
    UNION ALL
    SELECT 10, 'registered agents', '5', '>=', count(*)::bigint
      FROM memory_agents

    UNION ALL
    SELECT 11, 'sharing policy rows', '11', '>=', count(*)::bigint
      FROM memory_share_policy

    -- 9. the invariants
    UNION ALL
    SELECT 12, 'duplicate active keys (must be 0)', '0', '=', count(*)::bigint
      FROM (
            SELECT agent_id, memory_key
              FROM agent_memories
             WHERE status = 'active'
             GROUP BY agent_id, memory_key
            HAVING count(*) > 1
           ) duplicates

    UNION ALL
    SELECT 13, 'orphan provenance rows (must be 0)', '0', '=', count(*)::bigint
      FROM memory_provenance p
      LEFT JOIN agent_memories m ON m.id = p.memory_id
     WHERE m.id IS NULL

    UNION ALL
    SELECT 14, 'self-test leftovers (must be 0)', '0', '=', count(*)::bigint
      FROM agent_memories
     WHERE agent_id IN ('__selftest_source__', '__selftest_target__')

    -- 10. the data must not have shrunk
    UNION ALL
    SELECT 15, 'agent_memories rows', '6', '>=', count(*)::bigint
      FROM agent_memories

    UNION ALL
    SELECT 16, 'memory_provenance rows', '2', '>=', count(*)::bigint
      FROM memory_provenance

    UNION ALL
    SELECT 17, 'agent_memory_summaries rows', '0', '=', count(*)::bigint
      FROM agent_memory_summaries

    -- 11. the delete actions of the foreign keys. 001 only creates a foreign
    -- key when no constraint with that name exists yet, so a pre-existing
    -- constraint can keep its own action. The cleanup code must not assume
    -- CASCADE: check the declared actions (c = cascade, n = set null,
    -- r = restrict) instead of trusting the names.
    UNION ALL
    SELECT 18, 'foreign key delete actions (c,c,n,r,r,n,r)', '7', '=', count(*)::bigint
      FROM (VALUES
            ('memory_provenance_memory_id_fkey', 'c'),
            ('memory_provenance_parent_memory_id_fkey', 'c'),
            ('memory_provenance_from_agent_id_fkey', 'n'),
            ('memory_provenance_to_agent_id_fkey', 'r'),
            ('agent_memories_agent_id_fkey', 'r'),
            ('agent_memories_source_agent_id_fkey', 'n'),
            ('agent_memory_summaries_agent_id_fkey', 'r')
           ) AS expected(constraint_name, delete_action)
      JOIN pg_constraint c
        ON c.conname = expected.constraint_name
       AND c.connamespace = 'public'::regnamespace
       AND c.contype = 'f'
       AND c.confdeltype::text = expected.delete_action
)
SELECT check_name,
       expected,
       actual,
       comparison,
       CASE
           WHEN comparison = '=' AND actual = expected::bigint THEN 'OK'
           WHEN comparison = '>=' AND actual >= expected::bigint THEN 'OK'
           ELSE 'FAIL'
       END AS status
  FROM checks
 ORDER BY seq;
