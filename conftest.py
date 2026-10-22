"""Pytest configuration for the repository root.

The three ad-hoc scripts that live in the repository root
(``test_memory.py``, ``test_versioning.py``, ``test_propagation.py``) write to
the shared Supabase project when executed directly. They are excluded from
pytest collection so that *no* pytest invocation -- ``pytest``,
``pytest .``, ``pytest -k ...`` -- can accidentally modify the database.

The equivalent, safe versions of these checks live under ``tests/``:
``tests/unit`` runs without a database, ``tests/integration`` performs
read-only queries only.
"""

collect_ignore = [
    "test_memory.py",
    "test_versioning.py",
    "test_propagation.py",
]
