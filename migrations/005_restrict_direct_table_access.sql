-- =====================================================================
-- 005_restrict_direct_table_access.sql
-- Database-level write isolation: force all writes through the atomic
-- functions; stop direct table DML from PostgREST's anon/authenticated
-- roles.
--
-- *** DRAFTED ONLY -- NOT APPLIED. Review before running. ***
-- =====================================================================
--
-- THE GAP
--   001/002 added referential integrity and six functions that enforce
--   the sharing policy and keep provenance consistent, but those
--   functions are SECURITY INVOKER by default (Postgres's default --
--   001/002 never set SECURITY DEFINER) and nothing revokes direct
--   table privileges. PostgREST exposes every public table over REST,
--   so if the API key the app uses is `anon` or `authenticated` (not
--   `service_role`, which bypasses grants and RLS entirely), a client
--   can INSERT/UPDATE/DELETE agent_memories, memory_provenance,
--   memory_agents or memory_share_policy directly over PostgREST's
--   auto REST endpoints -- bypassing save_memory_atomic /
--   share_memory_atomic entirely: no provenance edge, no sharing-policy
--   check, no version-chain guarantee, and memory_share_policy itself
--   (which is the only thing enforcing who may share with whom) could
--   be rewritten directly.
--
-- WHAT THIS DOES
--   1. REVOKE INSERT/UPDATE/DELETE on the four tables from anon and
--      authenticated. SELECT is left untouched -- reads keep working
--      exactly as before, under the app's existing (FastAPI-level)
--      authorization.
--   2. Switches the six functions to SECURITY DEFINER with a pinned
--      search_path, so they keep working for anon/authenticated once
--      the table grants above are revoked. A pinned search_path is
--      required hardening for SECURITY DEFINER: without it, a caller
--      could create an object earlier in their own search_path that
--      shadows an unqualified name the function uses, and have the
--      function run it with the definer's privileges.
--   No table or function body is touched; no row is read, written or
--   deleted.
--
-- WHAT THIS DELIBERATELY DOES NOT DO
--   It does not enable row-level security or enforce one-agent-per-row
--   access at the database layer. Doing that correctly needs each
--   agent to authenticate as itself (e.g. a distinct JWT per agent
--   carrying an `agent_id` claim a policy can check), so the database
--   has something trustworthy to key a policy on. Today every agent
--   shares one Supabase API key, so a blanket RLS policy could only be
--   "allow everything" (no real isolation, false sense of security) or
--   "deny everything" (breaks the app for whichever role the key maps
--   to). That is a separate, larger change than a migration file --
--   see the per-agent token scoping already implemented at the API
--   layer (app/api/auth.py: MEMORY_API_TOKEN_<AGENT>) for the
--   isolation that exists today.
--
-- SAFETY / IMPACT BY KEY TYPE
--   * SUPABASE_KEY = service_role (likely, given the app uses one key
--     for every agent and every operation): this migration has NO
--     observable effect. service_role bypasses table grants and RLS
--     unconditionally, so nothing here changes what the app can do.
--     Still worth applying as defense in depth against the key ever
--     being swapped for anon/authenticated, or a future client using
--     the project's anon key directly.
--   * SUPABASE_KEY = anon or authenticated: this closes the direct-
--     write hole described above. Reads are unaffected. Writes through
--     the Python app (which only ever calls the RPC functions) are
--     unaffected. Anything that was writing to these tables directly
--     over PostgREST, bypassing the app, would stop working -- which
--     is the point.
--
-- RE-RUN SAFE
--   Yes. REVOKE on a privilege that is already absent is a no-op, and
--   ALTER FUNCTION ... SECURITY DEFINER / SET search_path is
--   idempotent.
--
-- HOW TO APPLY
--   Supabase dashboard -> SQL Editor -> paste -> Run, after 001, 002
--   and 004. One transaction; nothing applies if any statement fails.
--
-- HOW TO UNDO (not part of the shared ROLLBACK.sql, which only covers
-- 001+002; apply this block by hand if needed):
--   BEGIN;
--   ALTER FUNCTION public.save_memory_atomic(text, text, text, text, text, text, timestamptz, text, timestamptz) SECURITY INVOKER;
--   ALTER FUNCTION public.supersede_memory_atomic(text, text, text, text, text, timestamptz, text, timestamptz) SECURITY INVOKER;
--   ALTER FUNCTION public.share_memory_atomic(uuid, text, text, integer) SECURITY INVOKER;
--   ALTER FUNCTION public.get_ancestry(uuid, integer) SECURITY INVOKER;
--   ALTER FUNCTION public.get_descendants(uuid, integer) SECURITY INVOKER;
--   ALTER FUNCTION public.match_agent_memories(text, text, double precision, integer) SECURITY INVOKER;
--   GRANT INSERT, UPDATE, DELETE ON TABLE public.agent_memories TO anon, authenticated;
--   GRANT INSERT, UPDATE, DELETE ON TABLE public.memory_provenance TO anon, authenticated;
--   GRANT INSERT, UPDATE, DELETE ON TABLE public.memory_agents TO anon, authenticated;
--   GRANT INSERT, UPDATE, DELETE ON TABLE public.memory_share_policy TO anon, authenticated;
--   COMMIT;
-- =====================================================================

BEGIN;

-- 1. Stop direct writes to the app tables from anon/authenticated -----
REVOKE INSERT, UPDATE, DELETE ON TABLE public.agent_memories FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.memory_provenance FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.memory_agents FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.memory_share_policy FROM anon, authenticated;

-- 2. Let the atomic functions keep working for anon/authenticated -----
--    now that the underlying tables are locked down for those roles.
ALTER FUNCTION public.save_memory_atomic(text, text, text, text, text, text, timestamptz, text, timestamptz)
    SECURITY DEFINER SET search_path = public, pg_temp;
ALTER FUNCTION public.supersede_memory_atomic(text, text, text, text, text, timestamptz, text, timestamptz)
    SECURITY DEFINER SET search_path = public, pg_temp;
ALTER FUNCTION public.share_memory_atomic(uuid, text, text, integer)
    SECURITY DEFINER SET search_path = public, pg_temp;
ALTER FUNCTION public.get_ancestry(uuid, integer)
    SECURITY DEFINER SET search_path = public, pg_temp;
ALTER FUNCTION public.get_descendants(uuid, integer)
    SECURITY DEFINER SET search_path = public, pg_temp;
ALTER FUNCTION public.match_agent_memories(text, text, double precision, integer)
    SECURITY DEFINER SET search_path = public, pg_temp;

COMMIT;
