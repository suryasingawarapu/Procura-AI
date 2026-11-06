"""Vendor email API: draft and send purchase-order mails, check replies, follow up.

All admin-only. Sending also needs MAIL_ALLOW_SEND=1 and an explicit
``confirm: true``; with MAIL_TEST_RECIPIENT set every mail goes to that address.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.procurement import workflow
from app.procurement.mail import composio, outbox, service
from app.procurement.mcp.client import get_configured_client

from .auth import authorize_admin, require_auth

router = APIRouter(prefix="/procurement/mail", tags=["mail"])


class DraftRequest(BaseModel):
    pr_id: str
    vendor_email: Optional[str] = Field(default=None, description="Overrides the address on file")


class SendRequest(BaseModel):
    confirm: bool = False


class FollowupRequest(BaseModel):
    confirm: bool = False
    older_than_days: Optional[int] = Field(default=None, ge=0, le=60)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except outbox.MailError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except composio.ComposioError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/status")
def mail_status(auth_token: str = Depends(require_auth)) -> Dict[str, Any]:
    authorize_admin(auth_token)
    return outbox.settings()


@router.get("")
def list_mail(pr_id: Optional[str] = None, auth_token: str = Depends(require_auth)) -> Dict[str, Any]:
    authorize_admin(auth_token)
    return {"emails": outbox.list_mail(pr_id)}


@router.post("/draft", status_code=201)
def draft(request: DraftRequest, auth_token: str = Depends(require_auth)) -> Dict[str, Any]:
    """Draft the purchase-order email for an approved request. Nothing is sent."""
    authorize_admin(auth_token)
    pr = workflow.get_purchase_request(request.pr_id)
    if pr is None:
        raise HTTPException(status_code=404, detail=f"no such request: {request.pr_id}")
    client, _ = get_configured_client()
    return _wrap(service.draft_for_request, pr, client=client, vendor_email=request.vendor_email)


@router.post("/{mail_id}/send")
def send(mail_id: str, request: SendRequest, auth_token: str = Depends(require_auth)) -> Dict[str, Any]:
    authorize_admin(auth_token)
    if not request.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true to send an email")
    return _wrap(outbox.send_mail, mail_id)


@router.post("/check-replies")
def check_replies(auth_token: str = Depends(require_auth)) -> Dict[str, Any]:
    """Read the threads of sent mails and record any vendor replies as memory."""
    authorize_admin(auth_token)
    return {"new_replies": _wrap(outbox.check_replies)}


@router.post("/followups")
def followups(request: FollowupRequest, auth_token: str = Depends(require_auth)) -> Dict[str, Any]:
    authorize_admin(auth_token)
    if not request.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true to send follow-ups")
    return {"sent": _wrap(outbox.send_followups, older_than_days=request.older_than_days)}
