"""API surface for the procurement coordinator (Vendra/Procura/Finora).

Kept as its own router/module rather than folded into main.py, matching
the existing project's preference for small, focused files.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
from typing import Any, Dict, Iterator, Optional
from unittest.mock import patch

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse

from app.memory import conflicts as memory_conflicts
from app.observability import langfuse_tracing as tracing
from app.procurement import (
    chat_memory, chat_router, document_validity, progress, quotation_claims, task_orchestrator, vendor_master, workflow,
)
from app.procurement.coordinator import run_coordinator
from app.procurement.demo_fixtures import DEMO_ITEM_CODE, DEMO_QUANTITY, build_demo_seed
from app.procurement.mcp.client import ERPNextUnavailable, SeededERPNextClient, get_configured_client
from app.procurement.messages import TaskRequest
from app.procurement.systems.procura import submit_purchase_order

from .auth import authorize_admin, require_auth
from .procurement_schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalLimitsResponse,
    ChatMessageRequest,
    ChatMessageResponse,
    ConflictResolveRequest,
    DocumentValidityRequest,
    QuotationClaimRequest,
    CreatePurchaseRequestRequest,
    CreateVendorRequest,
    DemoResetVendorsResponse,
    DemoSeedVendorsRequest,
    DemoSeedVendorsResponse,
    DemoTaskRequest,
    DemoTaskResponse,
    ImportReportResponse,
    ImportVendorsRequest,
    IntegrationHealthResponse,
    ProcurementTaskRequest,
    ProcurementTaskResponse,
    PurchaseOrderRequest,
    PurchaseOrderResponse,
    PurchaseRequestResponse,
    SetVendorStatusRequest,
    StartProcurementTaskRequest,
    TransitionPurchaseRequestRequest,
    UpdatePurchaseRequestRequest,
    UpdateVendorRequest,
    VendorErpnextSyncResponse,
    VendorListResponse,
    VendorResponse,
)

router = APIRouter(prefix="/procurement", tags=["procurement"])


@router.post("/tasks", response_model=ProcurementTaskResponse)
def create_task(
    request: ProcurementTaskRequest,
    auth_token: str = Depends(require_auth),
):
    """Run one procurement task through the coordinator.

    Admin-token only: a task can touch multiple systems, so this is not
    one system's own resource (mirrors /workflow/run's admin-only gate).
    Never submits a purchase order itself -- see POST /purchase-orders.
    """
    authorize_admin(auth_token)
    client, _backend = get_configured_client()
    task = TaskRequest(description=request.description)
    try:
        result = run_coordinator(task, client, item_code=request.item_code, quantity=request.quantity)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return ProcurementTaskResponse(
        task_id=result.task_id,
        correlation_id=result.correlation_id,
        systems_invoked=result.systems_invoked,
        messages=[m.model_dump(mode="json") for m in result.messages],
        requires_approval=result.requires_approval,
        approval_summary=result.approval_summary,
        result=result.result,
        failed=result.failed,
        blocked=result.blocked,
        error=result.error,
    )


@router.post("/demo/run", response_model=DemoTaskResponse)
def run_demo(
    request: DemoTaskRequest,
    auth_token: str = Depends(require_auth),
):
    """Safe Demo Mode: runs the real coordinator, the real LangGraph
    pipeline, and the real specialists -- exactly like POST /tasks --
    but against an isolated, in-memory, clearly-labeled synthetic
    SeededERPNextClient (never live ERPNext), with memory writes stubbed
    out so no demo data ever reaches the real Supabase project. Nothing
    here is hardcoded: the recommendation, budget check, and blocked/
    eligible outcome are computed for real from the synthetic data below.
    """
    authorize_admin(auth_token)
    demo_client = SeededERPNextClient(build_demo_seed(request.scenario))
    task = TaskRequest(
        description=(
            f"[SIMULATED DEMO -- {request.scenario}] Find eligible suppliers for {DEMO_QUANTITY} laptops, "
            "collect/compare quotations, check the available budget, and prepare the purchase for approval."
        )
    )
    # Demo-only isolation: never write this synthetic run's facts into the
    # real Supabase project. Scoped tightly around this one call -- see
    # app.procurement.demo_fixtures' module docstring for why this reuses
    # the exact mocking pattern already proven throughout this project's
    # own test suite, rather than a new untested mechanism.
    with patch("app.procurement.systems.vendra.enrich_memory"), patch(
        "app.procurement.systems.procura.enrich_memory"
    ), patch("app.procurement.systems.finora.enrich_memory"), patch(
        "app.tools.memory_tools.save_memory", return_value={"id": "demo-mem-stub"}
    ):
        try:
            result = run_coordinator(task, demo_client, item_code=DEMO_ITEM_CODE, quantity=DEMO_QUANTITY)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return DemoTaskResponse(
        scenario=request.scenario,
        task=ProcurementTaskResponse(
            task_id=result.task_id,
            correlation_id=result.correlation_id,
            systems_invoked=result.systems_invoked,
            messages=[m.model_dump(mode="json") for m in result.messages],
            requires_approval=result.requires_approval,
            approval_summary=result.approval_summary,
            result=result.result,
            failed=result.failed,
            blocked=result.blocked,
            error=result.error,
        ),
    )


def _pr_to_response(pr) -> PurchaseRequestResponse:
    return PurchaseRequestResponse(**pr.__dict__)


@router.post("/tasks/start", response_model=PurchaseRequestResponse, status_code=201)
def start_procurement_task_route(
    request: StartProcurementTaskRequest,
    auth_token: str = Depends(require_auth),
    x_user_id: Optional[str] = Header(default=None),
):
    """The one-instruction entry point: parse free text, create a linked,
    durable Purchase Request, and actually run the real Vendra/Procura/
    Finora pipeline against it as far as the available information (and,
    in live mode, real data) allow. Check the response's ``workflow_state``
    -- it is not "complete" just because this call returned.

    ``mode="demo"`` is fully isolated (synthetic data, no real ERPNext or
    Supabase writes beyond this task's own record); ``mode="live"`` uses
    the real configured ERPNext backend and real memory, exactly like
    POST /tasks.
    """
    authorize_admin(auth_token)
    with tracing.trace("Run Task", user_id=x_user_id, input=request.instruction, metadata={"mode": request.mode}) as t:
        try:
            pr = task_orchestrator.start_procurement_task(**request.model_dump())
        except workflow.PurchaseRequestError as e:
            raise HTTPException(status_code=400, detail=str(e))
        t.update(output=f"{pr.pr_id}: {pr.workflow_state}")
        return _pr_to_response(pr)


@router.post("/tasks/{pr_id}/resume", response_model=PurchaseRequestResponse)
def resume_procurement_task_route(
    pr_id: str, auth_token: str = Depends(require_auth), x_user_id: Optional[str] = Header(default=None)
):
    """Re-attempt a task stuck in "Running" (e.g. after a process
    restart) or "Waiting For Information" (once the missing fields have
    been filled in via PATCH /requests/{pr_id}). A no-op for any other
    state -- never re-runs an already-finished task."""
    authorize_admin(auth_token)
    with tracing.trace("Resume Task", user_id=x_user_id, input=pr_id) as t:
        try:
            pr = task_orchestrator.resume_procurement_task(pr_id)
        except workflow.PurchaseRequestError as e:
            raise HTTPException(status_code=404, detail=str(e))
        t.update(output=f"{pr.pr_id}: {pr.workflow_state}")
        return _pr_to_response(pr)


@router.post("/chat", response_model=ChatMessageResponse)
def chat_message_route(
    request: ChatMessageRequest,
    auth_token: str = Depends(require_auth),
    x_user_id: Optional[str] = Header(default=None),
):
    """The real front door for the chat UI: an LLM (see
    app.procurement.chat_router) reads this one message plus the current
    task's real state and decides whether it's a new purchase request, the
    missing detail a "Waiting For Information" task needs, a plain
    question to just answer, or a request for something not built yet
    (e.g. actually sending an email) -- which is reported honestly, never
    faked. It never decides anything about suppliers, compliance, or
    money itself; those are still only ever the real, deterministic
    Vendra/Procura/Finora pipeline's outputs.
    """
    authorize_admin(auth_token)
    with tracing.trace(
        "Chat", user_id=x_user_id, session_id=request.conversation_id, input=request.message, metadata={"mode": request.mode}
    ) as t:
        current_pr = workflow.get_purchase_request(request.pr_id) if request.pr_id else None
        memory_context = _memory_context_for(request)
        extra = {"memory_context": memory_context} if memory_context else {}
        decision = chat_router.route_chat_message(message=request.message, pr=current_pr, history=request.history, **extra)
        response = _act_on_decision(request, decision, current_pr)
        _remember_turn(request, response)
        t.update(output=_reply_text(response), metadata={"intent": decision.intent})
        return response


def _uses_chat_memory(request: ChatMessageRequest) -> bool:
    """Conversation memory is live-mode only: demo mode must never reach the
    real database."""
    return request.mode == "live" and chat_memory.valid_conversation_id(request.conversation_id)


def _memory_context_for(request: ChatMessageRequest) -> Optional[str]:
    if not _uses_chat_memory(request):
        return None
    try:
        return chat_memory.format_for_prompt(chat_memory.conversation_context(request.conversation_id))
    except Exception:
        return None  # memory is an aid to the chat, never a reason for it to fail


def _reply_text(response: ChatMessageResponse) -> str:
    if response.pr is None:
        return response.reply or ""
    pr = response.pr
    parts = [f"[task {pr.pr_id} is {pr.workflow_state}]"]
    if pr.blockers:
        parts.append("waiting on: " + "; ".join(pr.blockers))
    result = ((pr.coordinator_result or {}).get("result")) or {}
    if result.get("recommended_supplier"):
        parts.append(f"recommended {result['recommended_supplier']} at {result.get('recommended_total_cost')}")
    return " ".join(parts)


def _spawn(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def _summarize_in_background(conversation_id: str) -> None:
    try:
        from app.agents.workflow import get_llm

        try:
            llm = get_llm("chat_router")
        except Exception:
            llm = None
        chat_memory.maybe_summarize(conversation_id, llm=llm)
    except Exception:
        pass  # a failed summary is retried on the next turn


def _remember_turn(request: ChatMessageRequest, response: ChatMessageResponse) -> None:
    if not _uses_chat_memory(request):
        return
    try:
        chat_memory.record_turn(
            request.conversation_id,
            request.message,
            _reply_text(response),
            task_id=response.pr.pr_id if response.pr else request.pr_id,
        )
    except Exception:
        return
    _spawn(_summarize_in_background, request.conversation_id)


@router.delete("/chat/conversations/{conversation_id}")
def delete_conversation_route(conversation_id: str, auth_token: str = Depends(require_auth)):
    """Delete everything stored for one conversation (its turns and summaries)."""
    authorize_admin(auth_token)
    if not chat_memory.valid_conversation_id(conversation_id):
        raise HTTPException(status_code=400, detail="invalid conversation id")
    try:
        return chat_memory.delete_conversation(conversation_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


_INTENT_STATUS = {
    "new_task": "Router: new purchase request",
    "provide_info": "Router: this supplies the missing detail (decided in code, no LLM)",
    "vendor_info": "Router: vendor details lookup (decided in code, no LLM)",
    "send_po_email": "Router: purchase-order email (decided in code, no LLM)",
}


@router.post("/chat/stream")
def chat_stream_route(
    request: ChatMessageRequest,
    auth_token: str = Depends(require_auth),
    x_user_id: Optional[str] = Header(default=None),
):
    """Same behaviour as POST /chat, delivered as Server-Sent Events while it
    runs: ``status`` events at real pipeline milestones, ``token`` events
    carrying an LLM answer as it is generated, then one ``final`` event with
    exactly the body /chat would have returned (or ``error``)."""
    authorize_admin(auth_token)
    events: "queue.Queue[tuple]" = queue.Queue()

    def push(kind: Optional[str], data: Optional[Dict[str, Any]] = None) -> None:
        events.put((kind, data))

    def work() -> None:
        # The trace is opened here, in the worker thread: tracing context does not cross into a new thread.
        try:
            with tracing.trace(
                "Chat", user_id=x_user_id, session_id=request.conversation_id, input=request.message,
                metadata={"mode": request.mode, "streamed": "yes"},
            ) as t:
                try:
                    with progress.capture(lambda stage, text: push("status", {"stage": stage, "text": text})):
                        current_pr = workflow.get_purchase_request(request.pr_id) if request.pr_id else None
                        push("status", {"stage": "router", "text": "Router: reading your message"})
                        memory_context = _memory_context_for(request)
                        if memory_context:
                            push("status", {"stage": "memory", "text": "Conversation memory recalled (summary + recent turns)"})
                        extra = {"memory_context": memory_context} if memory_context else {}
                        decision = chat_router.route_chat_message(
                            message=request.message,
                            pr=current_pr,
                            history=request.history,
                            on_token=lambda text: push("token", {"text": text}),
                            **extra,
                        )
                        if decision.intent in _INTENT_STATUS and not (decision.intent == "provide_info" and current_pr is None):
                            push("status", {"stage": "router", "text": _INTENT_STATUS[decision.intent]})
                        response = _act_on_decision(request, decision, current_pr)
                        _remember_turn(request, response)
                    t.update(output=_reply_text(response), metadata={"intent": decision.intent})
                    push("final", response.model_dump(mode="json"))
                except HTTPException as exc:
                    t.warning(f"HTTP {exc.status_code}: {exc.detail}")
                    push("error", {"detail": exc.detail})
                except Exception as exc:  # reported to the client, never a silently dead stream
                    t.error(f"{type(exc).__name__}: {exc}")
                    push("error", {"detail": f"{type(exc).__name__}: {exc}"})
        finally:
            push(None)

    threading.Thread(target=work, daemon=True).start()

    def stream() -> Iterator[str]:
        while True:
            kind, data = events.get()
            if kind is None:
                return
            yield f"event: {kind}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _vendor_info_reply(pr: workflow.PurchaseRequest, supplier: Optional[str]) -> str:
    """The real details of one supplier of this request, from ERPNext / the vendor master and the quotation
    actually compared. Anything not on file is said to be missing, never filled in."""
    from app.procurement.mail import service

    name = supplier or service.recommended(pr)["supplier"]
    try:
        client, _ = task_orchestrator._resolve_client(pr)
    except Exception:
        client = None
    d = service.supplier_details(name, client)
    na = "not on file"
    recommended = name == service.recommended(pr)["supplier"]
    lines = [f"**{name}**" + (" (the recommended supplier)" if recommended else "")]
    lines += [f"- Email: {d['email'] or na}", f"- Phone: {d['phone'] or na}", f"- Website: {d['website'] or na}"]
    if d["country"] or d["group"]:
        lines.append(f"- Country / group: {d['country'] or na} / {d['group'] or na}")
    score = next((x for x in chat_router._quotation_scores(pr.coordinator_result or {}) if x.get("supplier") == name), None)
    if score:
        qty = pr.quantity or 0
        total = score.get("total_cost")
        parts = [f"total ₹{total:,.0f}" if isinstance(total, (int, float)) else "total not recorded"]
        if isinstance(total, (int, float)) and qty:
            parts.append(f"₹{total / qty:,.0f} per unit")
        if score.get("delivery_days") is not None:
            parts.append(f"delivery in {score['delivery_days']} days")
        if score.get("payment_terms_days") is not None:
            parts.append(f"payment terms net {score['payment_terms_days']} days")
        lines.append(f"- Quotation (rank {score.get('rank')}): " + ", ".join(parts))
    if d["source"]:
        lines.append(f"\n*Contact details from {d['source']}.*")
    if recommended and d["email"]:
        lines.append("\nTo email them the purchase order, say **send the purchase order email to the vendor**.")
    return "\n".join(lines)


def _po_email_response(pr: Optional[workflow.PurchaseRequest]) -> ChatMessageResponse:
    """Prepare (never send) the purchase-order email: the person reviews it and presses Send in the chat."""
    from app.procurement.mail import outbox, service

    if pr is None or not service.recommended(pr)["supplier"]:
        return ChatMessageResponse(reply="There is no recommended supplier yet, so there is no purchase order to email. Run a purchase request first.")
    try:
        client, _ = task_orchestrator._resolve_client(pr)
        mail = service.draft_for_request(pr, client=client)
    except outbox.MailError as exc:
        return ChatMessageResponse(reply=f"I can't prepare that email: {exc}. Add the supplier's email in ERPNext (Supplier contact) or the vendor master, then ask again.")
    cfg = outbox.settings()
    if mail["status"] != "draft":
        reply = f"That purchase order email to {mail['vendor']} was already sent ({mail.get('sent_at') or 'earlier'})."
    else:
        reply = f"Here is the purchase order email for **{mail['vendor']}**, with the PO attached as a PDF. It is sent only when you press **Send**."
    if cfg["test_recipient"]:
        reply += f"\n\nTest mode is on: it will go to {cfg['test_recipient']}, not to the vendor."
    if not cfg["sending_allowed"]:
        reply += "\n\nSending is switched off on the server (MAIL_ALLOW_SEND), so the Send button will be refused."
    return ChatMessageResponse(reply=reply, mail=service.preview(mail))


def _act_on_decision(
    request: ChatMessageRequest, decision: "chat_router.ChatDecision", current_pr: Optional[workflow.PurchaseRequest]
) -> ChatMessageResponse:
    """Carry out what the router decided. Shared by /chat and /chat/stream."""
    if decision.intent == "new_task":
        try:
            # Always the user's own original wording, never an LLM
            # paraphrase of it -- the deterministic requirement parser
            # (budget/delivery/deadline regexes) depends on specific
            # phrasing, and a rewrite could silently corrupt it (e.g. a
            # paraphrase that drops the exact "within <amount>" pattern).
            pr = task_orchestrator.start_procurement_task(
                instruction=request.message,
                requested_by=request.requested_by,
                mode=request.mode,
            )
        except workflow.PurchaseRequestError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return ChatMessageResponse(pr=_pr_to_response(pr))

    if decision.intent == "provide_info" and current_pr is not None:
        match = re.search(r"\b([A-Z][A-Z0-9-]{3,})\b", request.message)
        if match:
            workflow.update_purchase_request(current_pr.pr_id, item_code=match.group(1))
        try:
            pr = task_orchestrator.resume_procurement_task(current_pr.pr_id)
        except workflow.PurchaseRequestError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return ChatMessageResponse(pr=_pr_to_response(pr))

    if decision.intent == "vendor_info" and current_pr is not None:
        return ChatMessageResponse(reply=_vendor_info_reply(current_pr, decision.supplier))

    if decision.intent == "send_po_email":
        return _po_email_response(current_pr)

    # "answer" / "unsupported_action", or "provide_info" with no active
    # task to resume (nothing sensible to do but answer honestly).
    return ChatMessageResponse(reply=decision.reply or "I'm not sure how to help with that yet.")


@router.post("/requests", response_model=PurchaseRequestResponse, status_code=201)
def create_purchase_request_route(
    request: CreatePurchaseRequestRequest,
    auth_token: str = Depends(require_auth),
):
    """Create a new Purchase Request (status: Draft). Any authenticated
    caller may create one -- per spec, "authorized employees" -- approval
    is a separate, gated step below."""
    try:
        pr = workflow.create_purchase_request(**request.model_dump())
    except workflow.PurchaseRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _pr_to_response(pr)


@router.get("/requests/{pr_id}", response_model=PurchaseRequestResponse)
def get_purchase_request_route(pr_id: str, auth_token: str = Depends(require_auth)):
    authorize_admin(auth_token)
    pr = workflow.get_purchase_request(pr_id)
    if pr is None:
        raise HTTPException(status_code=404, detail=f"no such purchase request: {pr_id}")
    return _pr_to_response(pr)


@router.patch("/requests/{pr_id}", response_model=PurchaseRequestResponse)
def update_purchase_request_route(
    pr_id: str, request: UpdatePurchaseRequestRequest, auth_token: str = Depends(require_auth)
):
    """Fill in missing fields on a task (typically one sitting in
    "Waiting For Information") -- follow with POST /tasks/{pr_id}/resume
    to actually re-attempt the pipeline."""
    authorize_admin(auth_token)
    try:
        pr = workflow.update_purchase_request(pr_id, **request.model_dump(exclude_unset=True))
    except workflow.PurchaseRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _pr_to_response(pr)


@router.post("/requests/{pr_id}/transition", response_model=PurchaseRequestResponse)
def transition_purchase_request_route(
    pr_id: str,
    request: TransitionPurchaseRequestRequest,
    auth_token: str = Depends(require_auth),
):
    """Move a Purchase Request to a new status (e.g. Draft -> Pending
    Approval, or any status -> Cancelled). Invalid transitions are
    rejected, never silently allowed."""
    authorize_admin(auth_token)
    try:
        pr = workflow.transition_purchase_request(pr_id, request.new_status)
    except workflow.PurchaseRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _pr_to_response(pr)


@router.post("/requests/{pr_id}/approve", response_model=ApprovalDecisionResponse)
def approve_purchase_request_route(
    pr_id: str,
    request: ApprovalDecisionRequest,
    auth_token: str = Depends(require_auth),
    x_user_id: Optional[str] = Header(default=None),
):
    """Record a human approval decision. Enforces the approver's
    role-based spending limit (see app.procurement.workflow.APPROVAL_LIMITS)
    -- a request above the approver's limit is rejected, never silently
    approved or auto-escalated. NOTE: ``approver_role`` is a self-declared
    request field, not yet derived from a real authenticated human-identity
    system -- this project has no SSO/role directory to draw one from; see
    the final report for why this is a known limitation, not a hidden one.
    """
    authorize_admin(auth_token)
    with tracing.trace("Approval", user_id=x_user_id or request.approver_id, input=f"{pr_id}: {request.decision}") as t:
        try:
            return _record_approval(pr_id, request, t)
        except workflow.ApprovalLimitExceeded as e:
            raise HTTPException(status_code=403, detail=str(e))
        except workflow.StaleApprovalError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except workflow.PurchaseRequestError as e:
            raise HTTPException(status_code=400, detail=str(e))


def _record_approval(pr_id: str, request: ApprovalDecisionRequest, t) -> "ApprovalDecisionResponse":
    try:
        decision = workflow.record_approval_decision(pr_id=pr_id, **request.model_dump())
    except workflow.ApprovalLimitExceeded as e:
        raise HTTPException(status_code=403, detail=str(e))
    except workflow.StaleApprovalError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except workflow.PurchaseRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if decision.decision == "Approved":
        # The one deliberate, explicitly human-approved step: actually
        # submit the recommended purchase order now that a human really
        # has approved it. A failure *inside* submission is recorded
        # honestly on the PR itself (GET /requests/{pr_id} shows it)
        # rather than failing this response -- the approval decision
        # itself already succeeded. Only a missing-PR lookup error (e.g.
        # a mocked/test environment with no real backing record) is
        # swallowed here, since there is nowhere else to report it.
        try:
            task_orchestrator.submit_approved_purchase_order(pr_id)
        except workflow.PurchaseRequestError:
            pass
    t.update(output=f"{decision.decision} by {decision.approver_id} ({decision.approver_role})")
    return ApprovalDecisionResponse(**decision.__dict__)


@router.get("/approval-limits", response_model=ApprovalLimitsResponse)
def get_approval_limits_route(auth_token: str = Depends(require_auth)):
    """The real, currently-configured role -> spending-limit policy (see
    app.procurement.workflow.APPROVAL_LIMITS's own docstring: a clearly-
    labeled placeholder, not a real company policy). Lets a UI show only
    an approval option that can actually succeed, instead of a button
    guaranteed to fail with "exceeds that limit"."""
    return ApprovalLimitsResponse(
        limits={role: (None if limit == float("inf") else limit) for role, limit in workflow.APPROVAL_LIMITS.items()}
    )


@router.get("/requests/{pr_id}/approvals")
def get_approval_history_route(pr_id: str, auth_token: str = Depends(require_auth)):
    authorize_admin(auth_token)
    return {"pr_id": pr_id, "history": workflow.get_approval_history(pr_id)}


def _vendor_to_response(v) -> VendorResponse:
    return VendorResponse(**v.__dict__)


@router.post("/vendors", response_model=VendorResponse, status_code=201)
def create_vendor_route(request: CreateVendorRequest, auth_token: str = Depends(require_auth)):
    authorize_admin(auth_token)
    try:
        vendor = vendor_master.create_vendor(**request.model_dump())
    except vendor_master.VendorMasterError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _vendor_to_response(vendor)


@router.get("/vendors/{vendor_code}", response_model=VendorResponse)
def get_vendor_route(vendor_code: str, auth_token: str = Depends(require_auth)):
    authorize_admin(auth_token)
    vendor = vendor_master.get_vendor(vendor_code)
    if vendor is None:
        raise HTTPException(status_code=404, detail=f"no such vendor: {vendor_code}")
    return _vendor_to_response(vendor)


@router.get("/vendors", response_model=VendorListResponse)
def search_vendors_route(
    q: str | None = None,
    business_category: str | None = None,
    country: str | None = None,
    status: str | None = None,
    is_demo: bool | None = None,
    auth_token: str = Depends(require_auth),
):
    authorize_admin(auth_token)
    vendors = vendor_master.search_vendors(
        query=q, business_category=business_category, country=country, status=status, is_demo=is_demo
    )
    return VendorListResponse(vendors=[_vendor_to_response(v) for v in vendors])


@router.patch("/vendors/{vendor_code}", response_model=VendorResponse)
def update_vendor_route(vendor_code: str, request: UpdateVendorRequest, auth_token: str = Depends(require_auth)):
    authorize_admin(auth_token)
    try:
        vendor = vendor_master.update_vendor(vendor_code, **request.model_dump(exclude_unset=True))
    except vendor_master.VendorMasterError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _vendor_to_response(vendor)


@router.post("/vendors/{vendor_code}/status", response_model=VendorResponse)
def set_vendor_status_route(vendor_code: str, request: SetVendorStatusRequest, auth_token: str = Depends(require_auth)):
    authorize_admin(auth_token)
    try:
        vendor = vendor_master.set_vendor_status(vendor_code, request.status)
    except vendor_master.VendorMasterError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _vendor_to_response(vendor)


@router.post("/vendors/import", response_model=ImportReportResponse)
def import_vendors_route(request: ImportVendorsRequest, auth_token: str = Depends(require_auth)):
    """Preview (``commit: false``) or actually create (``commit: true``)
    a batch of vendors -- always call with ``commit: false`` first to
    review duplicates/errors before writing anything."""
    authorize_admin(auth_token)
    report = vendor_master.import_vendors(request.rows, commit=request.commit)
    return ImportReportResponse(**report.__dict__)


@router.post("/vendors/demo-seed", response_model=DemoSeedVendorsResponse)
def demo_seed_vendors_route(request: DemoSeedVendorsRequest, auth_token: str = Depends(require_auth)):
    """Create up to ``count`` clearly-labeled DEMO vendors -- idempotent,
    isolated from live ERPNext, safe non-routable contact details."""
    authorize_admin(auth_token)
    created = vendor_master.seed_demo_vendors(request.count)
    return DemoSeedVendorsResponse(created_count=len(created), created_vendor_codes=[v.vendor_code for v in created])


@router.post("/vendors/demo-reset", response_model=DemoResetVendorsResponse)
def demo_reset_vendors_route(auth_token: str = Depends(require_auth)):
    """Remove every DEMO vendor (is_demo=True) -- never touches a real one."""
    authorize_admin(auth_token)
    return DemoResetVendorsResponse(removed_count=vendor_master.remove_demo_vendors())


@router.get("/vendors/{vendor_code}/erpnext-sync", response_model=VendorErpnextSyncResponse)
def vendor_erpnext_sync_route(vendor_code: str, auth_token: str = Depends(require_auth)):
    """Read-only: is this vendor already a real Supplier record in the
    configured ERPNext backend? Never writes to ERPNext."""
    authorize_admin(auth_token)
    vendor = vendor_master.get_vendor(vendor_code)
    if vendor is None:
        raise HTTPException(status_code=404, detail=f"no such vendor: {vendor_code}")
    client, _backend = get_configured_client()
    status = vendor_master.check_erpnext_sync_status(vendor, client)
    return VendorErpnextSyncResponse(vendor_code=vendor_code, linked=status["linked"], detail=status)


@router.post("/purchase-orders", response_model=PurchaseOrderResponse, status_code=201)
def create_purchase_order(
    request: PurchaseOrderRequest,
    auth_token: str = Depends(require_auth),
):
    """Submit a purchase order. Requires the admin token AND an explicit
    `confirm: true` in the body -- two independent gates, neither of which
    is a request the coordinator or any system can trigger on its own."""
    authorize_admin(auth_token)
    if not request.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true to submit a purchase order")

    client, backend = get_configured_client()
    try:
        po = submit_purchase_order(
            client,
            supplier=request.supplier,
            item_code=request.item_code,
            quantity=request.quantity,
            approved=True,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ERPNextUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return PurchaseOrderResponse(purchase_order=po, backend=backend)


@router.get("/health", response_model=IntegrationHealthResponse)
def integration_health(auth_token: str = Depends(require_auth)):
    """Which backends are actually configured -- never exposes secret values."""
    _client, backend = get_configured_client()
    return IntegrationHealthResponse(
        erpnext_backend=backend,
        erpnext_writes_allowed=os.getenv("ERPNEXT_ALLOW_WRITES", "").strip().lower() in {"1", "true", "yes", "on"},
        supabase_configured=bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY")),
        llm_provider_configured=bool(os.getenv("NVIDIA_API_KEY") or os.getenv("OPENAI_API_KEY")),
        langfuse_configured=bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")),
        ai_summaries_enabled=os.getenv("PROCUREMENT_AI_SUMMARIES", "").strip().lower() in {"1", "true", "yes", "on"},
    )


# -- quotation claims, conflicts and document validity ---------------------------------------------------------------


def _claim_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return quotation_claims._claim_summary(row)


@router.post("/quotation-claims", status_code=201)
def record_quotation_claim_route(request: QuotationClaimRequest, auth_token: str = Depends(require_auth)):
    """Record a quotation from any source with where it came from. The claim is
    then checked against every other claim for the same supplier and item: the
    more authoritative / newer document is used automatically, and if that
    cannot be decided the next run asks for a decision."""
    authorize_admin(auth_token)
    try:
        claim = quotation_claims.record_claim(**request.model_dump())
        report = memory_conflicts.detect_conflicts(
            quotation_claims.PROCURA, quotation_claims.subject_for(request.item_code, request.supplier)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "claim": {"memory_id": claim["id"], "subject_key": quotation_claims.subject_for(request.item_code, request.supplier),
                  "source_type": request.source_type, "source_reference": request.source_reference},
        "conflict": quotation_claims.conflict_view(report, item_code=request.item_code),
    }


@router.get("/quotation-claims")
def list_quotation_claims_route(item_code: str, supplier: Optional[str] = None, auth_token: str = Depends(require_auth)):
    authorize_admin(auth_token)
    return {"claims": [_claim_row(r) | {"supplier": (r.get("structured_fact") or {}).get("supplier")}
                       for r in quotation_claims.list_claims(item_code, supplier)]}


@router.get("/conflicts")
def list_conflicts_route(item_code: str, auth_token: str = Depends(require_auth)):
    """Every disagreement between sources on this item's quotations, decided or not."""
    authorize_admin(auth_token)
    reports = memory_conflicts.scan_conflicts(quotation_claims.PROCURA, f"quote:{item_code}:")
    return {"item_code": item_code, "conflicts": [quotation_claims.conflict_view(r, item_code=item_code) for r in reports]}


@router.post("/conflicts/resolve")
def resolve_conflict_route(request: ConflictResolveRequest, auth_token: str = Depends(require_auth)):
    """A person decides which claim is right. Overrides the automatic rules."""
    authorize_admin(auth_token)
    try:
        report = memory_conflicts.resolve_subject(
            quotation_claims.PROCURA, request.subject_key, request.winner_memory_id,
            resolved_by=request.resolved_by, note=request.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    item_code = request.subject_key.split(":")[1] if request.subject_key.count(":") >= 2 else ""
    return quotation_claims.conflict_view(report, item_code=item_code)


@router.post("/vendors/{supplier}/document-validity", status_code=201)
def record_document_validity_route(supplier: str, request: DocumentValidityRequest, auth_token: str = Depends(require_auth)):
    """Record that a supplier's document was checked and is valid until a date.
    When that date passes, the document counts as expired and Vendra's verdict
    for the supplier goes stale."""
    authorize_admin(auth_token)
    saved = document_validity.record_validity(
        supplier=supplier, document=request.document, valid_until=request.valid_until,
        verified_by=request.verified_by, source_reference=request.source_reference, note=request.note,
    )
    return {"memory_id": saved["id"], "supplier": supplier, "document": request.document,
            "valid_until": request.valid_until.isoformat(), "verified_by": request.verified_by}
