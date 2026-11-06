"""Purchase Request lifecycle and human approval workflow.

Built entirely on the project's existing, already-migrated Supabase memory
system -- no new database migration required. A Purchase Request is a
versioned memory owned by Procura (memory_key=f"purchase_request:{pr_id}");
each approval decision is a versioned memory owned by Finora
(memory_key=f"approval:{pr_id}"). Every status transition and decision is
a NEW version of its memory (via the project's existing atomic save/
supersede mechanism), so the full audit trail -- who changed what, when,
and why -- already exists for free; nothing new had to be built for that.

Approval limits are a per-role configuration this module reads from
``APPROVAL_LIMITS`` below. These numbers are NOT a real company's actual
policy (no such policy was provided) -- they are a clearly-labeled,
overridable default so the enforcement mechanism is real and testable
today, while being honest that the real limits need real configuration.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.memory.provenance_fields import enrich_memory
from app.memory.store import get_memory_by_key
from app.tools.memory_tools import build_agent_tools

from .identities import FINORA, PROCURA, SYSTEM_IDS

__all__ = [
    "PurchaseRequest",
    "PR_STATUSES",
    "VALID_PR_TRANSITIONS",
    "ApprovalDecision",
    "APPROVAL_LIMITS",
    "PurchaseRequestError",
    "ApprovalLimitExceeded",
    "StaleApprovalError",
    "create_purchase_request",
    "get_purchase_request",
    "update_purchase_request",
    "transition_purchase_request",
    "record_approval_decision",
    "record_auto_approval",
    "get_approval_history",
    "approval_check_amount",
]

_tools_cache: Dict[str, Dict[str, Any]] = {}


def _tool(agent_id: str, name: str):
    if agent_id not in _tools_cache:
        _tools_cache[agent_id] = {t.name: t for t in build_agent_tools(agent_id, valid_ids=SYSTEM_IDS)}
    return _tools_cache[agent_id][name]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PurchaseRequestError(ValueError):
    """An invalid Purchase Request operation (bad input or status transition)."""


class ApprovalLimitExceeded(PermissionError):
    """The approver's configured role limit is below the request's estimated budget."""


class StaleApprovalError(PurchaseRequestError):
    """The request changed since the approver last reviewed it -- they must
    re-review the current version before deciding, not approve stale data."""


PR_STATUSES = (
    "Draft",
    "Pending Approval",
    "Approved",
    "Rejected",
    "Returned for Changes",
    "Cancelled",
)

VALID_PR_TRANSITIONS: Dict[str, set] = {
    "Draft": {"Pending Approval", "Cancelled"},
    "Pending Approval": {"Approved", "Rejected", "Returned for Changes", "Cancelled"},
    "Returned for Changes": {"Pending Approval", "Cancelled"},
    "Approved": set(),
    "Rejected": set(),
    "Cancelled": set(),
}

# Clearly-labeled placeholder policy -- see module docstring. Override by
# passing a different mapping into record_approval_decision's
# ``approval_limits`` parameter once a real company policy exists.
APPROVAL_LIMITS: Dict[str, float] = {
    "manager": 500_000.0,
    "director": 5_000_000.0,
    "cfo": float("inf"),
}


@dataclass
class PurchaseRequest:
    pr_id: str
    requested_by: str
    item_description: str
    quantity: int
    estimated_budget: float
    delivery_location: str
    required_date: str
    business_justification: str
    status: str = "Draft"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    # Agent-execution progress, orthogonal to the human-approval `status`
    # above (that lifecycle is unchanged) -- set by
    # app.procurement.task_orchestrator, not by the manual PR endpoints.
    mode: Optional[str] = None  # "demo" | "live" | None (not yet an agent-run task)
    item_code: Optional[str] = None
    workflow_state: str = "Draft"  # see task_orchestrator.py's docstring for the full state set
    blockers: List[str] = field(default_factory=list)
    coordinator_task_id: Optional[str] = None
    coordinator_result: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None
    # Set only once a human Approved decision has actually been followed
    # through into a real (or, in demo mode, simulated) Purchase Order --
    # see app.procurement.task_orchestrator.submit_approved_purchase_order.
    # None until then, even after an "Approved" status/decision exists:
    # the approval and the PO submission are two separate, honestly
    # distinguishable steps, never conflated.
    purchase_order: Optional[Dict[str, Any]] = None


def approval_check_amount(pr: "PurchaseRequest") -> float:
    """The real amount an approval decision must be checked against.

    This is deliberately NEVER ``pr.estimated_budget`` once a real
    recommendation exists: ``estimated_budget`` is only the requester's
    own, possibly very rough, original guess (and the agent pipeline's
    actual recommended purchase can come out far higher, e.g. a vague
    "~45,000" guess followed by a real recommendation at 759,000) --
    enforcing a spending limit against the wrong, stale number would let
    an approver sign off on an amount their role was never actually
    authorized for. Falls back to ``estimated_budget`` only when no
    coordinator recommendation exists yet at all (e.g. a Purchase Request
    created through the older, manual, non-agent-run path).
    """
    cr = pr.coordinator_result or {}
    total = (cr.get("result") or {}).get("recommended_total_cost")
    return float(total) if total is not None else pr.estimated_budget


@dataclass
class ApprovalDecision:
    pr_id: str
    approver_id: str
    approver_role: str
    decision: str  # "Approved" | "Rejected" | "Returned for Changes"
    comment: Optional[str]
    evaluation_version: Optional[str]
    # Honest, visible limitation (not just documentation): this project has
    # no authenticated human-identity/SSO system, so approver_role is a
    # self-declared request field, not verified proof of who the approver
    # actually is. Recorded as data so every consumer of this decision can
    # see that, rather than silently trusting it.
    identity_verified: bool = False
    decided_at: str = field(default_factory=_now)


def _is_demo(pr: PurchaseRequest) -> bool:
    return getattr(pr, "mode", None) == "demo"


# A real bug found live: the ONLY thing keeping a demo-mode task's writes
# out of the real Supabase project was a narrow `unittest.mock.patch`
# wrapped around app.procurement.task_orchestrator's own call to
# run_coordinator() -- it protected the coordinator's internal specialist
# narration writes, but every PurchaseRequest-record write (this
# function, called before/after that patch window closes -- on create,
# on every status change, and even the very first "created" save, which
# happens before a caller has had any chance to set pr.mode at all) and
# every approval-decision write (record_approval_decision below) fell
# completely outside that window and hit the real database on every
# single demo-mode run throughout this project, contradicting the
# explicit, repeated guarantee that demo mode never touches it. Fixed at
# the root, here, instead of hunting down every call site that needs its
# own patch -- impossible to miss this protection in a NEW call site
# added later, since there's nothing to remember to wrap.
#
# A demo PR still needs to be retrievable across the several separate
# HTTP requests one task spans (create -> supply missing info -> resume
# -> approve), so this can't be a pure no-op -- it's an isolated,
# in-memory-only store instead, keyed by pr_id, never Supabase. Lost on
# process restart; that's fine, demo tasks are explicitly throwaway.
_DEMO_STUB = {"id": "demo-mem-stub"}
_DEMO_PR_STORE: Dict[str, "PurchaseRequest"] = {}
_DEMO_APPROVAL_STORE: Dict[str, List["ApprovalDecision"]] = {}


def _save_pr(pr: PurchaseRequest, *, task_id: Optional[str], change_reason: Optional[str]) -> Dict[str, Any]:
    if _is_demo(pr):
        import copy

        _DEMO_PR_STORE[pr.pr_id] = copy.deepcopy(pr)
        return dict(_DEMO_STUB)
    memory_key = f"purchase_request:{pr.pr_id}"
    # Everything that marks a real step (approval status, workflow state,
    # item code, PO outcome) is in the content, so each step becomes its own
    # version instead of an in-place edit of the active row's fields.
    po_status = (pr.purchase_order or {}).get("status")
    content = (
        f"Purchase Request {pr.pr_id}: {pr.quantity}x {pr.item_description} "
        f"for {pr.requested_by} -- status: {pr.status} -- state: {pr.workflow_state}"
        f" -- item_code: {pr.item_code or 'not set'}"
        + (f" -- purchase_order: {po_status}" if po_status else "")
    )
    saved = _tool(PROCURA, "save_memory_entry").invoke({"memory_key": memory_key, "content": content})
    enrich_memory(
        saved["id"],
        source_type="computed",
        task_id=task_id or pr.pr_id,
        change_reason=change_reason,
        verification_status="verified",
        sharing_classification="shareable",
        structured_fact=asdict(pr),
    )
    return saved


def create_purchase_request(
    *,
    requested_by: str,
    item_description: str,
    quantity: int,
    estimated_budget: float,
    delivery_location: str,
    required_date: str,
    business_justification: str,
    task_id: Optional[str] = None,
    mode: Optional[str] = None,
) -> PurchaseRequest:
    """Create a new Purchase Request in "Draft" status. Raises
    PurchaseRequestError on invalid input rather than silently clamping it.

    ``mode``: pass "demo" here, at creation time, rather than setting
    ``pr.mode`` on the object the caller gets back -- the very first
    "created" save happens inside this function, before the caller would
    otherwise ever get a chance to mark it as demo, which is exactly the
    gap that let a demo task's first save always hit the real database
    regardless (see _save_pr's own docstring/comment on this bug).
    """
    if not requested_by or not requested_by.strip():
        raise PurchaseRequestError("requested_by is required")
    if quantity <= 0:
        raise PurchaseRequestError("quantity must be positive")
    if estimated_budget < 0:
        raise PurchaseRequestError("estimated_budget cannot be negative")
    if not business_justification or not business_justification.strip():
        raise PurchaseRequestError("business_justification is required")

    pr = PurchaseRequest(
        pr_id=f"PR-{uuid.uuid4().hex[:8].upper()}",
        requested_by=requested_by,
        item_description=item_description,
        quantity=quantity,
        estimated_budget=estimated_budget,
        delivery_location=delivery_location,
        required_date=required_date,
        business_justification=business_justification,
        status="Draft",
        mode=mode,
    )
    _save_pr(pr, task_id=task_id, change_reason="created")
    return pr


def get_purchase_request(pr_id: str) -> Optional[PurchaseRequest]:
    """The current (active) version of one Purchase Request, or None if it
    doesn't exist. Checks the in-memory demo store first (see _save_pr) --
    a real pr_id can never collide with one, so this is always safe."""
    if pr_id in _DEMO_PR_STORE:
        import copy

        return copy.deepcopy(_DEMO_PR_STORE[pr_id])
    rows = get_memory_by_key(PROCURA, f"purchase_request:{pr_id}")
    if not rows or not rows[0].get("structured_fact"):
        return None
    return PurchaseRequest(**rows[0]["structured_fact"])


def update_purchase_request(pr_id: str, *, task_id: Optional[str] = None, **changes: Any) -> PurchaseRequest:
    """Update one or more fields on an existing Purchase Request (e.g.
    filling in the missing quantity/budget/item_code a "Waiting For
    Information" task is blocked on) -- a new version, not an overwrite.
    Rejects an unknown field name rather than silently ignoring a typo."""
    pr = get_purchase_request(pr_id)
    if pr is None:
        raise PurchaseRequestError(f"no such purchase request: {pr_id}")
    unknown = set(changes) - set(pr.__dataclass_fields__)
    if unknown:
        raise PurchaseRequestError(f"unknown purchase request field(s): {sorted(unknown)}")
    for key, value in changes.items():
        setattr(pr, key, value)
    pr.updated_at = _now()
    _save_pr(pr, task_id=task_id, change_reason=f"updated: {', '.join(sorted(changes))}")
    return pr


def transition_purchase_request(
    pr_id: str, new_status: str, *, task_id: Optional[str] = None
) -> PurchaseRequest:
    """Move a Purchase Request to ``new_status`` if that's a valid
    transition from its current status -- raises PurchaseRequestError
    otherwise (never silently allows an invalid state change)."""
    pr = get_purchase_request(pr_id)
    if pr is None:
        raise PurchaseRequestError(f"no such purchase request: {pr_id}")
    if new_status not in PR_STATUSES:
        raise PurchaseRequestError(f"unknown status: {new_status!r}")
    allowed = VALID_PR_TRANSITIONS.get(pr.status, set())
    if new_status not in allowed:
        raise PurchaseRequestError(
            f"invalid transition for {pr_id}: {pr.status!r} -> {new_status!r} (allowed: {sorted(allowed) or 'none -- terminal status'})"
        )
    pr.status = new_status
    pr.updated_at = _now()
    _save_pr(pr, task_id=task_id, change_reason=f"status -> {new_status}")
    return pr


def record_approval_decision(
    *,
    pr_id: str,
    approver_id: str,
    approver_role: str,
    decision: str,
    comment: Optional[str] = None,
    evaluation_version: Optional[str] = None,
    reviewed_updated_at: Optional[str] = None,
    approval_limits: Optional[Dict[str, float]] = None,
    task_id: Optional[str] = None,
) -> ApprovalDecision:
    """Record a human approval decision against a Purchase Request --
    enforces the approver's role-based spending limit (raises
    ApprovalLimitExceeded if the request's estimated budget is above it)
    and the request's own valid-transition rules. No AI agent calls this;
    it exists to record a decision a human actually made, with their
    identity, timestamp, comment, and exactly which evaluation version
    they approved -- never to make the decision itself.

    ``reviewed_updated_at``: pass the ``updated_at`` the approver actually
    saw when they reviewed the request. If the request has since changed
    (a new version exists with a different ``updated_at``), this raises
    StaleApprovalError instead of silently approving data the approver
    never actually reviewed. Omit it only for a request that cannot have
    changed since creation (e.g. an automated test); a real approval UI
    should always pass it.
    """
    if decision not in ("Approved", "Rejected", "Returned for Changes"):
        raise PurchaseRequestError(f"invalid decision: {decision!r}")

    pr = get_purchase_request(pr_id)
    if pr is None:
        raise PurchaseRequestError(f"no such purchase request: {pr_id}")

    if reviewed_updated_at is not None and reviewed_updated_at != pr.updated_at:
        raise StaleApprovalError(
            f"{pr_id} changed since it was reviewed (reviewed version updated_at={reviewed_updated_at!r}, "
            f"current updated_at={pr.updated_at!r}) -- re-review the current version before deciding"
        )

    limits = approval_limits if approval_limits is not None else APPROVAL_LIMITS
    limit = limits.get(approver_role)
    if limit is None:
        raise PurchaseRequestError(f"unknown approver role: {approver_role!r} (configured roles: {sorted(limits)})")
    amount = approval_check_amount(pr)
    if decision == "Approved" and amount > limit:
        raise ApprovalLimitExceeded(
            f"{approver_role} may approve up to {limit:,.2f}; this request's actual amount "
            f"({amount:,.2f}) exceeds that limit -- route to a higher authority"
        )

    transition_purchase_request(pr_id, decision, task_id=task_id)

    record = ApprovalDecision(
        pr_id=pr_id,
        approver_id=approver_id,
        approver_role=approver_role,
        decision=decision,
        comment=comment,
        evaluation_version=evaluation_version,
    )
    if _is_demo(pr):
        # Same real bug, same fix as _save_pr: a demo task can genuinely
        # reach the approval UI (its workflow_state doesn't block that),
        # and clicking Approve/Reject on one used to write a real
        # approval record to Finora's real memory regardless of mode.
        _DEMO_APPROVAL_STORE.setdefault(pr_id, []).append(record)
        return record
    memory_key = f"approval:{pr_id}"
    content = f"{pr_id}: {decision} by {approver_id} ({approver_role})" + (f" -- {comment}" if comment else "")
    saved = _tool(FINORA, "save_memory_entry").invoke({"memory_key": memory_key, "content": content})
    enrich_memory(
        saved["id"],
        source_type="computed",
        task_id=task_id or pr_id,
        verification_status="verified",
        sharing_classification="shareable",
        structured_fact=asdict(record),
    )
    return record


def record_auto_approval(pr_id: str, *, task_id: Optional[str] = None) -> ApprovalDecision:
    """Record that a Purchase Request was approved with NO human review at
    all -- used only by the live-mode auto-approval path
    (app.procurement.task_orchestrator, gated behind the explicit, off-by-
    default ``LIVE_MODE_AUTO_APPROVE`` env var). Deliberately separate
    from record_approval_decision: there is no human and no role here, so
    no role/spending-limit check applies -- applying one would be
    meaningless (there's nothing to check it against) or, worse, dishonest
    (pretending some role's authority was actually exercised). The
    approver_id/approver_role/identity_verified fields make the "no human
    reviewed this" fact plainly visible in the audit trail, never
    disguised as a real decision.
    """
    pr = get_purchase_request(pr_id)
    if pr is None:
        raise PurchaseRequestError(f"no such purchase request: {pr_id}")

    transition_purchase_request(pr_id, "Approved", task_id=task_id)

    record = ApprovalDecision(
        pr_id=pr_id,
        approver_id="SYSTEM-AUTO-APPROVAL",
        approver_role="none (LIVE_MODE_AUTO_APPROVE -- no human review)",
        decision="Approved",
        comment="Submitted automatically with no human review, per LIVE_MODE_AUTO_APPROVE configuration.",
        evaluation_version=None,
    )
    memory_key = f"approval:{pr_id}"
    content = f"{pr_id}: Approved by SYSTEM-AUTO-APPROVAL (no human review)"
    saved = _tool(FINORA, "save_memory_entry").invoke({"memory_key": memory_key, "content": content})
    enrich_memory(
        saved["id"],
        source_type="computed",
        task_id=task_id or pr_id,
        verification_status="unverified",  # honestly: no human verified this
        sharing_classification="shareable",
        structured_fact=asdict(record),
    )
    return record


def get_approval_history(pr_id: str) -> List[Dict[str, Any]]:
    """Every approval decision ever recorded for this Purchase Request,
    oldest first -- the version chain is the audit trail. Checks the
    in-memory demo store first (see record_approval_decision) -- a real
    pr_id can never collide with one, so this is always safe."""
    if pr_id in _DEMO_APPROVAL_STORE:
        return [asdict(r) for r in _DEMO_APPROVAL_STORE[pr_id]]
    rows = get_memory_by_key(FINORA, f"approval:{pr_id}", include_superseded=True)
    return [r["structured_fact"] for r in rows if r.get("structured_fact")]
