
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Set SUPABASE_URL and SUPABASE_KEY in your .env file."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def save_memory(
    agent_id: str,
    memory_key: str,
    content: str,
    source_agent_id: str | None = None,
    source_reference: str | None = None,
):
    """Save a memory to the specified agent's memory space."""

    result = (
        supabase.table("agent_memories")
        .insert({
            "agent_id": agent_id,
            "memory_key": memory_key,
            "content": content,
            "source_agent_id": source_agent_id,
            "source_reference": source_reference,
        })
        .execute()
    )

    return result.data


def get_agent_memories(agent_id: str):
    """Retrieve active memories belonging to one agent."""

    result = (
        supabase.table("agent_memories")
        .select("*")
        .eq("agent_id", agent_id)
        .eq("status", "active")
        .order("created_at", desc=True)
        .execute()
    )

    return result.data
