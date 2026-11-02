"""Mandatory-field completeness check (pure Python rules CMP-001..CMP-006).

Mandatory fields: customer name, policy number, event date,
event description, loss/damage details, claim amount.
"""

from __future__ import annotations

from datetime import date

from .models import ClaimExtraction, CompletenessResult, FieldCheck, FieldStatus

MANDATORY_FIELDS: list[tuple[str, str, str]] = [
    ("CMP-001", "customer_name", "Customer name"),
    ("CMP-002", "policy_number", "Policy number"),
    ("CMP-003", "event_date", "Event date"),
    ("CMP-004", "event_description", "Event description"),
    ("CMP-005", "loss_damage_details", "Loss / damage details"),
    ("CMP-006", "claimed_amount", "Claim amount"),
]
MANDATORY_LABELS = [label for _, _, label in MANDATORY_FIELDS]


def _issue_for(extraction: ClaimExtraction, field: str) -> str | None:
    prefix = "claimed_amount_text" if field == "claimed_amount" else field
    return next((i for i in extraction.grounding_issues if i.startswith(prefix + ":")), None)


def check_completeness(extraction: ClaimExtraction, processing_date: date) -> CompletenessResult:
    data = extraction.data
    checks: list[FieldCheck] = []

    for rule_id, field, label in MANDATORY_FIELDS:
        issue = _issue_for(extraction, field)
        if field == "claimed_amount":
            raw, value = data.claimed_amount_text, extraction.claimed_amount
        elif field == "event_date":
            raw, value = data.event_date, extraction.event_date
        else:
            raw = getattr(data, field)
            value = raw.strip() if isinstance(raw, str) and raw.strip() else None

        if issue:
            status, note = FieldStatus.INVALID, issue.split(": ", 1)[1]
        elif value is None:
            status, note = (FieldStatus.MISSING, "not provided") if not raw else (FieldStatus.INVALID, "unclear value")
        elif field == "claimed_amount" and value <= 0:
            status, note = FieldStatus.INVALID, "amount must be greater than zero"
        elif field == "event_date" and value > processing_date:
            status, note = FieldStatus.INVALID, "event date is in the future"
        else:
            status, note = FieldStatus.AVAILABLE, ""
        checks.append(FieldCheck(field=field, label=label, status=status, note=f"[{rule_id}] {note}".strip()))

    return CompletenessResult(is_complete=all(c.status == FieldStatus.AVAILABLE for c in checks), fields=checks)
