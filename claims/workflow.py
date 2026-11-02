"""Claim workflow orchestration:

email -> extract -> complete? --no--> follow-up email
                        |yes
                        v
                  classify -> policy check -> calculate -> decide -> customer email

Any LLM/service failure is fail-safe: the claim is marked PENDING + HUMAN_REVIEW, the error is
recorded, and no customer email is generated from unverified data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .audit import AuditLog
from .classification import ClaimClassifier
from .communication.composer import EmailComposer, EmailContext, customer_name_if_verified
from .communication.outbox import DraftOutbox
from .completeness import check_completeness
from .config import Settings
from .decision import decide
from .errors import ClaimsError
from .extraction import ClaimExtractor
from .ingestion import claim_reference
from .llm.client import LLMClient
from .models import ClaimDecision, ClaimResult, InternalStatus, Outcome, RawEmail, RuleStatus
from .payout import calculate_payout
from .policy.catalog import get_policy
from .policy.matcher import PolicyMatcher
from .policy.validator import (PolicyValidator, check_classification, check_policy_number_format,
                               select_coverage_limit)

logger = logging.getLogger(__name__)


class ClaimsProcessor:
    def __init__(self, llm: LLMClient, settings: Settings, outbox: DraftOutbox | None = None,
                 audit: AuditLog | None = None):
        self._settings = settings
        self._extractor = ClaimExtractor(llm)
        self._classifier = ClaimClassifier(llm, settings.classification_confidence_threshold)
        self._matcher = PolicyMatcher(llm)
        self._validator = PolicyValidator()
        self._composer = EmailComposer(llm, use_llm=settings.use_llm_for_customer_emails)
        self._outbox = outbox or DraftOutbox(None)
        self._audit = audit or AuditLog(None)

    def process(self, email: RawEmail) -> ClaimResult:
        ref = claim_reference(email)
        result = ClaimResult(claim_reference=ref, email_id=email.email_id, processed_at=datetime.now(timezone.utc),
                             decision=_placeholder_decision())
        self._audit.record(ref, "ingestion", "OK", email_id=email.email_id)
        steps = ["extraction"]  # last element = step currently running (for error reporting)
        try:
            result.extraction = self._extractor.extract(email)
            self._audit.record(ref, "extraction", "OK", grounding_issues=len(result.extraction.grounding_issues),
                               intent=result.extraction.data.intent)

            steps.append("completeness")
            result.completeness = check_completeness(result.extraction, self._settings.processing_date)
            self._audit.record(ref, "completeness", "COMPLETE" if result.completeness.is_complete else "INCOMPLETE",
                               missing=[f.field for f in result.completeness.problem_fields])

            if result.completeness.is_complete:
                self._assess(email, result, steps)
            else:
                result.decision = decide(result.completeness)

            self._audit.record(ref, "decision", result.decision.outcome.value,
                               internal_status=result.decision.internal_status.value,
                               failed_rules=result.decision.failed_rules)

            steps.append("communication")
            data = result.extraction.data
            ctx = EmailContext(
                claim_reference=ref, decision=result.decision, completeness=result.completeness,
                customer_name=customer_name_if_verified(result.completeness, data.customer_name),
                policy_number=data.policy_number if _rule_passed(result, "POL-001") else None,
                insurance_type=result.classification.final_type if result.classification else None,
                incident_summary=data.event_description, payout=result.payout, validations=result.validations)
            result.communication = self._outbox.submit(self._composer.compose(ctx))
            self._audit.record(ref, "communication", result.communication.delivery_status,
                               email_type=result.communication.email_type.value,
                               generated_by=result.communication.generated_by)
        except ClaimsError as exc:
            step = steps[-1]
            logger.error("Claim %s failed at step '%s': %s", ref, step, type(exc).__name__)
            result.errors.append(f"{step}: {exc}")
            result.communication = None
            result.decision = ClaimDecision(
                outcome=Outcome.PENDING, internal_status=InternalStatus.HUMAN_REVIEW,
                rationale=f"Automated processing could not complete at step '{step}'.",
                review_reasons=[f"{step}: {type(exc).__name__}"],
                recommended_next_action="Route to a claims handler; no customer email was generated automatically.")
            self._audit.record(ref, step, "ERROR", error=type(exc).__name__)
        return result

    def _assess(self, email: RawEmail, result: ClaimResult, steps: list[str]) -> None:
        ref = result.claim_reference
        extraction = result.extraction
        steps.append("classification")
        result.classification = self._classifier.classify(extraction)
        self._audit.record(ref, "classification", "RELIABLE" if result.classification.reliable else "UNRELIABLE",
                           zero_shot=result.classification.zero_shot.insurance_type,
                           few_shot=result.classification.few_shot.insurance_type)

        if result.classification.final_type is None:
            result.validations = [check_policy_number_format(extraction.data.policy_number),
                                  check_classification(result.classification)]
            result.decision = decide(result.completeness, result.validations, None)
            return

        steps.append("policy_validation")
        policy = get_policy(result.classification.final_type)
        result.policy_match = self._matcher.match(email, policy)
        result.validations = self._validator.validate(email=email, extraction=extraction,
                                                      classification=result.classification,
                                                      policy=policy, match=result.policy_match)
        limit_name, limit, limit_rule = select_coverage_limit(policy, extraction.claimed_amount)
        result.validations.append(limit_rule)
        self._audit.record(ref, "policy_validation", "DONE",
                           results={r.rule_id: r.status.value for r in result.validations})

        steps.append("payout")
        result.payout = calculate_payout(claimed_amount=extraction.claimed_amount, coverage_limit_name=limit_name,
                                         coverage_limit=limit, deductible=policy.deductible,
                                         currency=self._settings.currency, precision=self._settings.money_precision)
        result.decision = decide(result.completeness, result.validations, result.payout)
        if result.decision.outcome == Outcome.REJECTED:
            result.payout = None  # never present a payout figure for a rejected claim
        self._audit.record(ref, "payout", "COMPLETE" if result.payout and result.payout.complete else "NONE")


def _rule_passed(result: ClaimResult, rule_id: str) -> bool:
    return any(r.rule_id == rule_id and r.status == RuleStatus.PASSED for r in result.validations)


def _placeholder_decision() -> ClaimDecision:
    return ClaimDecision(outcome=Outcome.PENDING, internal_status=InternalStatus.HUMAN_REVIEW,
                         rationale="Processing not started.", recommended_next_action="Process the claim.")
