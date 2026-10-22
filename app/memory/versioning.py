
from app.memory.store import supabase


def update_memory(
    agent_id: str,
    memory_key: str,
    new_content: str,
    source_reference: str | None = None,
):
    """Create a new version and supersede the previous active version."""

    # Find the current active version for this agent and memory key.
    result = (
        supabase.table("agent_memories")
        .select("*")
        .eq("agent_id", agent_id)
        .eq("memory_key", memory_key)
        .eq("status", "active")
        .order("version", desc=True)
        .limit(1)
        .execute()
    )

    old_memories = result.data

    if not old_memories:
        raise ValueError(
            f"No active memory found for {agent_id}: {memory_key}"
        )

    old_memory = old_memories[0]
    new_version = old_memory["version"] + 1

    # Mark the old version as superseded.
    (
        supabase.table("agent_memories")
        .update({"status": "superseded"})
        .eq("id", old_memory["id"])
        .execute()
    )

    # Insert the replacement version.
    inserted = (
        supabase.table("agent_memories")
        .insert({
            "agent_id": agent_id,
            "memory_key": memory_key,
            "content": new_content,
            "source_agent_id": agent_id,
            "source_reference": source_reference,
            "version": new_version,
            "status": "active",
        })
        .execute()
    )

    new_memory = inserted.data[0]

    # Record the supersession relationship.
    (
        supabase.table("memory_provenance")
        .insert({
            "memory_id": new_memory["id"],
            "parent_memory_id": old_memory["id"],
            "from_agent_id": agent_id,
            "to_agent_id": agent_id,
            "relationship": "supersedes",
        })
        .execute()
    )

    return new_memory
