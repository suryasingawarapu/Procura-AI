
from app.memory.store import supabase
from app.memory.propagation import share_memory
from app.utils.console import enable_utf8_console
from app.utils.writes import describe_write_policy, writes_enabled

enable_utf8_console()

WRITE = writes_enabled()

print(describe_write_policy())

# Find the active Researcher memory created by our versioning test.
result = (
    supabase.table("agent_memories")
    .select("*")
    .eq("agent_id", "researcher")
    .eq("memory_key", "versioning_demo")
    .eq("status", "active")
    .execute()
)

if not result.data:
    raise RuntimeError("Run test_versioning.py first.")

source = result.data[0]

if WRITE:
    # Share the finding with the Executor.
    copied = share_memory(
        source_memory_id=source["id"],
        target_agent_id="executor",
    )

    print("Source agent:", source["agent_id"])
    print("Target agent:", copied["agent_id"])
    print("Shared content:", copied["content"])
    print("Original memory ID:", source["id"])
    print("Copied memory ID:", copied["id"])
else:
    print("\nSkipping share_memory() (dry run).")
    print("Source agent:", source["agent_id"])
    print("Original memory ID:", source["id"])

if WRITE:
    # Verify the provenance link.
    links = (
        supabase.table("memory_provenance")
        .select("*")
        .eq("memory_id", copied["id"])
        .execute()
    )

    print("\nProvenance links:", links.data)
