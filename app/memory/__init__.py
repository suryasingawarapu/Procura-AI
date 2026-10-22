"""Persistent, versioned, provenance-aware agent memory.

Modules
-------
``client``       Supabase client construction.
``errors``       Typed errors raised by this package.
``formatting``   Serialisation helpers for the SQL function parameters.
``rpc``          Bridge to the atomic SQL functions and error translation.
``store``        Save and read operations.
``versioning``   Version chains and supersession.
``propagation``  Controlled sharing and provenance traversal.

Nothing is re-exported here on purpose: importing ``app.memory`` must not
pull in the Supabase client (which needs credentials at construction time).
"""
