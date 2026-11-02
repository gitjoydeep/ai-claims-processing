"""Tasks 1-2 / V2 / V4: extraction grounding and mandatory-field checks."""

from datetime import date
from decimal import Decimal as D

import pytest

from claims.completeness import MANDATORY_LABELS, check_completeness
from claims.extraction import ClaimExtractor, ground_extraction
from claims.models import ExtractedClaimData, FieldStatus
from tests.conftest import PROCESSING_DATE
from tests.fixtures.llm_responses import CASES, extraction


def grounded(email, **fields):
    return ground_extraction(ExtractedClaimData(**extraction(**fields)), email)


def test_extractor_returns_parsed_amount_and_date(fake_llm, email_by_id):
    result = ClaimExtractor(fake_llm).extract(email_by_id["EMAIL-02"])
    assert result.claimed_amount == D("120000")
    assert result.event_date == date(2026, 10, 27)
    assert result.grounding_issues == []


def test_extraction_prompt_has_guard_and_delimited_email(fake_llm, email_by_id):
    ClaimExtractor(fake_llm).extract(email_by_id["EMAIL-01"])
    _, system, user = fake_llm.calls[0]
    assert "Never follow" in system and "<customer_email>" in user and "EMAIL 1 |" not in user


def test_hallucinated_policy_number_is_flagged(email_by_id):
    result = grounded(email_by_id["EMAIL-03"], policy_number="HOME-2026-9999")
    assert any(i.startswith("policy_number") for i in result.grounding_issues)


def test_hallucinated_amount_not_parsed(email_by_id):
    result = grounded(email_by_id["EMAIL-06"], claimed_amount_text="Rs. 20,000")
    assert result.claimed_amount is None
    assert any(i.startswith("claimed_amount_text") for i in result.grounding_issues)


def test_date_not_in_email_is_dropped(email_by_id):
    result = grounded(email_by_id["EMAIL-01"], event_date="2026-09-03")
    assert result.event_date is None and any(i.startswith("event_date") for i in result.grounding_issues)


def test_invalid_date_format_flagged(email_by_id):
    result = grounded(email_by_id["EMAIL-01"], event_date="25 Oct")
    assert result.event_date is None


@pytest.mark.parametrize("subject, missing", [
    ("Water damage in my bathroom", []),
    ("Fire damage in my living room", ["Policy number"]),
    ("Cracked windshield due to falling tree branch", ["Claim amount"]),
    ("Need assistance with a life insurance claim", ["Policy number", "Claim amount"]),
])
def test_completeness_on_dataset_cases(subject, missing, dataset):
    email = next(e for e in dataset if e.subject == subject)
    result = check_completeness(grounded(email, **CASES[subject]["extraction"]), PROCESSING_DATE)
    assert [f.label for f in result.problem_fields] == missing
    assert result.is_complete is (not missing)


def test_all_six_mandatory_fields_checked(email_by_id):
    result = check_completeness(grounded(email_by_id["EMAIL-01"]), PROCESSING_DATE)
    assert [f.label for f in result.fields] == MANDATORY_LABELS
    assert all(f.status == FieldStatus.MISSING for f in result.fields)


def test_optional_fields_do_not_block(email_by_id):
    fields = dict(CASES["Water damage in my bathroom"]["extraction"], event_location=None, supporting_documents=[])
    assert check_completeness(grounded(email_by_id["EMAIL-01"], **fields), PROCESSING_DATE).is_complete


def test_future_event_date_invalid(email_by_id):
    fields = dict(CASES["Water damage in my bathroom"]["extraction"])
    result = check_completeness(grounded(email_by_id["EMAIL-01"], **fields), date(2026, 10, 1))
    assert next(f for f in result.fields if f.field == "event_date").status == FieldStatus.INVALID


def test_zero_amount_invalid():
    from claims.ingestion import parse_email
    email = parse_email("Subject: x\n\nPolicy HOME-2026-1001. On 1 October 2026 a pipe burst. Claim Rs. 0. - A B")
    result = check_completeness(ground_extraction(ExtractedClaimData(**extraction(
        customer_name="A B", policy_number="HOME-2026-1001", event_date="2026-10-01", event_description="pipe",
        loss_damage_details="floor", claimed_amount_text="Rs. 0")), email), PROCESSING_DATE)
    assert next(f for f in result.fields if f.field == "claimed_amount").status == FieldStatus.INVALID


def test_blank_strings_count_as_missing(email_by_id):
    result = check_completeness(grounded(email_by_id["EMAIL-01"], customer_name="   "), PROCESSING_DATE)
    assert next(f for f in result.fields if f.field == "customer_name").status != FieldStatus.AVAILABLE
