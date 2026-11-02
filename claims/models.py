"""Pydantic data models.

Two kinds of models live here:
* LLM-facing schemas (suffix `LLM`/`Data`): every field required, `extra="forbid"` so they can be
  sent as strict JSON schemas. They contain only what the model read from text.
* Domain models: produced by deterministic code (validation, payout, decision).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

InsuranceType = Literal["Home Insurance", "Vehicle Insurance", "Medical Insurance", "Life Insurance"]
INSURANCE_TYPES: tuple[str, ...] = ("Home Insurance", "Vehicle Insurance", "Medical Insurance", "Life Insurance")


class Outcome(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PENDING = "PENDING"


class InternalStatus(str, Enum):
    AUTO_DECIDED = "AUTO_DECIDED"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class RuleStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class FieldStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    INVALID = "INVALID"


class EmailType(str, Enum):
    APPROVAL = "APPROVAL"
    REJECTION = "REJECTION"
    PENDING_MISSING_DETAILS = "PENDING_MISSING_DETAILS"
    PENDING_OUTSTANDING_ITEMS = "PENDING_OUTSTANDING_ITEMS"


# ---------------------------------------------------------------- input


class RawEmail(BaseModel):
    email_id: str
    subject: str = ""
    body: str
    source_label: Optional[str] = Field(
        default=None, description="Dataset header label (ground truth). Never sent to the LLM.")

    @property
    def full_text(self) -> str:
        return f"Subject: {self.subject}\n\n{self.body}" if self.subject else self.body


# ---------------------------------------------------------------- LLM-facing schemas


class ExtractedClaimData(BaseModel):
    """What the LLM read from the email. Null means 'not stated by the customer'."""

    model_config = ConfigDict(extra="forbid")

    customer_name: Optional[str]
    policy_number: Optional[str]
    event_date: Optional[str] = Field(description="Date of the incident as YYYY-MM-DD, null if not stated")
    event_location: Optional[str]
    event_description: Optional[str]
    loss_damage_details: Optional[str]
    claimed_amount_text: Optional[str] = Field(description="Amount exactly as written in the email, e.g. 'Rs. 1,20,000'")
    supporting_documents: list[str] = Field(description="Documents the customer says are attached or already provided")
    pending_documents: list[str] = Field(description="Documents the customer says are not yet available")
    customer_request: Optional[str]
    intent: Literal["new_claim", "follow_up", "enquiry"]
    vehicle_registration: Optional[str]
    fir_number: Optional[str]
    hospital_name: Optional[str]
    admission_date: Optional[str]
    discharge_date: Optional[str]
    insured_person_name: Optional[str]
    claimant_relationship: Optional[str]
    ambiguities: list[str] = Field(description="Unclear or contradictory statements, quoted briefly")


class ClassificationLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insurance_type: Literal["Home Insurance", "Vehicle Insurance", "Medical Insurance", "Life Insurance", "Uncertain"]
    confidence: float = Field(description="0.0 to 1.0")
    reason: str


class EmailDraftLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    body: str


# ---------------------------------------------------------------- domain models


class ClaimExtraction(BaseModel):
    data: ExtractedClaimData
    claimed_amount: Optional[Decimal] = None
    event_date: Optional[date] = None
    grounding_issues: list[str] = Field(default_factory=list,
                                        description="Extracted values that could not be found in the email text")


class FieldCheck(BaseModel):
    field: str
    label: str
    status: FieldStatus
    note: str = ""


class CompletenessResult(BaseModel):
    is_complete: bool
    fields: list[FieldCheck]

    @property
    def problem_fields(self) -> list[FieldCheck]:
        return [f for f in self.fields if f.status != FieldStatus.AVAILABLE]


class ClassificationResult(BaseModel):
    method: Literal["zero_shot", "few_shot"]
    insurance_type: str
    confidence: float
    reason: str


class ClassificationOutcome(BaseModel):
    zero_shot: ClassificationResult
    few_shot: ClassificationResult
    final_type: Optional[InsuranceType]
    agreed: bool
    reliable: bool
    note: str


class ExclusionFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exclusion: str
    evidence: str = Field(description="Verbatim quote from the email supporting this exclusion")


class PolicyMatch(BaseModel):
    matched_coverages: list[str]
    coverage_evidence: Optional[str]
    exclusion_findings: list[ExclusionFinding]
    reason: str


class RuleResult(BaseModel):
    rule_id: str
    description: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    status: RuleStatus
    explanation: str
    evidence: Optional[str] = None
    source: str
    outstanding_item: Optional[str] = Field(default=None, description="What the customer must supply, if anything")
    requires_human_review: bool = False


class PayoutAssessment(BaseModel):
    complete: bool
    currency: str = "INR"
    claimed_amount: Optional[Decimal] = None
    eligible_loss: Optional[Decimal] = None
    coverage_limit_name: Optional[str] = None
    coverage_limit: Optional[Decimal] = None
    capped_loss: Optional[Decimal] = None
    deductible: Optional[Decimal] = None
    payout: Optional[Decimal] = None
    formula: str = "Potential Payout = max(0, min(Eligible Loss, Coverage Limit) - Deductible)"
    steps: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    rule_refs: list[str] = Field(default_factory=list)


class ClaimDecision(BaseModel):
    outcome: Outcome
    internal_status: InternalStatus
    rationale: str
    failed_rules: list[str] = Field(default_factory=list)
    outstanding_items: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    recommended_next_action: str


class CustomerEmail(BaseModel):
    email_type: EmailType
    claim_reference: str
    recipient_name: Optional[str]
    subject: str
    body: str
    generated_by: Literal["llm", "template"]
    fallback_reason: Optional[str] = None
    idempotency_key: str
    delivery_status: Literal["DRAFT", "DUPLICATE_SUPPRESSED"] = "DRAFT"


class ClaimResult(BaseModel):
    claim_reference: str
    email_id: str
    processed_at: datetime
    extraction: Optional[ClaimExtraction] = None
    completeness: Optional[CompletenessResult] = None
    classification: Optional[ClassificationOutcome] = None
    policy_match: Optional[PolicyMatch] = None
    validations: list[RuleResult] = Field(default_factory=list)
    payout: Optional[PayoutAssessment] = None
    decision: ClaimDecision
    communication: Optional[CustomerEmail] = None
    errors: list[str] = Field(default_factory=list)
