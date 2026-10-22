"""Tests for the Windows console encoding fix.

These tests reproduce the original failure (printing a rupee sign to a stream
that defaults to cp1252) and verify that ``enable_utf8_console()`` removes it.
"""

from __future__ import annotations

import io
import sys

import pytest

from app.utils.console import enable_utf8_console, stdout_encoding


def _cp1252_stream() -> io.TextIOWrapper:
    """A text stream that encodes with cp1252, like a redirected Windows stdout."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


def test_printing_rupee_fails_without_the_fix() -> None:
    """Documents the original bug: cp1252 cannot encode the rupee sign."""
    stream = _cp1252_stream()
    try:
        with pytest.raises(UnicodeEncodeError):
            stream.write("Price: \u20b955,000")
    finally:
        stream.detach()


def test_enable_utf8_console_switches_the_encoding() -> None:
    stream = _cp1252_stream()
    try:
        assert stream.encoding.lower().replace("-", "") == "cp1252"

        enable_utf8_console(stream)

        assert stream.encoding.lower().replace("-", "") == "utf8"
    finally:
        stream.detach()


def test_enable_utf8_console_allows_non_ascii_output() -> None:
    """The actual regression test: non-ASCII content must not raise."""
    stream = _cp1252_stream()
    try:
        enable_utf8_console(stream)

        stream.write("Price: \u20b955,000")
        stream.flush()

        assert "55,000" in stream.getvalue()
    finally:
        stream.detach()


def test_enable_utf8_console_ignores_streams_without_reconfigure() -> None:
    """A stream that cannot be reconfigured must not raise."""

    class Dummy:
        pass

    enable_utf8_console(Dummy())  # type: ignore[arg-type]


def test_enable_utf8_console_defaults_to_stdout_and_stderr() -> None:
    original_stdout, original_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout = _cp1252_stream()
        sys.stderr = _cp1252_stream()
        try:
            enable_utf8_console()
            assert sys.stdout.encoding.lower().replace("-", "") == "utf8"
            assert sys.stderr.encoding.lower().replace("-", "") == "utf8"
        finally:
            sys.stdout.detach()
            sys.stderr.detach()
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr


def test_stdout_encoding_helper_reports_a_value() -> None:
    assert stdout_encoding() != ""
