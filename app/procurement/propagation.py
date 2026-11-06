"""The agents' memory path, and the agents actually using it.

    Vendra  supplier_eligibility:<supplier>  --(coordinator relays, hop 1)-->  Procura
    Procura shared_supplier_eligibility:<s>  --(hop 2)-->                      Finora
    Procura quotation_comparison:<item>      --(hop 1)-->                      Finora

Three steps, all in live mode only (demo data never reaches the database):

1. ``relay_verdicts`` -- after Vendra runs, the coordinator shares Vendra's
   eligibility verdicts for the eligible suppliers with Procura. Each share is
   a ``derived_from`` edge tagged with the task and "relayed by coordinator".
2. ``select_eligible`` -- Procura chooses suppliers *from what it received*:
   a supplier whose verdict is stale (its source changed, or a document
   behind it expired) or not "compliant" is excluded. A supplier with no
   copy at all is kept (the pipeline's own result stands) and reported.
3. ``forward_evidence`` / ``evidence_check`` -- the evidence behind the
   recommendation is passed on to Finora, which refuses to proceed if
   Vendra's verdict it holds is no longer current.

Everything degrades safely: a share or read that fails is reported, never
raised, and never turns a good result into a failure.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from app.memory import errors
from app.memory.client import supabase
from app.memory.freshness import check_freshness
from app.memory.propagation import share_memory
from app.memory.provenance_fields import enrich_memory, enrich_share_edge
from app.memory.staleness import drop_stale_copies
from app.memory.store import get_memory_by_key

from . import progress
from .identities import FINORA, PROCURA, VENDRA

__all__ = [
    "propagation_enabled",
    "relay_verdicts",
    "select_eligible",
    "forward_evidence",
    "evidence_check",
]

VERDICT = "supplier_eligibility:"
PROCURA_COPY = "shared_supplier_eligibility:"
FINORA_COPY = "shared_shared_supplier_eligibility:"


def propagation_enabled() -> bool:
    return os.getenv("PROCUREMENT_MEMORY_PROPAGATION", "1").strip().lower() not in {"0", "false", "no", "off"}


def _active_rows(agent: str, keys: List[str]) -> Dict[str, Dict[str, Any]]:
    if not keys:
        return {}
    rows = (
        supabase.table("agent_memories").select("*").eq("agent_id", agent).eq("status", "active")
        .in_("memory_key", keys).execute().data
        or []
    )
    return {r["memory_key"]: r for r in rows}


def _share(source: Dict[str, Any], target: str, *, task_id: str, purpose: str, hop_note: str) -> Dict[str, Any]:
    copy = share_memory(source["id"], target)
    enrich_memory(
        copy["id"],
        source_type="shared_memory",
        verification_status=source.get("verification_status") or "unverified",
        sharing_classification=source.get("sharing_classification") or "shareable",
        task_id=task_id,
        change_reason=f"shared from {source['agent_id']} ({hop_note})",
    )
    enrich_share_edge(copy["id"], source["id"], task_id=task_id, purpose=purpose, accepted=True)
    return copy


def _retry(fn):
    """One retry on any error: a dropped connection should not lose a share."""

    def wrapped(item):
        try:
            return fn(item)
        except Exception:
            time.sleep(0.4)
            return fn(item)

    return wrapped


def _parallel(fn, items, workers: int = 8):
    fn = _retry(fn)
    items = list(items)
    if len(items) <= 1:
        return [fn(i) for i in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(fn, items))


# -- 1. the coordinator relays Vendra's verdicts to Procura ------------------------------------------


def relay_verdicts(supplier_names: List[str], *, task_id: str) -> Dict[str, Any]:
    """Share Vendra's verdict for each of ``supplier_names`` with Procura."""
    out: Dict[str, Any] = {"shared": [], "failed": [], "missing": []}
    try:
        rows = _active_rows(VENDRA, [VERDICT + n for n in supplier_names])
    except Exception as exc:
        progress.emit("memory", f"Could not read Vendra's verdicts to relay them ({type(exc).__name__})")
        out["failed"] = [{"supplier": n, "reason": type(exc).__name__} for n in supplier_names]
        return out

    def one(name: str):
        source = rows.get(VERDICT + name)
        if source is None:
            return name, "missing", None
        for attempt in (1, 2):
            try:
                _share(source, PROCURA, task_id=task_id, purpose="supplier eligibility, relayed by the coordinator for supplier selection",
                       hop_note="hop 1, relayed by coordinator")
                return name, "shared", None
            except errors.MemoryError as exc:  # a refusal (policy, hop limit) is deterministic: do not retry
                return name, "failed", f"{type(exc).__name__}: {exc}"
            except Exception as exc:  # a dropped connection: try once more
                if attempt == 2:
                    return name, "failed", type(exc).__name__
                time.sleep(0.4)

    for name, state, reason in _parallel(one, supplier_names):
        if state == "failed":
            out["failed"].append({"supplier": name, "reason": reason})
        else:
            out[state].append(name)
    progress.emit(
        "memory",
        f"Coordinator relayed Vendra's eligibility verdicts to Procura: {len(out['shared'])} shared"
        + (f", {len(out['failed'])} failed" if out["failed"] else "")
        + (f", {len(out['missing'])} had no verdict" if out["missing"] else ""),
    )
    return out


# -- 2. Procura selects suppliers from what it received ---------------------------------------------------


def select_eligible(candidates: List[str]) -> Dict[str, Any]:
    """Which of ``candidates`` Procura accepts, judged by its own copies of
    Vendra's verdicts. ``kept`` = candidates minus the rejected ones."""
    out: Dict[str, Any] = {"accepted": [], "rejected": [], "no_copy": [], "kept": list(candidates), "note": None}
    try:
        rows = _active_rows(PROCURA, [PROCURA_COPY + n for n in candidates])
        current = {r["id"] for r in drop_stale_copies(list(rows.values()))}
    except Exception as exc:
        out["note"] = f"selection from shared memory unavailable ({type(exc).__name__}); the pipeline's own list was used"
        progress.emit("memory", out["note"])
        return out

    for name in candidates:
        copy = rows.get(PROCURA_COPY + name)
        if copy is None:
            out["no_copy"].append(name)
        elif check_freshness(copy) == "stale":
            until = str(copy.get("valid_until") or "")[:10]
            out["rejected"].append({"supplier": name, "reason": f"the shared verdict expired{f' on {until}' if until else ''}"})
        elif copy["id"] not in current:
            out["rejected"].append({"supplier": name, "reason": "Vendra's verdict changed after it was shared (the copy is out of date)"})
        elif copy.get("verification_status") != "verified":
            out["rejected"].append({"supplier": name, "reason": f"the shared verdict is '{copy.get('verification_status')}', not compliant"})
        else:
            out["accepted"].append(name)
    rejected = {r["supplier"] for r in out["rejected"]}
    out["kept"] = [n for n in candidates if n not in rejected]
    progress.emit(
        "memory",
        f"Procura selected suppliers from its shared memory: {len(out['accepted'])} confirmed"
        + (f", {len(out['rejected'])} excluded" if out["rejected"] else "")
        + (f", {len(out['no_copy'])} without a shared verdict" if out["no_copy"] else ""),
    )
    for item in out["rejected"]:
        progress.emit("memory", f"Excluded {item['supplier']}: {item['reason']}")
    return out


# -- 3. the evidence goes on to Finora, which checks it ----------------------------------------------------


def forward_evidence(*, supplier: str, item_code: str, task_id: str) -> List[str]:
    """Pass the recommendation's evidence to Finora: Vendra's verdict (hop 2,
    Vendra -> Procura -> Finora) and the quotation comparison (hop 1)."""
    lines: List[str] = []

    def report(text: str) -> None:
        lines.append(text)
        progress.emit("memory", text)

    def step(label: str, source: Optional[Dict[str, Any]], purpose: str, hop_note: str) -> None:
        if source is None:
            report(f"Not shared: {label} (no active source memory found)")
            return
        try:
            copy = _share(source, FINORA, task_id=task_id, purpose=purpose, hop_note=hop_note)
        except errors.MemoryError as exc:
            report(f"Not shared: {label} -> Finora ({type(exc).__name__}: {exc})")
            return
        except Exception as exc:
            report(f"Not shared: {label} -> Finora ({type(exc).__name__})")
            return
        refreshed = (copy.get("version") or 1) > 1
        report(f"{label} shared {source['agent_id'].capitalize()} → Finora ({hop_note}{', refreshed: the source had changed' if refreshed else ''})")

    def active(agent: str, key: str) -> Optional[Dict[str, Any]]:
        rows = get_memory_by_key(agent, key)
        return rows[0] if rows else None

    step(f"Eligibility verdict for {supplier}", active(PROCURA, PROCURA_COPY + supplier),
         "supplier eligibility evidence for the budget and approval check", "hop 2, Vendra → Procura → Finora")
    step(f"Quotation comparison for {item_code}", active(PROCURA, f"quotation_comparison:{item_code}"),
         "the quotation ranking behind the recommended total", "hop 1")
    return lines


def evidence_check(supplier: str) -> Dict[str, Any]:
    """Finora's check that Vendra's verdict, as it holds it, is still current.
    ``status``: ``current`` | ``stale`` | ``missing`` | ``unavailable``."""
    try:
        rows = _active_rows(FINORA, [FINORA_COPY + supplier])
        copy = rows.get(FINORA_COPY + supplier)
        if copy is None:
            return {"status": "missing", "reason": "Finora holds no copy of Vendra's verdict"}
        if check_freshness(copy) == "stale":
            until = str(copy.get("valid_until") or "")[:10]
            return {"status": "stale", "reason": f"the verdict's documents expired{f' on {until}' if until else ''}"}
        if not drop_stale_copies([copy]):
            return {"status": "stale", "reason": "Vendra's verdict changed after it was shared"}
        return {"status": "current", "reason": "the verdict is current"}
    except Exception as exc:
        return {"status": "unavailable", "reason": type(exc).__name__}
