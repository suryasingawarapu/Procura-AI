"""Procura -- Procurement Operations.

Procura is itself an orchestrator: it understands the task, runs its six
specialist subagents (app.procurement.subagents.procura -- RFQ Management,
Quotation Processing, Quotation Comparison, Supplier Matching, Delivery
Schedule Estimation, and Purchase Order Preparation) and combines their
results into one message for the coordinator. Procura does not
re-discover suppliers itself: it consumes Vendra's eligible, source-backed
supplier list (per spec, "Share authorized, source-backed supplier facts
with Procura... when needed"), rather than duplicating discovery/
compliance logic.

Purchase Request interpretation (turning "20 laptops" into item_code=20)
is the one place a real LLM is used (app.agents.workflow.get_llm), because
parsing free-text quantities/items is genuine language understanding, not
arithmetic.
"""

from __future__ import annotations

import json
import re
import hashlib
from typing import Any, Dict, List, Optional

from app.memory.provenance_fields import enrich_memory
from app.observability import langfuse_tracing as tracing
from app.tools.memory_tools import build_agent_tools

from ..calculations import ComparisonResult, compare_quotations
from ..identities import PROCURA, SYSTEM_IDS
from ..mcp.client import ERPNextClient, ERPNextUnavailable
from ..messages import MessageType, SourceReference, SystemMessage, new_id
from .. import progress
from ..narration import narrate
from ..subagents.base import run_specialists
from ..subagents.procura import SPECIALISTS as PROCURA_SPECIALISTS
from ..subagents.procura import to_quotation as _to_quotation

_tools = None


def _tool(name: str):
    global _tools
    if _tools is None:
        _tools = {t.name: t for t in build_agent_tools(PROCURA, valid_ids=SYSTEM_IDS)}
    return _tools[name]


_QTY_ITEM_PATTERN = re.compile(r"(\d+)\s+([a-zA-Z][a-zA-Z \-]*?)s?\b")


def interpret_purchase_request(task_text: str, *, llm: Optional[Any] = None) -> Dict[str, Any]:
    """Purchase Request: turn free text into a structured {quantity, item_description}.

    Uses a real LLM (get_llm("procura")) when one is injected or the
    default factory can build one; falls back to a plain regex extraction
    when no LLM is available (e.g. in tests, or if the configured
    provider is unreachable) rather than failing the whole task outright.
    The regex fallback is honestly a heuristic, not a parser -- it is only
    ever used when the LLM path is unavailable.
    """
    if llm is None:
        try:
            from app.agents.workflow import get_llm

            llm = get_llm("procura")
        except Exception:
            llm = None

    if llm is not None:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage as LCSystemMessage

            reply = llm.invoke(
                [
                    LCSystemMessage(
                        content="Extract the purchase request as JSON only: "
                        '{"quantity": <int>, "item_description": "..."}.'
                    ),
                    HumanMessage(content=task_text),
                ],
                **tracing.llm_kwargs("Procura Extract Request"),
            )
            match = re.search(r"\{.*\}", str(getattr(reply, "content", "")), re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                return {"quantity": int(data["quantity"]), "item_description": str(data["item_description"])}
        except Exception:
            pass  # fall through to the regex heuristic below

    match = _QTY_ITEM_PATTERN.search(task_text)
    if match:
        return {"quantity": int(match.group(1)), "item_description": match.group(2).strip()}
    return {"quantity": None, "item_description": task_text}


def run_procurement_task(
    *,
    task_id: str,
    correlation_id: str,
    client: ERPNextClient,
    item_code: str,
    quantity: int,
    eligible_supplier_names: List[str],
    llm: Optional[Any] = None,
    use_memory: bool = False,
) -> SystemMessage:
    """Procura's orchestrator entry point: RFQ -> quotations -> deterministic
    comparison -> a recommendation. Does NOT submit a purchase order --
    that is a separate, explicitly-approved step (submit_purchase_order).

    If Vendra found zero eligible (compliant) suppliers, procurement stops
    here: no RFQ is created (no write is attempted at all) and no
    quotation is compared. Quotations are only ever compared from
    suppliers on the eligible list -- there is no fallback to "compare
    everyone" when that filter comes up empty, so a non-compliant supplier
    can never be recommended just because nobody eligible had a quote.

    ``llm`` is an optional subagent (NVIDIA Nemotron by default) used only
    to narrate the comparison result (``payload["ai_summary"]``) -- the
    recommendation itself is always ``comparison.recommended_supplier``,
    computed deterministically above; narration can never change it.
    """
    context = {
        "client": client,
        "item_code": item_code,
        "quantity": quantity,
        "eligible_supplier_names": eligible_supplier_names,
        "use_memory": use_memory,
        "task_id": task_id,
    }
    specialist_results = run_specialists(
        PROCURA_SPECIALISTS,
        context,
        needed=[
            "rfq_management", "quotation_processing", "quotation_comparison", "supplier_matching",
            "delivery_schedule_estimation", "quotation_history",
        ],
    )
    history = specialist_results["quotation_history"]
    if history.status == "ok":
        h = history.data
        changed = h["changes"][0] if h["changes"] else None
        progress.emit(
            "procura",
            "Quotation history: "
            + (f"stored summary used ({h['summary_state']}) + {h['records_read_after_summary']} newer claims" if h["summary_used"]
               else f"no usable summary yet, {h['claims_in_scope']} claims in scope")
            + f"; {h['records_opened']} supporting records opened"
            + (f"; latest change: {changed['supplier']} {changed['field']} {changed['from']} \u2192 {changed['to']}" if changed else ""),
        )

    # RFQ Management and Quotation Processing are load-bearing: a real
    # backend failure there must still surface to the coordinator (so its
    # one-retry policy applies), exactly as before this pass -- including
    # the write-gate's own ERPNextUnavailable when ERPNEXT_ALLOW_WRITES
    # isn't set, which is a genuine failure, not the clean "blocked"
    # (zero-eligible-suppliers) outcome below.
    for critical in ("rfq_management", "quotation_processing"):
        if specialist_results[critical].status == "error":
            raise ERPNextUnavailable(specialist_results[critical].error)

    rfq = specialist_results["rfq_management"].data.get("rfq")
    quote_rows: List[Dict[str, Any]] = specialist_results["quotation_processing"].data.get("quote_rows", [])

    quotations = [_to_quotation({**row, "qty": quantity}) for row in quote_rows]
    comparison: ComparisonResult = compare_quotations(quotations)

    reconciliation = specialist_results["quotation_processing"].data.get("reconciliation") or {}
    blocking = specialist_results["quotation_comparison"].data.get("blocking_conflicts") or []
    if blocking:
        # A disputed price that could change the winner: no recommendation until a person decides.
        names = ", ".join(c.get("supplier") or "?" for c in blocking)
        comparison = comparison.model_copy(
            update={
                "recommended_supplier": None,
                "rationale": f"Recommendation withheld: the quoted price for {names} is disputed between sources and "
                f"could change the outcome. {comparison.rationale}",
            }
        )

    def _source_type(ref: str) -> str:
        return "erpnext_doctype" if ref.startswith("Supplier Quotation/") else "external_document"

    sources = [
        SourceReference(
            source_type=_source_type(row.get("source_reference", row["name"])),
            reference=row.get("source_reference", row["name"]),
        )
        for row in quote_rows
    ]
    source_refs = [row.get("source_reference", row["name"]) for row in quote_rows]

    memory_key = f"quotation_comparison:{item_code}"
    content = comparison.rationale
    save_args: Dict[str, Any] = {"memory_key": memory_key, "content": content}
    if source_refs:
        save_args["source_reference"] = ", ".join(source_refs[:5]) + (f" (+{len(source_refs) - 5} more)" if len(source_refs) > 5 else "")
    saved = _tool("save_memory_entry").invoke(save_args)
    enrich_memory(
        saved["id"],
        source_type="computed",
        source_hash=hashlib.sha256(json.dumps(sorted(source_refs) + [str(r.get("rate")) for r in quote_rows]).encode()).hexdigest()
        if source_refs else None,
        task_id=task_id,
        verification_status="verified" if quote_rows else "unverified",
        sharing_classification="shareable",
        structured_fact={
            "comparison": comparison.model_dump(),
            "supplier_matching": specialist_results["supplier_matching"].data,
            "delivery_schedule_estimation": specialist_results["delivery_schedule_estimation"].data,
            "source_documents": source_refs,
        },
    )

    if not eligible_supplier_names:
        error = "no eligible (compliant) suppliers; blocked before any RFQ was created or quotation was compared"
    elif blocking:
        error = "price conflict needs clarification: " + "; ".join(
            f"{c.get('supplier')} ({c.get('reason')})" for c in blocking
        )
    elif not quote_rows:
        error = f"no quotations found for {item_code}"
    else:
        error = None

    ai_summary = narrate(
        "You are Procura, a procurement-operations assistant. In 2-3 plain-language sentences, "
        "summarize the quotation comparison result below for a human reviewer. State only what "
        "the data says -- never recommend a supplier the data doesn't already recommend.",
        f"Item: {item_code}. Eligible suppliers: {eligible_supplier_names}. "
        f"Quotations considered: {len(quote_rows)}. "
        f"Recommended supplier: {comparison.recommended_supplier!r}. "
        f"Rationale: {comparison.rationale} "
        + (f"Blocked: {error}" if error else ""),
        llm=llm,
    )

    return SystemMessage(
        message_id=new_id("msg"),
        task_id=task_id,
        correlation_id=correlation_id,
        sender=PROCURA,
        receiver="coordinator",
        message_type=MessageType.RESPONSE,
        payload={
            "rfq": rfq,
            "quotations_considered": len(quote_rows),
            "conflicts": blocking,
            "reconciliation": reconciliation,
            "comparison": comparison.model_dump(),
            "recommended_supplier": comparison.recommended_supplier,
            "recommended_total_cost": next(
                (s.total_cost for s in comparison.scores if s.supplier == comparison.recommended_supplier), None
            ),
            "ai_summary": ai_summary,
            # Full per-specialist detail (status/data/error for five of
            # Procura's six specialists; Purchase Order Preparation is the
            # sixth and is never auto-run here -- see submit_purchase_order
            # below). Additive: every field above this one is unchanged.
            "specialists": {name: r.to_dict() for name, r in specialist_results.items()},
        },
        sources=sources,
        purpose="quotation_comparison_and_recommendation",
        status="ok" if quote_rows and not blocking else "error",
        error=error,
    )


def submit_purchase_order(
    client: ERPNextClient, *, supplier: str, item_code: str, quantity: int, approved: bool, rate: Optional[float] = None
) -> Dict[str, Any]:
    """Explicitly separate from run_procurement_task: never called unless a
    human has approved. Raises PermissionError (surfaced to the caller,
    never swallowed) if approved is not True.

    ``rate``: the real, already-recommended unit price (e.g.
    recommended_total_cost / quantity) -- omitted only for a caller that
    genuinely doesn't have one; a real PO recorded with no price at all
    would misrepresent what was actually approved."""
    item: Dict[str, Any] = {"item_code": item_code, "qty": quantity}
    if rate is not None:
        item["rate"] = rate
    return client.create_purchase_order(
        supplier=supplier, items=[item], approved=approved
    )
