"""Vendra -- Vendor Intelligence & Management.

Vendra is itself an orchestrator: it understands the task, runs its six
specialist subagents (app.procurement.subagents.vendra -- Vendor
Discovery, Document Verification, Vendor Onboarding Readiness, Vendor
Profile, Vendor Performance Snapshot, Vendor Risk Flagging) through the
shared LangGraph pipeline in app.procurement.subagents.base, and combines
their results into one message for the coordinator. Compliance itself
stays 100% deterministic -- there is nothing in "find suppliers that are
not on hold" or "check which required documents are on file" that
benefits from an LLM; the spec asks for deterministic logic wherever
arithmetic or lookups suffice, and compliance verdicts are exactly that.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Dict, List, Optional

from app.memory.provenance_fields import enrich_memory
from app.tools.memory_tools import build_agent_tools

from .. import progress
from ..expiry import end_of_day_utc
from ..identities import SYSTEM_IDS, VENDRA
from ..mcp.client import ERPNextClient, ERPNextUnavailable
from ..messages import MessageType, SourceReference, SystemMessage, new_id
from ..narration import narrate
from ..subagents.base import run_specialists
from ..subagents.vendra import SPECIALISTS as VENDRA_SPECIALISTS
from ..subagents.vendra import (
    REQUIRED_SUPPLIER_DOCUMENTS,
    check_compliance,
    discover_suppliers,
)

_tools = None


def _tool(name: str):
    global _tools
    if _tools is None:
        _tools = {t.name: t for t in build_agent_tools(VENDRA, valid_ids=SYSTEM_IDS)}
    return _tools[name]


def run_vendor_eligibility_check(
    *,
    task_id: str,
    correlation_id: str,
    client: ERPNextClient,
    item_code: Optional[str] = None,
    llm: Optional[Any] = None,
    use_memory: bool = False,
) -> SystemMessage:
    """Vendra's orchestrator entry point for the demo workflow: discover
    suppliers, check each one's compliance, save an eligibility fact per
    supplier (with full provenance), and return the eligible, source-backed
    list as a message the coordinator can hand to Procura.

    ``llm`` is an optional subagent (NVIDIA Nemotron by default, via
    app.agents.workflow.get_llm) used only to narrate the eligibility
    result in plain language (``payload["ai_summary"]``) -- it never
    decides compliance; that stays 100% deterministic above, unaffected by
    whether narration succeeds, fails, or is unavailable."""
    # Validity dates a person recorded (live mode only: demo mode never reads the real database).
    recorded_expiries: Dict[str, Dict[str, date]] = {}
    if use_memory:
        try:
            from ..document_validity import load_recorded_validity

            recorded_expiries = load_recorded_validity()
        except Exception:
            progress.emit("vendra", "Recorded document validity could not be read; using file names only")
    specialist_results = run_specialists(
        VENDRA_SPECIALISTS, {"client": client, "item_code": item_code, "recorded_expiries": recorded_expiries}
    )

    # Vendor Discovery and Document Verification are load-bearing: every
    # other specialist (and this orchestrator's own memory writes) depends
    # on their output. A real backend outage there must still surface to
    # the coordinator as a failure (so its one-retry policy applies, as
    # before) -- it must NOT be silently absorbed into an "ok" message just
    # because the shared framework protects the *rest* of the pipeline from
    # one specialist's exception. The other four specialists are instead
    # best-effort enrichment: one of them failing is reported in
    # payload["specialists"], never raised.
    for critical in ("vendor_discovery", "document_verification"):
        if specialist_results[critical].status == "error":
            raise ERPNextUnavailable(specialist_results[critical].error)

    discovery = specialist_results["vendor_discovery"].data
    verification = specialist_results["document_verification"].data
    candidates: List[Dict[str, Any]] = discovery.get("candidates", [])
    compliance_by_supplier: Dict[str, Any] = verification.get("compliance_by_supplier", {})
    risk_by_supplier = specialist_results["vendor_risk_flagging"].data.get("risk_by_supplier", {})

    eligible: List[Dict[str, Any]] = []
    sources: List[SourceReference] = []

    for supplier in candidates:
        compliance = compliance_by_supplier[supplier["name"]]
        verification_status = "verified" if compliance["compliant"] else "disputed"

        memory_key = f"supplier_eligibility:{supplier['name']}"
        label = f"{supplier.get('supplier_name', supplier['name'])} ({supplier['name']})"
        if compliance["compliant"]:
            until = compliance.get("earliest_expiry")
            verdict = "compliant" + (f", documents valid until {until}" if until else "")
        else:
            problems = []
            if compliance["missing_documents"]:
                problems.append("missing " + ", ".join(compliance["missing_documents"]))
            if compliance.get("expired_documents"):
                problems.append(
                    "expired "
                    + ", ".join(f"{e['document']} (on {e['expired_on']})" for e in compliance["expired_documents"])
                )
            verdict = "; ".join(problems)
        content = f"{label}: {verdict}"
        save_args: Dict[str, Any] = {
            "memory_key": memory_key,
            "content": content,
            "source_reference": f"Supplier/{supplier['name']}",
            "subject_key": f"eligibility:{supplier['name']}",
        }
        if compliance["compliant"] and compliance.get("earliest_expiry"):
            # The verdict is only as good as the documents behind it: it goes stale when the first one lapses.
            save_args["valid_until"] = end_of_day_utc(date.fromisoformat(compliance["earliest_expiry"])).isoformat()
        saved = _tool("save_memory_entry").invoke(save_args)
        evidence = {"supplier": supplier["name"], "documents": compliance.get("document_refs", []),
                    "documents_on_file": compliance.get("documents_on_file", []),
                    "earliest_expiry": compliance.get("earliest_expiry"),
                    "expired": compliance.get("expired_documents", [])}
        enrich_memory(
            saved["id"],
            source_type="erpnext_doctype",
            source_hash=hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
            task_id=task_id,
            message_id=None,
            verification_status=verification_status,
            sharing_classification="shareable",
            structured_fact={
                "supplier": supplier,
                "compliance": compliance,
                "risk": risk_by_supplier.get(supplier["name"]),
            },
        )

        source = SourceReference(
            source_type="erpnext_doctype",
            reference=supplier.get("source_reference", f"Supplier/{supplier['name']}"),
            verification_status=verification_status,
        )
        sources.append(source)

        if compliance["compliant"]:
            eligible.append({**supplier, "compliance": compliance})

    ai_summary = narrate(
        "You are Vendra, a vendor-compliance assistant. In 2-3 plain-language sentences, "
        "summarize the eligibility result below for a human reviewer. State only what the "
        "data says -- never claim a supplier is compliant or eligible unless the data says so.",
        f"Candidates checked: {len(candidates)}. "
        f"Eligible: {[s['name'] for s in eligible]}. "
        f"Excluded (non-compliant): "
        f"{[c['name'] for c in candidates if c['name'] not in {e['name'] for e in eligible}]}.",
        llm=llm,
    )

    return SystemMessage(
        message_id=new_id("msg"),
        task_id=task_id,
        correlation_id=correlation_id,
        sender=VENDRA,
        receiver="coordinator",
        message_type=MessageType.RESPONSE,
        payload={
            "eligible_suppliers": eligible,
            "candidates_checked": discovery.get("candidates_checked", len(candidates)),
            "excluded_on_hold_or_disabled": len(client.list_suppliers()) - len(candidates),
            "ai_summary": ai_summary,
            # Full per-specialist detail (status/data/error for all six),
            # keyed by name -- see app.procurement.subagents.vendra. Additive:
            # every field above this one is unchanged from before this pass.
            "specialists": {name: r.to_dict() for name, r in specialist_results.items()},
        },
        sources=sources,
        purpose="vendor_eligibility_for_procurement",
        status="ok",
    )
