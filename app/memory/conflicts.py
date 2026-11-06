"""Conflict detection and resolution between memories that claim different
values for the same subject.

A *claim* is an active memory with a ``subject_key`` (what it is about) and a
``structured_fact`` holding ``value`` (and optionally ``currency``,
``document_date``). Two claims for one subject conflict when their values
differ. Resolution is rule-based, never a guess:

1. The claim from the more authoritative *source tier* wins (a formal
   document outranks an email, which outranks a chat remark).
2. Within one tier, the claim from the *newer document* wins -- an updated
   quotation replaces an older one.
3. A lower-tier claim that is strictly *newer* than the best claim, or two
   same-tier claims with no clear date order, are **not** auto-resolved:
   the conflict is marked ``needs_clarification`` and a person decides.

A person's decision (``resolve_subject``) always overrides the rules, for
every claim on that subject. The outcome is recorded on the claims themselves
(a ``conflict`` entry in each claim's ``structured_fact``, and the loser's
``verification_status`` becomes ``disputed``; the claim is kept), which is the
source of truth. It is *also* recorded as ``conflicts_with`` / ``resolved_by``
edges in ``memory_provenance`` when the database allows those relationships
(migration 008); if it does not, everything still works and ``edges_recorded``
says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .client import supabase
from .summaries import _parse_ts

__all__ = [
    "DEFAULT_AUTHORITY",
    "ConflictReport",
    "claim_value",
    "claim_date",
    "resolve_rows",
    "detect_conflicts",
    "scan_conflicts",
    "resolve_subject",
]

# Higher = more authoritative. Unknown source types rank 0.
DEFAULT_AUTHORITY: Dict[str, int] = {
    "erpnext_doctype": 3,
    "pdf": 3,
    "document": 3,
    "email": 2,
    "chat": 1,
    "manual": 1,
    "phone": 1,
}


@dataclass
class ConflictReport:
    subject_key: str
    status: str  # none | resolved | needs_clarification
    claims: List[Dict[str, Any]] = field(default_factory=list)
    winner: Optional[Dict[str, Any]] = None
    losers: List[Dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    manual: bool = False
    edges_recorded: bool = True

    @property
    def conflicting(self) -> bool:
        return self.status != "none"


# -- reading a claim ---------------------------------------------------------------------


def claim_value(row: Dict[str, Any]) -> Optional[Tuple[Any, str]]:
    """``(value, currency)`` of a claim, or None if it carries no value."""
    fact = row.get("structured_fact") or {}
    value = fact.get("value")
    if value is None:
        return None
    try:
        value = round(float(value), 2)
    except (TypeError, ValueError):
        value = str(value)
    return (value, str(fact.get("currency") or ""))


def claim_date(row: Dict[str, Any]) -> Optional[datetime]:
    """The date of the *document* the claim came from (not when it was stored)."""
    return _parse_ts((row.get("structured_fact") or {}).get("document_date"))


def _tier(row: Dict[str, Any], authority: Dict[str, int]) -> int:
    return authority.get(row.get("source_type") or "", 0)


def _describe(row: Dict[str, Any]) -> str:
    value = claim_value(row)
    return f"{value[1]} {value[0]} ({row.get('source_type') or 'unknown source'})".strip()


# -- the rules (pure) ------------------------------------------------------------------------


def resolve_rows(rows: List[Dict[str, Any]], authority: Optional[Dict[str, int]] = None) -> ConflictReport:
    """Apply the rules to the claims of ONE subject. No database access."""
    authority = authority or DEFAULT_AUTHORITY
    subject = next((r.get("subject_key") for r in rows if r.get("subject_key")), "")
    claims = [r for r in rows if claim_value(r) is not None]
    if len({claim_value(r) for r in claims}) < 2:
        return ConflictReport(subject, "none", claims)

    top = max(_tier(r, authority) for r in claims)
    top_rows = [r for r in claims if _tier(r, authority) == top]

    winner: Optional[Dict[str, Any]] = None
    if len({claim_value(r) for r in top_rows}) == 1:
        dated = [r for r in top_rows if claim_date(r)]
        winner = max(dated, key=claim_date) if dated else top_rows[0]
    else:
        # Same tier, different values: only a clear date order can decide.
        if all(claim_date(r) for r in top_rows):
            newest = max(claim_date(r) for r in top_rows)
            latest = [r for r in top_rows if claim_date(r) == newest]
            if len({claim_value(r) for r in latest}) == 1:
                winner = latest[0]
        if winner is None:
            return ConflictReport(
                subject, "needs_clarification", claims,
                reason=f"{len(top_rows)} equally authoritative sources disagree and no document is clearly the newer one: "
                + "; ".join(_describe(r) for r in top_rows),
            )

    wdate = claim_date(winner)
    for row in claims:
        if row is winner or claim_value(row) == claim_value(winner) or _tier(row, authority) >= top:
            continue
        rdate = claim_date(row)
        if rdate and wdate and rdate > wdate:
            return ConflictReport(
                subject, "needs_clarification", claims,
                reason=f"a newer {row.get('source_type')} ({_describe(row)}) contradicts the more authoritative but "
                f"older {winner.get('source_type')} ({_describe(winner)})",
            )

    losers = [r for r in claims if claim_value(r) != claim_value(winner)]
    same_tier = [r for r in losers if _tier(r, authority) == top]
    reason = (
        f"{_describe(winner)} is the newer of equally authoritative sources"
        if same_tier
        else f"{_describe(winner)} outranks " + ", ".join(sorted({r.get('source_type') or 'unknown' for r in losers}))
    )
    return ConflictReport(subject, "resolved", claims, winner=winner, losers=losers, reason=reason)


# -- recording (database) ------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_edge(client: Any, memory_id: str, parent_id: str, relationship: str, agent_id: str, **fields: Any) -> bool:
    """Record a provenance edge. Returns False (and records nothing) if the
    database does not accept this relationship type or the write fails: the
    decision itself lives on the claims, so a missing edge loses no state."""
    try:
        existing = (
            client.table("memory_provenance").select("id")
            .eq("memory_id", memory_id).eq("parent_memory_id", parent_id).eq("relationship", relationship)
            .execute().data
            or []
        )
        if existing:
            if fields.get("resolved_at"):
                client.table("memory_provenance").update(
                    {k: v for k, v in fields.items() if k in ("resolved_at", "note", "accepted", "purpose")}
                ).eq("id", existing[0]["id"]).execute()
            return True
        client.table("memory_provenance").insert(
            {
                "memory_id": memory_id, "parent_memory_id": parent_id, "from_agent_id": agent_id, "to_agent_id": agent_id,
                "relationship": relationship, **fields,
            }
        ).execute()
        return True
    except Exception:
        return False


def _mark(client: Any, row: Dict[str, Any], status: str, marker: Dict[str, Any]) -> None:
    """Record a claim's place in a conflict on the claim itself: its
    verification status and a ``conflict`` entry in its structured fact."""
    fact = dict(row.get("structured_fact") or {})
    fact["conflict"] = marker
    client.table("agent_memories").update({"verification_status": status, "structured_fact": fact}).eq("id", row["id"]).execute()
    row["verification_status"], row["structured_fact"] = status, fact


def _prior_decision(rows: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], set]:
    """The most recent decision already recorded among these claims (by a
    person or an earlier run): its winner, and the ids of the claims decided
    against it. A later decision replaces an earlier one, so a person can
    overrule an automatic result."""
    winners = [r for r in rows if ((r.get("structured_fact") or {}).get("conflict") or {}).get("status") == "won"]
    if not winners:
        return None, set()
    winner = max(winners, key=lambda r: str(r["structured_fact"]["conflict"].get("decided_at") or ""))
    covered = {
        r["id"] for r in rows
        if ((r.get("structured_fact") or {}).get("conflict") or {}).get("status") == "lost"
        and r["structured_fact"]["conflict"].get("to") == winner["id"]
    }
    return winner, covered


def _apply(agent_id: str, report: ConflictReport, client: Any, *, task_id: Optional[str] = None) -> None:
    now = _now()
    recorded = True
    if report.status == "resolved":
        winner = report.winner
        _mark(client, winner, "verified", {"status": "won", "reason": report.reason, "manual": report.manual,
                                           "decided_at": now, "against": [l["id"] for l in report.losers]})
        for loser in report.losers:
            _mark(client, loser, "disputed", {"status": "lost", "to": winner["id"], "reason": report.reason,
                                              "manual": report.manual, "decided_at": now})
            note = f"{_describe(loser)} vs {_describe(winner)}"
            recorded &= _ensure_edge(client, loser["id"], winner["id"], "conflicts_with", agent_id, note=note,
                                     purpose="value conflict", task_id=task_id, resolved_at=now)
            recorded &= _ensure_edge(client, loser["id"], winner["id"], "resolved_by", agent_id, note=report.reason,
                                     purpose="manual decision" if report.manual else "automatic: source authority and recency",
                                     task_id=task_id, resolved_at=now, accepted=True)
    elif report.status == "needs_clarification":
        ids = [r["id"] for r in report.claims]
        for i, a in enumerate(report.claims):
            _mark(client, a, "disputed", {"status": "disputed", "with": [x for x in ids if x != a["id"]], "reason": report.reason})
            for b in report.claims[i + 1:]:
                if claim_value(a) != claim_value(b):
                    recorded &= _ensure_edge(client, a["id"], b["id"], "conflicts_with", agent_id,
                                             note=f"{_describe(a)} vs {_describe(b)}", purpose="value conflict", task_id=task_id)
    report.edges_recorded = recorded


def _active_claims(agent_id: str, client: Any, *, subject_key: Optional[str] = None, prefix: Optional[str] = None):
    query = (
        client.table("agent_memories").select("*").eq("agent_id", agent_id).eq("status", "active")
    )
    if subject_key:
        query = query.eq("subject_key", subject_key)
    elif prefix:
        query = query.like("subject_key", prefix + "%")
    return query.execute().data or []


def _report_for(agent_id: str, subject: str, rows: List[Dict[str, Any]], client: Any, authority, apply, task_id):
    claims = [r for r in rows if claim_value(r) is not None]
    if len({claim_value(r) for r in claims}) < 2:
        return ConflictReport(subject, "none", claims)
    prior, decided = _prior_decision(claims)
    if prior is not None:
        pending = [r for r in claims if r is not prior and claim_value(r) != claim_value(prior) and r["id"] not in decided]
        if not pending:
            report = ConflictReport(
                subject, "resolved", claims, winner=prior,
                losers=[r for r in claims if claim_value(r) != claim_value(prior)],
                reason="decision already recorded for this subject", manual=True,
            )
        else:
            # A claim arrived after the decision: it competes with the decided one under the normal rules.
            report = resolve_rows([prior] + pending, authority)
    else:
        report = resolve_rows(claims, authority)
    if apply:
        _apply(agent_id, report, client, task_id=task_id)
    return report


def detect_conflicts(
    agent_id: str, subject_key: str, *, authority: Optional[Dict[str, int]] = None, apply: bool = True,
    task_id: Optional[str] = None, client: Any = None,
) -> ConflictReport:
    """Check one subject. With ``apply`` the outcome is recorded (idempotent)."""
    active_client = client if client is not None else supabase
    rows = _active_claims(agent_id, active_client, subject_key=subject_key)
    return _report_for(agent_id, subject_key, rows, active_client, authority, apply, task_id)


def scan_conflicts(
    agent_id: str, subject_prefix: str, *, authority: Optional[Dict[str, int]] = None, apply: bool = True,
    task_id: Optional[str] = None, client: Any = None,
) -> List[ConflictReport]:
    """Check every subject under ``subject_prefix`` with one read."""
    active_client = client if client is not None else supabase
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in _active_claims(agent_id, active_client, prefix=subject_prefix):
        grouped.setdefault(row["subject_key"], []).append(row)
    reports = []
    for subject, rows in grouped.items():
        if len(rows) < 2:
            continue
        report = _report_for(agent_id, subject, rows, active_client, authority, apply, task_id)
        if report.conflicting:
            reports.append(report)
    return reports


def resolve_subject(
    agent_id: str, subject_key: str, winner_memory_id: str, *, resolved_by: str, note: Optional[str] = None,
    task_id: Optional[str] = None, client: Any = None,
) -> ConflictReport:
    """A person decides which claim is right for this subject. Overrides the
    rules; every other differing claim on the subject becomes a loser."""
    active_client = client if client is not None else supabase
    rows = _active_claims(agent_id, active_client, subject_key=subject_key)
    winner = next((r for r in rows if r["id"] == winner_memory_id), None)
    if winner is None:
        raise ValueError(f"{winner_memory_id} is not an active claim for {subject_key}")
    losers = [r for r in rows if claim_value(r) is not None and claim_value(r) != claim_value(winner)]
    reason = f"decided by {resolved_by}" + (f": {note}" if note else "")
    report = ConflictReport(subject_key, "resolved", rows, winner=winner, losers=losers, reason=reason, manual=True)
    _apply(agent_id, report, active_client, task_id=task_id)
    return report
