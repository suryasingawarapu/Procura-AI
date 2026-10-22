"""Integration tests.

Every test in this package is **read-only**: it may issue SELECT queries
against the configured Supabase project but must never insert, update or
delete a row. Tests that need to write live behind the ``dbwrite`` marker and
are not part of the default run.
"""
