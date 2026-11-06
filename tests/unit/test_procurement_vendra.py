"""Unit tests for Vendra. The ERPNext side is the real SeededERPNextClient
(no mocking needed, no live instance required); only the memory-write path
(real Supabase otherwise) is mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.procurement.mcp.client import ERPNextUnavailable, SeededERPNextClient
from app.procurement.systems import vendra


def test_discover_suppliers_excludes_on_hold():
    client = SeededERPNextClient()
    names = {s["name"] for s in vendra.discover_suppliers(client)}
    assert names == {"SUP-0001", "SUP-0002"}  # SUP-0003 is on_hold


def test_check_compliance_flags_missing_documents():
    client = SeededERPNextClient()
    complete = vendra.check_compliance(client, "SUP-0001")
    incomplete = vendra.check_compliance(client, "SUP-0002")
    assert complete["compliant"] is True
    assert complete["missing_documents"] == []
    assert incomplete["compliant"] is False
    assert incomplete["missing_documents"] == ["Business License"]


def test_check_compliance_never_invents_documents_for_unknown_supplier():
    client = SeededERPNextClient()
    result = vendra.check_compliance(client, "SUP-9999")
    assert result["documents_on_file"] == []
    assert set(result["missing_documents"]) == set(vendra.REQUIRED_SUPPLIER_DOCUMENTS)


# -- country-appropriate compliance documents --------------------------------
#
# A US-specific W-9 Tax Form has no legitimate bearing on a supplier
# incorporated outside the US. These cover the real gap found in the live
# demo company (an Indian company, with Indian suppliers that were being
# asked for a W-9).


class _CountryClient:
    """Minimal stand-in: one supplier with a given country and whatever
    documents are passed in."""

    def __init__(self, country, documents):
        self._country = country
        self._documents = documents

    def get_supplier(self, supplier_name):
        return {"name": supplier_name, "country": self._country}

    def list_supplier_documents(self, supplier_name):
        return self._documents


def test_an_indian_supplier_is_never_asked_for_a_us_w9():
    client = _CountryClient("India", documents=[])
    result = vendra.check_compliance(client, "SUP-IN")
    assert "W-9 Tax Form" not in result["required_documents"]
    assert result["required_documents"] == ["PAN Card", "GST Registration Certificate"]


def test_an_indian_supplier_is_compliant_with_real_pan_and_gst_evidence():
    client = _CountryClient(
        "India", documents=[{"file_name": "PAN_Card_ABCDE1234F.pdf"}, {"file_name": "GST_Registration_Certificate.pdf"}]
    )
    result = vendra.check_compliance(client, "SUP-IN")
    assert result["compliant"] is True
    assert result["missing_documents"] == []


def test_an_indian_supplier_with_zero_attachments_is_missing_the_real_indian_documents():
    client = _CountryClient("India", documents=[])
    result = vendra.check_compliance(client, "SUP-IN")
    assert result["compliant"] is False
    assert set(result["missing_documents"]) == {"PAN Card", "GST Registration Certificate"}


def test_a_us_supplier_still_requires_the_original_w9_and_business_license():
    client = _CountryClient("US", documents=[{"file_name": "w9_form.pdf"}, {"file_name": "business_license.pdf"}])
    result = vendra.check_compliance(client, "SUP-US")
    assert result["required_documents"] == ["W-9 Tax Form", "Business License"]
    assert result["compliant"] is True


def test_pan_pattern_does_not_false_positive_on_an_unrelated_substring():
    """"japan_office.pdf" contains "pan" as a bare substring -- must never
    be mistaken for PAN Card evidence."""
    client = _CountryClient("India", documents=[{"file_name": "japan_office_photo.pdf"}])
    result = vendra.check_compliance(client, "SUP-IN")
    assert "PAN Card" in result["missing_documents"]


def test_country_lookup_failure_falls_back_to_the_default_rather_than_crashing():
    """A client without get_supplier (e.g. the real-filename test double
    above) must never make the compliance check itself fail."""

    class _NoCountryClient:
        def list_supplier_documents(self, supplier_name):
            return []

    result = vendra.check_compliance(_NoCountryClient(), "SUP-X")
    assert result["required_documents"] == list(vendra.REQUIRED_SUPPLIER_DOCUMENTS)


# -- real-filename compliance matching (File doctype shape, not the fixture) --
#
# These exercise check_compliance() against a stand-in client returning
# real-File-shaped records ({"file_name": ...}), not the seeded fixture's
# {"title": ...} shape -- proving the match works on the live data shape,
# not just on the fixture's clean labels.


class _FileShapedClient:
    """Minimal stand-in: list_supplier_documents returns real File-style
    records for one supplier, nothing for any other -- exactly the shape
    ERPNextRestClient/FrappeMCPClient now return."""

    def __init__(self, documents):
        self._documents = documents

    def list_supplier_documents(self, supplier_name):
        return self._documents if supplier_name == "SUP-REAL" else []


def test_compliance_matches_real_filenames_conservatively():
    client = _FileShapedClient(
        [
            {"name": "f1", "file_name": "W9_SupplierA_2026.pdf"},
            {"name": "f2", "file_name": "business-license-scan.pdf"},
        ]
    )
    result = vendra.check_compliance(client, "SUP-REAL")
    assert result["compliant"] is True
    assert result["missing_documents"] == []
    assert result["documents_on_file"] == ["W9_SupplierA_2026.pdf", "business-license-scan.pdf"]


def test_compliance_does_not_treat_unrelated_filename_as_evidence():
    """An unrelated attachment (e.g. a logo, an invoice) must never be
    mistaken for W-9/business-license evidence -- the pattern is deliberately
    conservative, not "any file present"."""
    client = _FileShapedClient([{"name": "f1", "file_name": "company_logo.png"}])
    result = vendra.check_compliance(client, "SUP-REAL")
    assert result["compliant"] is False
    assert set(result["missing_documents"]) == set(vendra.REQUIRED_SUPPLIER_DOCUMENTS)


def test_compliance_with_zero_attachments_is_missing_not_compliant():
    """Exactly the real current state of both live demo suppliers: no
    files attached at all. Must report fully missing, never compliant."""
    client = _FileShapedClient([])
    result = vendra.check_compliance(client, "SUP-REAL")
    assert result["compliant"] is False
    assert result["documents_on_file"] == []
    assert set(result["missing_documents"]) == set(vendra.REQUIRED_SUPPLIER_DOCUMENTS)


def test_compliance_rejects_near_miss_w9_pattern():
    """"w90xyz" must not satisfy the W-9 pattern -- word-boundary guards
    against a filename that merely starts with "w9"."""
    client = _FileShapedClient([{"name": "f1", "file_name": "w90_misc_report.pdf"}])
    result = vendra.check_compliance(client, "SUP-REAL")
    assert "W-9 Tax Form" in result["missing_documents"]


@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.procurement.systems.vendra.enrich_memory")
@patch("app.tools.memory_tools.save_memory")
def test_run_vendor_eligibility_check_only_returns_compliant_suppliers(mock_save, mock_enrich, _mock_ctx):
    mock_save.side_effect = lambda *a, **k: {"id": f"mem-{k.get('memory_key', a[1] if len(a) > 1 else '?')}"}
    client = SeededERPNextClient()

    msg = vendra.run_vendor_eligibility_check(task_id="task-1", correlation_id="corr-1", client=client)

    assert msg.status == "ok"
    assert msg.sender == "vendra"
    eligible_names = {s["name"] for s in msg.payload["eligible_suppliers"]}
    assert eligible_names == {"SUP-0001"}  # SUP-0002 is missing a document, SUP-0003 is on hold
    assert msg.payload["candidates_checked"] == 2  # SUP-0003 excluded before compliance is even checked
    assert len(msg.sources) == 2  # one source per candidate checked (0001 verified, 0002 disputed)
    # every call saved a memory and enriched it with the real task_id
    assert mock_save.call_count == 2
    assert all(call.kwargs.get("task_id") == "task-1" for call in mock_enrich.call_args_list)


@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.procurement.systems.vendra.enrich_memory")
@patch("app.tools.memory_tools.save_memory")
def test_run_vendor_eligibility_check_without_llm_has_no_ai_summary(mock_save, mock_enrich, _mock_ctx):
    """Default behavior (no llm passed, matching every other test in this
    file and the coordinator when PROCUREMENT_AI_SUMMARIES is unset): no
    narration is attempted, so this makes no network call."""
    mock_save.side_effect = lambda *a, **k: {"id": "mem-1"}
    client = SeededERPNextClient()

    msg = vendra.run_vendor_eligibility_check(task_id="task-1", correlation_id="corr-1", client=client)

    assert msg.payload["ai_summary"] is None
    assert {s["name"] for s in msg.payload["eligible_suppliers"]} == {"SUP-0001"}


class _FakeLLM:
    def invoke(self, messages):
        class _Reply:
            content = "SUP-0001 is compliant; SUP-0002 and SUP-0003 are not eligible."

        return _Reply()


@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.procurement.systems.vendra.enrich_memory")
@patch("app.tools.memory_tools.save_memory")
def test_run_vendor_eligibility_check_narrates_without_changing_the_verdict(mock_save, mock_enrich, _mock_ctx):
    """A genuine subagent call (injected here instead of a real NVIDIA
    call) narrates the result -- it must never change which suppliers are
    actually eligible, only add an explanatory summary alongside them."""
    mock_save.side_effect = lambda *a, **k: {"id": "mem-1"}
    client = SeededERPNextClient()

    msg = vendra.run_vendor_eligibility_check(
        task_id="task-1", correlation_id="corr-1", client=client, llm=_FakeLLM()
    )

    assert msg.payload["ai_summary"] == "SUP-0001 is compliant; SUP-0002 and SUP-0003 are not eligible."
    assert {s["name"] for s in msg.payload["eligible_suppliers"]} == {"SUP-0001"}


@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.procurement.systems.vendra.enrich_memory")
@patch("app.tools.memory_tools.save_memory")
def test_run_vendor_eligibility_check_exposes_all_six_specialists(mock_save, mock_enrich, _mock_ctx):
    """The six-specialist detail is real, inspectable output, not just an
    internal implementation detail."""
    mock_save.side_effect = lambda *a, **k: {"id": "mem-1"}
    client = SeededERPNextClient()

    msg = vendra.run_vendor_eligibility_check(task_id="task-1", correlation_id="corr-1", client=client)

    specialists = msg.payload["specialists"]
    assert set(specialists.keys()) == {
        "vendor_discovery",
        "document_verification",
        "vendor_onboarding_readiness",
        "vendor_profile",
        "vendor_performance_snapshot",
        "vendor_risk_flagging",
    }
    assert all(specialists[name]["status"] in ("ok", "skipped") for name in specialists)
    assert specialists["vendor_risk_flagging"]["data"]["risk_by_supplier"]["SUP-0003"] == "on_hold"


@patch("app.tools.memory_tools.get_relevant_context", return_value=[])
@patch("app.procurement.systems.vendra.enrich_memory")
@patch("app.tools.memory_tools.save_memory")
def test_run_vendor_eligibility_check_raises_on_a_core_specialist_backend_failure(mock_save, mock_enrich, _mock_ctx):
    """Vendor Discovery/Document Verification are load-bearing: a real
    backend outage there must still surface to the coordinator as a
    failure (so its one-retry policy applies) -- the shared specialist
    framework's own exception safety-net must not silently turn this into
    an "ok" message with broken downstream specialists."""
    mock_save.side_effect = lambda *a, **k: {"id": "mem-1"}
    client = SeededERPNextClient()
    client.list_suppliers = MagicMock(side_effect=ERPNextUnavailable("connection refused"))

    with pytest.raises(ERPNextUnavailable):
        vendra.run_vendor_eligibility_check(task_id="task-1", correlation_id="corr-1", client=client)
