"""Provenance-Aware Shared Memory for Multi-Agent LLM Systems.

Package layout
--------------
``app.memory``    Persistent, versioned, provenance-aware memory store.
``app.agents``    Agent definitions and LangGraph nodes (M5).
``app.tools``     LangChain tools exposed to the agents (M5).
``app.utils``     Cross-cutting helpers (console encoding, write guards).

Sub-packages are intentionally side-effect free on import: importing a package
must never create a database client or require credentials. The Supabase
client is created inside ``app.memory.store``.
"""
