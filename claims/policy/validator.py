"""Deterministic policy validation engine (Task 5).

Pure functions: same inputs -> same RuleResults. No LLM calls here. LLM evidence from the matcher is
accepted only if its quotes are found verbatim in the customer's email.
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..ingestion import text_contains
from ..models import ClaimExtraction, ClassificationOutcome, PolicyMatch, RawEmail, RuleResult, RuleStatus
from .catalog import (DETERMINABLE_EXCLUSIONS, POLICY_NUMBER_PREFIX, POLICY_NUMBER_RE, POLICY_SOURCE, Policy,
                      RequirementSpec)

SRC = POLICY_SOURCE


def check_policy_number_format(policy_number: str | None) -> RuleResult:
    value = (policy_number or "").strip().upper()
    ok = bool(POLICY_NUMBER_RE.match(value))
    return RuleResult(
        rule_id="POL-001", description="Policy number has a recognised format",
        inputs={"policy_number_present": bool(value)},
        status=RuleStatus.PASSED if ok else RuleStatus.FAILED,
        explanation=("Policy number matches the format <HOME|VEH|MED|LIFE>-YYYY-NNNN." if ok else
                     "Policy number does not match any recognised policy number format."),
        source="Sample policy numbers (assumption A-3)",
        outstanding_item=None if ok else "A valid policy number as shown on your policy document",
    )


def check_policy_prefix(policy_number: str | None, insurance_type: str) -> RuleResult:
    match = POLICY_NUMBER_RE.match((policy_number or "").strip().upper())
    prefix_type = POLICY_NUMBER_PREFIX.get(match.group(1)) if match else None
    if prefix_type is None:
        return RuleResult(rule_id="POL-002", description="Policy number type matches the claim type",
                          status=RuleStatus.NOT_APPLICABLE, explanation="Policy number format not recognised (see POL-001).",
                          source="Sample policy numbers (assumption A-3)")
    ok = prefix_type == insurance_type
    return RuleResult(
        rule_id="POL-002", description="Policy number type matches the claim type",
        inputs={"policy_type_from_number": prefix_type, "classified_type": insurance_type},
        status=RuleStatus.PASSED if ok else RuleStatus.NEEDS_REVIEW,
        explanation=(f"Policy number belongs to {prefix_type}, consistent with the claim." if ok else
                     f"Policy number belongs to {prefix_type} but the claim was classified as {insurance_type}."),
        source="Sample policy numbers (assumption A-3)", requires_human_review=not ok,
    )


def check_policy_record() -> RuleResult:
    return RuleResult(
        rule_id="POL-003", description="Policy status, effective/expiry dates and policyholder match",
        status=RuleStatus.NOT_APPLICABLE,
        explanation="No policy register (holders, status, dates) was supplied; this check cannot be performed.",
        source="No policy register available",
    )


def check_classification(classification: ClassificationOutcome) -> RuleResult:
    return RuleResult(
        rule_id="CLS-001", description="Insurance type established reliably",
        inputs={"zero_shot": classification.zero_shot.insurance_type, "few_shot": classification.few_shot.insurance_type,
                "confidences": [classification.zero_shot.confidence, classification.few_shot.confidence]},
        status=RuleStatus.PASSED if classification.reliable else RuleStatus.NEEDS_REVIEW,
        explanation=classification.note, source="Classification agreement rule", requires_human_review=not classification.reliable,
    )


def check_coverage(match: PolicyMatch, policy: Policy, email: RawEmail) -> RuleResult:
    valid = [c for c in match.matched_coverages if c in policy.covered_claim_categories]
    grounded = text_contains(email.full_text, match.coverage_evidence)
    ok = bool(valid) and grounded
    if ok:
        explanation = f"Incident matches covered categor{'y' if len(valid) == 1 else 'ies'}: {', '.join(valid)}."
    elif not valid:
        explanation = "The incident could not be matched to any covered category of the policy."
    else:
        explanation = "A coverage match was proposed but its supporting quote was not found in the email."
    return RuleResult(
        rule_id="COV-001", description="Incident falls under a covered claim category",
        inputs={"matched_coverages": valid}, status=RuleStatus.PASSED if ok else RuleStatus.NEEDS_REVIEW,
        explanation=explanation, evidence=match.coverage_evidence if grounded else None,
        source=f"{SRC}: {policy.insurance_type}.covered_claim_categories", requires_human_review=not ok,
    )


def check_exclusions(match: PolicyMatch, policy: Policy, email: RawEmail) -> RuleResult:
    conclusive, review, discarded = [], [], []
    for finding in match.exclusion_findings:
        if finding.exclusion not in policy.exclusions or not text_contains(email.full_text, finding.evidence):
            discarded.append(finding.exclusion)
        elif finding.exclusion in DETERMINABLE_EXCLUSIONS:
            conclusive.append(finding)
        else:
            review.append(finding)

    base = dict(rule_id="EXC-001", description="No policy exclusion applies",
                source=f"{SRC}: {policy.insurance_type}.exclusions",
                inputs={"conclusive": [f.exclusion for f in conclusive], "needs_review": [f.exclusion for f in review],
                        "discarded_unverified": discarded})
    if conclusive:
        names = ", ".join(f'"{f.exclusion}"' for f in conclusive)
        return RuleResult(**base, status=RuleStatus.FAILED,
                          explanation=f"The customer's own description matches the policy exclusion(s) {names}.",
                          evidence=" | ".join(f.evidence for f in conclusive))
    if review:
        names = ", ".join(f'"{f.exclusion}"' for f in review)
        return RuleResult(**base, status=RuleStatus.NEEDS_REVIEW, requires_human_review=True,
                          explanation=f"Possible exclusion(s) {names} need investigation before a decision.",
                          evidence=" | ".join(f.evidence for f in review))
    note = " Unverified suggestions were discarded." if discarded else ""
    return RuleResult(**base, status=RuleStatus.PASSED,
                      explanation="No listed exclusion is indicated by the facts in the email." + note)


def _matches_keyword(document: str, keyword: str) -> bool:
    return re.search(rf"\b{re.escape(keyword)}", document, re.IGNORECASE) is not None


def _requirement_applies(spec: RequirementSpec, coverages: list[str]) -> bool:
    if spec.applies_if_any and not spec.applies_if_any.intersection(coverages):
        return False
    if spec.waived_if_only and coverages and set(coverages) <= spec.waived_if_only:
        return False
    return True


def check_requirements(extraction: ClaimExtraction, policy: Policy, coverages: list[str]) -> list[RuleResult]:
    results = []
    data = extraction.data
    for index, spec in enumerate(policy.requirement_specs, start=1):
        rule_id = f"DOC-{index:03d}"
        source = f"{SRC}: {policy.insurance_type}.claim_requirements"
        description = f"Claim requirement: {spec.requirement}"
        if not _requirement_applies(spec, coverages):
            results.append(RuleResult(rule_id=rule_id, description=description, status=RuleStatus.NOT_APPLICABLE,
                                      explanation=f"Not required for this type of incident ({', '.join(coverages)}).",
                                      source=source + " (event-aware applicability, decision D-2)"))
            continue
        field_hit = next((f for f in spec.fields if getattr(data, f, None)), None)
        doc_hit = next((d for d in data.supporting_documents for k in spec.keywords if _matches_keyword(d, k)), None)
        satisfied = bool(field_hit or doc_hit)
        results.append(RuleResult(
            rule_id=rule_id, description=description,
            inputs={"provided_documents": data.supporting_documents, "field_checked": list(spec.fields)},
            status=RuleStatus.PASSED if satisfied else RuleStatus.FAILED,
            explanation=(f"Provided ({doc_hit or field_hit})." if satisfied else f"{spec.requirement} has not been provided."),
            evidence=doc_hit, source=source,
            outstanding_item=None if satisfied else spec.requirement,
        ))
    return results


def select_coverage_limit(policy: Policy, claimed_amount: Decimal) -> tuple[str | None, Decimal | None, RuleResult]:
    """Pick the applicable limit. With several sub-limits and no selection rule in the policy, the lowest
    sub-limit is used only when the claim is within it (so the choice cannot change the payout)."""
    limits = policy.coverage_limits
    source = f"{SRC}: {policy.insurance_type}.coverage_limits"
    if len(limits) == 1:
        name, value = next(iter(limits.items()))
        return name, value, RuleResult(rule_id="FIN-001", description="Applicable coverage limit identified",
                                       inputs={"limits": {k: str(v) for k, v in limits.items()}},
                                       status=RuleStatus.PASSED, explanation=f"Single coverage limit '{name}' applies.",
                                       source=source)
    name, value = min(limits.items(), key=lambda kv: kv[1])
    if claimed_amount <= value:
        return name, value, RuleResult(
            rule_id="FIN-001", description="Applicable coverage limit identified",
            inputs={"limits": {k: str(v) for k, v in limits.items()}, "claimed_amount": str(claimed_amount)},
            status=RuleStatus.PASSED,
            explanation=(f"Claim is within the lowest sub-limit '{name}', so every sub-limit gives the same "
                         f"payout; '{name}' is applied."),
            source=source + " (sub-limit rule)")
    return None, None, RuleResult(
        rule_id="FIN-001", description="Applicable coverage limit identified",
        inputs={"limits": {k: str(v) for k, v in limits.items()}, "claimed_amount": str(claimed_amount)},
        status=RuleStatus.NEEDS_REVIEW,
        explanation=(f"Claim exceeds the '{name}' sub-limit and the policy does not say how to split the loss "
                     f"between {', '.join(limits)}."),
        source=source + " (sub-limit rule)",
        outstanding_item="A breakdown of the claimed amount between " + " and ".join(k.replace('_', ' ') for k in limits))


class PolicyValidator:
    """Runs all applicable rules for a complete, classified claim."""

    def validate(self, *, email: RawEmail, extraction: ClaimExtraction, classification: ClassificationOutcome,
                 policy: Policy, match: PolicyMatch) -> list[RuleResult]:
        policy_number = extraction.data.policy_number
        coverages = [c for c in match.matched_coverages if c in policy.covered_claim_categories]
        return [
            check_policy_number_format(policy_number),
            check_policy_prefix(policy_number, policy.insurance_type),
            check_policy_record(),
            check_classification(classification),
            check_coverage(match, policy, email),
            check_exclusions(match, policy, email),
            *check_requirements(extraction, policy, coverages),
        ]
