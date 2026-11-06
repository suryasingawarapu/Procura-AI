"""End-to-end verification of the memory system against the REAL Supabase.

Run:   python scripts/verify_memory_system.py            (uses the real LLM)
       python scripts/verify_memory_system.py --no-llm   (labelled extractive summaries only)
       python scripts/verify_memory_system.py --keep     (leave the test rows so you can look at them)

Every row it creates carries a unique run id (``verify_<run>``) in its key, or
belongs to a throwaway agent, so nothing real is touched. Unless --keep is
given, everything is deleted afterwards and the script confirms zero residue.
Writes nothing to ERPNext. Exit code 1 if any check FAILS (SKIP is not a
failure, but is reported honestly).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import random
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(".env")

from app.memory import errors  # noqa: E402
from app.memory.client import supabase  # noqa: E402
from app.memory.context import get_relevant_context  # noqa: E402
from app.memory.embeddings import embed_or_none  # noqa: E402
from app.memory.freshness import check_freshness  # noqa: E402
from app.memory.propagation import get_ancestry, get_descendants, share_memory  # noqa: E402
from app.memory.provenance_fields import enrich_memory, enrich_share_edge  # noqa: E402
from app.memory.search import search_memories  # noqa: E402
from app.memory.staleness import find_stale_copies, refresh_stale_copies  # noqa: E402
from app.memory.store import get_memory_by_key, save_memory  # noqa: E402
from app.memory.summaries import get_active_summary, refresh_summary  # noqa: E402
from app.procurement import chat_memory  # noqa: E402
from app.procurement.chat_router import route_chat_message  # noqa: E402
from app.procurement import propagation as memory_path  # noqa: E402

RUN = uuid.uuid4().hex[:8]
TAG = f"verify_{RUN}"
CTX_AGENT = f"verify_ctx_{RUN}"
CONV = f"{TAG}_conv"
RESULTS: list[tuple[str, str, str, str]] = []
EXTRA_KEY_PATTERNS: list[str] = []  # keys of rows created by checks that cannot embed the run tag
_section = ""


def _transient(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}"
    return any(k in text for k in ("ConnectError", "ConnectTimeout", "ReadTimeout", "RemoteProtocolError", "WinError 100"))


def run_section(fn, *args) -> None:
    """Run one section; if it dies of a transient network error, wait and run it once more
    (every section creates only tagged rows, so a second attempt is safe)."""
    for attempt in (1, 2):
        mark = len(RESULTS)
        try:
            fn(*args)
            return
        except Exception as exc:
            if attempt == 1 and _transient(exc):
                print(f"  (transient network error in {fn.__name__}: {type(exc).__name__}; retrying once)")
                del RESULTS[mark:]
                time.sleep(5)
                continue
            check(f"{fn.__name__} ran without an error", False, f"{type(exc).__name__}: {exc}")
            return


def section(title: str) -> None:
    global _section
    _section = title
    print(f"\n=== {title} ===")


def check(name: str, ok: bool, evidence: str = "", *, skip: bool = False, warn: bool = False) -> bool:
    status = "SKIP" if skip else ("PASS" if ok else ("WARN" if warn else "FAIL"))
    RESULTS.append((_section, name, status, evidence))
    print(f"  [{status}] {name}" + (f"\n         {evidence}" if evidence else ""))
    return ok


def expect_raises(exc_type, fn):
    try:
        fn()
    except exc_type as e:
        return e
    except Exception as e:  # wrong type
        return e
    return None


def fake_embedding(text: str) -> list[float]:
    """Deterministic unit vector (1536-d) derived from the text, so retrieval
    can be tested without any embedding provider."""
    rng = random.Random(int(hashlib.sha256(text.encode()).hexdigest()[:12], 16))
    v = [rng.gauss(0, 1) for _ in range(1536)]
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v]


def active(agent: str, key: str):
    rows = get_memory_by_key(agent, key)
    return rows[0] if rows else None


def edges(memory_id: str, relationship: str):
    return (
        supabase.table("memory_provenance").select("*").eq("memory_id", memory_id).eq("relationship", relationship)
        .execute().data
    )


def get_llm_or_none(disabled: bool):
    if disabled:
        return None
    try:
        from app.agents.workflow import get_llm

        return get_llm("chat_router")
    except Exception as exc:
        print(f"  (no LLM available: {type(exc).__name__}; using extractive summaries)")
        return None


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------


def versioning():
    section("1. Storage, versioning and supersession")
    key = f"{TAG}:versioning"
    v1 = save_memory("vendra", key, "ACME: documents missing")
    again = save_memory("vendra", key, "ACME: documents missing")
    check("identical content is a no-op (no new version)", again["id"] == v1["id"] and again["version"] == 1,
          f"same row id returned, version={again['version']}")
    v2 = save_memory("vendra", key, "ACME: compliant")
    rows = get_memory_by_key("vendra", key, include_superseded=True)
    check("changed content creates v2 and supersedes v1 (nothing deleted)",
          [r["version"] for r in rows] == [1, 2] and [r["status"] for r in rows] == ["superseded", "active"],
          "versions/status: " + ", ".join(f"v{r['version']}={r['status']}" for r in rows))
    edge = edges(v2["id"], "supersedes")
    check("a 'supersedes' provenance edge links v2 to v1", len(edge) == 1 and edge[0]["parent_memory_id"] == v1["id"],
          f"edge: {v2['id'][:8]} supersedes {v1['id'][:8]}")
    check("exactly one active row per key", sum(1 for r in rows if r["status"] == "active") == 1)


def provenance_tagging():
    section("2. Provenance tagging")
    key = f"{TAG}:tags"
    mem = save_memory("procura", key, "quotation ranking for LAPTOP")
    enrich_memory(mem["id"], source_type="erpnext_doctype", verification_status="verified", task_id=f"T-{RUN}",
                  change_reason="verification run", sharing_classification="shareable",
                  structured_fact={"supplier": "X", "rank": 1})
    row = active("procura", key)
    ok = (row["source_type"] == "erpnext_doctype" and row["verification_status"] == "verified"
          and row["task_id"] == f"T-{RUN}" and row["sharing_classification"] == "shareable"
          and row["structured_fact"] == {"supplier": "X", "rank": 1} and row["change_reason"] == "verification run")
    check("row tags are stored and read back (source, verification, task, classification, structured fact)", ok,
          f"source_type={row['source_type']} verification_status={row['verification_status']} task_id={row['task_id']}")

    src = save_memory("vendra", f"{TAG}:edge-src", "fact to share")
    copy = share_memory(src["id"], "procura")
    enrich_share_edge(copy["id"], src["id"], task_id=f"T-{RUN}", purpose="verification", accepted=True)
    e = edges(copy["id"], "derived_from")[0]
    check("share edge carries task, purpose and accepted flag",
          e["task_id"] == f"T-{RUN}" and e["purpose"] == "verification" and e["accepted"] is True,
          f"derived_from edge: task_id={e['task_id']} purpose={e['purpose']} accepted={e['accepted']}")


def propagation():
    section("3. Propagation path (Vendra -> Procura -> Finora)")
    src = save_memory("vendra", f"{TAG}:path", "supplier verdict: compliant")
    c1 = share_memory(src["id"], "procura")
    check("Vendra -> Procura is allowed: copy is a new row at hop 1, pointing at its source",
          c1["agent_id"] == "procura" and c1["hop_depth"] == 1 and c1["source_reference"] == src["id"],
          f"copy key={c1['memory_key']} hop_depth={c1['hop_depth']}")
    d1 = edges(c1["id"], "derived_from")
    check("the share is recorded as a 'derived_from' edge", len(d1) == 1 and d1[0]["parent_memory_id"] == src["id"])
    c2 = share_memory(c1["id"], "finora")
    check("Procura -> Finora continues the path at hop 2", c2["hop_depth"] == 2 and c2["source_reference"] == c1["id"],
          f"finora copy hop_depth={c2['hop_depth']}")
    anc = get_ancestry(c2["id"])
    check("ancestry of Finora's copy walks back to Vendra's original", any(a["memory_id"] == src["id"] for a in anc),
          "ancestry depths: " + ", ".join(f"{a.get('depth')}:{a['agent_id']}" for a in anc))
    desc = get_descendants(src["id"])
    check("descendants of Vendra's original list both copies",
          {c1["id"], c2["id"]} <= {d["memory_id"] for d in desc},
          "descendants: " + ", ".join(f"{d['agent_id']}(hop {d.get('depth')})" for d in desc))
    err = expect_raises(errors.HopLimitExceeded, lambda: share_memory(c2["id"], "procura"))
    check("hop limit stops a memory travelling too far (hop 3 refused)", isinstance(err, errors.HopLimitExceeded),
          f"{type(err).__name__}: {err}")
    err = expect_raises(errors.ShareNotAllowed, lambda: share_memory(src["id"], "coordinator"))
    check("a pair not in the policy is denied (Vendra -> Coordinator)", isinstance(err, errors.ShareNotAllowed),
          f"{type(err).__name__}: {err}")
    err = expect_raises(errors.SelfShareNotAllowed, lambda: share_memory(src["id"], "vendra"))
    check("sharing with yourself is refused", isinstance(err, errors.SelfShareNotAllowed), f"{type(err).__name__}")

    # the procurement pipeline's own memory path: relay -> select -> forward -> check
    sup, item = f"{TAG}_sup", f"{TAG}_item"
    v = save_memory("vendra", f"supplier_eligibility:{sup}", f"{sup}: compliant")
    enrich_memory(v["id"], source_type="erpnext_doctype", verification_status="verified", sharing_classification="shareable")
    save_memory("procura", f"quotation_comparison:{item}", f"{item}: {sup} ranked first")

    relay = memory_path.relay_verdicts([sup], task_id=f"T-{RUN}")
    pcopy = active("procura", f"shared_supplier_eligibility:{sup}")
    edge = edges(pcopy["id"], "derived_from")[0] if pcopy else {}
    check("the coordinator relays Vendra's verdict to Procura (hop 1), tagged as relayed by the coordinator",
          relay["shared"] == [sup] and bool(pcopy) and pcopy["hop_depth"] == 1 and "relayed by the coordinator" in (edge.get("purpose") or ""),
          f"purpose={edge.get('purpose')!r}")
    sel = memory_path.select_eligible([sup, f"{TAG}_nobody"])
    check("Procura selects the supplier from the verdict it RECEIVED; one with no verdict is kept and reported",
          sel["accepted"] == [sup] and sel["no_copy"] == [f"{TAG}_nobody"] and sup in sel["kept"], f"accepted={sel['accepted']} no_copy={len(sel['no_copy'])}")
    lines = memory_path.forward_evidence(supplier=sup, item_code=item, task_id=f"T-{RUN}")
    check("the evidence is forwarded to Finora (verdict at hop 2, comparison at hop 1), none refused",
          len(lines) == 2 and not any(l.startswith("Not shared") for l in lines), "\n         ".join(lines))
    fcopy = active("finora", f"shared_shared_supplier_eligibility:{sup}")
    check("Finora holds Vendra's verdict at hop 2, tagged with its source, verification status and task",
          bool(fcopy) and fcopy["hop_depth"] == 2 and fcopy["source_type"] == "shared_memory"
          and fcopy["verification_status"] == "verified" and fcopy["task_id"] == f"T-{RUN}",
          f"finora copy: hop_depth={fcopy and fcopy['hop_depth']} source_type={fcopy and fcopy['source_type']} "
          f"verification_status={fcopy and fcopy['verification_status']}")
    check("Finora's evidence check finds the verdict current", memory_path.evidence_check(sup)["status"] == "current")

    # the source changes after it was shared: both agents must notice
    save_memory("vendra", f"supplier_eligibility:{sup}", f"{sup}: non-compliant, GST expired")
    sel = memory_path.select_eligible([sup])
    check("when Vendra's verdict changes, Procura rejects the supplier: its copy is out of date",
          [r["supplier"] for r in sel["rejected"]] == [sup] and "changed after it was shared" in sel["rejected"][0]["reason"],
          sel["rejected"][0]["reason"] if sel["rejected"] else "not rejected")
    ev = memory_path.evidence_check(sup)
    check("...and Finora refuses to rely on its copy of that copy (found upstream)", ev["status"] == "stale", ev["reason"])

    # a verdict whose documents expire
    exp_sup = f"{TAG}_expsup"
    past = datetime.now(timezone.utc) - timedelta(days=1)
    ev_row = save_memory("vendra", f"supplier_eligibility:{exp_sup}", f"{exp_sup}: compliant, documents valid until yesterday", valid_until=past)
    enrich_memory(ev_row["id"], source_type="erpnext_doctype", verification_status="verified", sharing_classification="shareable")
    memory_path.relay_verdicts([exp_sup], task_id=f"T-{RUN}")
    sel = memory_path.select_eligible([exp_sup])
    check("a relayed verdict whose documents have expired is rejected by Procura (the expiry travels with the copy)",
          [r["supplier"] for r in sel["rejected"]] == [exp_sup] and "expired" in sel["rejected"][0]["reason"],
          sel["rejected"][0]["reason"] if sel["rejected"] else "not rejected")


def stale_detection():
    section("4. Stale memory detection")
    # -- read-time freshness (throwaway agent so results are isolated) ----------
    save_memory(CTX_AGENT, f"{TAG}:old", "first version of a fact")
    save_memory(CTX_AGENT, f"{TAG}:old", "second version of a fact")
    items = get_relevant_context(CTX_AGENT)
    contents = [i["content"] for i in items]
    check("a superseded version is excluded from retrieval, the current one is returned",
          "second version of a fact" in contents and "first version of a fact" not in contents,
          f"retrieved: {contents}")

    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=30)
    save_memory(CTX_AGENT, f"{TAG}:expired", "an expired fact", valid_until=past)
    save_memory(CTX_AGENT, f"{TAG}:valid", "a still-valid fact", valid_until=future)
    contents = [i["content"] for i in get_relevant_context(CTX_AGENT)]
    check("a memory past its valid_until is excluded; one before it is kept",
          "an expired fact" not in contents and "a still-valid fact" in contents, f"retrieved: {contents}")
    exp = active(CTX_AGENT, f"{TAG}:expired")
    check("freshness classifier reports the expired row as 'stale' (it is still stored)",
          check_freshness(exp) == "stale", f"status={exp['status']} valid_until={exp['valid_until'][:19]} -> stale")

    # -- stale shared copies across the multi-hop path --------------------------
    sup = f"{TAG}_stalesup"
    src = save_memory("vendra", f"supplier_eligibility:{sup}", f"{sup}: disputed, missing GST")
    pc = share_memory(src["id"], "procura")
    fc = share_memory(pc["id"], "finora")
    check("copies are not stale while their sources are current",
          not [i for i in find_stale_copies("procura") if sup in i["copy"]["memory_key"]]
          and not [i for i in find_stale_copies("finora") if sup in i["copy"]["memory_key"]])

    save_memory("vendra", f"supplier_eligibility:{sup}", f"{sup}: compliant, GST on file")  # the source changes
    sp = [i for i in find_stale_copies("procura") if sup in i["copy"]["memory_key"]]
    sf = [i for i in find_stale_copies("finora") if sup in i["copy"]["memory_key"]]
    check("when Vendra's verdict changes, Procura's copy is detected as stale (direct)",
          len(sp) == 1 and sp[0]["via"] == "direct", f"stale: {[i['via'] for i in sp]}")
    check("...and Finora's copy of that copy is detected as stale too (upstream)",
          len(sf) == 1 and sf[0]["via"] == "upstream", f"stale: {[i['via'] for i in sf]}")
    for agent in ("procura", "finora"):
        names = [r["memory_key"] for r in get_relevant_context(agent, limit=None) if sup in r.get("memory_key", "")]
        check(f"retrieval for {agent} no longer returns the outdated copy", names == [], f"returned copies: {names}")

    refreshed_p = [r for r in refresh_stale_copies("procura") if sup in r["memory_key"]]
    check("refreshing Procura re-shares the current verdict as a NEW VERSION of the copy",
          len(refreshed_p) == 1 and refreshed_p[0]["version"] == 2 and "compliant, GST" in refreshed_p[0]["content"],
          f"procura copy now v{refreshed_p[0]['version']}: {refreshed_p[0]['content']}" if refreshed_p else "nothing refreshed")
    check("the refresh is recorded with a 'supersedes' edge (old copy kept as history)",
          bool(refreshed_p) and len(edges(refreshed_p[0]["id"], "supersedes")) == 1)
    refreshed_f = [r for r in refresh_stale_copies("finora") if sup in r["memory_key"]]
    check("then Finora's copy can be refreshed (now a direct stale)", len(refreshed_f) == 1 and refreshed_f[0]["version"] == 2)
    left = [i for a in ("procura", "finora") for i in find_stale_copies(a) if sup in i["copy"]["memory_key"]]
    check("after refreshing the path, nothing is stale", left == [], f"stale copies left: {len(left)}")


PAN_FILE = "PAN_Card_ABCDE1234F.pdf"


def _seed(suppliers, quotations=None, item=None):
    """A seeded ERPNext (in memory only): suppliers is [(name, [file names])]."""
    return {
        "suppliers": [
            {"name": n, "supplier_name": n, "country": "India", "disabled": 0, "on_hold": 0, "source_reference": f"Supplier/{n}"}
            for n, _f in suppliers
        ],
        "supplier_documents": {n: [{"name": f"F-{n[-4:]}-{i}", "file_name": f} for i, f in enumerate(files)] for n, files in suppliers},
        "items": [{"name": item, "item_name": item, "item_group": "Test", "source_reference": f"Item/{item}"}] if item else [],
        "quotations": quotations or [],
        "budget": {"default": 1_000_000.0},
        "invoices": [], "rfqs": [], "purchase_orders": [],
    }


def _quote(supplier, item, rate, name, doc_date="2026-03-10", delivery=10):
    return {"name": name, "supplier": supplier, "item_code": item, "rate": rate, "qty": 10, "currency": "INR",
            "delivery_days": delivery, "payment_terms_days": 30, "source_reference": f"Supplier Quotation/{name}",
            "document_date": doc_date}


def expiry_checks():
    section("4c. Document expiry: a verdict is only as current as the documents behind it")
    from app.procurement.document_validity import load_recorded_validity, record_validity
    from app.procurement.mcp.client import SeededERPNextClient
    from app.procurement.subagents.vendra import check_compliance
    from app.procurement.systems import vendra as vendra_system

    today = datetime.now(timezone.utc).date()  # the code under test uses the UTC date
    future, lapsed = today + timedelta(days=90), today - timedelta(days=1)
    sup = f"{TAG}_gst"

    def client(gst_file, name=sup):
        return SeededERPNextClient(_seed([(name, [PAN_FILE, gst_file])]))

    c = check_compliance(client(f"GST_Certificate_valid_until_{future}.pdf"), sup, country="India")
    check("a document valid until a future date keeps the supplier compliant and reports the earliest expiry",
          c["compliant"] and c["earliest_expiry"] == future.isoformat(), f"earliest_expiry={c['earliest_expiry']}")
    c = check_compliance(client(f"GST_Certificate_valid_until_{lapsed}.pdf"), sup, country="India")
    check("a document past its date makes the supplier NON-compliant and names the document that expired",
          not c["compliant"] and c["expired_documents"][0]["document"] == "GST Registration Certificate" and not c["missing_documents"],
          f"expired: {c['expired_documents']}")

    record_validity(supplier=sup, document="GST Registration Certificate", valid_until=lapsed, verified_by="verify-script", source_reference="File/verify")
    recorded = load_recorded_validity().get(sup, {})
    check("a recorded validity (who verified it, until when) is stored and read back, even once it has lapsed",
          recorded.get("GST Registration Certificate") == lapsed, f"recorded: {recorded}")
    c = check_compliance(client("GST_Certificate.pdf"), sup, country="India", recorded_expiries=recorded)
    check("a lapsed recorded validity makes a file with no date in its name EXPIRED, not 'unknown'",
          not c["compliant"] and bool(c["expired_documents"]))
    record_validity(supplier=sup, document="GST Registration Certificate", valid_until=future, verified_by="verify-script")
    versions = get_memory_by_key("vendra", f"document_validity:{sup}:GST Registration Certificate", include_superseded=True)
    check("renewing the validity is a NEW VERSION; the lapsed one stays in history",
          [(v["version"], v["status"]) for v in versions] == [(1, "superseded"), (2, "active")],
          ", ".join(f"v{v['version']}={v['status']}" for v in versions))
    recorded = load_recorded_validity().get(sup, {})
    c = check_compliance(client("GST_Certificate.pdf"), sup, country="India", recorded_expiries=recorded)
    check("after renewal the supplier is compliant again, with the renewed date as its expiry",
          c["compliant"] and c["earliest_expiry"] == future.isoformat())

    # the verdict memory, written by the real Vendra system function
    sup2 = f"{TAG}_gst2"
    vendra_system.run_vendor_eligibility_check(task_id=f"T-{RUN}", correlation_id="c", client=client(f"GST_Certificate_valid_until_{future}.pdf", sup2))
    row = active("vendra", f"supplier_eligibility:{sup2}")
    check("the verdict records its exact source, a content hash and the expiry of the first document behind it",
          row["source_reference"] == f"Supplier/{sup2}" and len(row["source_hash"] or "") == 64
          and str(row["valid_until"]).startswith(future.isoformat()) and "valid until" in row["content"],
          f"source_reference={row['source_reference']} valid_until={str(row['valid_until'])[:19]} hash={str(row['source_hash'])[:12]}...")
    after = datetime.combine(future + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc)
    check("the verdict goes stale on its own once that date passes (no re-run needed), and is fresh before",
          check_freshness(row, after) == "stale" and check_freshness(row) == "fresh")
    vendra_system.run_vendor_eligibility_check(task_id=f"T-{RUN}", correlation_id="c", client=client(f"GST_Certificate_valid_until_{lapsed}.pdf", sup2))
    versions = get_memory_by_key("vendra", f"supplier_eligibility:{sup2}", include_superseded=True)
    newest = versions[-1]
    check("when the document lapses the next run records a new NON-compliant verdict and keeps the old one as history",
          [v["status"] for v in versions] == ["superseded", "active"] and "expired GST Registration Certificate" in newest["content"]
          and newest["verification_status"] == "disputed" and not newest["valid_until"],
          f"v2: {newest['content']}")


def conflict_checks():
    section("4d. Conflict detection and resolution between sources")
    from app.memory.conflicts import detect_conflicts, resolve_subject
    from app.procurement.quotation_claims import record_claim, subject_for
    from app.procurement.quotation_history import price_and_delivery_changes

    item = f"{TAG}_citem"
    sup = f"{TAG}_csup"
    subject = subject_for(item, sup)
    email = record_claim(item_code=item, supplier=sup, unit_price=50000, source_type="email", source_reference="re: laptop quote",
                         document_date=date(2026, 3, 1), recorded_by="verify-script", task_id=f"T-{RUN}")
    pdf = record_claim(item_code=item, supplier=sup, unit_price=53000, source_type="pdf", source_reference="acme_quote_v2.pdf",
                       document_date=date(2026, 3, 5), recorded_by="verify-script", task_id=f"T-{RUN}", content_sha256="ab" * 32)
    check("each claim is stored with its exact source, source type, document date, content hash and who recorded it",
          active("procura", f"quote:{item}:{sup}:pdf:acme_quote_v2.pdf")["source_reference"] == "acme_quote_v2.pdf"
          and active("procura", f"quote:{item}:{sup}:pdf:acme_quote_v2.pdf")["source_hash"] == "ab" * 32
          and active("procura", f"quote:{item}:{sup}:pdf:acme_quote_v2.pdf")["structured_fact"]["document_date"] == "2026-03-05"
          and active("procura", f"quote:{item}:{sup}:pdf:acme_quote_v2.pdf")["structured_fact"]["recorded_by"] == "verify-script",
          "source_type=pdf source_reference=acme_quote_v2.pdf document_date=2026-03-05")

    report = detect_conflicts("procura", subject, task_id=f"T-{RUN}")
    check("YOUR EXAMPLE: the email says 50,000, the updated PDF says 53,000 -> the conflict is found and the PDF is authoritative",
          report.status == "resolved" and report.winner["id"] == pdf["id"], f"{report.status}: {report.reason}")
    email_row = active("procura", f"quote:{item}:{sup}:email:re_laptop_quote")
    check("the losing claim is KEPT (active) but marked disputed; the winner is verified",
          email_row["status"] == "active" and email_row["verification_status"] == "disputed"
          and active("procura", f"quote:{item}:{sup}:pdf:acme_quote_v2.pdf")["verification_status"] == "verified")
    marker = email_row["structured_fact"]["conflict"]
    check("the decision is recorded on the claims themselves: who won, why, and when",
          marker["status"] == "lost" and marker["to"] == pdf["id"] and "outranks email" in marker["reason"]
          and active("procura", f"quote:{item}:{sup}:pdf:acme_quote_v2.pdf")["structured_fact"]["conflict"]["status"] == "won",
          f"reason: {marker['reason']}")
    EDGE_HINT = ("this database refuses conflicts_with / resolved_by edges (its relationship rule is narrower than "
                 "migration 001 intended): apply migrations/008_allow_conflict_relationships.sql. Decisions are kept on the claims meanwhile")
    conflict_edge = edges(email["id"], "conflicts_with")
    resolved_edge = edges(email["id"], "resolved_by")
    if report.edges_recorded:
        check("the conflict and its resolution are ALSO recorded as provenance edges (conflicts_with + resolved_by)",
              len(conflict_edge) == 1 and conflict_edge[0]["resolved_at"] and len(resolved_edge) == 1
              and resolved_edge[0]["parent_memory_id"] == pdf["id"] and resolved_edge[0]["accepted"] is True,
              f"note: {conflict_edge[0]['note'] if conflict_edge else None}")
    else:
        check("the conflict and its resolution are ALSO recorded as provenance edges (conflicts_with + resolved_by)", False, EDGE_HINT, warn=True)
    again = detect_conflicts("procura", subject)
    check("running detection again changes nothing (idempotent)",
          again.status == "resolved" and again.winner["id"] == pdf["id"]
          and len(edges(email["id"], "conflicts_with")) == len(conflict_edge) and len(edges(email["id"], "resolved_by")) == len(resolved_edge))

    # equally authoritative sources, no date order -> a person must decide
    sup2 = f"{TAG}_csup2"
    subject2 = subject_for(item, sup2)
    a = record_claim(item_code=item, supplier=sup2, unit_price=50000, source_type="pdf", source_reference="a.pdf", document_date=date(2026, 3, 1))
    b = record_claim(item_code=item, supplier=sup2, unit_price=53000, source_type="pdf", source_reference="b.pdf", document_date=date(2026, 3, 1))
    rep2 = detect_conflicts("procura", subject2)
    both_disputed = all(active("procura", k)["verification_status"] == "disputed" and active("procura", k)["structured_fact"]["conflict"]["status"] == "disputed"
                        for k in (f"quote:{item}:{sup2}:pdf:a.pdf", f"quote:{item}:{sup2}:pdf:b.pdf"))
    unresolved = [e for m in (a, b) for e in edges(m["id"], "conflicts_with")]
    check("two equally authoritative sources with no clear newer one -> NEEDS CLARIFICATION (nothing decided silently)",
          rep2.status == "needs_clarification" and both_disputed and all(not e["resolved_at"] for e in unresolved), rep2.reason[:120])
    resolve_subject("procura", subject2, a["id"], resolved_by="verify-script", note="a.pdf is the signed copy")
    rep3 = detect_conflicts("procura", subject2)
    check("a person's decision is recorded and sticks on the next check",
          rep3.status == "resolved" and rep3.winner["id"] == a["id"] and rep3.manual)
    c = record_claim(item_code=item, supplier=sup2, unit_price=57000, source_type="pdf", source_reference="c.pdf", document_date=date(2026, 3, 20))
    rep4 = detect_conflicts("procura", subject2)
    check("a newer formal document arriving later competes under the normal rules and wins", rep4.winner and rep4.winner["id"] == c["id"])

    # versioning: your 15 -> 25 days example
    sup3 = f"{TAG}_csup3"
    for days in (15, 25):
        record_claim(item_code=item, supplier=sup3, unit_price=50000, source_type="erpnext_doctype",
                     source_reference="Supplier Quotation/VERIFY-Q9", delivery_days=days)
    key = f"quote:{item}:{sup3}:erpnext_doctype:Supplier_Quotation_VERIFY-Q9"
    versions = get_memory_by_key("procura", key, include_superseded=True)
    changes = price_and_delivery_changes(versions)
    check("YOUR EXAMPLE: delivery time changes 15 -> 25 days: the new value is current, the old one stays in history",
          [(v["version"], v["status"], v["structured_fact"]["delivery_days"]) for v in versions] == [(1, "superseded", 15), (2, "active", 25)]
          and any(ch["field"] == "delivery_days" and (ch["from"], ch["to"]) == (15, 25) for ch in changes),
          "; ".join(f"v{v['version']}={v['status']} ({v['structured_fact']['delivery_days']} days)" for v in versions))


def pipeline_end_to_end():
    section("4e. The whole live pipeline, end to end (real agents + real memory; seeded ERPNext, nothing reaches the real one)")
    from unittest.mock import patch

    from app.memory.conflicts import resolve_subject
    from app.memory.summaries import scoped_context
    from app.procurement import coordinator as coord
    from app.procurement import progress, quotation_history, task_orchestrator, workflow
    from app.procurement.coordinator import run_coordinator
    from app.procurement.mcp.client import SeededERPNextClient
    from app.procurement.messages import TaskRequest
    from app.procurement.quotation_claims import record_claim

    item = f"{TAG}_pitem"
    s1, s2, s3 = f"{TAG}_s1", f"{TAG}_s2", f"{TAG}_s3"
    today = datetime.now(timezone.utc).date()
    future = today + timedelta(days=120)
    lapsed = today - timedelta(days=2)
    seed = _seed(
        [(s1, [PAN_FILE, f"GST_Certificate_valid_until_{future}.pdf"]),   # compliant, expires in 120 days
         (s2, [PAN_FILE, f"GST_Certificate_valid_until_{lapsed}.pdf"]),   # GST EXPIRED (and the cheapest quote)
         (s3, [PAN_FILE, "GST_Certificate.pdf"])],                        # compliant, no expiry known
        [_quote(s1, item, 100, "VQ1"), _quote(s2, item, 70, "VQ2"), _quote(s3, item, 90, "VQ3")], item,
    )
    client = SeededERPNextClient(seed)
    # sources that disagree with ERPNext: an OLDER email for s1; a PDF for s3 with the SAME date as its ERPNext quotation
    record_claim(item_code=item, supplier=s1, unit_price=95, source_type="email", source_reference="email about s1",
                 document_date=date(2026, 3, 1), recorded_by="verify-script")
    record_claim(item_code=item, supplier=s3, unit_price=85, source_type="pdf", source_reference="s3_quote.pdf",
                 document_date=date(2026, 3, 10), recorded_by="verify-script")

    pr = workflow.create_purchase_request(
        requested_by="verify@test", item_description="verification item", quantity=10, estimated_budget=5_000_000.0,
        delivery_location="Nowhere", required_date="2026-12-31", business_justification="verification run", mode="live",
    )
    EXTRA_KEY_PATTERNS.append(pr.pr_id)
    pr.item_code, pr.workflow_state = item, "Running"
    events: list[tuple[str, str]] = []
    env = {"LIVE_MODE_AUTO_APPROVE": "0", "PROCUREMENT_MEMORY_PROPAGATION": "1"}

    def patches():
        return (
            patch("app.procurement.task_orchestrator._resolve_client", return_value=(client, None)),
            patch.object(coord, "_ai_summary_llm", return_value=None),
            patch.dict(os.environ, env),
            patch.object(quotation_history, "_spawn", side_effect=lambda fn, *a: fn(*a)),
        )

    def run(fn):
        p1, p2, p3, p4 = patches()
        with p1, p2, p3, p4, progress.capture(lambda st, t: events.append((st, t))):
            return fn()

    out = run(lambda: task_orchestrator._run_pipeline(pr))
    cr = out.coordinator_result
    eligible = cr["result"]["eligible_supplier_names"]
    check("Vendra excludes the supplier whose GST certificate has expired, even though it quoted the lowest price",
          s2 not in eligible and set(eligible) == {s1, s3}, f"eligible: {[e.replace(TAG + '_', '') for e in eligible]}")
    relay_line = next((t for st, t in events if "relayed Vendra's eligibility verdicts" in t), "")
    select_line = next((t for st, t in events if "selected suppliers from its shared memory" in t), "")
    check("the coordinator relays Vendra's verdicts to Procura, and Procura selects its suppliers from what it received",
          "2 shared" in relay_line and "2 confirmed" in select_line, f"{relay_line}\n         {select_line}")
    procura_msg = next(m for m in cr["messages"] if m["sender"] == "procura")
    degraded = procura_msg["payload"]["reconciliation"].get("note")
    if degraded:  # the safe fallback engaged (a transient error): that is not what is being verified -- retry the section
        raise ConnectionError(f"ConnectError: reconciliation degraded: {degraded}")
    resolved = procura_msg["payload"]["reconciliation"]["resolved"]
    check("an older email that disagrees with the ERPNext quotation is resolved automatically (ERPNext wins) and marked disputed",
          any(r["supplier"] == s1 and r["status"] == "resolved" for r in resolved)
          and active("procura", f"quote:{item}:{s1}:email:email_about_s1")["verification_status"] == "disputed", resolved[0]["reason"] if resolved else "")
    check("a conflict that could change the winner makes the task WAIT FOR CLARIFICATION (not block, not guess)",
          out.workflow_state == "Waiting For Clarification" and cr["result"]["conflicts"] and cr["result"]["recommended_supplier"] is None,
          f"state={out.workflow_state}; {out.blockers[0][:140]}")
    conflict = cr["result"]["conflicts"][0]
    check("the question carries both claims with their sources, ready for the user to choose",
          conflict["supplier"] == s3 and sorted(c["source_type"] for c in conflict["claims"]) == ["erpnext_doctype", "pdf"],
          "; ".join(f"{c['source_type']} {c['value']}" for c in conflict["claims"]))

    # the person decides in the chat; the run resumes
    winner = next(c for c in conflict["claims"] if c["source_type"] == "erpnext_doctype")["memory_id"]
    resolve_subject("procura", conflict["subject_key"], winner, resolved_by="verify-script")
    out2 = run(lambda: task_orchestrator.resume_procurement_task(pr.pr_id))
    cr2 = out2.coordinator_result
    check("after the decision the task resumes, recommends the cheapest eligible supplier and waits for approval",
          out2.workflow_state == "Waiting For Approval" and cr2["result"]["recommended_supplier"] == s3
          and cr2["result"]["recommended_total_cost"] == 900.0,
          f"state={out2.workflow_state} recommended={str(cr2['result']['recommended_supplier']).replace(TAG + '_', '')} total={cr2['result']['recommended_total_cost']}")
    path = cr2["result"]["memory_path"]
    check("Finora checked the evidence it holds before approving, and found it current", path["evidence_check"]["status"] == "current", path["evidence_check"]["reason"])
    pcopy = active("procura", f"shared_supplier_eligibility:{s3}")
    fcopy = active("finora", f"shared_shared_supplier_eligibility:{s3}")
    check("the path is recorded as data: Vendra -> Procura (hop 1, relayed by the coordinator) -> Finora (hop 2)",
          bool(pcopy and fcopy) and pcopy["hop_depth"] == 1 and fcopy["hop_depth"] == 2
          and "relayed by the coordinator" in (edges(pcopy["id"], "derived_from")[0]["purpose"] or ""),
          f"procura copy hop {pcopy and pcopy['hop_depth']}, finora copy hop {fcopy and fcopy['hop_depth']}")
    cmp_row = active("procura", f"quotation_comparison:{item}")
    check("provenance is first-class: the comparison names the exact quotation documents it used and carries a content hash",
          "Supplier Quotation/VQ1" in (cmp_row["source_reference"] or "") and "Supplier Quotation/VQ3" in cmp_row["source_reference"]
          and len(cmp_row["source_hash"] or "") == 64, f"source_reference={cmp_row['source_reference']}")
    erp_claim = active("procura", f"quote:{item}:{s3}:erpnext_doctype:Supplier_Quotation_VQ3")
    check("every ERPNext quotation was kept as a claim: exact document, date, hash, verified",
          erp_claim["source_reference"] == "Supplier Quotation/VQ3" and len(erp_claim["source_hash"] or "") == 64
          and erp_claim["structured_fact"]["document_date"] == "2026-03-10" and erp_claim["verification_status"] == "verified")

    # a price changes in ERPNext -> history shows the change; the summary then gets used
    for row in client._data["quotations"]:
        if row["supplier"] == s1:
            row["rate"] = 110
    task = TaskRequest(description="Find eligible suppliers, compare quotations and check the budget.")

    def coordinator_run():
        return run_coordinator(task, client, item_code=item, quantity=10, use_memory=True)

    r3 = run(coordinator_run)
    hist3 = next(m for m in r3.messages if m.sender == "procura").payload["specialists"]["quotation_history"]["data"]
    price_change = next((c for c in hist3["changes"] if c["field"] == "price" and c["supplier"] == s1), None)
    check("Procura's quotation history opens the supporting records and reports the change: a price moved 100 -> 110",
          bool(price_change) and (price_change["from"], price_change["to"], price_change["change_pct"]) == (100.0, 110.0, 10.0),
          f"{hist3['records_opened']} records opened; change: {price_change}")
    ctx = scoped_context("procura", f"quote:{item}:")
    check("a summary of the item's quotation claims was written in the background", ctx["summary"] is not None,
          f"summary: {(ctx['summary'] or {}).get('summary', '')[:140]}")
    r4 = run(coordinator_run)
    hist4 = next(m for m in r4.messages if m.sender == "procura").payload["specialists"]["quotation_history"]["data"]
    check("SUMMARY FIRST: the next run reads the stored summary, then only the claims newer than it, then opens the supporting records",
          hist4["summary_used"] and hist4["summary_state"] in ("fresh", "partial") and hist4["records_read_after_summary"] < hist4["claims_in_scope"],
          f"summary used ({hist4['summary_state']}); {hist4['records_read_after_summary']} of {hist4['claims_in_scope']} claims read after it; {hist4['records_opened']} supporting records opened")

    # Procura-only task: nothing relayed by a Vendra run, but the verdicts still decide
    only = TaskRequest(description="Compare quotations and prepare the purchase for approval.", requested_systems=["procura"])
    r5 = run(lambda: run_coordinator(only, client, item_code=item, quantity=10, use_memory=True))
    sel = r5.result["memory_path"]["selection"]
    check("a Procura-only task still excludes the supplier with the expired document, from the verdict it received",
          s2 in [r["supplier"] for r in sel["rejected"]] and sel["vendra_ran"] is False,
          "; ".join(f"{r['supplier'].replace(TAG + '_', '')}: {r['reason']}" for r in sel["rejected"]))


def summaries(llm):
    section("5. Summary of memories and retrieval through the summary")
    facts = [
        ("supplier_a", "Clearline Hardware Partners is compliant; quoted INR 50,600 per laptop"),
        ("supplier_b", "Apex Micro Distributors is non-compliant: GST certificate missing"),
        ("supplier_c", "Silverline Technologies is compliant; quoted INR 51,700 per laptop"),
        ("budget", "Budget for IT hardware is INR 11,00,000"),
        ("policy", "Purchases above INR 5,00,000 need director approval"),
    ]
    for k, c in facts:
        save_memory(CTX_AGENT, f"{TAG}:sum:{k}", c)
    # the earlier stale-detection section added memories to this agent too; isolate with a prefix scope
    scope = f"{TAG}:sum:"
    s1 = refresh_summary(CTX_AGENT, scope_key_prefix=scope, llm=llm)
    used_llm = llm is not None and not s1["summary"].startswith("(extractive")
    check("a summary is written, recording which memories it covers and how far in time it reaches",
          s1 is not None and len(s1["memory_ids"]) == 5 and s1["covered_observed_at"] and s1["status"] == "active",
          f"covers {len(s1['memory_ids'])} memories, covered_observed_at={str(s1['covered_observed_at'])[:19]}")
    print(f"         summary text ({'LLM' if used_llm else 'extractive fallback'}): {s1['summary'][:240]}")
    check("the summary is grounded in the memories (mentions their identifiers)",
          any(w in s1["summary"] for w in ("Clearline", "Apex", "Silverline")),
          "found a supplier name from the memories in the summary text")
    if llm is not None:
        check("summary was produced by the LLM (not the extractive fallback)", used_llm, warn=True)

    again = refresh_summary(CTX_AGENT, scope_key_prefix=scope, llm=llm)
    check("an up-to-date summary is reused, not rewritten", again["id"] == s1["id"])

    items = get_relevant_context(CTX_AGENT)
    summary_rows = [i for i in items if "summary" in i]
    check("retrieval returns the summary first (summary-first)", bool(items) and "summary" in items[0],
          f"first item is a {'summary' if items and 'summary' in items[0] else 'memory'} row")

    time.sleep(0.05)
    save_memory(CTX_AGENT, f"{TAG}:sum:new", "A new quote arrived: Gracewood quoted INR 49,900")
    items = get_relevant_context(CTX_AGENT)
    contents = [i.get("summary") or i["content"] for i in items]
    check("a memory newer than the summary is returned alongside it (summary + delta)",
          any("Gracewood" in c for c in contents) and any(i.get("id") == s1["id"] for i in items),
          f"{len(summary_rows)} summary + {len(items) - len(summary_rows)} newer memories")

    s2 = refresh_summary(CTX_AGENT, scope_key_prefix=scope, llm=llm)
    check("refreshing folds the new memory into a new summary and retires the old one",
          s2["id"] != s1["id"] and len(s2["memory_ids"]) == 6
          and get_active_summary(CTX_AGENT, containing_memory_id=s2["memory_ids"][0])["id"] == s2["id"],
          f"new summary covers {len(s2['memory_ids'])} memories; old summary status -> superseded")

    save_memory(CTX_AGENT, f"{TAG}:sum:supplier_b", "Apex Micro Distributors is now compliant: GST certificate received")
    items = get_relevant_context(CTX_AGENT)
    check("when a memory the summary covers changes, the summary is STALE and is not used",
          not any(i.get("id") == s2["id"] for i in items) and any("now compliant" in i.get("content", "") for i in items),
          f"retrieved {len(items)} current memories instead of the outdated summary")
    s3 = refresh_summary(CTX_AGENT, scope_key_prefix=scope, llm=llm)
    old_b = [m["id"] for m in get_memory_by_key(CTX_AGENT, f"{TAG}:sum:supplier_b", include_superseded=True) if m["status"] == "superseded"]
    check("refreshing rebuilds the summary from current memories (the superseded fact is no longer covered)",
          s3["id"] != s2["id"] and not (set(old_b) & set(s3["memory_ids"])),
          f"rebuilt summary covers {len(s3['memory_ids'])} memories, none superseded")


def chat_memory_checks(llm):
    section("6. Chat conversation memory: store -> summarize -> retrieve through the summary")
    turns = [
        ("I usually buy office chairs and my preferred supplier is Gracewood Supplies Ltd.", "Noted: you prefer Gracewood Supplies Ltd for office chairs."),
        ("What is an RFQ?", "A request for quotation asks suppliers to price an item."),
        ("Our Mumbai office budget is 4 lakh this quarter.", "Understood, a 4 lakh budget for the Mumbai office."),
        ("Delivery should be within 15 days.", "Got it: delivery within 15 days."),
        ("Thanks.", "You're welcome."),
    ]
    for u, a in turns:
        chat_memory.record_turn(CONV, u, a, task_id=f"T-{RUN}")
    stored = supabase.table("agent_memories").select("memory_key,source_type,verification_status,task_id").eq("agent_id", "coordinator").like("memory_key", f"chat:{CONV}:%").execute().data
    check("each chat turn is stored as a memory, tagged source=chat and with its task",
          len(stored) == 5 and all(r["source_type"] == "chat" and r["task_id"] == f"T-{RUN}" for r in stored),
          f"{len(stored)} turn memories, e.g. key={stored[0]['memory_key']}")
    ctx = chat_memory.conversation_context(CONV)
    check("before any summary exists, context is the recent turns verbatim",
          ctx["summary"] is None and len(ctx["recent"]) == 5)
    summary = chat_memory.maybe_summarize(CONV, llm=llm)
    check("after enough turns the conversation is summarized", summary is not None and len(summary["memory_ids"]) == 5,
          f"summary covers {len(summary['memory_ids']) if summary else 0} turns")
    if summary:
        print(f"         conversation summary: {summary['summary'][:240]}")
    ctx = chat_memory.conversation_context(CONV)
    check("context now = the summary with no raw turns (nothing newer)",
          bool(ctx["summary"]) and ctx["recent"] == [] and ctx["summary_state"] == "fresh", f"state={ctx['summary_state']}")
    chat_memory.record_turn(CONV, "Which chairs did the last order use?", "I don't have an order on record.")
    ctx = chat_memory.conversation_context(CONV)
    check("a turn after the summary appears next to it (summary + recent)",
          bool(ctx["summary"]) and len(ctx["recent"]) == 1 and ctx["summary_state"] == "partial",
          f"state={ctx['summary_state']}, {len(ctx['recent'])} newer turn")

    prompt_block = chat_memory.format_for_prompt(chat_memory.conversation_context(CONV))
    check("the router prompt receives the summary", "Summary of earlier turns:" in (prompt_block or ""))
    if llm is not None:
        reply = ""
        for _attempt in range(3):  # a transient provider error falls back to a canned reply; retry before judging
            decision = route_chat_message(
                message="Which supplier did I say I prefer for chairs?",
                history=[],  # nothing from the browser: the answer can only come from stored memory
                llm=llm,
                memory_context=prompt_block,
            )
            reply = decision.reply or ""
            if "gracewood" in reply.lower():
                break
            time.sleep(2)
        if "I'm not sure what you need yet" in reply:  # the router's canned fallback: the model could not be reached
            check("RETRIEVAL THROUGH THE SUMMARY: the router answers a question only the stored summary can answer", False,
                  "the language model was unreachable on all 3 attempts (the router returned its canned fallback), so this could "
                  "not be verified this run: re-run", skip=True)
        else:
            check("RETRIEVAL THROUGH THE SUMMARY: the router answers a question only the stored summary can answer",
                  "gracewood" in reply.lower(), f"asked with no browser history; reply: {reply[:200]}")
    else:
        check("retrieval through the summary with the LLM", False, "needs the LLM (run without --no-llm)", skip=True)

    deleted = chat_memory.delete_conversation(CONV)
    left = supabase.table("agent_memories").select("id").eq("agent_id", "coordinator").like("memory_key", f"chat:{CONV}:%").execute().data
    left_s = supabase.table("agent_memory_summaries").select("id").eq("agent_id", "coordinator").execute().data
    check("deleting the conversation removes its turns and summaries from the database",
          deleted["turns"] == 6 and deleted["summaries"] >= 1 and left == [],
          f"deleted {deleted['turns']} turns and {deleted['summaries']} summaries; {len(left)} turns remain")


def vector_search():
    section("7. Vector search (pgvector)")
    a = save_memory(CTX_AGENT, f"{TAG}:vec:a", "vector doc A", embedding=fake_embedding("A"))
    save_memory(CTX_AGENT, f"{TAG}:vec:b", "vector doc B", embedding=fake_embedding("B"))
    hits = search_memories(CTX_AGENT, fake_embedding("A"), match_threshold=0.9, match_count=5)
    check("a stored embedding is retrievable by similarity; unrelated vectors are not returned",
          [h["content"] for h in hits] == ["vector doc A"] and hits[0]["similarity"] > 0.99,
          f"hits: {[(h['content'], round(h['similarity'], 3)) for h in hits]}")
    stored = supabase.table("agent_memories").select("id", count="exact").eq("id", a["id"]).not_.is_("embedding", "null").execute()
    check("the embedding is persisted in the vector column", stored.count == 1)
    live = embed_or_none("probe")
    if live is None:
        check("live embedding generation for new memories", False,
              "no embedding provider configured (OPENAI_API_KEY is not set), so memories saved by the agents have no "
              "embedding and are found by key/recency, not similarity", skip=True)
    else:
        check("live embedding generation for new memories", len(live) == 1536, f"{len(live)} dimensions")


def real_data_audit():
    section("8. Audit of the real data (read-only)")
    rows = supabase.table("agent_memories").select("agent_id,memory_key,status").eq("status", "active").execute().data
    rows = [r for r in rows if TAG not in r["memory_key"]]
    from collections import Counter

    dupes = [k for k, n in Counter((r["agent_id"], r["memory_key"]) for r in rows).items() if n > 1]
    check("no (agent, key) has more than one active version", dupes == [], f"{len(rows)} active rows checked")
    proc = supabase.table("agent_memories").select("agent_id,memory_key,source_type,verification_status,task_id,created_at").in_(
        "agent_id", ["vendra", "procura", "finora"]).eq("status", "active").execute().data
    proc = [r for r in proc if TAG not in r["memory_key"] and not any(p in r["memory_key"] for p in EXTRA_KEY_PATTERNS)]  # this run's scratch rows are not real data
    # Vendor master records are master data, not the product of a task, so a task link does not apply to them.
    needs_task = [r for r in proc if not r["memory_key"].startswith("vendor:")]
    no_source = [r for r in proc if not r["source_type"]]
    no_task = [r for r in needs_task if not r["task_id"]]
    check("every procurement memory has a source type", not no_source,
          f"{len(proc) - len(no_source)}/{len(proc)} active Vendra/Procura/Finora rows have source_type")
    since = "2026-11-02"  # tagging of PR / approval rows with their task id was completed on this date
    recent_no_task = [r for r in no_task if r["created_at"][:10] >= since]
    check("every procurement memory written since the task-id fix is linked to its task", not recent_no_task,
          f"{len(recent_no_task)} rows since {since} lack task_id")
    check("older rows without a task link (written before the fix)", not no_task,
          f"{len(no_task)} of {len(needs_task)} task-bound rows from before {since} have no task_id "
          "(left as they are: history is never rewritten)", warn=True)
    sup = supabase.table("agent_memories").select("id", count="exact").eq("status", "superseded").execute()
    edge = supabase.table("memory_provenance").select("id", count="exact").eq("relationship", "supersedes").execute()
    check("every superseded row has a 'supersedes' edge (no silent history loss)", sup.count <= edge.count,
          f"{sup.count} superseded rows, {edge.count} supersedes edges")


# ---------------------------------------------------------------------------


def _my_memory_filter() -> str:
    parts = [f"memory_key.like.*{TAG}*", f"agent_id.eq.{CTX_AGENT}"] + [f"memory_key.like.*{p}*" for p in EXTRA_KEY_PATTERNS]
    return ",".join(parts)


def cleanup() -> int:
    """Delete exactly the rows this run created: dependants first."""
    ids = [r["id"] for r in supabase.table("agent_memories").select("id").or_(_my_memory_filter()).execute().data]
    if ids:
        idset = set(ids)
        found = (
            supabase.table("agent_memory_summaries").select("id,memory_ids")
            .execute().data
        )
        summary_ids = [s["id"] for s in found if set(s.get("memory_ids") or []) & idset]
        if summary_ids:
            supabase.table("agent_memory_summaries").delete().in_("id", summary_ids).execute()
        for column in ("memory_id", "parent_memory_id"):
            for i in range(0, len(ids), 40):
                supabase.table("memory_provenance").delete().in_(column, ids[i:i + 40]).execute()
        for i in range(0, len(ids), 40):
            supabase.table("agent_memories").delete().in_("id", ids[i:i + 40]).execute()
    supabase.table("agent_memory_summaries").delete().eq("agent_id", CTX_AGENT).execute()
    supabase.table("memory_agents").delete().eq("agent_id", CTX_AGENT).execute()
    return supabase.table("agent_memories").select("id", count="exact").or_(_my_memory_filter()).execute().count


def cleanup_leftovers() -> int:
    """Delete everything any verification run (this or an earlier one, including one that was
    interrupted) left behind: rows tagged ``verify_...``, scratch agents, and the scratch purchase requests."""
    ids = [r["id"] for r in supabase.table("agent_memories").select("id").or_(
        "memory_key.like.*verify_*,agent_id.like.verify_*,content.like.*verification item*").execute().data]
    print(f"leftover verification memories found: {len(ids)}")
    if ids:
        idset = set(ids)
        found = supabase.table("agent_memory_summaries").select("id,memory_ids").execute().data
        summary_ids = [x["id"] for x in found if set(x.get("memory_ids") or []) & idset]
        if summary_ids:
            supabase.table("agent_memory_summaries").delete().in_("id", summary_ids).execute()
        for column in ("memory_id", "parent_memory_id"):
            for i in range(0, len(ids), 40):
                supabase.table("memory_provenance").delete().in_(column, ids[i:i + 40]).execute()
        for i in range(0, len(ids), 40):
            supabase.table("agent_memories").delete().in_("id", ids[i:i + 40]).execute()
    for agent in supabase.table("memory_agents").select("agent_id").like("agent_id", "verify_%").execute().data:
        supabase.table("agent_memory_summaries").delete().eq("agent_id", agent["agent_id"]).execute()
        supabase.table("memory_agents").delete().eq("agent_id", agent["agent_id"]).execute()
    left = supabase.table("agent_memories").select("id", count="exact").or_(
        "memory_key.like.*verify_*,agent_id.like.verify_*,content.like.*verification item*").execute().count
    print(f"remaining after cleanup: {left}")
    return 0 if left == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup-leftovers", action="store_true",
                        help="only delete rows left by earlier (possibly interrupted) verification runs, then exit")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--keep", action="store_true", help="do not delete the test rows afterwards")
    args = parser.parse_args()
    if args.cleanup_leftovers:
        return cleanup_leftovers()

    print(f"Memory system verification  run={RUN}  (real Supabase, throwaway data tagged '{TAG}')")
    llm = get_llm_or_none(args.no_llm)
    supabase.table("memory_agents").insert({"agent_id": CTX_AGENT, "role": "agent", "display_name": "verification scratch agent"}).execute()
    try:
        for fn in (versioning, provenance_tagging, propagation, stale_detection, expiry_checks, conflict_checks, pipeline_end_to_end):
            run_section(fn)
        for fn in (summaries, chat_memory_checks):
            run_section(fn, llm)
        for fn in (vector_search, real_data_audit):
            run_section(fn)
    finally:
        if args.keep:
            print(f"\n--keep: test rows left in place. Look at them with:\n"
                  f"  select agent_id, memory_key, version, status, hop_depth from agent_memories where memory_key like '%{TAG}%' or agent_id = '{CTX_AGENT}' order by agent_id, memory_key, version;\n"
                  f"Remove them later by re-running without --keep after setting the run id, or delete by that key pattern.")
        else:
            left = cleanup()
            check("cleanup removed every test row (zero residue)", left == 0, f"{left} rows remain")

    passed = sum(1 for r in RESULTS if r[2] == "PASS")
    failed = [r for r in RESULTS if r[2] == "FAIL"]
    skipped = [r for r in RESULTS if r[2] == "SKIP"]
    warned = [r for r in RESULTS if r[2] == "WARN"]
    print(f"\n{'=' * 60}\nRESULT: {passed} passed, {len(failed)} failed, {len(warned)} warnings, {len(skipped)} skipped")
    for s, n, st, ev in failed:
        print(f"  FAILED  [{s}] {n}\n          {ev}")
    for s, n, st, ev in warned:
        print(f"  WARNING [{s}] {n}\n          {ev}")
    for s, n, st, ev in skipped:
        print(f"  SKIPPED [{s}] {n}\n          {ev}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
