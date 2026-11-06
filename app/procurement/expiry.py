"""Document validity dates.

ERPNext's attached-file records (the ``File`` doctype) carry no expiry field,
so a compliance document's validity has to come from somewhere explicit:

1. The file name, by convention: ``GST_Certificate_valid_until_2027-03-31.pdf``
   (also ``valid_till``/``valid_upto``/``expires``/``expiry``/``exp``).
2. A recorded verification (:mod:`app.procurement.document_validity`): who
   checked the document and until when it is valid.

A recorded validity wins over the file name. A document with no known expiry
is treated exactly as before (valid): nothing is assumed expired.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Optional

__all__ = ["parse_expiry", "end_of_day_utc", "today_utc"]

_EXPIRY_RE = re.compile(
    r"(?:valid[\s_-]*(?:until|till|upto|up[\s_-]*to|thru)|exp(?:ires?|iry|iration)?(?:[\s_-]*(?:date|on))?)"
    r"[\s_-]*(\d{4})[-_.](\d{2})[-_.](\d{2})",
    re.IGNORECASE,
)


def parse_expiry(label: str) -> Optional[date]:
    """The expiry date written into a file name, or None."""
    match = _EXPIRY_RE.search(label or "")
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def end_of_day_utc(day: date) -> datetime:
    """A document valid until ``day`` is valid through the end of that day."""
    return datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=timezone.utc)


def today_utc() -> date:
    return datetime.now(timezone.utc).date()
