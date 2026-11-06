"""LLM-based conversational routing for the chat frontend.

Decides what ONE incoming chat message is actually asking for: start/resume
a procurement task, just answer a question (explain the system, the
current task, procurement concepts in general), or honestly decline
something not built yet (e.g. actually sending an email, calling a
vendor). This sits IN FRONT OF the existing, completely unmodified
deterministic pipeline (app.procurement.task_orchestrator) -- it never
changes what Vendra/Procura/Finora compute or decide; it only decides
which existing, already-real action (or no action at all) this message
should trigger.

Falls back to a plain keyword heuristic -- the exact logic the chat
frontend used to run client-side -- whenever the LLM is unavailable or its
response can't be parsed, exactly like
app.procurement.systems.procura.interpret_purchase_request already does.
Never raises, never fabricates an action it didn't actually take.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from app.observability import langfuse_tracing as tracing

from . import workflow

__all__ = ["ChatDecision", "route_chat_message"]

_NEW_INSTRUCTION_RE = re.compile(r"\b(purchase|buy|procure|order|need\s+\d+|want\s+\d+)\b", re.IGNORECASE)
# A genuine question ("why do you need an item code?") must still reach
# the LLM even while a task is "Waiting For Information" -- only messages
# that don't look like a question are treated as deterministically
# supplying the missing value.
# Two requests that are plain lookups or actions, not conversations: they are recognised here, by
# rule, so they are never mistaken for a new purchase request (which would re-run the whole pipeline).
# Sending needs a send verb aimed at the PO/vendor; "send me ..", "give me ..", "draft .." are not sends.
_SEND_MAIL_RE = re.compile(
    r"\b(send|mail|forward|dispatch)\b[^.?!]{0,60}\b(po|purchase\s+order|order|e-?mail|vendor|supplier|them|him|it|quote)\b"
    r"|\be-?mail\s+(the\s+)?(vendor|supplier|them)\b",
    re.IGNORECASE,
)
_NOT_A_SEND_RE = re.compile(r"\b(send|mail)\s+me\b|\bgive\s+me\b|\bshow\s+me\b|\btell\s+me\b|\bdraft\b|\bwrite\b|\bcompose\b", re.IGNORECASE)
_DRAFTING_RE = re.compile(r"\b(draft|write|compose)\b", re.IGNORECASE)
_VENDOR_WORD_RE = re.compile(r"\b(vendor|supplier)s?\b", re.IGNORECASE)
_VENDOR_INFO_RE = re.compile(
    r"\b(info|information|details?|contact|e-?mail(\s+(id|address))?|phone|mobile|address|number|website)\b", re.IGNORECASE
)
_LOOKS_LIKE_A_QUESTION_RE = re.compile(r"\?\s*$|^\s*(why|what|how|who|when|where|which|can|could|is|are|do|does)\b", re.IGNORECASE)

# The real specialist functions per system (name: one accurate line, taken
# directly from each function's own docstring in app.procurement.subagents)
# -- given to the LLM verbatim so an "answer" about "what does Vendra
# check" describes what this code actually does, not generic textbook
# vendor-management criteria it might otherwise invent.
_ARCHITECTURE_FACTS = """Real architecture (the ONLY source of truth for any question about it):
Coordinator routes a task to Vendra, then Procura, then Finora. Vendra, Procura and Finora each run 6 \
specialists in order (18 in total), plus submit_purchase_order, which only runs after approval.

Vendra (vendor discovery/compliance):
- vendor_discovery: which suppliers are even candidates (active, not on hold/disabled).
- document_verification: which candidates have the required compliance documents on file and still valid (an expired document does not count).
- vendor_onboarding_readiness: readiness triage -- does ERPNext have the basics on file.
- vendor_profile: a consolidated profile per candidate (master data + compliance + more).
- vendor_performance_snapshot: price competitiveness for the current item only.
- vendor_risk_flagging: one risk category per supplier, from what ERPNext actually has on file.

Procura (RFQs/quotations/negotiation):
- rfq_management: prepares a real RFQ for the eligible suppliers.
- quotation_processing: collects quotations for the item, filtered to eligible suppliers; keeps each as a claim with its source and reconciles sources that disagree.
- quotation_comparison: deterministic ranking over whatever quotations were collected.
- supplier_matching: which eligible suppliers actually have a quotation on file for this item.
- delivery_schedule_estimation: each considered quotation's promised delivery timeframe.
- quotation_history: reads the stored summary of this item's quotation history first, then opens the supporting records to report changes (live mode only).
- (submit_purchase_order exists but is never run automatically -- only after a human approves.)

Finora (budget/finance):
- budget_verification: compares the estimated cost against a real, configured budget.
- cost_analysis: deeper arithmetic over budget_verification's own result.
- invoice_matching: matches any existing invoices for the supplier.
- payment_terms_review: reviews whatever payment-related fields actually exist.
- approval_routing: formalizes that no purchase order is ever auto-submitted.
- financial_records: assembles the consolidated record the orchestrator saves.

After a purchase order is submitted, a purchase-order email to the vendor is drafted automatically (never sent by chat);
a person sends it, and replies and follow-ups are tracked, through the mail API.
Not yet built at all: calling/contacting a vendor by phone, making a payment.
"""

_SYSTEM_PROMPT = f"""You are the router in front of a real, deterministic multi-agent \
procurement system. You never run the agents yourself and never decide anything about \
which supplier is eligible, what anything costs, or what budget is available -- that is \
all real, already-computed data given to you as context. Your only job is to decide what \
this ONE chat message needs.

{_ARCHITECTURE_FACTS}

Reply with JSON only, exactly one of these shapes:
{{"intent": "new_task"}}
{{"intent": "provide_info", "reply": "<short natural acknowledgement, e.g. \\"Got it, checking that.\\">"}}
{{"intent": "answer", "reply": "<a helpful, honest, conversational answer, grounded only in the \
architecture facts above and the task context below>"}}
{{"intent": "unsupported_action", "reply": "<honestly say this isn't built yet, and what would be needed instead>"}}

Rules:
- "new_task" ONLY when the message names an actual item/category and at least a rough \
quantity or need -- e.g. "purchase 10 laptops", "we need some office chairs", "order \
more packaging boxes". A bare greeting, small talk, "hi"/"hii"/"hello"/"thanks"/"ok", \
or anything that does not describe something to buy is NEVER "new_task" -- it is \
"answer" (reply naturally, e.g. "Hi! Tell me what you'd like to purchase, or ask me \
anything about the system."). When genuinely unsure whether a vague message is a \
purchase request, prefer "answer" and ask a clarifying question rather than guessing \
"new_task" -- a wrongly-started task is worse than asking once.
- Do NOT rewrite or paraphrase the purchase request for "new_task" -- the caller uses \
the user's own original wording (a separate, deterministic parser depends on specific \
phrasing like "within 60 lakh" or "within 20 days", and a paraphrase could silently \
corrupt it).
- "provide_info" whenever the context says a task is waiting on a specific missing \
detail (e.g. an item code) and this message looks like an attempt to answer that, even \
a short one like just a code or a number. You do not extract or validate the value \
yourself -- the caller does that, and the real system (not you) is what checks whether \
it's actually correct. NEVER judge whether a value "looks like" a real/valid code, item, \
or ID, and never second-guess or reject it on that basis -- you have no way to actually \
know what's valid in the real ERPNext instance. If it's remotely plausible as an answer \
to what's missing, classify it "provide_info" and let the real downstream check decide, \
rather than answering with your own guess about whether it's valid.
- "unsupported_action" for any request to actually perform a real external action \
that chat cannot perform -- e.g. "send an email to the vendor" (emails are sent only \nby a person through the mail API, after the PO is submitted), "call them", "pay \nthe invoice". Never claim to have done it or pretend it already happened. You may \nstill offer to draft the text in your reply (drafting is fine; sending is not).
- "answer" for everything else: greetings, small talk, questions, "why"/"what does X \
mean", asking about the recommendation, the architecture, or general procurement \
concepts.
- When asked what a specialist or system does, describe EXACTLY what the architecture \
facts above say -- never substitute generic vendor-management/procurement textbook \
criteria that aren't listed there, even if they sound plausible.
- Never invent a supplier name, price, compliance fact, or budget number that is not \
already present in the task context below -- if you don't know, say so plainly.
- "Conversation memory" (when present) is a stored summary of earlier turns plus the turns \
after it. Use it to follow what the user means by "that one" or "the same supplier", but it \
is only what was said, never a source of supplier, price or compliance facts.
"""


_JSON_ESCAPES = {"n": "\n", "t": "\t", "r": "", "b": "", "f": "", '"': '"', "\\": "\\", "/": "/"}
_SHOWN_INTENTS = ("answer", "unsupported_action")


class _ReplyStreamer:
    """Turns raw streamed LLM text (a JSON object being written out) into
    the decoded ``reply`` string, forwarded piece by piece as it arrives.

    Only starts once the intent is known to be one whose reply the user
    actually sees; JSON escapes split across chunk boundaries are held back
    until complete. Purely cosmetic: the final decision is still parsed from
    the full text afterwards, exactly as in the non-streaming path."""

    _INTENT_RE = re.compile(r'"intent"\s*:\s*"(\w+)"')
    _REPLY_RE = re.compile(r'"reply"\s*:\s*"')

    def __init__(self, on_token: Callable[[str], None], *, allow_prose: bool = True):
        self._on_token = on_token
        self._allow_prose = allow_prose
        self._prose = False
        self._buf = ""
        self._pos: Optional[int] = None
        self._done = False

    def feed(self, chunk: str) -> None:
        self._buf += chunk
        if self._done:
            return
        if self._prose:
            if chunk:
                self._on_token(chunk)
            return
        if self._pos is None:
            # The model sometimes ignores the JSON instruction and just
            # answers in prose; that prose becomes the answer (see
            # route_chat_message), so stream it as-is.
            head = self._buf.lstrip()
            if self._allow_prose and head and head[0] not in "{`":
                self._prose = True
                self._on_token(head)
                return
            intent = self._INTENT_RE.search(self._buf)
            reply = self._REPLY_RE.search(self._buf)
            if not (intent and reply):
                return
            if intent.group(1) not in _SHOWN_INTENTS:
                self._done = True
                return
            self._pos = reply.end()

        buf, i, out = self._buf, self._pos, []
        while i < len(buf):
            c = buf[i]
            if c == "\\":
                if i + 1 >= len(buf):
                    break
                nxt = buf[i + 1]
                if nxt == "u":
                    if i + 6 > len(buf):
                        break
                    try:
                        out.append(chr(int(buf[i + 2 : i + 6], 16)))
                    except ValueError:
                        pass
                    i += 6
                    continue
                out.append(_JSON_ESCAPES.get(nxt, nxt))
                i += 2
                continue
            if c == '"':
                self._done = True
                i += 1
                break
            out.append(c)
            i += 1
        self._pos = i
        if out:
            self._on_token("".join(out))


@dataclass
class ChatDecision:
    """``intent`` is the only thing this module decides. For "new_task" the
    caller always runs the user's own original message through the
    deterministic parser -- never an LLM-rewritten version of it (see the
    system prompt's rule on this)."""

    intent: str  # "new_task" | "provide_info" | "answer" | "unsupported_action" | "vendor_info" | "send_po_email"
    reply: Optional[str] = None
    supplier: Optional[str] = None  # for "vendor_info" / "send_po_email": the supplier the message is about


def _quotation_scores(coordinator_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Procura's own per-supplier comparison (rank/total_cost/delivery_days
    /payment_terms_days) -- this is real, already-computed data sitting in
    coordinator_result["messages"], just never surfaced to the chat before.
    Without this, a natural follow-up like "how much did each vendor
    quote" had no real data to answer from, even though the real numbers
    already existed."""
    for message in coordinator_result.get("messages") or []:
        if message.get("sender") != "procura":
            continue
        specialists = (message.get("payload") or {}).get("specialists") or {}
        comparison = (specialists.get("quotation_comparison") or {}).get("data", {}).get("comparison") or {}
        scores = comparison.get("scores")
        if scores:
            return scores
    return []


def _context_summary(pr: Optional[workflow.PurchaseRequest]) -> str:
    if pr is None:
        return "No active task yet -- this is the start of a new conversation."
    lines = [
        f"Task {pr.pr_id}: workflow_state={pr.workflow_state}, mode={pr.mode or 'unset'}",
        f"Requested: {pr.quantity} x {pr.item_description} (item_code={pr.item_code or 'not set'}), "
        f"budget={pr.estimated_budget}, delivery_location={pr.delivery_location}, "
        f"required_date={pr.required_date}",
    ]
    if pr.blockers:
        lines.append(f"Waiting on: {'; '.join(pr.blockers)}")
    cr = pr.coordinator_result or {}
    result = cr.get("result") or {}
    if result.get("recommended_supplier"):
        lines.append(
            f"Recommended supplier: {result['recommended_supplier']} "
            f"(total cost: {result.get('recommended_total_cost')})"
        )
    if result.get("eligible_supplier_names"):
        lines.append(f"Other eligible suppliers considered: {', '.join(result['eligible_supplier_names'])}")
    scores = _quotation_scores(cr)
    if scores:
        lines.append("Per-supplier quotations actually compared (rank, supplier, total cost, delivery days):")
        for s in scores:
            lines.append(
                f"  #{s.get('rank')} {s.get('supplier')}: total_cost={s.get('total_cost')}, "
                f"delivery_days={s.get('delivery_days')}, payment_terms_days={s.get('payment_terms_days')}"
            )
    if cr.get("approval_summary"):
        lines.append(f"Approval summary: {cr['approval_summary']}")
    if pr.purchase_order:
        lines.append(f"Purchase order status: {pr.purchase_order.get('status')}")
    return "\n".join(lines)


def _recommended_supplier(pr: Optional[workflow.PurchaseRequest]) -> Optional[str]:
    if pr is None:
        return None
    return (((pr.coordinator_result or {}).get("result")) or {}).get("recommended_supplier")


def _supplier_named(message: str, pr: workflow.PurchaseRequest, *, only_named: bool = False) -> Optional[str]:
    """A supplier this request actually compared that the message names, else the recommended one."""
    cr = pr.coordinator_result or {}
    names = [s.get("supplier") for s in _quotation_scores(cr)] + list((cr.get("result") or {}).get("eligible_supplier_names") or [])
    lowered = message.lower()
    for name in names:
        if name and name.lower() in lowered:
            return name
    return None if only_named else _recommended_supplier(pr)


def _detect_action(message: str, pr: Optional[workflow.PurchaseRequest]) -> Optional[ChatDecision]:
    if _SEND_MAIL_RE.search(message) and not _NOT_A_SEND_RE.search(message):
        return ChatDecision(intent="send_po_email", supplier=_recommended_supplier(pr))
    if (
        _recommended_supplier(pr)
        and _VENDOR_INFO_RE.search(message)
        and not _DRAFTING_RE.search(message)
        and (_VENDOR_WORD_RE.search(message) or _supplier_named(message, pr, only_named=True))
    ):
        return ChatDecision(intent="vendor_info", supplier=_supplier_named(message, pr))
    return None


def route_chat_message(
    *,
    message: str,
    pr: Optional[workflow.PurchaseRequest] = None,
    history: Optional[List[Dict[str, str]]] = None,
    llm: Optional[Any] = None,
    on_token: Optional[Callable[[str], None]] = None,
    memory_context: Optional[str] = None,
) -> ChatDecision:
    """Classify one chat message. Never raises -- falls back to a plain
    keyword heuristic (and, for a task genuinely "Waiting For Information",
    "provide_info") whenever the LLM is unavailable or its response can't
    be parsed as one of the expected shapes.

    "provide_info" itself is decided here FIRST, deterministically, never
    by the LLM -- a real bug found live: the LLM misclassified a message
    that unambiguously supplied the one missing field (e.g. a bare item
    code, in reply to a task genuinely "Waiting For Information") as a
    plain question roughly 1 in 3 times, for no identifiable reason,
    leaving the task stuck needing the same thing re-sent. This is safety-
    and correctness-relevant (resuming the right task with the right
    value) and must never depend on an LLM's classification being
    reliable call-to-call when a reliable, simple heuristic already
    exists. The LLM is still fully used for everything else (answer /
    new_task / unsupported_action), where there's no single deterministically-
    correct answer to short-circuit to.
    """
    if (
        pr is not None
        and pr.workflow_state == "Waiting For Information"
        and not _NEW_INSTRUCTION_RE.search(message)
        and not _LOOKS_LIKE_A_QUESTION_RE.search(message)
    ):
        return ChatDecision(intent="provide_info")

    action = _detect_action(message, pr)
    if action is not None:
        return action

    if llm is None:
        try:
            from app.agents.workflow import get_llm

            llm = get_llm("chat_router")
        except Exception:
            llm = None

    if llm is not None:
        try:
            from langchain_core.messages import HumanMessage
            from langchain_core.messages import SystemMessage as LCSystemMessage

            convo = "\n".join(f"{h.get('role', '?')}: {h.get('content', '')}" for h in (history or [])[-8:])
            memory_block = (
                f"Conversation memory (from persistent storage):\n{memory_context}\n\n" if memory_context else ""
            )
            user_content = (
                f"Context:\n{_context_summary(pr)}\n\n{memory_block}"
                f"Recent conversation:\n{convo}\n\nLatest message: {message}"
            )
            prompt = [LCSystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=user_content)]
            if on_token is not None:
                streamer = _ReplyStreamer(on_token, allow_prose=not _NEW_INSTRUCTION_RE.search(message))
                parts: List[str] = []
                for chunk in llm.stream(prompt, **tracing.llm_kwargs("Router")):
                    text = getattr(chunk, "content", "")
                    text = text if isinstance(text, str) else str(text or "")
                    parts.append(text)
                    streamer.feed(text)
                content = "".join(parts)
            else:
                reply = llm.invoke(prompt, **tracing.llm_kwargs("Router"))
                content = str(getattr(reply, "content", ""))
            match = re.search(r"\{.*\}", content, re.DOTALL)
            # A real failure mode found live: for some questions the model
            # ignores the JSON instruction and replies in plain prose. That
            # prose is a genuine, grounded answer -- discarding it showed the
            # user a generic "I'm not sure" instead. A purchase-like message
            # still falls through to the heuristic below (-> new_task).
            if (
                not match
                and content.strip()
                and '"intent"' not in content
                and not _NEW_INSTRUCTION_RE.search(message)
            ):
                return ChatDecision(intent="answer", reply=content.strip())
            json_parse_failed = True
            if match:
                try:
                    data = json.loads(match.group(0))
                    json_parse_failed = False
                    intent = data.get("intent")
                    if intent in ("new_task", "provide_info", "answer", "unsupported_action"):
                        return ChatDecision(intent=intent, reply=data.get("reply"))
                except json.JSONDecodeError:
                    pass  # json_parse_failed stays True -- fall through to the salvage attempt

            if json_parse_failed:
                # A real failure mode found live: a genuinely long "answer"
                # (e.g. describing all 18 specialists) can get cut off
                # mid-JSON by the completion-token limit -- there may be no
                # closing "}" at all, so the strict match above never even
                # matches. Salvage whatever "reply" text was actually
                # generated (even truncated) directly from the raw content,
                # rather than silently discarding a real, mostly-good answer
                # for a generic "I'm not sure" non-answer. (Only applies
                # when the JSON itself was unparseable -- a validly-parsed
                # but unrecognized intent falls through to the heuristic
                # below instead, same as before.)
                salvage = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)', content, re.DOTALL)
                if salvage:
                    text = salvage.group(1).replace('\\n', '\n').replace('\\"', '"').replace("\\\\", "\\")
                    if text.strip():
                        return ChatDecision(intent="answer", reply=text)
        except Exception:
            pass  # fall through to the heuristic below

    # (The common "Waiting For Information" / provide_info case is already
    # handled deterministically at the top of this function, before the
    # LLM is ever consulted. A message that looks like a genuine question
    # while a task waits can still reach here if the LLM itself failed --
    # falls through to the generic "answer" below, same as any other
    # unclassifiable message, rather than wrongly treating a question as
    # though it supplied the missing value.)
    if _NEW_INSTRUCTION_RE.search(message):
        return ChatDecision(intent="new_task")
    # Genuinely ambiguous (the LLM was unavailable/unparseable AND this
    # doesn't look like a purchase request at all, e.g. a plain "hi") --
    # never guess "new_task" here: that used to send things like "hii"
    # straight into create_purchase_request with no real quantity/budget,
    # surfacing a raw "quantity must be positive" backend error instead of
    # a sensible reply.
    return ChatDecision(
        intent="answer",
        reply="I'm not sure what you need yet -- tell me what you'd like to purchase, "
        "or ask me a question about the system.",
    )
