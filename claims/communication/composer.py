"""Tasks 3 & 7: customer email generation.

Facts are assembled deterministically from verified results. The LLM only writes prose around them
(RTCO prompt). Every LLM draft is checked: claim reference present, every amount mentioned is one
of the verified amounts, wording matches the outcome, and all requested items are listed. Any
failure (or LLM error) falls back to a deterministic template, so a correct email is always produced.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal

from ..completeness import MANDATORY_LABELS
from ..errors import LLMResponseError
from ..ingestion import text_contains
from ..llm.client import LLMClient
from ..llm.prompts import EMAIL_SYSTEM, email_user
from ..models import (ClaimDecision, CompletenessResult, CustomerEmail, EmailDraftLLM, EmailType, FieldStatus,
                      Outcome, PayoutAssessment, RuleResult, RuleStatus)
from ..money import amounts_in_text, format_inr, quantize

logger = logging.getLogger(__name__)


@dataclass
class EmailContext:
    claim_reference: str
    decision: ClaimDecision
    completeness: CompletenessResult
    customer_name: str | None = None
    policy_number: str | None = None
    insurance_type: str | None = None
    incident_summary: str | None = None
    payout: PayoutAssessment | None = None
    validations: list[RuleResult] = field(default_factory=list)


def email_type_for(decision: ClaimDecision, completeness: CompletenessResult) -> EmailType:
    if decision.outcome == Outcome.APPROVED:
        return EmailType.APPROVAL
    if decision.outcome == Outcome.REJECTED:
        return EmailType.REJECTION
    return EmailType.PENDING_MISSING_DETAILS if not completeness.is_complete else EmailType.PENDING_OUTSTANDING_ITEMS


def idempotency_key(ctx: EmailContext, email_type: EmailType) -> str:
    """Derived from the decision content (not LLM text) so a retried run maps to the same message."""
    payout = ctx.payout.payout if ctx.payout and ctx.payout.payout is not None else ""
    material = "|".join([ctx.claim_reference, email_type.value, str(payout),
                         ",".join(sorted(ctx.decision.outstanding_items)), ",".join(sorted(ctx.decision.failed_rules))])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def build_facts(ctx: EmailContext, email_type: EmailType) -> dict:
    facts: dict = {
        "email_type": email_type.value,
        "outcome": ctx.decision.outcome.value,
        "claim_reference": ctx.claim_reference,
        "customer_name": ctx.customer_name,
        "policy_number": ctx.policy_number,
        "insurance_type": ctx.insurance_type,
        "incident_summary": ctx.incident_summary,
    }
    if email_type == EmailType.APPROVAL:
        p = ctx.payout
        facts.update({
            "claimed_amount": format_inr(p.claimed_amount),
            "coverage_limit": f"{format_inr(p.coverage_limit)} ({p.coverage_limit_name.replace('_', ' ')})",
            "deductible": format_inr(p.deductible),
            "approved_potential_payout": format_inr(p.payout),
            "calculation": "min(claimed amount, coverage limit) - deductible",
            "matched_coverage": next((r.inputs.get("matched_coverages") for r in ctx.validations
                                      if r.rule_id == "COV-001"), []),
            "next_steps": "Our claims team will contact you to complete the settlement.",
        })
    elif email_type == EmailType.REJECTION:
        exc = next(r for r in ctx.validations if r.rule_id == "EXC-001" and r.status == RuleStatus.FAILED)
        facts.update({
            "policy_exclusions_applied": exc.inputs.get("conclusive", []),
            "reason": exc.explanation,
            "customer_statement_relied_on": exc.evidence,
            "next_steps": "If you believe any of the information we relied on is incorrect, please reply "
                          "to this email with the details and supporting documents.",
        })
    elif email_type == EmailType.PENDING_MISSING_DETAILS:
        facts.update({
            "details_to_confirm": MANDATORY_LABELS,
            "missing_details": [f.label for f in ctx.completeness.problem_fields],
            "how_to_respond": "Reply to this email with the details, quoting the claim reference.",
        })
    else:
        facts.update({
            "outstanding_items": ctx.decision.outstanding_items,
            "under_review": ctx.decision.internal_status.value == "HUMAN_REVIEW",
            "how_to_respond": "Reply to this email with the items, quoting the claim reference.",
        })
    return facts


def allowed_amounts(ctx: EmailContext) -> set[Decimal]:
    p = ctx.payout
    if not p or not p.complete:
        return set()
    return {quantize(v) for v in (p.claimed_amount, p.coverage_limit, p.deductible, p.payout, p.capped_loss)
            if v is not None}


# ------------------------------------------------------------------ deterministic templates


def _greeting(name: str | None) -> str:
    return f"Dear {name}," if name else "Dear Customer,"


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, start=1))


def render_template(facts: dict) -> tuple[str, str]:
    ref, kind = facts["claim_reference"], facts["email_type"]
    policy = f" (Policy {facts['policy_number']})" if facts.get("policy_number") else ""
    greeting = _greeting(facts.get("customer_name"))
    sign = "\n\nRegards,\nClaims Team"

    if kind == EmailType.APPROVAL.value:
        subject = f"Claim {ref} approved{policy}"
        body = (f"{greeting}\n\nThank you for submitting your claim. We have completed our assessment and are "
                f"pleased to inform you that your {facts['insurance_type']} claim {ref} has been approved.\n\n"
                f"Claim summary\n"
                f"- Incident: {facts.get('incident_summary') or 'As described in your email'}\n"
                f"- Covered under: {', '.join(facts['matched_coverage'])}\n"
                f"- Claimed amount: {facts['claimed_amount']}\n"
                f"- Coverage limit: {facts['coverage_limit']}\n"
                f"- Deductible: {facts['deductible']}\n"
                f"- Approved potential payout: {facts['approved_potential_payout']}\n\n"
                f"How this was calculated: {facts['calculation']}.\n\n"
                f"Next steps: {facts['next_steps']}" + sign)
    elif kind == EmailType.REJECTION.value:
        subject = f"Update on your claim {ref}{policy}"
        exclusions = ", ".join(facts["policy_exclusions_applied"])
        body = (f"{greeting}\n\nThank you for submitting your claim {ref}. After reviewing it against your "
                f"{facts['insurance_type']} policy, we regret to inform you that we are unable to approve it.\n\n"
                f"Reason: the incident falls under the policy exclusion(s): {exclusions}. "
                f"Your email states: \"{facts['customer_statement_relied_on']}\".\n\n"
                f"Next steps: {facts['next_steps']}" + sign)
    elif kind == EmailType.PENDING_MISSING_DETAILS.value:
        subject = f"Additional information required for your claim {ref}"
        missing = set(facts["missing_details"])
        items = [f"{label} (missing)" if label in missing else label for label in facts["details_to_confirm"]]
        context = f" regarding: {facts['incident_summary']}" if facts.get("incident_summary") else ""
        body = (f"{greeting}\n\nThank you for contacting us{context}.\n\nTo proceed with your insurance claim "
                f"(reference {ref}), please confirm or provide the following details:\n\n{_numbered(items)}\n\n"
                f"{facts['how_to_respond']} Once we receive these details, we can continue processing your claim."
                + sign)
    else:
        subject = f"Your claim {ref}: further information needed"
        review = ("\n\nYour claim is also being reviewed by a member of our claims team, who may contact you."
                  if facts.get("under_review") else "")
        items = facts["outstanding_items"]
        request = (f"To complete our assessment, please provide the following:\n\n{_numbered(items)}\n\n"
                   f"{facts['how_to_respond']}" if items else
                   "We will contact you if we need anything further.")
        body = (f"{greeting}\n\nThank you for submitting your claim {ref}{policy}. Your claim is currently "
                f"pending.\n\n{request}{review}" + sign)
    return subject, body


# ------------------------------------------------------------------ LLM output verification

_OUTCOME_MARKERS = {
    EmailType.APPROVAL: (r"\bapproved\b", None),
    EmailType.REJECTION: (r"\b(unable to approve|not able to approve|cannot approve|declined|rejected|not covered)\b",
                          r"\bhas been approved\b"),
    EmailType.PENDING_MISSING_DETAILS: (r"\b(provide|confirm|send)\b", r"\bhas been (approved|rejected)\b"),
    EmailType.PENDING_OUTSTANDING_ITEMS: (r"\b(pending|provide|review|send|submit|share|need|require[sd]?)\b",
                                          r"\bhas been (approved|rejected)\b"),
}


def verify_draft(subject: str, body: str, facts: dict, email_type: EmailType, allowed: set[Decimal]) -> list[str]:
    problems = []
    text = f"{subject}\n{body}"
    if facts["claim_reference"] not in text:
        problems.append("claim reference missing")
    unexpected = amounts_in_text(text) - allowed
    if unexpected:
        problems.append("contains unverified amounts")
    required, forbidden = _OUTCOME_MARKERS[email_type]
    if not re.search(required, text, re.IGNORECASE):
        problems.append("outcome wording missing")
    if forbidden and re.search(forbidden, text, re.IGNORECASE):
        problems.append("contradictory outcome wording")
    if email_type == EmailType.APPROVAL and not amounts_in_text(facts["approved_potential_payout"]) <= amounts_in_text(text):
        problems.append("approved payout amount missing")
    items = (facts.get("details_to_confirm") if email_type == EmailType.PENDING_MISSING_DETAILS
             else facts.get("outstanding_items") if email_type == EmailType.PENDING_OUTSTANDING_ITEMS else [])
    for item in items or []:
        if not text_contains(text, item.split(" (")[0]):
            problems.append(f"requested item not listed: {item}")
    return problems


class EmailComposer:
    def __init__(self, llm: LLMClient | None = None, use_llm: bool = True):
        self._llm = llm
        self._use_llm = use_llm and llm is not None

    def compose(self, ctx: EmailContext) -> CustomerEmail:
        email_type = email_type_for(ctx.decision, ctx.completeness)
        facts = build_facts(ctx, email_type)
        key = idempotency_key(ctx, email_type)
        base = dict(email_type=email_type, claim_reference=ctx.claim_reference, recipient_name=ctx.customer_name,
                    idempotency_key=key)

        fallback_reason = "LLM email generation disabled" if not self._use_llm else None
        if self._use_llm:
            try:
                draft = self._llm.complete_structured(system=EMAIL_SYSTEM, user=email_user(facts),
                                                      response_model=EmailDraftLLM, schema_name="customer_email")
                problems = verify_draft(draft.subject, draft.body, facts, email_type, allowed_amounts(ctx))
                if not problems:
                    return CustomerEmail(**base, subject=draft.subject.strip(), body=draft.body.strip(),
                                         generated_by="llm")
                fallback_reason = "LLM draft failed verification: " + "; ".join(problems)
            except LLMResponseError as exc:
                fallback_reason = f"LLM unavailable: {exc}"
            logger.info("Using template email for %s (%s)", ctx.claim_reference, fallback_reason)

        subject, body = render_template(facts)
        return CustomerEmail(**base, subject=subject, body=body, generated_by="template",
                             fallback_reason=fallback_reason)


def customer_name_if_verified(completeness: CompletenessResult, name: str | None) -> str | None:
    status = next((f.status for f in completeness.fields if f.field == "customer_name"), None)
    return name.strip() if name and status == FieldStatus.AVAILABLE else None
