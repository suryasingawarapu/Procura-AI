"""Pydantic schemas for the procurement coordinator's API surface."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ProcurementTaskRequest(BaseModel):
    description: str = Field(..., min_length=1)
    item_code: str = Field(..., min_length=1)
    quantity: Optional[int] = Field(default=None, gt=0)


class ProcurementTaskResponse(BaseModel):
    task_id: str
    correlation_id: str
    systems_invoked: List[str]
    messages: List[Dict[str, Any]]
    requires_approval: bool
    approval_summary: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    failed: bool = False
    blocked: bool = False
    error: Optional[str] = None


class DemoTaskRequest(BaseModel):
    scenario: Literal["eligible", "ineligible"] = "eligible"


class DemoTaskResponse(BaseModel):
    simulated: Literal[True] = True
    scenario: Literal["eligible", "ineligible"]
    task: ProcurementTaskResponse


class CreatePurchaseRequestRequest(BaseModel):
    requested_by: str = Field(..., min_length=1)
    item_description: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)
    estimated_budget: float = Field(..., ge=0)
    delivery_location: str = Field(..., min_length=1)
    required_date: str = Field(..., min_length=1)
    business_justification: str = Field(..., min_length=1)


class PurchaseRequestResponse(BaseModel):
    pr_id: str
    requested_by: str
    item_description: str
    quantity: int
    estimated_budget: float
    delivery_location: str
    required_date: str
    business_justification: str
    status: str
    created_at: str
    updated_at: str
    # Agent-execution progress (set by the task orchestrator, not the
    # manual PR endpoints above) -- None/defaults for a PR that was only
    # ever created manually and never run as a one-instruction task.
    mode: Optional[str] = None
    item_code: Optional[str] = None
    workflow_state: str = "Draft"
    blockers: List[str] = Field(default_factory=list)
    coordinator_task_id: Optional[str] = None
    coordinator_result: Optional[Dict[str, Any]] = None
    purchase_order: Optional[Dict[str, Any]] = None


class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1)
    requested_by: str = Field(..., min_length=1)
    mode: Literal["demo", "live"] = "demo"
    pr_id: Optional[str] = None
    history: List[Dict[str, str]] = Field(
        default_factory=list, description="Recent {role, content} turns, most recent last; only the last few are used."
    )
    conversation_id: Optional[str] = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]{1,80}$",
        description="Identifies the conversation. When given (live mode only), each turn is stored in persistent "
        "memory, summarized over time, and the summary is used as context for later messages.",
    )


class ChatMessageResponse(BaseModel):
    reply: Optional[str] = None
    pr: Optional[PurchaseRequestResponse] = None
    mail: Optional[Dict[str, Any]] = None  # a purchase-order email awaiting a person's Send


class StartProcurementTaskRequest(BaseModel):
    instruction: str = Field(..., min_length=1)
    requested_by: str = Field(..., min_length=1)
    mode: Literal["demo", "live"] = "demo"
    item_code: Optional[str] = None
    delivery_location: Optional[str] = None
    required_date: Optional[str] = None
    idempotency_key: Optional[str] = None


class TransitionPurchaseRequestRequest(BaseModel):
    new_status: str = Field(..., min_length=1)


class UpdatePurchaseRequestRequest(BaseModel):
    """Only the fields to change -- used to fill in what a
    "Waiting For Information" task is blocked on before resuming it.
    Every field is optional; omitted fields are left as-is."""

    item_code: Optional[str] = None
    quantity: Optional[int] = Field(default=None, gt=0)
    estimated_budget: Optional[float] = Field(default=None, ge=0)
    delivery_location: Optional[str] = None
    required_date: Optional[str] = None


class ApprovalDecisionRequest(BaseModel):
    approver_id: str = Field(..., min_length=1)
    approver_role: str = Field(..., min_length=1)
    decision: Literal["Approved", "Rejected", "Returned for Changes"]
    comment: Optional[str] = None
    evaluation_version: Optional[str] = None
    reviewed_updated_at: Optional[str] = Field(
        default=None,
        description="The Purchase Request's updated_at you actually reviewed. "
        "If the request has changed since, the decision is rejected as stale.",
    )


class ApprovalLimitsResponse(BaseModel):
    """Role -> spending limit; ``null`` means no limit (e.g. cfo).
    JSON has no literal Infinity, so app.procurement.workflow's
    ``float("inf")`` is translated to ``null`` here -- the one place
    that representation choice is made."""

    limits: Dict[str, Optional[float]]


class ApprovalDecisionResponse(BaseModel):
    pr_id: str
    approver_id: str
    approver_role: str
    decision: str
    comment: Optional[str] = None
    evaluation_version: Optional[str] = None
    identity_verified: bool = False
    decided_at: str


class CreateVendorRequest(BaseModel):
    vendor_code: str = Field(..., min_length=1)
    legal_name: str = Field(..., min_length=1)
    business_category: str = Field(..., min_length=1)
    products_services: str = Field(..., min_length=1)
    country: str = Field(..., min_length=1)
    address: str = Field(..., min_length=1)
    authorized_contacts: List[str] = Field(default_factory=list)
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    currency: str = "USD"
    payment_terms_days: Optional[int] = None
    delivery_capabilities: Optional[str] = None
    warranty_info: Optional[str] = None
    registration_status: str = "Unverified"
    notes: Optional[str] = None


class VendorResponse(BaseModel):
    vendor_code: str
    legal_name: str
    business_category: str
    products_services: str
    country: str
    address: str
    authorized_contacts: List[str]
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    currency: str
    payment_terms_days: Optional[int] = None
    delivery_capabilities: Optional[str] = None
    warranty_info: Optional[str] = None
    registration_status: str
    notes: Optional[str] = None
    status: str
    is_demo: bool
    source: str
    created_at: str
    updated_at: str


class VendorListResponse(BaseModel):
    vendors: List[VendorResponse]


class UpdateVendorRequest(BaseModel):
    """Only the fields to change -- any VendorProfile field name is
    accepted; omitted fields are left as-is."""

    model_config = {"extra": "allow"}


class SetVendorStatusRequest(BaseModel):
    status: Literal["Active", "Inactive", "Archived"]


class ImportVendorsRequest(BaseModel):
    rows: List[Dict[str, Any]]
    commit: bool = False


class ImportReportResponse(BaseModel):
    total_rows: int
    created: List[str]
    skipped_duplicates: List[Dict[str, str]]
    errors: List[Dict[str, str]]
    committed: bool


class DemoSeedVendorsRequest(BaseModel):
    count: int = Field(default=35, ge=1, le=200)


class DemoSeedVendorsResponse(BaseModel):
    simulated: Literal[True] = True
    created_count: int
    created_vendor_codes: List[str]


class DemoResetVendorsResponse(BaseModel):
    removed_count: int


class VendorErpnextSyncResponse(BaseModel):
    vendor_code: str
    linked: bool
    detail: Dict[str, Any]


class PurchaseOrderRequest(BaseModel):
    supplier: str = Field(..., min_length=1)
    item_code: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)
    confirm: bool = Field(
        ..., description="Must be explicitly true. This is the human-approval gate before any real submission."
    )


class PurchaseOrderResponse(BaseModel):
    purchase_order: Dict[str, Any]
    backend: str


class IntegrationHealthResponse(BaseModel):
    erpnext_backend: str  # "erpnext_official_mcp" | "erpnext_mcp" | "erpnext_rest" | "seeded"
    erpnext_writes_allowed: bool
    supabase_configured: bool
    llm_provider_configured: bool
    langfuse_configured: bool
    ai_summaries_enabled: bool = False


# -- quotation claims, conflicts, document validity ------------------------------------------------------------------


class QuotationClaimRequest(BaseModel):
    """A quotation that arrived from some source (an email, a vendor's PDF, ...)
    to be recorded with where it came from and the date of that document."""

    item_code: str = Field(..., min_length=1, max_length=120)
    supplier: str = Field(..., min_length=1, max_length=140)
    unit_price: float = Field(..., gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    source_type: Literal["pdf", "document", "email", "chat", "manual", "phone", "erpnext_doctype"]
    source_reference: str = Field(..., min_length=1, max_length=300, description="The file name, email subject or document id")
    document_date: Optional[date] = Field(default=None, description="The date ON the document (not today's)")
    delivery_days: Optional[int] = Field(default=None, ge=0)
    payment_terms_days: Optional[int] = Field(default=None, ge=0)
    recorded_by: str = Field(default="api-user", min_length=1, max_length=120)
    content_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    note: Optional[str] = Field(default=None, max_length=500)


class ConflictResolveRequest(BaseModel):
    subject_key: str = Field(..., min_length=1, max_length=300)
    winner_memory_id: str = Field(..., min_length=1, max_length=80)
    resolved_by: str = Field(..., min_length=1, max_length=120)
    note: Optional[str] = Field(default=None, max_length=500)


class DocumentValidityRequest(BaseModel):
    document: str = Field(..., min_length=1, max_length=120, description='e.g. "GST Registration Certificate"')
    valid_until: date
    verified_by: str = Field(..., min_length=1, max_length=120)
    source_reference: Optional[str] = Field(default=None, max_length=300)
    note: Optional[str] = Field(default=None, max_length=500)
