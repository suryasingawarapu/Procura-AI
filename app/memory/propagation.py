
from app.memory.store import supabase, save_memory


def share_memory(
    source_memory_id: str,
    target_agent_id: str,
):
    """Copy an active memory to another agent and record its origin."""

    # Fetch the original memory.
    result = (
        supabase.table("agent_memories")
        .select("*")
        .eq("id", source_memory_id)
        .eq("status", "active")
        .execute()
    )

    if not result.data:
        raise ValueError("Source memory not found or is not active.")

    source = result.data[0]

    # Save a copy in the receiving agent's memory.
    copied = save_memory(
        agent_id=target_agent_id,
        memory_key=f"shared_{source['memory_key']}",
        content=source["content"],
        source_agent_id=source["agent_id"],
        source_reference=source["id"],
    )[0]

    # Record the direct relationship between both memories.
    supabase.table("memory_provenance").insert({
        "memory_id": copied["id"],
        "parent_memory_id": source["id"],
        "from_agent_id": source["agent_id"],
        "to_agent_id": target_agent_id,
        "relationship": "derived_from",
    }).execute()

    return copied
