"""Internal specialist subagents for Vendra, Procura and Finora.

Each main agent coordinates exactly six specialists of its own (see
``vendra.py``/``procura.py``/``finora.py`` in this package) through a small
LangGraph ``StateGraph`` built by :func:`app.procurement.subagents.base.
build_specialist_graph` -- the same deterministic, dependency-ordered
pipeline pattern the cross-system coordinator already uses one level up
(``app.procurement.coordinator``), not a free-form LLM picking which
specialist runs next.
"""
