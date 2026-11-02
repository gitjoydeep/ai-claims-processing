"""Task 4: claim classification with zero-shot and few-shot prompting, plus comparison.

Final category is accepted only when both methods agree and both are confident (rule CLS-001);
otherwise the claim is flagged for review instead of forcing a category.
"""

from __future__ import annotations

from .llm.client import LLMClient
from .llm.prompts import ZERO_SHOT_CLASSIFICATION_SYSTEM, classification_user, few_shot_classification_system
from .models import (INSURANCE_TYPES, ClaimExtraction, ClassificationLLMOutput, ClassificationOutcome,
                     ClassificationResult)


def claim_summary(extraction: ClaimExtraction) -> dict:
    """Only extracted, claim-relevant fields are sent for classification (no raw email, no PII beyond need)."""
    d = extraction.data
    return {
        "event_description": d.event_description,
        "loss_damage_details": d.loss_damage_details,
        "event_location": d.event_location,
        "supporting_documents": d.supporting_documents,
        "vehicle_registration_present": bool(d.vehicle_registration),
        "hospital_name": d.hospital_name,
        "insured_person_named": bool(d.insured_person_name),
        "claimant_relationship": d.claimant_relationship,
    }


class ClaimClassifier:
    def __init__(self, llm: LLMClient, confidence_threshold: float = 0.7):
        self._llm = llm
        self._threshold = confidence_threshold

    def _run(self, method: str, system: str, summary: dict) -> ClassificationResult:
        out = self._llm.complete_structured(system=system, user=classification_user(summary),
                                            response_model=ClassificationLLMOutput,
                                            schema_name=f"classification_{method}")
        confidence = min(max(out.confidence, 0.0), 1.0)
        return ClassificationResult(method=method, insurance_type=out.insurance_type, confidence=confidence,
                                    reason=out.reason)

    def zero_shot(self, extraction: ClaimExtraction) -> ClassificationResult:
        return self._run("zero_shot", ZERO_SHOT_CLASSIFICATION_SYSTEM, claim_summary(extraction))

    def few_shot(self, extraction: ClaimExtraction) -> ClassificationResult:
        return self._run("few_shot", few_shot_classification_system(), claim_summary(extraction))

    def classify(self, extraction: ClaimExtraction) -> ClassificationOutcome:
        return combine(self.zero_shot(extraction), self.few_shot(extraction), self._threshold)


def combine(zero: ClassificationResult, few: ClassificationResult, threshold: float) -> ClassificationOutcome:
    """Deterministic reconciliation of the two classifiers (rule CLS-001)."""
    agreed = zero.insurance_type == few.insurance_type
    valid = zero.insurance_type in INSURANCE_TYPES
    confident = min(zero.confidence, few.confidence) >= threshold
    reliable = agreed and valid and confident
    if reliable:
        note = f"Zero-shot and few-shot agree on {zero.insurance_type} with confidence >= {threshold}."
    elif not agreed:
        note = f"Methods disagree (zero-shot: {zero.insurance_type}, few-shot: {few.insurance_type})."
    elif not valid:
        note = "Both methods returned Uncertain."
    else:
        note = f"Methods agree but confidence is below {threshold}."
    return ClassificationOutcome(zero_shot=zero, few_shot=few, final_type=zero.insurance_type if reliable else None,
                                 agreed=agreed, reliable=reliable, note=note)
