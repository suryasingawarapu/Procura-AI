"""Optional, best-effort natural-language narration over an already
deterministically-computed result -- one genuinely separate, independently
invoked LLM call per system (Vendra/Procura/Finora). The coordinator
builds each system's own ``llm`` via ``app.agents.workflow.get_llm(role)``
(NVIDIA Nemotron by default, same model/config Procura's existing
``interpret_purchase_request`` already uses) and passes it in here.

This NEVER decides anything: Vendra's compliance verdict, Procura's
quotation comparison, and Finora's budget check are all computed in plain,
deterministic Python *before* this is ever called, exactly as before. The
narration is purely an explanatory layer appended to the message payload
(``ai_summary``) -- if the LLM is unavailable, misconfigured, or errors,
this returns ``None`` and callers keep using their existing deterministic
text; the pipeline's behavior and every compliance/financial/comparison
outcome is identical whether or not narration succeeds.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.observability import langfuse_tracing as tracing


def narrate(system_prompt: str, user_content: str, *, llm: Optional[Any] = None) -> Optional[str]:
    """One best-effort LLM turn, only if ``llm`` is actually given.

    Deliberately does NOT self-resolve a default model when ``llm`` is
    ``None`` -- unlike ``interpret_purchase_request``, every system
    orchestrator calling this is exercised directly by many existing unit
    tests with no ``llm`` argument, and auto-resolving there would turn
    every one of those into a real, billed network call. Whether
    narration is enabled at all is decided once, by the coordinator (see
    ``PROCUREMENT_AI_SUMMARIES`` in coordinator.py), which explicitly
    builds and passes an ``llm`` down when it's turned on.

    Returns the reply text, or ``None`` if no LLM was given or the call
    fails for any reason -- never raises.
    """
    if llm is None:
        return None

    try:
        from langchain_core.messages import HumanMessage
        from langchain_core.messages import SystemMessage as LCSystemMessage

        m = re.match(r"You are (\w+)", system_prompt)
        step = f"{m.group(1)} Narration" if m else "Narration"
        reply = llm.invoke(
            [LCSystemMessage(content=system_prompt), HumanMessage(content=user_content)], **tracing.llm_kwargs(step)
        )
        text = str(getattr(reply, "content", "") or "").strip()
        return text or None
    except Exception:
        return None
