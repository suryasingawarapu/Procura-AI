"""The three procurement systems' memory-owning identities.

Specialist task executors (Vendor Discovery, RFQ Management, Budget
Verification, ...) are not memory-owning identities -- per spec they are
task executors, not independent durable-memory owners. Only the three
system orchestrators below own a private memory scope, registered in
``memory_agents`` by migrations/006_procurement_provenance_fields.sql.
"""

from __future__ import annotations

SYSTEM_IDS = ("vendra", "procura", "finora")

# Owns the chat front door's conversation memory (registered by migration 007).
# Not one of the three procurement systems, so it is not in SYSTEM_IDS.
COORDINATOR = "coordinator"
MEMORY_IDS = SYSTEM_IDS + (COORDINATOR,)

VENDRA = "vendra"
PROCURA = "procura"
FINORA = "finora"
