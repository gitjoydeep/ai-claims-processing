"""V3 classification logic and policy catalog integrity."""

import ast
import re

import pytest

from claims.classification import ClaimClassifier, claim_summary, combine
from claims.config import PROJECT_ROOT
from claims.errors import PolicyNotFoundError
from claims.extraction import ground_extraction
from claims.llm.prompts import FEW_SHOT_EXAMPLES, few_shot_classification_system
from claims.models import ClassificationResult, ExtractedClaimData
from claims.policy.catalog import DETERMINABLE_EXCLUSIONS, INSURANCE_POLICIES, REQUIREMENT_SPECS, get_policy
from tests.conftest import FakeLLM
from tests.fixtures.llm_responses import CASES


def res(method, t, c=0.9):
    return ClassificationResult(method=method, insurance_type=t, confidence=c, reason="r")


def test_agreeing_confident_methods_are_reliable():
    out = combine(res("zero_shot", "Home Insurance"), res("few_shot", "Home Insurance"), 0.7)
    assert out.reliable and out.final_type == "Home Insurance"


@pytest.mark.parametrize("zero, few, why", [
    (res("zero_shot", "Home Insurance"), res("few_shot", "Vehicle Insurance"), "disagree"),
    (res("zero_shot", "Uncertain"), res("few_shot", "Uncertain"), "Uncertain"),
    (res("zero_shot", "Life Insurance", 0.5), res("few_shot", "Life Insurance", 0.9), "below"),
])
def test_unreliable_classification_is_flagged_not_forced(zero, few, why):
    out = combine(zero, few, 0.7)
    assert not out.reliable and out.final_type is None and why in out.note


@pytest.mark.parametrize("subject", list(CASES))
def test_classifier_on_all_four_categories(subject, dataset):
    email = next(e for e in dataset if e.subject == subject)
    extraction = ground_extraction(ExtractedClaimData(**CASES[subject]["extraction"]), email)
    out = ClaimClassifier(FakeLLM()).classify(extraction)
    assert out.final_type == CASES[subject]["type"]


def test_classification_input_excludes_pii(dataset):
    case = CASES["Request to initiate death claim"]
    summary = claim_summary(ground_extraction(ExtractedClaimData(**case["extraction"]), dataset[8]))
    flat = str(summary)
    # Identity fields are not sent; free-text descriptions are sent because classification needs them.
    assert "Lakshmi" not in flat and "LIFE-2026-4001" not in flat and "Ramesh Kumar'" not in flat
    assert "customer_name" not in summary and "policy_number" not in summary


def test_confidence_clamped():
    llm = FakeLLM({"classification_zero_shot": lambda s, u: {"insurance_type": "Home Insurance", "confidence": 7,
                                                             "reason": "x"}})
    case = CASES["Water damage in my bathroom"]
    from claims.ingestion import parse_email
    e = parse_email("Subject: Water damage in my bathroom\n\n" + "x")
    out = ClaimClassifier(llm).zero_shot(ground_extraction(ExtractedClaimData(**case["extraction"]), e))
    assert out.confidence == 1.0


def test_few_shot_prompt_contains_examples_not_dataset_emails(dataset):
    prompt = few_shot_classification_system()
    assert all(text in prompt for text, _, _ in FEW_SHOT_EXAMPLES)
    assert all(e.body[:60] not in prompt for e in dataset)


def _policy_dict_from_rtf() -> dict:
    raw = (PROJECT_ROOT / "Policy.rtf").read_text(encoding="utf-8")
    body = raw[raw.index("insurance_policies = \\{") + len("insurance_policies = "):]
    body = body.replace("\\{", "{").replace("\\}", "}").replace("\\\n", "\n")
    body = body[: body.rindex("}")]  # drop RTF closing brace
    body = re.sub(r"#.*", "", body)
    return ast.literal_eval(body)


@pytest.mark.skipif(not (PROJECT_ROOT / "Policy.rtf").exists(), reason="Policy.rtf source file not present")
def test_policy_dictionary_matches_supplied_policy_file():
    assert INSURANCE_POLICIES == _policy_dict_from_rtf()


def test_requirement_specs_cover_every_policy_requirement():
    for t, policy in INSURANCE_POLICIES.items():
        assert [s.requirement for s in REQUIREMENT_SPECS[t]] == policy["claim_requirements"]


def test_determinable_exclusions_are_real_policy_exclusions():
    all_exclusions = {x for p in INSURANCE_POLICIES.values() for x in p["exclusions"]}
    assert DETERMINABLE_EXCLUSIONS <= all_exclusions
    assert "Fraudulent claims" not in DETERMINABLE_EXCLUSIONS


def test_unknown_policy_type():
    with pytest.raises(PolicyNotFoundError):
        get_policy("Pet Insurance")
