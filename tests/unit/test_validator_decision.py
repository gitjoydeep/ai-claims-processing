"""V5 policy validation and V7 decision logic."""

from decimal import Decimal as D

import pytest

from claims.classification import combine
from claims.completeness import check_completeness
from claims.decision import decide
from claims.extraction import ground_extraction
from claims.ingestion import parse_email
from claims.models import (ClassificationResult, ExclusionFinding, ExtractedClaimData, InternalStatus, Outcome,
                           PolicyMatch, RuleResult, RuleStatus)
from claims.payout import calculate_payout
from claims.policy.catalog import get_policy
from claims.policy.validator import (PolicyValidator, check_coverage, check_exclusions, check_policy_number_format,
                                     check_policy_prefix, check_requirements)
from tests.conftest import PROCESSING_DATE
from tests.fixtures.llm_responses import CASES


def setup(subject, dataset, match=None, **overrides):
    email = next(e for e in dataset if e.subject == subject)
    case = CASES[subject]
    extraction = ground_extraction(ExtractedClaimData(**{**case["extraction"], **overrides}), email)
    policy = get_policy(case["type"])
    match = PolicyMatch(**(match or case["match"] or
                           dict(matched_coverages=[], coverage_evidence=None, exclusion_findings=[], reason="")))
    cls = combine(*(ClassificationResult(method=m, insurance_type=case["type"], confidence=0.95, reason="r")
                    for m in ("zero_shot", "few_shot")), 0.7)
    return email, extraction, policy, match, cls


def statuses(results):
    return {r.rule_id: r.status.value for r in results}


@pytest.mark.parametrize("number, ok", [("HOME-2026-1001", True), ("veh-2026-2001", True), ("HOME2026", False),
                                        ("ABC-2026-1001", False), (None, False)])
def test_policy_number_format(number, ok):
    assert (check_policy_number_format(number).status == RuleStatus.PASSED) is ok


def test_policy_prefix_mismatch_needs_review():
    r = check_policy_prefix("VEH-2026-2001", "Home Insurance")
    assert r.status == RuleStatus.NEEDS_REVIEW and r.requires_human_review


def test_full_validation_email_1_all_pass(dataset):
    email, ext, policy, match, cls = setup("Water damage in my bathroom", dataset)
    results = PolicyValidator().validate(email=email, extraction=ext, classification=cls, policy=policy, match=match)
    s = statuses(results)
    assert s["POL-003"] == "NOT_APPLICABLE"
    assert all(v in ("PASSED", "NOT_APPLICABLE") for v in s.values())
    assert all(r.source for r in results) and all(r.explanation for r in results)


def test_exclusion_with_verified_quote_fails_rule(dataset):
    email, ext, policy, match, _ = setup("Engine breakdown claim", dataset)
    r = check_exclusions(match, policy, email)
    assert r.status == RuleStatus.FAILED and "Mechanical breakdown" in r.inputs["conclusive"] and r.evidence


def test_exclusion_with_fabricated_quote_is_discarded(dataset):
    email, _, policy, _, _ = setup("Engine breakdown claim", dataset)
    fake = PolicyMatch(matched_coverages=[], coverage_evidence=None, reason="",
                       exclusion_findings=[ExclusionFinding(exclusion="Driving under the influence",
                                                            evidence="I had been drinking")])
    r = check_exclusions(fake, policy, email)
    assert r.status == RuleStatus.PASSED and r.inputs["discarded_unverified"] == ["Driving under the influence"]


def test_exclusion_not_in_policy_is_discarded(dataset):
    email, _, policy, _, _ = setup("Water damage in my bathroom", dataset)
    fake = PolicyMatch(matched_coverages=[], coverage_evidence=None, reason="",
                       exclusion_findings=[ExclusionFinding(exclusion="Mechanical breakdown", evidence="my geyser burst")])
    assert check_exclusions(fake, policy, email).status == RuleStatus.PASSED


def test_non_determinable_exclusion_goes_to_review_not_rejection():
    email = parse_email("Subject: claim\n\nMy husband died by suicide on 2 October 2026. Policy LIFE-2026-4005.")
    match = PolicyMatch(matched_coverages=[], coverage_evidence=None, reason="",
                        exclusion_findings=[ExclusionFinding(exclusion="Suicide during the applicable exclusion period",
                                                             evidence="died by suicide")])
    r = check_exclusions(match, get_policy("Life Insurance"), email)
    assert r.status == RuleStatus.NEEDS_REVIEW and r.requires_human_review


def test_coverage_needs_verified_quote(dataset):
    email, _, policy, match, _ = setup("Burglary at my home", dataset)
    assert check_coverage(match, policy, email).status == RuleStatus.PASSED
    bad = match.model_copy(update={"coverage_evidence": "thieves cut the lock"})
    assert check_coverage(bad, policy, email).status == RuleStatus.NEEDS_REVIEW


def test_no_coverage_match_needs_review(dataset):
    email, _, policy, match, _ = setup("Burglary at my home", dataset)
    none = match.model_copy(update={"matched_coverages": []})
    assert check_coverage(none, policy, email).requires_human_review


def test_event_aware_documents_vehicle_theft_waives_photos_and_estimate(dataset):
    _, ext, policy, _, _ = setup("My car has been stolen", dataset)
    s = {r.description.split(": ")[1]: r.status.value for r in check_requirements(ext, policy, ["Vehicle theft"])}
    assert s["Photos of damage"] == s["Repair estimate"] == "NOT_APPLICABLE"
    assert s["FIR for theft claims"] == "PASSED"


def test_fir_required_for_vehicle_theft_without_fir(dataset):
    _, ext, policy, _, _ = setup("My car has been stolen", dataset, fir_number=None,
                                 supporting_documents=["vehicle registration certificate"])
    r = next(r for r in check_requirements(ext, policy, ["Vehicle theft"]) if "FIR" in r.description)
    assert r.status == RuleStatus.FAILED and r.outstanding_item == "FIR for theft claims"


def test_fir_not_required_for_natural_death(dataset):
    _, ext, policy, _, _ = setup("Request to initiate death claim", dataset)
    r = next(r for r in check_requirements(ext, policy, ["Natural death"]) if "FIR" in r.description)
    assert r.status == RuleStatus.NOT_APPLICABLE


def test_missing_documents_detected(dataset):
    _, ext, policy, _, _ = setup("Hospitalisation reimbursement request", dataset)
    failed = [r.outstanding_item for r in check_requirements(ext, policy, ["Hospitalisation"])
              if r.status == RuleStatus.FAILED]
    assert failed == ["Medical reports"]


def test_validation_is_deterministic(dataset):
    email, ext, policy, match, cls = setup("Burglary at my home", dataset)
    v = PolicyValidator()
    a = v.validate(email=email, extraction=ext, classification=cls, policy=policy, match=match)
    b = v.validate(email=email, extraction=ext, classification=cls, policy=policy, match=match)
    assert [r.model_dump() for r in a] == [r.model_dump() for r in b]


# ------------------------------------------------------------------ decision


def complete(dataset, subject="Water damage in my bathroom"):
    _, ext, *_ = setup(subject, dataset)
    return check_completeness(ext, PROCESSING_DATE)


def rule(rule_id, status, review=False, item=None):
    return RuleResult(rule_id=rule_id, description="d", status=status, explanation=f"{rule_id} expl", source="s",
                      requires_human_review=review, outstanding_item=item)


PAYOUT = calculate_payout(claimed_amount=D("45000"), coverage_limit_name="household_items",
                          coverage_limit=D("500000"), deductible=D("5000"))


def test_decision_approved(dataset):
    d = decide(complete(dataset), [rule("COV-001", RuleStatus.PASSED), rule("POL-003", RuleStatus.NOT_APPLICABLE)],
               PAYOUT)
    assert d.outcome == Outcome.APPROVED and "40,000.00" in d.rationale


def test_decision_rejected_only_on_exclusion(dataset):
    d = decide(complete(dataset), [rule("EXC-001", RuleStatus.FAILED)], PAYOUT)
    assert d.outcome == Outcome.REJECTED and d.failed_rules == ["EXC-001"]


def test_failed_document_rule_never_rejects(dataset):
    d = decide(complete(dataset), [rule("DOC-004", RuleStatus.FAILED, item="Repair estimate")], PAYOUT)
    assert d.outcome == Outcome.PENDING and d.outstanding_items == ["Repair estimate"]
    assert d.internal_status == InternalStatus.AUTO_DECIDED


def test_missing_mandatory_info_takes_priority_over_potential_rejection(dataset):
    incomplete = complete(dataset, "Fire damage in my living room")
    d = decide(incomplete, [rule("EXC-001", RuleStatus.FAILED)], PAYOUT)
    assert d.outcome == Outcome.PENDING and d.outstanding_items == ["Policy number"]


def test_conclusive_exclusion_rejects_even_with_documents_outstanding(dataset):
    d = decide(complete(dataset), [rule("EXC-001", RuleStatus.FAILED),
                                   rule("DOC-002", RuleStatus.FAILED, item="Vehicle registration number")], None)
    assert d.outcome == Outcome.REJECTED


def test_review_flag_kept_separate_from_outcome(dataset):
    d = decide(complete(dataset), [rule("COV-001", RuleStatus.NEEDS_REVIEW, review=True),
                                   rule("DOC-004", RuleStatus.FAILED, item="Repair estimate")], PAYOUT)
    assert d.outcome == Outcome.PENDING and d.internal_status == InternalStatus.HUMAN_REVIEW
    assert d.outstanding_items == ["Repair estimate"] and d.review_reasons


def test_incomplete_payout_blocks_approval(dataset):
    incomplete_payout = calculate_payout(claimed_amount=D("1"), coverage_limit_name=None, coverage_limit=None,
                                         deductible=D("0"))
    d = decide(complete(dataset), [rule("COV-001", RuleStatus.PASSED)], incomplete_payout)
    assert d.outcome == Outcome.PENDING and d.internal_status == InternalStatus.HUMAN_REVIEW


def test_not_applicable_rules_do_not_block(dataset):
    d = decide(complete(dataset), [rule("POL-003", RuleStatus.NOT_APPLICABLE)], PAYOUT)
    assert d.outcome == Outcome.APPROVED
