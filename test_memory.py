
from app.memory.store import save_memory, get_agent_memories
from app.utils.console import enable_utf8_console
from app.utils.writes import describe_write_policy, writes_enabled

enable_utf8_console()

WRITE = writes_enabled()

print(describe_write_policy())

if WRITE:
    # Save a memory for the Researcher
    save_memory(
        agent_id="researcher",
        memory_key="research_finding",
        content="The product price was reported as ₹55,000.",
        source_agent_id="researcher",
        source_reference="research_test",
    )

    # Save a separate memory for the Executor
    save_memory(
        agent_id="executor",
        memory_key="execution_note",
        content="The task is waiting for price verification.",
        source_agent_id="executor",
        source_reference="execution_test",
    )
else:
    print("\nSkipping both save_memory() calls (dry run).")

# Retrieve each agent's own memories
researcher_memories = get_agent_memories("researcher")
executor_memories = get_agent_memories("executor")

print("\n--- RESEARCHER MEMORY ---")
for memory in researcher_memories:
    print(memory["agent_id"], "|", memory["content"])

print("\n--- EXECUTOR MEMORY ---")
for memory in executor_memories:
    print(memory["agent_id"], "|", memory["content"])

# Verify isolation
assert all(
    m["agent_id"] == "researcher"
    for m in researcher_memories
)

assert all(
    m["agent_id"] == "executor"
    for m in executor_memories
)

print("\nSUCCESS: Each agent retrieves only its own memories.")
