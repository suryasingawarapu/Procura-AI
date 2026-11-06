"""Quotation history for one item: summary first, then the supporting records.

Instead of reading every earlier quotation, Procura first takes the stored
summary of this item's quotation claims (and only the claims newer than it),
then opens the supporting records -- every version of each claim from the
suppliers now being compared -- to see what actually changed: "Silverline's
price rose from 51,700 to 56,000", "delivery moved from 15 to 25 days".

A summary that covers a claim which has since changed is stale and is not
used. After the lookup, a summary is created or brought up to date in the
background (the lookup itself never waits for an LLM).
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from app.memory.client import supabase
from app.memory.summaries import refresh_summary, scoped_context

from .identities import PROCURA
from .quotation_claims import subject_for

__all__ = ["build_history", "price_and_delivery_changes"]

MIN_CLAIMS_FOR_A_SUMMARY = 3
MAX_CHANGES = 10


def price_and_delivery_changes(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Changes between consecutive versions of the same source document's claim."""
    by_key: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_key.setdefault(row["memory_key"], []).append(row)
    changes: List[Dict[str, Any]] = []
    for versions in by_key.values():
        versions.sort(key=lambda r: r.get("version") or 0)
        for previous, current in zip(versions, versions[1:]):
            before, after = previous.get("structured_fact") or {}, current.get("structured_fact") or {}
            base = {
                "supplier": after.get("supplier"), "source_reference": after.get("source_reference"),
                "recorded": str(current.get("created_at") or current.get("observed_at") or "")[:10],
            }
            if before.get("value") != after.get("value") and before.get("value") is not None and after.get("value") is not None:
                pct = round((after["value"] - before["value"]) / before["value"] * 100, 1) if before["value"] else None
                changes.append({**base, "field": "price", "from": before["value"], "to": after["value"],
                                "currency": after.get("currency"), "change_pct": pct})
            if before.get("delivery_days") != after.get("delivery_days") and None not in (before.get("delivery_days"), after.get("delivery_days")):
                changes.append({**base, "field": "delivery_days", "from": before["delivery_days"], "to": after["delivery_days"]})
    changes.sort(key=lambda c: c["recorded"], reverse=True)
    return changes[:MAX_CHANGES]


def _spawn(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def _refresh_in_background(prefix: str) -> None:
    try:
        try:
            from app.agents.workflow import get_llm

            llm = get_llm("procura")
        except Exception:
            llm = None
        refresh_summary(PROCURA, scope_key_prefix=prefix, llm=llm, min_new=MIN_CLAIMS_FOR_A_SUMMARY)
    except Exception:
        pass  # retried on the next run


def build_history(*, item_code: str, quote_rows: List[Dict[str, Any]], client: Any = None) -> Dict[str, Any]:
    active_client = client if client is not None else supabase
    prefix = f"quote:{item_code}:"
    ctx = scoped_context(PROCURA, prefix, client=active_client)
    summary, state = ctx["summary"], ctx["state"]

    suppliers = sorted({r["supplier"] for r in quote_rows if r.get("supplier")})
    versions: List[Dict[str, Any]] = []
    if suppliers:
        versions = (
            active_client.table("agent_memories")
            .select("memory_key,version,status,created_at,observed_at,structured_fact")
            .eq("agent_id", PROCURA)
            .in_("subject_key", [subject_for(item_code, s) for s in suppliers])
            .execute()
            .data
            or []
        )

    if ctx["scope_size"] >= MIN_CLAIMS_FOR_A_SUMMARY:
        _spawn(_refresh_in_background, prefix)

    return {
        "summary_used": summary is not None,
        "summary_state": state.state if state else None,
        "summary": (summary["summary"][:400] if summary else None),
        "claims_in_scope": ctx["scope_size"],
        "records_read_after_summary": len(ctx["records"]),
        "records_opened": len(versions),
        "changes": price_and_delivery_changes(versions),
    }
