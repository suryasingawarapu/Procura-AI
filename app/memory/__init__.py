"""Persistent, versioned, provenance-aware agent memory.

Modules
-------
``store``        Supabase client plus the basic save / read operations.
``versioning``   Version chain creation and supersession.
``propagation``  Controlled sharing of a memory between agents.

Nothing is re-exported here on purpose: importing ``app.memory`` must not
pull in the Supabase client (which needs credentials at construction time).
"""
