"""Console encoding helpers.

Why this exists
---------------
On Windows, ``sys.stdout``/``sys.stderr`` default to the legacy ANSI code page
(cp1252) whenever the stream is redirected to a pipe or a file instead of a
real console. Printing non-ASCII characters -- for example the rupee sign
used in the memory fixtures (``Rs 55,000``) -- then raises
``UnicodeEncodeError`` and aborts the script.

``enable_utf8_console()`` re-configures the streams to UTF-8. It only touches
the *encoding* of the output stream; it never touches memory content, business
logic, or the database.

Usage
-----
Call it once at the very start of an entry point::

    from app.utils.console import enable_utf8_console

    enable_utf8_console()
"""

from __future__ import annotations

import sys
from typing import TextIO


def enable_utf8_console(*streams: TextIO) -> None:
    """Force UTF-8 encoding on the given text streams.

    Defaults to ``sys.stdout`` and ``sys.stderr``. Streams that do not support
    ``reconfigure`` (or that are already closed) are skipped silently, so the
    helper is safe to call unconditionally.
    """
    targets = streams if streams else (sys.stdout, sys.stderr)

    for stream in targets:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            continue


def stdout_encoding() -> str:
    """Return the current encoding of ``sys.stdout`` (diagnostics helper)."""
    return getattr(sys.stdout, "encoding", None) or "unknown"
