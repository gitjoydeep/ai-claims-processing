"""LLM-assisted evidence gathering for coverage and exclusions.

The LLM may only pick coverage/exclusion names from the policy's own enumerated lists (enforced by
a per-policy Literal schema) and must quote the email verbatim. It makes no decision; the
deterministic validator verifies the quotes and applies the rules.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, create_model

from ..ingestion import wrap_for_prompt
from ..llm.client import LLMClient
from ..llm.prompts import POLICY_MATCH_SYSTEM, policy_match_user
from ..models import ExclusionFinding, PolicyMatch, RawEmail
from .catalog import Policy

_STRICT = ConfigDict(extra="forbid")


def build_match_model(policy: Policy) -> type[BaseModel]:
    coverage_enum = Literal[policy.covered_claim_categories]  # type: ignore[valid-type]
    exclusion_enum = Literal[policy.exclusions]  # type: ignore[valid-type]
    finding = create_model("ExclusionFindingLLM", __config__=_STRICT, exclusion=(exclusion_enum, ...),
                           evidence=(str, ...))
    return create_model("PolicyMatchLLM", __config__=_STRICT,
                        matched_coverages=(list[coverage_enum], ...),
                        coverage_evidence=(Optional[str], ...),
                        exclusion_findings=(list[finding], ...),
                        reason=(str, ...))


class PolicyMatcher:
    def __init__(self, llm: LLMClient):
        self._llm = llm

    def match(self, email: RawEmail, policy: Policy) -> PolicyMatch:
        model = build_match_model(policy)
        out = self._llm.complete_structured(
            system=POLICY_MATCH_SYSTEM,
            user=policy_match_user(policy.insurance_type, list(policy.covered_claim_categories),
                                   list(policy.exclusions), wrap_for_prompt(email)),
            response_model=model,
            schema_name="policy_match",
        )
        return PolicyMatch(
            matched_coverages=list(dict.fromkeys(out.matched_coverages)),
            coverage_evidence=out.coverage_evidence,
            exclusion_findings=[ExclusionFinding(exclusion=f.exclusion, evidence=f.evidence)
                                for f in out.exclusion_findings],
            reason=out.reason,
        )
