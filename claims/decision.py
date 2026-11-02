"""Claim decision engine (pure). Maps completeness + rule results + payout to a customer-facing
outcome and a separate internal review status.

Priority:
  1. Mandatory details missing/invalid            -> PENDING (follow-up for all six details)
  2. A rejection rule (EXC-001) conclusively FAILED -> REJECTED
  3. Any other rule not PASSED/NOT_APPLICABLE,
     or payout not computable                    -> PENDING (+ HUMAN_REVIEW when a rule asks for it)
  4. Otherwise                                    -> APPROVED with payout
Only rules in REJECTION_RULES can ever produce REJECTED; missing information never does.
"""

from __future__ import annotations

from .models import (ClaimDecision, CompletenessResult, InternalStatus, Outcome, PayoutAssessment, RuleResult,
                     RuleStatus)
from .money import format_inr

REJECTION_RULES = frozenset({"EXC-001"})


def decide(completeness: CompletenessResult, validations: list[RuleResult] | None = None,
           payout: PayoutAssessment | None = None) -> ClaimDecision:
    validations = validations or []

    if not completeness.is_complete:
        problems = completeness.problem_fields
        return ClaimDecision(
            outcome=Outcome.PENDING, internal_status=InternalStatus.AUTO_DECIDED,
            rationale="Mandatory claim details are missing or unclear: "
                      + ", ".join(f"{p.label} ({p.status.value.lower()})" for p in problems) + ".",
            failed_rules=[p.note.split("]")[0].strip("[") for p in problems],
            outstanding_items=[p.label for p in problems],
            recommended_next_action="Send follow-up email requesting the mandatory claim details.",
        )

    rejections = [r for r in validations if r.rule_id in REJECTION_RULES and r.status == RuleStatus.FAILED]
    if rejections:
        return ClaimDecision(
            outcome=Outcome.REJECTED, internal_status=InternalStatus.AUTO_DECIDED,
            rationale=" ".join(r.explanation for r in rejections),
            failed_rules=[r.rule_id for r in rejections],
            recommended_next_action="Send rejection email citing the policy exclusion.",
        )

    blockers = [r for r in validations if r.status in (RuleStatus.FAILED, RuleStatus.NEEDS_REVIEW)]
    reasons = [f"{r.rule_id}: {r.explanation}" for r in blockers]
    review_reasons = [f"{r.rule_id}: {r.explanation}" for r in blockers if r.requires_human_review]
    outstanding = list(dict.fromkeys(r.outstanding_item for r in blockers if r.outstanding_item))

    payout_blocked = payout is None or not payout.complete
    if payout_blocked and not blockers:  # blockers already explain why there is no payout
        missing = ", ".join(payout.missing_inputs) if payout else "no assessment available"
        reasons.append(f"FIN-002: payout could not be calculated ({missing}).")
        review_reasons.append(f"FIN-002: payout could not be calculated ({missing}).")

    if reasons:
        needs_review = bool(review_reasons)
        if needs_review and outstanding:
            action = "Route to a claims handler for review and request the outstanding items from the customer."
        elif needs_review:
            action = "Route to a claims handler for review; inform the customer the claim is under review."
        else:
            action = "Send email requesting the outstanding items."
        return ClaimDecision(
            outcome=Outcome.PENDING,
            internal_status=InternalStatus.HUMAN_REVIEW if needs_review else InternalStatus.AUTO_DECIDED,
            rationale="Claim cannot be finalised yet. " + " ".join(reasons),
            failed_rules=[r.rule_id for r in blockers],
            outstanding_items=outstanding,
            review_reasons=review_reasons,
            recommended_next_action=action,
        )

    return ClaimDecision(
        outcome=Outcome.APPROVED, internal_status=InternalStatus.AUTO_DECIDED,
        rationale=(f"All applicable policy rules passed. Potential payout {format_inr(payout.payout)} "
                   f"calculated as min(eligible loss, limit) - deductible."),
        recommended_next_action="Send approval email and initiate settlement of the potential payout.",
    )
