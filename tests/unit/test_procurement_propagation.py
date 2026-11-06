"""The agents' memory path: the coordinator relays Vendra's verdicts, Procura
selects from what it received, Finora checks the evidence, and the
orchestrator turns a price conflict into a question for the user."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.memory import errors
from app.procurement import coordinator as coordinator_module
from app.procurement import progress, propagation, task_orchestrator, workflow
from app.procurement.coordinator import run_coordinator
from app.procurement.mcp.client import SeededERPNextClient
from app.procurement.messages import TaskRequest, TaskResult

P = "app.procurement.propagation"
PAST = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
FUTURE = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()


def _row(agent, key, **extra):
    row = {"id": f"id-{agent}-{key}", "agent_id": agent, "memory_key": key, "status": "active",
           "verification_status": "verified", "sharing_classification": "shareable", "version": 1}
    row.update(extra)
    return row


# -- 1. the coordinator relays Vendra's verdicts ------------------------------------------------------------


@pytest.fixture
def sharing():
    with patch(f"{P}.share_memory", side_effect=lambda sid, target, **k: _row(target, "shared_x", version=1)) as share, patch(
        f"{P}.enrich_memory"
    ) as enrich, patch(f"{P}.enrich_share_edge") as edge:
        yield {"share": share, "enrich": enrich, "edge": edge}


def test_the_relay_shares_each_suppliers_verdict_with_procura_tagged_as_relayed_by_the_coordinator(sharing):
    rows = {f"supplier_eligibility:{n}": _row("vendra", f"supplier_eligibility:{n}") for n in ("A", "B")}
    with patch(f"{P}._active_rows", return_value=rows):
        out = propagation.relay_verdicts(["A", "B"], task_id="PR-1")

    assert sorted(out["shared"]) == ["A", "B"] and out["failed"] == [] and out["missing"] == []
    targets = {c.args[1] for c in sharing["share"].call_args_list}
    assert targets == {"procura"}
    edge = sharing["edge"].call_args.kwargs
    assert "relayed by the coordinator" in edge["purpose"] and edge["task_id"] == "PR-1" and edge["accepted"] is True
    assert sharing["enrich"].call_args.kwargs["source_type"] == "shared_memory"


def test_the_relay_reports_missing_and_refused_shares_without_raising(sharing):
    rows = {"supplier_eligibility:A": _row("vendra", "supplier_eligibility:A"), "supplier_eligibility:C": _row("vendra", "supplier_eligibility:C")}
    sharing["share"].side_effect = lambda sid, target, **k: (_ for _ in ()).throw(errors.ShareNotAllowed("no")) if "C" in sid else _row(target, "x")
    seen = []
    with patch(f"{P}._active_rows", return_value=rows), progress.capture(lambda s, t: seen.append(t)):
        out = propagation.relay_verdicts(["A", "B", "C"], task_id="PR-1")
    assert out["shared"] == ["A"] and out["missing"] == ["B"] and out["failed"][0]["supplier"] == "C"
    assert seen and "1 shared, 1 failed, 1 had no verdict" in seen[0]


def test_the_relay_survives_the_database_being_unreachable(sharing):
    with patch(f"{P}._active_rows", side_effect=ConnectionError("down")):
        out = propagation.relay_verdicts(["A"], task_id="PR-1")
    assert out["shared"] == [] and out["failed"][0]["reason"] == "ConnectionError"


# -- 2. Procura selects from what it received ------------------------------------------------------------------


def _procura_copies(**by_name):
    return {f"shared_supplier_eligibility:{n}": _row("procura", f"shared_supplier_eligibility:{n}", hop_depth=1, **extra)
            for n, extra in by_name.items()}


def _select(copies, candidates, stale_ids=()):
    with patch(f"{P}._active_rows", return_value=copies), patch(
        f"{P}.drop_stale_copies", side_effect=lambda rows, **k: [r for r in rows if r["id"] not in stale_ids]
    ):
        return propagation.select_eligible(candidates)


def test_a_fresh_verified_verdict_is_accepted():
    out = _select(_procura_copies(A={"valid_until": FUTURE}, B={}), ["A", "B"])
    assert out["accepted"] == ["A", "B"] and out["rejected"] == [] and out["kept"] == ["A", "B"]


def test_a_verdict_whose_documents_expired_is_rejected_even_if_the_pipeline_listed_the_supplier():
    out = _select(_procura_copies(A={"valid_until": PAST}, B={}), ["A", "B"])
    assert [r["supplier"] for r in out["rejected"]] == ["A"] and "expired" in out["rejected"][0]["reason"]
    assert out["kept"] == ["B"]


def test_a_copy_whose_source_changed_is_rejected_as_out_of_date():
    copies = _procura_copies(A={}, B={})
    out = _select(copies, ["A", "B"], stale_ids={"id-procura-shared_supplier_eligibility:A"})
    assert out["rejected"][0]["supplier"] == "A" and "changed after it was shared" in out["rejected"][0]["reason"]
    assert out["kept"] == ["B"]


def test_a_verdict_that_was_not_compliant_is_rejected():
    out = _select(_procura_copies(A={"verification_status": "disputed"}), ["A"])
    assert out["rejected"][0]["reason"].startswith("the shared verdict is 'disputed'") and out["kept"] == []


def test_a_supplier_without_a_copy_is_kept_and_reported():
    out = _select(_procura_copies(A={}), ["A", "NEW"])
    assert out["no_copy"] == ["NEW"] and out["kept"] == ["A", "NEW"]


def test_selection_falls_back_to_the_pipelines_list_if_memory_is_unreadable():
    with patch(f"{P}._active_rows", side_effect=ConnectionError("down")):
        out = propagation.select_eligible(["A", "B"])
    assert out["kept"] == ["A", "B"] and "unavailable" in out["note"]


def test_selection_progress_names_each_excluded_supplier_and_why():
    seen = []
    with progress.capture(lambda s, t: seen.append(t)):
        _select(_procura_copies(A={"valid_until": PAST}), ["A"])
    assert any(t.startswith("Excluded A: the shared verdict expired") for t in seen)


# -- 3. the evidence goes on to Finora, which checks it -----------------------------------------------------------


@pytest.fixture
def forward():
    rows = {
        ("procura", "shared_supplier_eligibility:ACME"): _row("procura", "shared_supplier_eligibility:ACME", hop_depth=1),
        ("procura", "quotation_comparison:LAPTOP"): _row("procura", "quotation_comparison:LAPTOP"),
    }
    with patch(f"{P}.get_memory_by_key", side_effect=lambda agent, key, **k: [rows[(agent, key)]] if (agent, key) in rows else []):
        yield rows


def test_the_evidence_forwarded_to_finora_is_the_verdict_at_hop_two_and_the_comparison(forward, sharing):
    lines = propagation.forward_evidence(supplier="ACME", item_code="LAPTOP", task_id="PR-1")
    assert [(c.args[0], c.args[1]) for c in sharing["share"].call_args_list] == [
        ("id-procura-shared_supplier_eligibility:ACME", "finora"),
        ("id-procura-quotation_comparison:LAPTOP", "finora"),
    ]
    assert "hop 2, Vendra → Procura → Finora" in lines[0] and not any(l.startswith("Not shared") for l in lines)


def test_a_missing_source_or_a_refused_share_is_reported_never_raised(forward, sharing):
    del forward[("procura", "quotation_comparison:LAPTOP")]
    sharing["share"].side_effect = errors.HopLimitExceeded("too far")
    lines = propagation.forward_evidence(supplier="ACME", item_code="LAPTOP", task_id="PR-1")
    assert lines[0].startswith("Not shared") and "HopLimitExceeded" in lines[0] and "no active source memory" in lines[1]


def _check(copy, stale=False):
    rows = {"shared_shared_supplier_eligibility:ACME": copy} if copy else {}
    with patch(f"{P}._active_rows", return_value=rows), patch(
        f"{P}.drop_stale_copies", side_effect=lambda r, **k: [] if stale else r
    ):
        return propagation.evidence_check("ACME")


def test_evidence_check_statuses():
    fresh = _row("finora", "shared_shared_supplier_eligibility:ACME", hop_depth=2, valid_until=FUTURE)
    assert _check(fresh)["status"] == "current"
    assert _check(dict(fresh, valid_until=PAST))["status"] == "stale"
    assert "expired" in _check(dict(fresh, valid_until=PAST))["reason"]
    assert _check(fresh, stale=True)["status"] == "stale"
    assert _check(None)["status"] == "missing"
    with patch(f"{P}._active_rows", side_effect=ConnectionError):
        assert propagation.evidence_check("ACME")["status"] == "unavailable"


# -- the coordinator uses the path only when told to -----------------------------------------------------------------

TASK = TaskRequest(description="Find eligible suppliers for 20 laptops, compare quotations, check the budget.")
COORD = "app.procurement.coordinator.propagation"


@pytest.fixture
def pipeline():
    with patch("app.memory.provenance_fields.supabase", new_callable=MagicMock), patch(
        "app.tools.memory_tools.get_relevant_context", return_value=[]
    ), patch("app.tools.memory_tools.save_memory", side_effect=lambda *a, **k: {"id": f"mem-{a[1]}"}):
        yield


def _run(use_memory, **overrides):
    patches = {
        "relay_verdicts": {"shared": ["SUP-0001"], "failed": [], "missing": []},
        "select_eligible": None,  # set per test
        "forward_evidence": ["shared"],
        "evidence_check": {"status": "current", "reason": "the verdict is current"},
    }
    patches.update(overrides)
    mocks = {}
    stack = []
    for name, value in patches.items():
        if name == "select_eligible" and value is None:
            m = patch(f"{COORD}.{name}", side_effect=lambda cands: {"accepted": list(cands), "rejected": [], "no_copy": [], "kept": list(cands), "note": None})
        else:
            m = patch(f"{COORD}.{name}", return_value=value)
        mocks[name] = m.start()
        stack.append(m)
    try:
        result = run_coordinator(TASK, SeededERPNextClient(), item_code="ITEM-LAPTOP-14", quantity=20, use_memory=use_memory)
    finally:
        for m in stack:
            m.stop()
    return result, mocks


def test_without_the_flag_the_memory_path_is_never_touched(pipeline):
    result, mocks = _run(False)
    assert result.failed is False and result.result["recommended_supplier"] == "SUP-0001"
    for name, mock in mocks.items():
        mock.assert_not_called()
    assert result.result["memory_path"] == {}


def test_with_the_flag_the_coordinator_relays_procura_selects_and_the_evidence_goes_to_finora(pipeline):
    result, mocks = _run(True)
    assert result.failed is False and result.result["recommended_supplier"] == "SUP-0001"
    mocks["relay_verdicts"].assert_called_once()
    assert mocks["relay_verdicts"].call_args.args[0] == ["SUP-0001"]
    mocks["select_eligible"].assert_called_once_with(["SUP-0001"])
    mocks["forward_evidence"].assert_called_once()
    mocks["evidence_check"].assert_called_once_with("SUP-0001")
    path = result.result["memory_path"]
    assert path["relay"]["shared"] == ["SUP-0001"] and path["selection"]["accepted"] == ["SUP-0001"]
    assert path["evidence_check"]["status"] == "current"
    procura = next(m for m in result.messages if m.sender == "procura")
    assert procura.payload["eligibility_basis"]["accepted"] == ["SUP-0001"]


def test_in_a_procura_only_task_the_coordinator_relays_vendras_verdicts_first_so_a_bad_one_is_received(pipeline):
    """Without Vendra in the task nothing was relayed; a supplier with an expired document must still be seen as such."""
    task = TaskRequest(description="Compare quotations and prepare the purchase for approval.", requested_systems=["procura"])
    patches = {
        "relay_verdicts": {"shared": ["SUP-0001", "SUP-0002"], "failed": [], "missing": []},
        "select_eligible": {"accepted": ["SUP-0001"], "rejected": [{"supplier": "SUP-0002", "reason": "the shared verdict expired"}],
                            "no_copy": [], "kept": ["SUP-0001"], "note": None},
        "forward_evidence": [],
        "evidence_check": {"status": "current", "reason": "the verdict is current"},
    }
    started = [patch(f"{COORD}.{n}", return_value=v) for n, v in patches.items()]
    mocks = {n: m.start() for n, m in zip(patches, started)}
    try:
        result = run_coordinator(task, SeededERPNextClient(), item_code="ITEM-LAPTOP-14", quantity=20, use_memory=True)
    finally:
        for m in started:
            m.stop()
    mocks["relay_verdicts"].assert_called_once()
    assert set(mocks["relay_verdicts"].call_args.args[0]) == {"SUP-0001", "SUP-0002"}
    assert mocks["select_eligible"].call_args.args[0] == mocks["relay_verdicts"].call_args.args[0]
    assert result.result["memory_path"]["selection"]["rejected"][0]["supplier"] == "SUP-0002"
    assert result.result["memory_path"]["selection"]["vendra_ran"] is False


def test_a_supplier_excluded_by_its_shared_verdict_never_reaches_the_comparison(pipeline):
    sel = {"accepted": [], "rejected": [{"supplier": "SUP-0001", "reason": "the shared verdict expired"}], "no_copy": [], "kept": [], "note": None}
    result, _mocks = _run(True, select_eligible=sel)
    assert result.result["recommended_supplier"] is None and result.blocked
    assert result.result["memory_path"]["selection"]["rejected"][0]["supplier"] == "SUP-0001"


def test_finora_refuses_to_proceed_when_the_verdict_it_holds_is_stale(pipeline):
    result, _mocks = _run(True, evidence_check={"status": "stale", "reason": "the verdict's documents expired on 2026-01-01"})
    assert result.blocked and "no longer current" in result.error and "expired on 2026-01-01" in result.error
    assert [m.sender for m in result.messages] == ["vendra", "procura"]  # Finora never ran


@pytest.mark.parametrize("status", ["missing", "unavailable"])
def test_a_missing_or_unreadable_evidence_copy_does_not_block_finora(pipeline, status):
    result, _mocks = _run(True, evidence_check={"status": status, "reason": "x"})
    assert not result.blocked and [m.sender for m in result.messages] == ["vendra", "procura", "finora"]


# -- the orchestrator -----------------------------------------------------------------------------------------------------


def _pr(mode="live", state="Running"):
    return workflow.PurchaseRequest(
        pr_id="PR-9", requested_by="a@b.c", item_description="laptops", quantity=10, estimated_budget=600000.0,
        delivery_location="Mumbai", required_date="2026-12-01", business_justification="x", status="Draft",
        mode=mode, workflow_state=state, item_code="LAPTOP-14IN",
    )


def _run_pipeline(pr, result):
    with patch("app.procurement.task_orchestrator.run_coordinator", return_value=result) as coordinator, patch(
        "app.procurement.task_orchestrator._resolve_client", return_value=(None, None)
    ), patch("app.procurement.task_orchestrator.workflow._save_pr"), patch(
        "app.procurement.task_orchestrator._auto_approve_if_configured", side_effect=lambda p: p
    ):  # the developer's own .env may enable auto-approve; these tests are about something else
        out = task_orchestrator._run_pipeline(pr)
    return out, coordinator


def _result(**kw):
    base = dict(task_id="t", correlation_id="c", systems_invoked=["vendra", "procura"], result={"recommended_supplier": None})
    base.update(kw)
    return TaskResult(**base)


def test_live_runs_use_the_memory_path(monkeypatch):
    monkeypatch.delenv("PROCUREMENT_MEMORY_PROPAGATION", raising=False)
    _out, coordinator = _run_pipeline(_pr("live"), _result(requires_approval=True, result={"recommended_supplier": "ACME", "recommended_total_cost": 1.0}))
    assert coordinator.call_args.kwargs["use_memory"] is True


def test_the_env_switch_turns_the_memory_path_off(monkeypatch):
    monkeypatch.setenv("PROCUREMENT_MEMORY_PROPAGATION", "0")
    _out, coordinator = _run_pipeline(_pr("live"), _result(requires_approval=True, result={"recommended_supplier": "ACME", "recommended_total_cost": 1.0}))
    assert coordinator.call_args.kwargs["use_memory"] is False


def test_demo_runs_never_use_the_memory_path():
    _out, coordinator = _run_pipeline(_pr("demo"), _result(requires_approval=True, result={"recommended_supplier": "ACME", "recommended_total_cost": 1.0}))
    assert "use_memory" not in coordinator.call_args.kwargs


def test_a_price_conflict_becomes_waiting_for_clarification_not_blocked():
    conflicts = [{"supplier": "ACME", "reason": "email vs pdf"}]
    out, _c = _run_pipeline(_pr("live"), _result(blocked=True, error="price conflict needs clarification: ACME", result={"conflicts": conflicts}))
    assert out.workflow_state == "Waiting For Clarification" and "price conflict" in out.blockers[0]
    assert out.coordinator_result["result"]["conflicts"] == conflicts


def test_an_ordinary_block_is_still_blocked():
    out, _c = _run_pipeline(_pr("live"), _result(blocked=True, error="procurement blocked: nobody eligible", result={"conflicts": []}))
    assert out.workflow_state == "Blocked"


@pytest.mark.parametrize("state,runs", [("Waiting For Clarification", True), ("Waiting For Information", True), ("Running", True), ("Completed", False), ("Waiting For Approval", False)])
def test_resume_reruns_only_from_a_state_that_can_continue(state, runs):
    pr = _pr("live", state)
    pr.quantity = 10
    with patch("app.procurement.task_orchestrator.workflow.get_purchase_request", return_value=pr), patch(
        "app.procurement.task_orchestrator._run_pipeline", return_value=pr
    ) as run, patch("app.procurement.task_orchestrator.workflow._save_pr"):
        task_orchestrator.resume_procurement_task("PR-9")
    assert run.called is runs


# -- every PR / approval memory write is linked to its task ------------------------------------------------------------------


@patch("app.procurement.workflow.enrich_memory")
@patch("app.procurement.workflow._tool")
def test_saving_a_purchase_request_links_the_row_to_its_own_id_when_no_task_id_is_given(mock_tool, enrich):
    mock_tool.return_value.invoke.return_value = {"id": "mem-1"}
    workflow._save_pr(_pr(), task_id=None, change_reason="created")
    assert enrich.call_args.kwargs["task_id"] == "PR-9"


@patch("app.procurement.workflow.enrich_memory")
@patch("app.procurement.workflow._tool")
@patch("app.procurement.workflow.transition_purchase_request")
@patch("app.procurement.workflow.get_purchase_request")
def test_recording_an_approval_links_the_row_to_the_purchase_request(mock_get, _transition, mock_tool, enrich):
    pr = _pr()
    pr.status = "Pending Approval"
    mock_get.return_value = pr
    mock_tool.return_value.invoke.return_value = {"id": "mem-2"}
    workflow.record_approval_decision(
        pr_id="PR-9", approver_id="mgr", approver_role="director", decision="Approved",
        approval_limits={"manager": 1e9, "director": 1e9, "cfo": None},
    )
    assert enrich.call_args.kwargs["task_id"] == "PR-9"


def test_a_share_that_fails_once_is_retried(sharing):
    attempts = {"n": 0}
    real = sharing["share"].side_effect

    def flaky(sid, target, **k):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("dropped")
        return real(sid, target, **k)

    sharing["share"].side_effect = flaky
    rows = {"supplier_eligibility:A": _row("vendra", "supplier_eligibility:A")}
    with patch(f"{P}._active_rows", return_value=rows), patch(f"{P}.time.sleep"):
        out = propagation.relay_verdicts(["A"], task_id="PR-1")
    assert out["shared"] == ["A"] and out["failed"] == [] and attempts["n"] == 2
