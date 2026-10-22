
from app.memory.store import save_memory, supabase
from app.memory.versioning import update_memory
from app.utils.console import enable_utf8_console
from app.utils.writes import describe_write_policy, writes_enabled

enable_utf8_console()

WRITE = writes_enabled()

agent = "researcher"
key = "versioning_demo"

print(describe_write_policy())

if WRITE:
    # Create a fresh test record.
    save_memory(
        agent_id=agent,
        memory_key=key,
        content="Product price is ₹55,000.",
        source_agent_id=agent,
        source_reference="version_test_v1",
    )

    # Update it to a new version.
    new_memory = update_memory(
        agent_id=agent,
        memory_key=key,
        new_content="Product price is ₹58,000.",
        source_reference="version_test_v2",
    )

    print("New version:", new_memory["version"])
    print("Current content:", new_memory["content"])
    print("Current status:", new_memory["status"])
else:
    print("\nSkipping save_memory() and update_memory() (dry run).")

# Show the complete version history.
history = (
    supabase.table("agent_memories")
    .select("version,content,status")
    .eq("agent_id", agent)
    .eq("memory_key", key)
    .order("version")
    .execute()
)

print("\nVersion history:")
for item in history.data:
    print(item)
