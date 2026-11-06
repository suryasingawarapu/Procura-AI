"""Glue between a purchase request and the mail outbox: who the vendor is, where their email is on
file, and drafting the purchase-order email for a request. Shared by the API, the chat and the
purchase-order hook so they all behave the same."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .. import vendor_master
from . import outbox


def supplier_details(supplier: str, client: Any = None) -> Dict[str, Any]:
    """What is on file for a supplier: ERPNext's Supplier record first (the live system of record),
    then the vendor master. Fields that are not on file are None, never invented."""
    details: Dict[str, Any] = {
        "name": supplier, "email": None, "phone": None, "website": None, "country": None, "group": None, "type": None,
        "source": None,
    }
    if client is not None:
        try:
            rec = client.get_supplier(supplier)
        except Exception:
            rec = None
        if rec:
            details.update(
                email=rec.get("email_id") or None, phone=rec.get("mobile_no") or None, website=rec.get("website") or None,
                country=rec.get("country") or None, group=rec.get("supplier_group") or None, type=rec.get("supplier_type") or None,
                source="ERPNext",
            )
    if not details["email"]:
        try:
            vendor = vendor_master.get_vendor(supplier) or next(
                (v for v in vendor_master.search_vendors(query=supplier) if v.legal_name.strip().lower() == supplier.strip().lower()), None
            )
        except Exception:
            vendor = None
        if vendor:
            details.update(email=vendor.email, phone=details["phone"] or vendor.phone, website=details["website"] or vendor.website,
                           country=details["country"] or vendor.country, source=details["source"] or "vendor master")
    return details


def recommended(pr: Any) -> Dict[str, Any]:
    """The recommended supplier and price from a request's real coordinator result."""
    result = ((pr.coordinator_result or {}).get("result")) or {}
    return {"supplier": result.get("recommended_supplier"), "total": result.get("recommended_total_cost")}


def draft_for_request(pr: Any, *, client: Any = None, vendor_email: Optional[str] = None, reuse: bool = True) -> Dict[str, Any]:
    """Draft the PO email for a request's recommended supplier. With ``reuse`` an existing mail for the
    request is returned instead of drafting a second one, so asking twice never duplicates a mail."""
    if reuse:
        existing = [m for m in outbox.list_mail(pr.pr_id) if m.get("kind") == "po"]
        if existing:
            return existing[0]
    rec = recommended(pr)
    if not rec["supplier"]:
        raise outbox.MailError("this request has no recommended supplier to email")
    email = vendor_email or supplier_details(rec["supplier"], client)["email"]
    po = (pr.purchase_order or {}).get("result")
    return outbox.draft_po_email(
        pr_id=pr.pr_id, vendor=rec["supplier"], vendor_email=email, item=pr.item_code or pr.item_description,
        quantity=pr.quantity, unit_price=(rec["total"] / pr.quantity) if rec["total"] and pr.quantity else None,
        total=rec["total"], po_number=po.get("name") if isinstance(po, dict) else None,
        delivery_location=pr.delivery_location, required_by=pr.required_date,
    )


def preview(mail: Dict[str, Any]) -> Dict[str, Any]:
    """The part of a mail record a chat UI shows before a person presses Send."""
    cfg = outbox.settings()
    return {
        "mail_id": mail["mail_id"], "pr_id": mail.get("pr_id"), "vendor": mail.get("vendor"), "to": mail.get("to"),
        "intended_to": mail.get("intended_to"), "subject": mail.get("subject"), "body": mail.get("body"),
        "attachment_name": mail.get("attachment_name"), "status": mail.get("status"), "sent_at": mail.get("sent_at"),
        "test_mode": bool(cfg["test_recipient"]), "sending_allowed": cfg["sending_allowed"],
    }
