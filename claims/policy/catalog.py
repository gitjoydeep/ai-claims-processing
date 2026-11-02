"""Policy information management (policy details stored in Python dictionaries).

`INSURANCE_POLICIES` is transcribed verbatim from the source policy document; an optional unit test
asserts equality with that file when it is present. Everything below it is *interpretation metadata* needed to apply
those terms deterministically. Each item is documented in docs/policy_and_financial_rules.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from ..errors import PolicyNotFoundError

POLICY_SOURCE = "Insurance policy catalog"

# --- verbatim from the policy document ------------------------------------------------------------
INSURANCE_POLICIES = {
    "Home Insurance": {
        "covered_claim_categories": [
            "Fire and smoke damage",
            "Accidental water leakage from burst pipes",
            "Damage to walls, floors and household items",
            "Theft involving forced entry",
            "Storm damage",
        ],
        "coverage_limits": {"building": 2500000, "household_items": 500000},
        "deductible": 5000,
        "exclusions": [
            "Poor maintenance",
            "Normal wear and tear",
            "Intentional damage",
            "Flood damage without additional flood cover",
        ],
        "claim_requirements": ["Policy number", "Incident date", "Photos of damaged items", "Repair estimate"],
    },
    "Vehicle Insurance": {
        "covered_claim_categories": [
            "Accidental vehicle damage",
            "Vehicle theft",
            "Fire damage",
            "Natural disaster damage",
            "Windshield and glass damage",
        ],
        "coverage_limits": {"vehicle": 800000},
        "deductible": 2000,
        "exclusions": [
            "Driving without a valid licence",
            "Driving under the influence",
            "Normal wear and tear",
            "Mechanical breakdown",
        ],
        "claim_requirements": [
            "Policy number",
            "Vehicle registration number",
            "Incident date",
            "Photos of damage",
            "Repair estimate",
            "FIR for theft claims",
        ],
    },
    "Medical Insurance": {
        "covered_claim_categories": ["Hospitalisation", "Surgery", "Emergency treatment", "Inpatient medical treatment"],
        "coverage_limits": {"hospitalisation": 500000},
        "deductible": 0,
        "exclusions": ["Cosmetic procedures", "Non-medical expenses", "Treatment without medical necessity"],
        "claim_requirements": [
            "Policy number",
            "Patient details",
            "Hospital admission date",
            "Discharge summary",
            "Hospital bills",
            "Medical reports",
        ],
    },
    "Life Insurance": {
        "covered_claim_categories": ["Natural death", "Accidental death", "Covered terminal illness"],
        "coverage_limits": {"sum_assured": 1500000},
        "deductible": 0,
        "exclusions": ["Fraudulent claims", "Material misrepresentation", "Suicide during the applicable exclusion period"],
        "claim_requirements": [
            "Policy number",
            "Insured person's details",
            "Nominee details",
            "Death certificate",
            "FIR for accidental death claims",
        ],
    },
}

# --- interpretation metadata (documented assumptions) -----------------------------------

POLICY_NUMBER_PREFIX = {"HOME": "Home Insurance", "VEH": "Vehicle Insurance", "MED": "Medical Insurance",
                        "LIFE": "Life Insurance"}
POLICY_NUMBER_RE = re.compile(r"^(HOME|VEH|MED|LIFE)-\d{4}-\d{4}$")

# Exclusions that can be conclusively established from facts the claimant states. All others
# (fraud, misrepresentation, undefined periods, unknown add-on cover, medical necessity) need an
# investigator and therefore route to human review, never to an automatic rejection.
DETERMINABLE_EXCLUSIONS = frozenset({
    "Poor maintenance", "Normal wear and tear", "Intentional damage",
    "Driving without a valid licence", "Driving under the influence", "Mechanical breakdown",
    "Cosmetic procedures", "Non-medical expenses",
})


@dataclass(frozen=True)
class RequirementSpec:
    """How to check one `claim_requirements` entry.

    kind="field": satisfied by any of `fields` being present in the extraction.
    kind="document": satisfied by a supporting document matching any `keywords`
                     (or any of `fields`, e.g. an FIR number).
    Event-aware applicability: `applies_if_any` -> only when one of these coverages matched;
    `waived_if_only` -> not applicable when the matched coverages are a subset of this set.
    """
    requirement: str
    kind: str
    fields: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    applies_if_any: frozenset[str] = field(default_factory=frozenset)
    waived_if_only: frozenset[str] = field(default_factory=frozenset)


_PHOTO = ("photo", "photograph", "picture", "image")
_ESTIMATE = ("estimate", "quotation", "quote")
_FIR = ("fir", "police complaint", "police report", "first information report")

REQUIREMENT_SPECS: dict[str, list[RequirementSpec]] = {
    "Home Insurance": [
        RequirementSpec("Policy number", "field", fields=("policy_number",)),
        RequirementSpec("Incident date", "field", fields=("event_date",)),
        RequirementSpec("Photos of damaged items", "document", keywords=_PHOTO),
        RequirementSpec("Repair estimate", "document", keywords=_ESTIMATE),
    ],
    "Vehicle Insurance": [
        RequirementSpec("Policy number", "field", fields=("policy_number",)),
        RequirementSpec("Vehicle registration number", "field", fields=("vehicle_registration",)),
        RequirementSpec("Incident date", "field", fields=("event_date",)),
        RequirementSpec("Photos of damage", "document", keywords=_PHOTO, waived_if_only=frozenset({"Vehicle theft"})),
        RequirementSpec("Repair estimate", "document", keywords=_ESTIMATE, waived_if_only=frozenset({"Vehicle theft"})),
        RequirementSpec("FIR for theft claims", "document", keywords=_FIR, fields=("fir_number",),
                        applies_if_any=frozenset({"Vehicle theft"})),
    ],
    "Medical Insurance": [
        RequirementSpec("Policy number", "field", fields=("policy_number",)),
        RequirementSpec("Patient details", "field", fields=("customer_name", "insured_person_name")),
        RequirementSpec("Hospital admission date", "field", fields=("admission_date", "event_date")),
        RequirementSpec("Discharge summary", "document", keywords=("discharge",)),
        RequirementSpec("Hospital bills", "document", keywords=("bill", "invoice", "receipt")),
        RequirementSpec("Medical reports", "document", keywords=("medical report", "reports", "test report",
                                                                 "lab report", "investigation report")),
    ],
    "Life Insurance": [
        RequirementSpec("Policy number", "field", fields=("policy_number",)),
        RequirementSpec("Insured person's details", "field", fields=("insured_person_name",)),
        RequirementSpec("Nominee details", "field", fields=("claimant_relationship",)),
        RequirementSpec("Death certificate", "document", keywords=("death certificate",)),
        RequirementSpec("FIR for accidental death claims", "document", keywords=_FIR, fields=("fir_number",),
                        applies_if_any=frozenset({"Accidental death"})),
    ],
}


@dataclass(frozen=True)
class Policy:
    insurance_type: str
    covered_claim_categories: tuple[str, ...]
    coverage_limits: dict[str, Decimal]
    deductible: Decimal
    exclusions: tuple[str, ...]
    claim_requirements: tuple[str, ...]
    requirement_specs: tuple[RequirementSpec, ...]


def get_policy(insurance_type: str) -> Policy:
    raw = INSURANCE_POLICIES.get(insurance_type)
    if raw is None:
        raise PolicyNotFoundError(f"No policy definition for insurance type '{insurance_type}'.")
    return Policy(
        insurance_type=insurance_type,
        covered_claim_categories=tuple(raw["covered_claim_categories"]),
        coverage_limits={k: Decimal(str(v)) for k, v in raw["coverage_limits"].items()},
        deductible=Decimal(str(raw["deductible"])),
        exclusions=tuple(raw["exclusions"]),
        claim_requirements=tuple(raw["claim_requirements"]),
        requirement_specs=tuple(REQUIREMENT_SPECS[insurance_type]),
    )
