-- =====================================================================
-- 007_register_coordinator.sql
-- Register the coordinator as a memory-owning identity.
-- =====================================================================
--
-- The chat front door stores each conversation turn as a memory and keeps a
-- rolling summary of the conversation. Those memories need an owner; this
-- registers one. Additive and re-run safe (ON CONFLICT DO NOTHING). No
-- sharing policy is added: nothing is ever shared from or to the coordinator.
--
-- ROLLBACK (only if no coordinator memories exist yet):
--   DELETE FROM memory_agents WHERE agent_id = 'coordinator';
-- =====================================================================

BEGIN;

INSERT INTO memory_agents (agent_id, role, display_name) VALUES
    ('coordinator', 'agent', 'Coordinator -- chat front door and conversation memory')
ON CONFLICT (agent_id) DO NOTHING;

COMMIT;
