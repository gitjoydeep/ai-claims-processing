"""V8 email generation, outbox idempotency, audit, LLM client behaviour."""

from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from claims.audit import AuditLog
from claims.communication.composer import EmailComposer, EmailContext, render_template, verify_draft, build_facts
from claims.communication.outbox import DraftOutbox
from claims.config import Settings
from claims.errors import LLMResponseError
from claims.llm.client import GroqLLMClient, to_strict_schema
from claims.models import (ClaimDecision, CompletenessResult, EmailType, ExtractedClaimData, FieldCheck, FieldStatus,
                           InternalStatus, Outcome, RuleResult, RuleStatus)
from claims.payout import calculate_payout
from tests.conftest import FakeLLM

COMPLETE = CompletenessResult(is_complete=True, fields=[])
PAYOUT = calculate_payout(claimed_amount=D("45000"), coverage_limit_name="household_items",
                          coverage_limit=D("500000"), deductible=D("5000"))
COV = RuleResult(rule_id="COV-001", description="d", status=RuleStatus.PASSED, explanation="e", source="s",
                 inputs={"matched_coverages": ["Accidental water leakage from burst pipes"]})
EXC = RuleResult(rule_id="EXC-001", description="d", status=RuleStatus.FAILED, explanation="Matches exclusion.",
                 source="s", evidence="mechanical breakdown due to wear and tear",
                 inputs={"conclusive": ["Mechanical breakdown"]})


def ctx(outcome, **kw):
    decision = ClaimDecision(outcome=outcome, internal_status=kw.pop("status", InternalStatus.AUTO_DECIDED),
                             rationale="r", recommended_next_action="a",
                             outstanding_items=kw.pop("outstanding", []), failed_rules=kw.pop("failed", []))
    base = dict(claim_reference="CLM-TEST0001", decision=decision, completeness=kw.pop("completeness", COMPLETE),
                customer_name="Neha Kapoor", policy_number="HOME-2026-1001", insurance_type="Home Insurance",
                incident_summary="Geyser burst.")
    base.update(kw)
    return EmailContext(**base)


def compose(c, llm=None):
    return EmailComposer(llm, use_llm=llm is not None).compose(c)


def test_approval_email_contains_verified_payout_and_breakdown():
    e = compose(ctx(Outcome.APPROVED, payout=PAYOUT, validations=[COV]))
    assert e.email_type == EmailType.APPROVAL
    for text in ("Neha Kapoor", "CLM-TEST0001", "Rs. 40,000.00", "Rs. 45,000.00", "Rs. 5,000.00", "approved"):
        assert text in e.body


def test_rejection_email_names_exclusion():
    e = compose(ctx(Outcome.REJECTED, validations=[EXC], failed=["EXC-001"]))
    assert e.email_type == EmailType.REJECTION
    assert "Mechanical breakdown" in e.body and "unable to approve" in e.body and "Rs." not in e.body


def test_pending_missing_details_lists_all_six_and_marks_missing():
    incomplete = CompletenessResult(is_complete=False, fields=[
        FieldCheck(field="policy_number", label="Policy number", status=FieldStatus.MISSING)])
    e = compose(ctx(Outcome.PENDING, completeness=incomplete, outstanding=["Policy number"]))
    assert e.email_type == EmailType.PENDING_MISSING_DETAILS
    assert "Policy number (missing)" in e.body
    for label in ("Customer name", "Event date", "Event description", "Loss / damage details", "Claim amount"):
        assert label in e.body


def test_pending_outstanding_items_only_lists_outstanding():
    e = compose(ctx(Outcome.PENDING, outstanding=["Repair estimate"]))
    assert e.email_type == EmailType.PENDING_OUTSTANDING_ITEMS
    assert "1. Repair estimate" in e.body and "Customer name" not in e.body


def test_no_name_falls_back_to_generic_greeting():
    assert compose(ctx(Outcome.PENDING, customer_name=None, outstanding=["x"])).body.startswith("Dear Customer,")


def test_llm_draft_accepted_when_verified():
    e = compose(ctx(Outcome.APPROVED, payout=PAYOUT, validations=[COV]), FakeLLM())
    assert e.generated_by == "llm" and e.fallback_reason is None


def test_llm_draft_with_invented_amount_falls_back_to_template():
    llm = FakeLLM({"customer_email": lambda s, u: {
        "subject": "Claim CLM-TEST0001 approved",
        "body": "Dear Neha, claim CLM-TEST0001 approved. You will receive Rs. 45,000 plus a bonus of Rs. 2,000."}})
    e = compose(ctx(Outcome.APPROVED, payout=PAYOUT, validations=[COV]), llm)
    assert e.generated_by == "template" and "unverified amounts" in e.fallback_reason
    assert "Rs. 40,000.00" in e.body


def test_llm_draft_contradicting_outcome_falls_back():
    llm = FakeLLM({"customer_email": lambda s, u: {
        "subject": "CLM-TEST0001", "body": "Good news, claim CLM-TEST0001 has been approved."}})
    e = compose(ctx(Outcome.REJECTED, validations=[EXC], failed=["EXC-001"]), llm)
    assert e.generated_by == "template"


def test_llm_failure_falls_back_to_template():
    llm = FakeLLM({"customer_email": lambda s, u: LLMResponseError("down")})
    e = compose(ctx(Outcome.PENDING, outstanding=["Repair estimate"]), llm)
    assert e.generated_by == "template" and "Repair estimate" in e.body


def test_verify_draft_requires_listed_items():
    c = ctx(Outcome.PENDING, outstanding=["Medical reports"])
    facts = build_facts(c, EmailType.PENDING_OUTSTANDING_ITEMS)
    assert "requested item not listed: Medical reports" in verify_draft(
        "s CLM-TEST0001", "please provide documents", facts, EmailType.PENDING_OUTSTANDING_ITEMS, set())


def test_templates_render_for_every_type():
    for outcome, kw in [(Outcome.APPROVED, dict(payout=PAYOUT, validations=[COV])),
                        (Outcome.REJECTED, dict(validations=[EXC], failed=["EXC-001"])),
                        (Outcome.PENDING, dict(outstanding=["Repair estimate"]))]:
        subject, body = render_template(build_facts(ctx(outcome, **kw), compose(ctx(outcome, **kw)).email_type))
        assert "CLM-TEST0001" in subject + body and body.rstrip().endswith("Claims Team")


def test_outbox_suppresses_duplicates(tmp_path):
    outbox = DraftOutbox(tmp_path)
    email = compose(ctx(Outcome.PENDING, outstanding=["Repair estimate"]))
    assert outbox.submit(email).delivery_status == "DRAFT"
    assert outbox.submit(email).delivery_status == "DUPLICATE_SUPPRESSED"
    assert DraftOutbox(tmp_path).submit(email).delivery_status == "DUPLICATE_SUPPRESSED"  # survives restart
    assert len(list(tmp_path.glob("*.md"))) == 1


def test_idempotency_key_stable_across_llm_wording():
    a = compose(ctx(Outcome.APPROVED, payout=PAYOUT, validations=[COV]))
    b = compose(ctx(Outcome.APPROVED, payout=PAYOUT, validations=[COV]), FakeLLM())
    assert a.idempotency_key == b.idempotency_key


def test_audit_log_writes_jsonl_without_pii(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("CLM-1", "decision", "APPROVED", failed_rules=[])
    content = (tmp_path / "audit.jsonl").read_text()
    assert '"claim": "CLM-1"' in content and len(log.records) == 1


def test_strict_schema_all_required_no_extras():
    schema = to_strict_schema(ExtractedClaimData)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


class _FakeCompletions:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.kwargs = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=out))])


def _client(outputs, retries=3):
    completions = _FakeCompletions(outputs)
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return GroqLLMClient(Settings(llm_max_retries=retries), client=fake), completions


@pytest.fixture(autouse=False)
def no_sleep(monkeypatch):
    monkeypatch.setattr("claims.llm.client.time.sleep", lambda s: None)


def test_llm_client_retries_invalid_json_then_succeeds(no_sleep):
    good = '{"subject": "s", "body": "b"}'
    from claims.models import EmailDraftLLM
    client, completions = _client(["not json", good])
    assert client.complete_structured(system="s", user="u", response_model=EmailDraftLLM, schema_name="x").body == "b"
    assert completions.kwargs[0]["response_format"]["json_schema"]["strict"] is True


def test_llm_client_raises_typed_error_after_retries(no_sleep):
    from claims.models import EmailDraftLLM
    client, _ = _client([RuntimeError("down")] * 3)
    with pytest.raises(LLMResponseError):
        client.complete_structured(system="s", user="u", response_model=EmailDraftLLM, schema_name="x")


def test_backoff_honours_retry_after():
    from claims.llm.client import _backoff_seconds
    err = Exception()
    err.response = SimpleNamespace(headers={"retry-after": "7"})
    assert _backoff_seconds(err, 1) == 8.0
    assert _backoff_seconds(RuntimeError(), 2) == 4.0
