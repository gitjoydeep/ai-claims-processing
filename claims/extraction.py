"""Task 1: AI information extraction, followed by deterministic grounding checks.

The LLM reads the email; Python then verifies that key values actually appear in the email text
(anti-hallucination) and converts amounts/dates with deterministic parsers.
"""

from __future__ import annotations

import re
from datetime import date

from .ingestion import text_contains, wrap_for_prompt
from .llm.client import LLMClient
from .llm.prompts import EXTRACTION_SYSTEM, extraction_user
from .models import ClaimExtraction, ExtractedClaimData, RawEmail
from .money import parse_amount

GROUNDED_FIELDS = ("customer_name", "policy_number", "claimed_amount_text", "vehicle_registration", "fir_number")


class ClaimExtractor:
    def __init__(self, llm: LLMClient):
        self._llm = llm

    def extract(self, email: RawEmail) -> ClaimExtraction:
        data = self._llm.complete_structured(
            system=EXTRACTION_SYSTEM,
            user=extraction_user(wrap_for_prompt(email)),
            response_model=ExtractedClaimData,
            schema_name="claim_extraction",
        )
        return ground_extraction(data, email)


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _date_grounded(value: date, text: str) -> bool:
    """The day and year of an ISO date the LLM produced must appear in the email."""
    return bool(re.search(rf"\b0?{value.day}\b", text)) and str(value.year) in text


def ground_extraction(data: ExtractedClaimData, email: RawEmail) -> ClaimExtraction:
    text = email.full_text
    issues: list[str] = []

    for field in GROUNDED_FIELDS:
        value = getattr(data, field)
        if value and not text_contains(text, value):
            issues.append(f"{field}: value not found in the email text")

    claimed_amount = None
    if data.claimed_amount_text and "claimed_amount_text: value not found in the email text" not in issues:
        claimed_amount = parse_amount(data.claimed_amount_text)
        if claimed_amount is None:
            issues.append("claimed_amount_text: amount could not be parsed unambiguously")

    event_date = parse_iso_date(data.event_date)
    if data.event_date and event_date is None:
        issues.append("event_date: not a valid YYYY-MM-DD date")
    elif event_date and not _date_grounded(event_date, text):
        issues.append("event_date: date not found in the email text")
        event_date = None

    return ClaimExtraction(data=data, claimed_amount=claimed_amount, event_date=event_date, grounding_issues=issues)
