"""Supabase client construction.

Kept in its own module so that :mod:`app.memory.store`, :mod:`app.memory.rpc`
and the test helpers can share a single client without creating an import
cycle.

The client is created eagerly so that a missing ``.env`` fails fast and
loudly instead of at the first query. No credential value is ever logged or
returned by this module.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Set SUPABASE_URL and SUPABASE_KEY in your .env file."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_client() -> Client:
    """Return the shared Supabase client."""
    return supabase
