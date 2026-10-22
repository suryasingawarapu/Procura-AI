"""Test suite for the provenance-aware shared memory project.

Layout
------
``tests/unit``        Pure logic, no database access, no credentials needed.
``tests/integration`` Read-only checks against the configured Supabase project.

The suite never writes to the database. Anything that writes must live behind
the ``dbwrite`` marker and be opt-in.
"""
