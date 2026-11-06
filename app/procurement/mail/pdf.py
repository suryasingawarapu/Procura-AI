"""The purchase order as a PDF, built from the stored PO data (no network, no template files)."""

from __future__ import annotations

import io
from typing import Any, Dict

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_INK = colors.HexColor("#1b2233")
_ACCENT = colors.HexColor("#4f5fe6")
_MUTED = colors.HexColor("#667085")


def _money(currency: str, value: Any) -> str:
    return f"{currency} {float(value):,.2f}" if value is not None else "-"


def po_filename(po: Dict[str, Any]) -> str:
    ref = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(po.get("reference") or "PO"))
    return f"Purchase-Order-{ref}.pdf"


def build_po_pdf(po: Dict[str, Any]) -> bytes:
    """``po`` keys: reference, date, vendor, vendor_email, buyer, item, quantity, unit_price, total,
    currency, delivery_location, required_by."""
    cur = po.get("currency") or "INR"
    base = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=base["Title"], fontName="Helvetica-Bold", fontSize=22, textColor=_INK, alignment=0, spaceAfter=2)
    meta = ParagraphStyle("meta", parent=base["Normal"], fontName="Helvetica", fontSize=9.5, textColor=_MUTED, leading=14)
    body = ParagraphStyle("body", parent=base["Normal"], fontName="Helvetica", fontSize=10.5, textColor=_INK, leading=15)
    label = ParagraphStyle("label", parent=meta, fontName="Helvetica-Bold", textColor=_ACCENT, fontSize=8.5)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm, bottomMargin=18 * mm,
                            title=f"Purchase Order {po.get('reference', '')}", author=po.get("buyer") or "Procurement Team")
    story = [
        Paragraph("PURCHASE ORDER", h1),
        Paragraph(f"No. {po.get('reference', '-')} &nbsp;&middot;&nbsp; Date {po.get('date', '-')}", meta),
        Spacer(1, 8 * mm),
    ]
    parties = Table(
        [[Paragraph("VENDOR", label), Paragraph("BUYER", label)],
         [Paragraph(f"<b>{po.get('vendor', '-')}</b><br/>{po.get('vendor_email') or ''}", body),
          Paragraph(f"<b>{po.get('buyer') or 'Procurement Team'}</b><br/>Deliver to: {po.get('delivery_location') or '-'}", body)]],
        colWidths=[85 * mm, 85 * mm],
    )
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, 0), 2)]))
    story += [parties, Spacer(1, 9 * mm)]

    rows = [["Item", "Qty", "Unit price", "Amount"],
            [Paragraph(str(po.get("item", "-")), body), str(po.get("quantity", "-")), _money(cur, po.get("unit_price")), _money(cur, po.get("total"))],
            ["", "", "Total", _money(cur, po.get("total"))]]
    items = Table(rows, colWidths=[78 * mm, 20 * mm, 36 * mm, 36 * mm])
    items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _INK), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, colors.HexColor("#d5d9e3")),
        ("FONTNAME", (2, 2), (-1, 2), "Helvetica-Bold"), ("LINEABOVE", (2, 2), (-1, 2), 1, _INK),
    ]))
    story += [items, Spacer(1, 8 * mm)]
    if po.get("required_by"):
        story.append(Paragraph(f"<b>Required by:</b> {po['required_by']}", body))
        story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Please reply to this email to confirm receipt of this purchase order, the delivery date, and any change to the above.", body))
    doc.build(story)
    return buf.getvalue()
