"""Unit tests for the optional narration helper. No network call: every
case here either supplies a FakeLLM or omits one -- narrate() never
self-resolves a default model (see its own docstring for why)."""

from __future__ import annotations

from app.procurement.narration import narrate


class _FakeLLM:
    def __init__(self, content=None, raises=None):
        self._content = content
        self._raises = raises

    def invoke(self, messages):
        if self._raises:
            raise self._raises

        class _Reply:
            content = self._content

        return _Reply()


def test_narrate_returns_none_when_no_llm_given():
    """No llm=... at all -- the default. Must never attempt to self-resolve
    one (that would make every caller's unit tests a live network call)."""
    assert narrate("system prompt", "user content", llm=None) is None


def test_narrate_returns_the_reply_text_when_llm_succeeds():
    llm = _FakeLLM(content="Two suppliers were checked; neither is compliant yet.")
    result = narrate("system prompt", "user content", llm=llm)
    assert result == "Two suppliers were checked; neither is compliant yet."


def test_narrate_returns_none_on_empty_reply():
    llm = _FakeLLM(content="   ")
    assert narrate("system prompt", "user content", llm=llm) is None


def test_narrate_returns_none_instead_of_raising_when_the_llm_errors():
    llm = _FakeLLM(raises=RuntimeError("rate limited"))
    assert narrate("system prompt", "user content", llm=llm) is None
