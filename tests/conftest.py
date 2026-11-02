from __future__ import annotations

import json
from datetime import date
from typing import Callable

import pytest

from claims.communication.composer import render_template
from claims.config import PROJECT_ROOT, Settings
from claims.ingestion import load_dataset
from claims.models import RawEmail
from tests.fixtures.llm_responses import CASES

PROCESSING_DATE = date(2026, 11, 1)


class FakeLLM:
    """Implements the LLMClient protocol. Handlers map schema_name -> fn(system, user) -> dict | Exception."""

    def __init__(self, handlers: dict[str, Callable[[str, str], object]] | None = None):
        self.handlers = {**default_handlers(), **(handlers or {})}
        self.calls: list[tuple[str, str, str]] = []

    def complete_structured(self, *, system, user, response_model, schema_name):
        self.calls.append((schema_name, system, user))
        out = self.handlers[schema_name](system, user)
        if isinstance(out, Exception):
            raise out
        return response_model.model_validate(out)


def _case_by_subject(user: str) -> dict:
    for subject, case in CASES.items():
        if f"Subject: {subject}" in user:
            return case
    raise KeyError("Unknown email in fake LLM prompt")


def _case_by_description(user: str) -> dict:
    for case in CASES.values():
        if case["extraction"]["event_description"] in user:
            return case
    raise KeyError("Unknown claim in fake classification prompt")


def _classification(user: str) -> dict:
    case = _case_by_description(user)
    return {"insurance_type": case["type"], "confidence": 0.95, "reason": "fixture"}


def _email(system: str, user: str) -> dict:
    facts = json.loads(user.split("VERIFIED FACTS:\n", 1)[1])
    subject, body = render_template(facts)
    return {"subject": subject, "body": body}


def default_handlers() -> dict:
    return {
        "claim_extraction": lambda s, u: _case_by_subject(u)["extraction"],
        "classification_zero_shot": lambda s, u: _classification(u),
        "classification_few_shot": lambda s, u: _classification(u),
        "policy_match": lambda s, u: _case_by_subject(u)["match"],
        "customer_email": _email,
    }


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(processing_date=PROCESSING_DATE, outputs_dir=tmp_path / "outputs",
                    dataset_path=PROJECT_ROOT / "insurance_claim_emails.txt")


@pytest.fixture
def dataset(settings) -> list[RawEmail]:
    return load_dataset(settings.dataset_path)


@pytest.fixture
def email_by_id(dataset) -> dict[str, RawEmail]:
    return {e.email_id: e for e in dataset}


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()
