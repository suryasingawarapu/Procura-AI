-- =====================================================================
-- 002_memory_functions.sql
-- Provenance-Aware Shared Memory -- atomic write functions
-- =====================================================================
--
-- WHY SQL FUNCTIONS
--   The Python layer used to perform writes as three separate REST calls
--   (mark superseded -> insert new row -> insert provenance edge). A failure
--   in the middle left two active rows or a memory without provenance.
--   PostgREST cannot run several statements in one transaction, so the whole
--   write path moves into Postgres where a transaction is native.
--
-- FUNCTIONS
--   save_memory_atomic(...)      create or idempotently update a memory
--   supersede_memory_atomic(...) create the next version of a memory
--   share_memory_atomic(...)     controlled share into another agent's space
--   get_ancestry(...)            multi-hop ancestors of a memory
--   get_descendants(...)         multi-hop copies of a memory
--   match_agent_memories(...)    vector similarity search (used from M3)
--
-- ERROR CODES (raised with ERRCODE so Python can map them precisely)
--   MEM01  agent is not registered
--   MEM02  the share is not permitted by memory_share_policy
--   MEM03  the hop limit would be exceeded
--   MEM04  the target key already holds an unrelated memory
--   MEM05  an agent tried to share a memory with itself
--   P0002  no such (active) memory
--
-- SAFETY
--   * The file ends with a self-test that exercises every function and
--     removes its own rows again. If the self-test fails, the whole
--     migration is rolled back and the error is reported.
--   * No existing row is modified.
--
-- NOTE ON p_embedding
--   The embedding is passed as text ("[0.1,0.2]") instead of a vector so
--   that PostgREST never has to coerce a JSON array into a custom type.
--   The cast to vector happens inside the function.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. save_memory_atomic
--    * no active row for (agent, key)  -> create version 1
--    * active row with the same content -> return it unchanged (idempotent)
--    * active row with other content    -> supersede it and create the next
--                                          version, both in one transaction
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION save_memory_atomic(
    p_agent_id         text,
    p_memory_key       text,
    p_content          text,
    p_source_agent_id  text        DEFAULT NULL,
    p_source_reference text        DEFAULT NULL,
    p_embedding        text        DEFAULT NULL,
    p_observed_at      timestamptz DEFAULT NULL,
    p_subject_key      text        DEFAULT NULL,
    p_valid_until      timestamptz DEFAULT NULL
) RETURNS SETOF agent_memories
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_existing agent_memories%ROWTYPE;
    v_row      agent_memories%ROWTYPE;
    v_observed timestamptz := COALESCE(p_observed_at, now());
    v_vector   vector;
BEGIN
    IF p_agent_id IS NULL OR btrim(p_agent_id) = '' THEN
        RAISE EXCEPTION 'agent_id is required' USING ERRCODE = 'MEM01';
    END IF;
    IF p_memory_key IS NULL OR btrim(p_memory_key) = '' THEN
        RAISE EXCEPTION 'memory_key is required' USING ERRCODE = 'MEM01';
    END IF;
    IF p_content IS NULL OR btrim(p_content) = '' THEN
        RAISE EXCEPTION 'content is required' USING ERRCODE = 'MEM01';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM memory_agents WHERE agent_id = p_agent_id) THEN
        RAISE EXCEPTION 'agent "%" is not registered', p_agent_id USING ERRCODE = 'MEM01';
    END IF;

    IF p_embedding IS NOT NULL AND btrim(p_embedding) <> '' THEN
        v_vector := p_embedding::vector;
    END IF;

    -- Serialise concurrent writes to the same (agent, key).
    PERFORM pg_advisory_xact_lock(hashtextextended(p_agent_id || '::' || p_memory_key, 0));

    SELECT * INTO v_existing
      FROM agent_memories
     WHERE agent_id = p_agent_id
       AND memory_key = p_memory_key
       AND status = 'active'
     FOR UPDATE;

    IF FOUND THEN
        IF v_existing.content = p_content THEN
            RETURN NEXT v_existing;
            RETURN;
        END IF;

        UPDATE agent_memories
           SET status = 'superseded',
               superseded_at = now()
         WHERE id = v_existing.id;

        INSERT INTO agent_memories (
            agent_id, memory_key, content, source_agent_id, source_reference,
            version, status, embedding, observed_at, subject_key, valid_until,
            hop_depth, source_version
        ) VALUES (
            p_agent_id, p_memory_key, p_content,
            COALESCE(p_source_agent_id, p_agent_id), p_source_reference,
            v_existing.version + 1, 'active', v_vector, v_observed,
            COALESCE(p_subject_key, v_existing.subject_key), p_valid_until,
            v_existing.hop_depth, v_existing.version
        ) RETURNING * INTO v_row;

        INSERT INTO memory_provenance (
            memory_id, parent_memory_id, from_agent_id, to_agent_id, relationship, note
        ) VALUES (
            v_row.id, v_existing.id, p_agent_id, p_agent_id, 'supersedes',
            'content changed, previous version superseded'
        );

        RETURN NEXT v_row;
        RETURN;
    END IF;

    INSERT INTO agent_memories (
        agent_id, memory_key, content, source_agent_id, source_reference,
        version, status, embedding, observed_at, subject_key, valid_until,
        hop_depth, source_version
    ) VALUES (
        p_agent_id, p_memory_key, p_content,
        COALESCE(p_source_agent_id, p_agent_id), p_source_reference,
        1, 'active', v_vector, v_observed, p_subject_key, p_valid_until, 0, NULL
    ) RETURNING * INTO v_row;

    RETURN NEXT v_row;
END
$fn$;

-- ---------------------------------------------------------------------
-- 2. supersede_memory_atomic
--    Explicit version update. Requires an existing active memory.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION supersede_memory_atomic(
    p_agent_id         text,
    p_memory_key       text,
    p_new_content      text,
    p_source_reference text        DEFAULT NULL,
    p_embedding        text        DEFAULT NULL,
    p_observed_at      timestamptz DEFAULT NULL,
    p_subject_key      text        DEFAULT NULL,
    p_valid_until      timestamptz DEFAULT NULL
) RETURNS SETOF agent_memories
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_old    agent_memories%ROWTYPE;
    v_new    agent_memories%ROWTYPE;
    v_observed timestamptz := COALESCE(p_observed_at, now());
    v_vector vector;
BEGIN
    IF p_agent_id IS NULL OR btrim(p_agent_id) = '' THEN
        RAISE EXCEPTION 'agent_id is required' USING ERRCODE = 'MEM01';
    END IF;
    IF p_memory_key IS NULL OR btrim(p_memory_key) = '' THEN
        RAISE EXCEPTION 'memory_key is required' USING ERRCODE = 'MEM01';
    END IF;
    IF p_new_content IS NULL OR btrim(p_new_content) = '' THEN
        RAISE EXCEPTION 'content is required' USING ERRCODE = 'MEM01';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM memory_agents WHERE agent_id = p_agent_id) THEN
        RAISE EXCEPTION 'agent "%" is not registered', p_agent_id USING ERRCODE = 'MEM01';
    END IF;

    IF p_embedding IS NOT NULL AND btrim(p_embedding) <> '' THEN
        v_vector := p_embedding::vector;
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(p_agent_id || '::' || p_memory_key, 0));

    SELECT * INTO v_old
      FROM agent_memories
     WHERE agent_id = p_agent_id
       AND memory_key = p_memory_key
       AND status = 'active'
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'no active memory found for "%::%"', p_agent_id, p_memory_key
            USING ERRCODE = 'P0002';
    END IF;

    UPDATE agent_memories
       SET status = 'superseded',
           superseded_at = now()
     WHERE id = v_old.id;

    INSERT INTO agent_memories (
        agent_id, memory_key, content, source_agent_id, source_reference,
        version, status, embedding, observed_at, subject_key, valid_until,
        hop_depth, source_version
    ) VALUES (
        p_agent_id, p_memory_key, p_new_content,
        COALESCE(v_old.source_agent_id, p_agent_id), p_source_reference,
        v_old.version + 1, 'active', v_vector, v_observed,
        COALESCE(p_subject_key, v_old.subject_key), p_valid_until,
        v_old.hop_depth, v_old.version
    ) RETURNING * INTO v_new;

    INSERT INTO memory_provenance (
        memory_id, parent_memory_id, from_agent_id, to_agent_id, relationship, note
    ) VALUES (
        v_new.id, v_old.id, p_agent_id, p_agent_id, 'supersedes',
        'explicit version update'
    );

    RETURN NEXT v_new;
END
$fn$;

-- ---------------------------------------------------------------------
-- 3. share_memory_atomic
--    Copies a memory into another agent's space under the sharing policy
--    and records the derived_from edge. The copy and the edge are written
--    in the same transaction, so a copy can never exist without its
--    provenance.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION share_memory_atomic(
    p_source_memory_id uuid,
    p_target_agent_id   text,
    p_memory_key        text    DEFAULT NULL,
    p_max_hops          integer DEFAULT NULL
) RETURNS SETOF agent_memories
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_source       agent_memories%ROWTYPE;
    v_copy         agent_memories%ROWTYPE;
    v_existing     agent_memories%ROWTYPE;
    v_key          text;
    v_allowed      boolean;
    v_max_hops     integer;
    v_next_version integer;
BEGIN
    IF p_source_memory_id IS NULL THEN
        RAISE EXCEPTION 'source_memory_id is required' USING ERRCODE = 'P0002';
    END IF;
    IF p_target_agent_id IS NULL OR btrim(p_target_agent_id) = '' THEN
        RAISE EXCEPTION 'target_agent_id is required' USING ERRCODE = 'MEM01';
    END IF;

    SELECT * INTO v_source
      FROM agent_memories
     WHERE id = p_source_memory_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'source memory % does not exist', p_source_memory_id
            USING ERRCODE = 'P0002';
    END IF;

    IF v_source.status <> 'active' THEN
        RAISE EXCEPTION 'source memory % is % and cannot be shared',
            p_source_memory_id, v_source.status USING ERRCODE = 'P0002';
    END IF;

    IF v_source.agent_id = p_target_agent_id THEN
        RAISE EXCEPTION 'agent "%" cannot share a memory with itself', v_source.agent_id
            USING ERRCODE = 'MEM05';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM memory_agents WHERE agent_id = p_target_agent_id) THEN
        RAISE EXCEPTION 'agent "%" is not registered', p_target_agent_id
            USING ERRCODE = 'MEM01';
    END IF;

    SELECT allowed, max_hops INTO v_allowed, v_max_hops
      FROM memory_share_policy
     WHERE from_agent_id = v_source.agent_id
       AND to_agent_id = p_target_agent_id;

    IF NOT FOUND OR v_allowed IS NOT TRUE THEN
        RAISE EXCEPTION 'sharing from "%" to "%" is not permitted',
            v_source.agent_id, p_target_agent_id USING ERRCODE = 'MEM02';
    END IF;

    v_max_hops := COALESCE(p_max_hops, v_max_hops, 3);
    IF v_source.hop_depth + 1 > v_max_hops THEN
        RAISE EXCEPTION 'hop limit % reached for "%" -> "%"',
            v_max_hops, v_source.agent_id, p_target_agent_id USING ERRCODE = 'MEM03';
    END IF;

    v_key := COALESCE(p_memory_key, 'shared_' || v_source.memory_key);

    PERFORM pg_advisory_xact_lock(hashtextextended(p_target_agent_id || '::' || v_key, 0));

    -- The key may already hold an active row: either the very copy we are
    -- about to return (same source version), a copy of an *older* version of
    -- the same source (the source was updated after it was shared), or an
    -- unrelated memory that happens to use the same key.
    SELECT * INTO v_existing
      FROM agent_memories
     WHERE agent_id = p_target_agent_id
       AND memory_key = v_key
       AND status = 'active'
     FOR UPDATE;

    IF FOUND THEN
        -- Sharing the same source version twice is a no-op.
        IF v_existing.source_reference = v_source.id::text THEN
            RETURN NEXT v_existing;
            RETURN;
        END IF;

        -- Refreshing a copy after its source was superseded is allowed.
        -- Every version of the copy chain carries a derived_from edge to the
        -- source chain it came from; an unrelated row has no such edge.
        IF NOT EXISTS (
            SELECT 1
              FROM memory_provenance pr
              JOIN agent_memories child  ON child.id = pr.memory_id
              JOIN agent_memories parent ON parent.id = pr.parent_memory_id
             WHERE pr.relationship = 'derived_from'
               AND child.agent_id = p_target_agent_id
               AND child.memory_key = v_key
               AND parent.agent_id = v_source.agent_id
               AND parent.memory_key = v_source.memory_key
        ) THEN
            RAISE EXCEPTION
                'key "%" on agent "%" already holds an unrelated memory',
                v_key, p_target_agent_id USING ERRCODE = 'MEM04';
        END IF;
    END IF;

    -- The copy has its own version chain, independent of the source chain.
    SELECT COALESCE(max(version), 0) INTO v_next_version
      FROM agent_memories
     WHERE agent_id = p_target_agent_id
       AND memory_key = v_key;
    v_next_version := v_next_version + 1;

    IF v_existing.id IS NOT NULL THEN
        UPDATE agent_memories
           SET status = 'superseded',
               superseded_at = now()
         WHERE id = v_existing.id;
    END IF;

    INSERT INTO agent_memories (
        agent_id, memory_key, content, source_agent_id, source_reference,
        version, status, embedding, observed_at, subject_key, valid_until,
        hop_depth, source_version
    ) VALUES (
        p_target_agent_id, v_key, v_source.content, v_source.agent_id,
        v_source.id::text, v_next_version, 'active', v_source.embedding,
        v_source.observed_at, v_source.subject_key, v_source.valid_until,
        v_source.hop_depth + 1, v_source.version
    ) RETURNING * INTO v_copy;

    IF v_existing.id IS NOT NULL THEN
        INSERT INTO memory_provenance (
            memory_id, parent_memory_id, from_agent_id, to_agent_id, relationship, note
        ) VALUES (
            v_copy.id, v_existing.id, p_target_agent_id, p_target_agent_id,
            'supersedes', 'shared copy refreshed after the source changed'
        );
    END IF;

    INSERT INTO memory_provenance (
        memory_id, parent_memory_id, from_agent_id, to_agent_id, relationship, note
    ) VALUES (
        v_copy.id, v_source.id, v_source.agent_id, p_target_agent_id, 'derived_from',
        'controlled share, hop ' || (v_source.hop_depth + 1)
    );

    RETURN NEXT v_copy;
END
$fn$;

-- ---------------------------------------------------------------------
-- 4. get_ancestry
--    Walks the provenance edges upwards and returns every ancestor with
--    the relationship that connects it. Depth 0 is the memory itself.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_ancestry(
    p_memory_id uuid,
    p_max_depth integer DEFAULT 10
) RETURNS TABLE (
    depth           integer,
    memory_id       uuid,
    parent_memory_id uuid,
    agent_id        text,
    memory_key      text,
    version         integer,
    status          text,
    relationship    text,
    content         text
)
LANGUAGE sql
STABLE
AS $fn$
    WITH RECURSIVE chain AS (
        SELECT 0                AS depth,
               m.id             AS memory_id,
               NULL::uuid       AS parent_memory_id,
               m.agent_id       AS agent_id,
               m.memory_key     AS memory_key,
               m.version        AS version,
               m.status         AS status,
               NULL::text       AS relationship,
               m.content        AS content
          FROM agent_memories m
         WHERE m.id = p_memory_id
        UNION ALL
        SELECT c.depth + 1,
               p.id,
               pr.parent_memory_id,
               p.agent_id,
               p.memory_key,
               p.version,
               p.status,
               pr.relationship,
               p.content
          FROM chain c
          JOIN memory_provenance pr ON pr.memory_id = c.memory_id
          JOIN agent_memories p     ON p.id = pr.parent_memory_id
         WHERE c.depth < p_max_depth
           AND pr.parent_memory_id IS NOT NULL
    )
    SELECT c.depth, c.memory_id, c.parent_memory_id, c.agent_id, c.memory_key,
           c.version, c.status, c.relationship, c.content
      FROM chain c
     ORDER BY c.depth;
$fn$;

-- ---------------------------------------------------------------------
-- 5. get_descendants
--    The opposite direction: every memory that was derived from this one.
--    Used to detect stale copies when a source memory is superseded.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_descendants(
    p_memory_id uuid,
    p_max_depth integer DEFAULT 10
) RETURNS TABLE (
    depth           integer,
    memory_id       uuid,
    parent_memory_id uuid,
    agent_id        text,
    memory_key      text,
    version         integer,
    status          text,
    relationship    text,
    hop_depth       integer
)
LANGUAGE sql
STABLE
AS $fn$
    WITH RECURSIVE tree AS (
        SELECT 0            AS depth,
               m.id         AS memory_id,
               NULL::uuid   AS parent_memory_id,
               m.agent_id   AS agent_id,
               m.memory_key AS memory_key,
               m.version    AS version,
               m.status     AS status,
               NULL::text   AS relationship,
               m.hop_depth  AS hop_depth
          FROM agent_memories m
         WHERE m.id = p_memory_id
        UNION ALL
        SELECT t.depth + 1,
               c.id,
               pr.parent_memory_id,
               c.agent_id,
               c.memory_key,
               c.version,
               c.status,
               pr.relationship,
               c.hop_depth
          FROM tree t
          JOIN memory_provenance pr ON pr.parent_memory_id = t.memory_id
          JOIN agent_memories c     ON c.id = pr.memory_id
         WHERE t.depth < p_max_depth
    )
    SELECT t.depth, t.memory_id, t.parent_memory_id, t.agent_id, t.memory_key,
           t.version, t.status, t.relationship, t.hop_depth
      FROM tree t
     ORDER BY t.depth, t.agent_id;
$fn$;

-- ---------------------------------------------------------------------
-- 6. match_agent_memories
--    Vector similarity search, restricted to one agent's active memories.
--    The embedding is passed as text and cast here (see the note at the
--    top of this file). The ANN index is added in milestone M3, once the
--    embedding dimension is fixed.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION match_agent_memories(
    p_agent_id        text,
    p_query_embedding text,
    p_match_threshold double precision DEFAULT 0.75,
    p_match_count     integer DEFAULT 10
) RETURNS TABLE (
    id          uuid,
    agent_id    text,
    memory_key  text,
    content     text,
    version     integer,
    status      text,
    observed_at timestamptz,
    similarity  double precision
)
LANGUAGE plpgsql
STABLE
AS $fn$
DECLARE
    v_query vector;
    v_agent text := p_agent_id;
    v_limit integer := LEAST(GREATEST(COALESCE(p_match_count, 10), 1), 100);
    v_threshold double precision := COALESCE(p_match_threshold, 0.75);
BEGIN
    IF p_query_embedding IS NULL OR btrim(p_query_embedding) = '' THEN
        RAISE EXCEPTION 'query embedding is required' USING ERRCODE = 'MEM01';
    END IF;

    v_query := p_query_embedding::vector;

    RETURN QUERY
    SELECT m.id, m.agent_id, m.memory_key, m.content, m.version, m.status,
           m.observed_at,
           (1 - (m.embedding <=> v_query))::double precision
      FROM agent_memories m
     WHERE m.agent_id = v_agent
       AND m.status = 'active'
       AND m.embedding IS NOT NULL
       AND (1 - (m.embedding <=> v_query)) >= v_threshold
     ORDER BY m.embedding <=> v_query
     LIMIT v_limit;
END
$fn$;

-- ---------------------------------------------------------------------
-- 7. Self-test
--    Exercises every function above and removes its own rows again.
--    A failure aborts the whole migration (nothing is applied).
-- ---------------------------------------------------------------------
DO $selftest$
DECLARE
    v_source_agent text := '__selftest_source__';
    v_target_agent text := '__selftest_target__';
    v_v1       uuid;
    v_v2       uuid;
    v_v3       uuid;
    v_v2_again uuid;
    v_copy     uuid;
    v_version  integer;
    v_rows     integer;
BEGIN
    INSERT INTO memory_agents (agent_id, role)
    VALUES (v_source_agent, 'agent'), (v_target_agent, 'agent')
    ON CONFLICT (agent_id) DO NOTHING;

    INSERT INTO memory_share_policy (from_agent_id, to_agent_id, allowed, max_hops)
    VALUES (v_source_agent, v_target_agent, true, 3)
    ON CONFLICT (from_agent_id, to_agent_id) DO NOTHING;

    -- 1. creating a memory yields version 1
    SELECT id, version INTO v_v1, v_version
      FROM save_memory_atomic(v_source_agent, 'selftest_key', 'first content');
    IF v_version <> 1 THEN
        RAISE EXCEPTION 'selftest: expected version 1, got %', v_version;
    END IF;

    -- 2. saving the same content again is idempotent
    SELECT id, version INTO v_v2_again, v_version
      FROM save_memory_atomic(v_source_agent, 'selftest_key', 'first content');
    IF v_v2_again <> v_v1 OR v_version <> 1 THEN
        RAISE EXCEPTION 'selftest: save_memory_atomic is not idempotent';
    END IF;

    -- 3. saving different content creates version 2
    SELECT id, version INTO v_v2, v_version
      FROM save_memory_atomic(v_source_agent, 'selftest_key', 'second content');
    IF v_version <> 2 THEN
        RAISE EXCEPTION 'selftest: expected version 2, got %', v_version;
    END IF;

    -- 4. the previous version is superseded, not deleted
    IF (SELECT status FROM agent_memories WHERE id = v_v1) <> 'superseded' THEN
        RAISE EXCEPTION 'selftest: previous version was not superseded';
    END IF;

    -- 5. the supersession is recorded in the provenance table
    IF NOT EXISTS (
        SELECT 1 FROM memory_provenance
         WHERE memory_id = v_v2 AND parent_memory_id = v_v1
           AND relationship = 'supersedes'
    ) THEN
        RAISE EXCEPTION 'selftest: supersedes edge is missing';
    END IF;

    -- 6. explicit supersession also works
    PERFORM supersede_memory_atomic(v_source_agent, 'selftest_key', 'third content');
    SELECT id INTO v_v3
      FROM agent_memories
     WHERE agent_id = v_source_agent AND memory_key = 'selftest_key'
       AND content = 'third content' AND status = 'active' AND version = 3;
    IF v_v3 IS NULL THEN
        RAISE EXCEPTION 'selftest: supersede_memory_atomic did not create version 3';
    END IF;

    -- 7. sharing writes the copy and its provenance edge. Use the *active*
    --    version (v_v3); v_v2 was superseded in step 6 and cannot be shared.
    SELECT id INTO v_copy FROM share_memory_atomic(v_v3, v_target_agent);
    IF NOT EXISTS (
        SELECT 1 FROM memory_provenance
         WHERE memory_id = v_copy AND parent_memory_id = v_v3
           AND relationship = 'derived_from'
    ) THEN
        RAISE EXCEPTION 'selftest: derived_from edge is missing';
    END IF;

    -- 8. the copy is visible in the target agent's space and is a copy
    IF NOT EXISTS (
        SELECT 1 FROM agent_memories
         WHERE id = v_copy AND agent_id = v_target_agent
           AND status = 'active' AND hop_depth = 1
           AND source_reference = v_v3::text AND source_version = 3
    ) THEN
        RAISE EXCEPTION 'selftest: shared copy is not correct';
    END IF;

    -- 9. sharing the same source version twice does not duplicate the copy
    PERFORM share_memory_atomic(v_v3, v_target_agent);
    SELECT count(*) INTO v_rows
      FROM agent_memories
     WHERE agent_id = v_target_agent AND source_reference = v_v3::text;
    IF v_rows <> 1 THEN
        RAISE EXCEPTION 'selftest: sharing is not idempotent (% copies)', v_rows;
    END IF;

    -- 10. the ancestry is multi-hop
    SELECT count(*) INTO v_rows FROM get_ancestry(v_copy);
    IF v_rows < 2 THEN
        RAISE EXCEPTION 'selftest: ancestry is too short (% rows)', v_rows;
    END IF;

    -- 11. the descendants are reachable as well
    SELECT count(*) INTO v_rows FROM get_descendants(v_v3);
    IF v_rows < 2 THEN
        RAISE EXCEPTION 'selftest: descendants are missing (% rows)', v_rows;
    END IF;

    -- 12. an unlisted share is denied
    BEGIN
        PERFORM share_memory_atomic(v_copy, v_source_agent);
        RAISE EXCEPTION 'selftest: a share that is not in the policy was allowed';
    EXCEPTION
        WHEN SQLSTATE 'MEM02' THEN NULL;
    END;

    -- 13. self sharing is denied (v_v3 is the active version owned by source)
    BEGIN
        PERFORM share_memory_atomic(v_v3, v_source_agent);
        RAISE EXCEPTION 'selftest: an agent shared a memory with itself';
    EXCEPTION
        WHEN SQLSTATE 'MEM05' THEN NULL;
    END;

    -- 14. re-sharing after the source changed versions the existing copy
    --     instead of colliding with the one-active-key index.
    PERFORM supersede_memory_atomic(v_source_agent, 'selftest_key', 'fourth content');
    SELECT id INTO v_v3
      FROM agent_memories
     WHERE agent_id = v_source_agent AND memory_key = 'selftest_key'
       AND status = 'active' AND version = 4;
    IF v_v3 IS NULL THEN
        RAISE EXCEPTION 'selftest: source version 4 was not created';
    END IF;

    SELECT id INTO v_copy FROM share_memory_atomic(v_v3, v_target_agent);
    IF NOT EXISTS (
        SELECT 1 FROM agent_memories
         WHERE id = v_copy
           AND agent_id = v_target_agent
           AND memory_key = 'shared_selftest_key'
           AND status = 'active'
           AND version = 2
           AND source_version = 4
           AND source_reference = v_v3::text
    ) THEN
        RAISE EXCEPTION 'selftest: re-sharing an updated source did not version the copy';
    END IF;

    SELECT count(*) INTO v_rows
      FROM agent_memories
     WHERE agent_id = v_target_agent
       AND memory_key = 'shared_selftest_key'
       AND status = 'active';
    IF v_rows <> 1 THEN
        RAISE EXCEPTION 'selftest: expected one active shared copy, found %', v_rows;
    END IF;

    SELECT count(*) INTO v_rows
      FROM agent_memories
     WHERE agent_id = v_target_agent
       AND memory_key = 'shared_selftest_key'
       AND status = 'superseded';
    IF v_rows <> 1 THEN
        RAISE EXCEPTION 'selftest: the previous copy was not superseded (% rows)', v_rows;
    END IF;

    RAISE NOTICE 'memory function self-test passed';

    -- clean up after ourselves
    DELETE FROM agent_memories WHERE agent_id IN (v_source_agent, v_target_agent);
    DELETE FROM memory_agents WHERE agent_id IN (v_source_agent, v_target_agent);
EXCEPTION
    WHEN OTHERS THEN
        DELETE FROM agent_memories WHERE agent_id IN (v_source_agent, v_target_agent);
        DELETE FROM memory_agents WHERE agent_id IN (v_source_agent, v_target_agent);
        RAISE;
END
$selftest$;

COMMIT;

-- =====================================================================
-- After running this file, run 003_verify_after_apply.sql.
-- =====================================================================
