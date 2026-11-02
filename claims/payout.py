"""Payout calculation. Pure Decimal arithmetic; never delegated to the LLM.

Formula:
    Potential Payout = max(0, min(Eligible Loss, Coverage Limit) - Deductible)
    Eligible Loss    = Claimed Amount
"""

from __future__ import annotations

from decimal import Decimal

from .errors import FinancialInputError
from .models import PayoutAssessment
from .money import format_inr, quantize


def calculate_payout(*, claimed_amount: Decimal | None, coverage_limit_name: str | None,
                     coverage_limit: Decimal | None, deductible: Decimal | None, currency: str = "INR",
                     precision: Decimal = Decimal("0.01")) -> PayoutAssessment:
    inputs = {"claimed amount": claimed_amount, "coverage limit": coverage_limit, "deductible": deductible}
    missing = [name for name, value in inputs.items() if value is None]
    if missing:
        return PayoutAssessment(complete=False, currency=currency, claimed_amount=claimed_amount,
                                coverage_limit_name=coverage_limit_name, coverage_limit=coverage_limit,
                                deductible=deductible, missing_inputs=missing,
                                steps=[f"Cannot calculate: missing {', '.join(missing)}."])
    for name, value in inputs.items():
        if not isinstance(value, Decimal):
            raise FinancialInputError(f"{name} must be a Decimal, got {type(value).__name__}.")
        if value < 0:
            raise FinancialInputError(f"{name} must not be negative.")

    claimed, limit, deductible = (quantize(v, precision) for v in (claimed_amount, coverage_limit, deductible))
    eligible = claimed
    capped = min(eligible, limit)
    raw_payout = capped - deductible
    payout = quantize(max(Decimal("0"), raw_payout), precision)

    steps = [
        f"Eligible loss = claimed amount = {format_inr(eligible)}",
        f"Coverage limit ({coverage_limit_name}) = {format_inr(limit)}",
        f"min(eligible loss, coverage limit) = {format_inr(capped)}",
        f"Deductible = {format_inr(deductible)}",
        f"{format_inr(capped)} - {format_inr(deductible)} = {format_inr(raw_payout)}",
    ]
    if raw_payout < 0:
        steps.append("Result is below zero, so the payout is floored at Rs. 0.00.")
    if eligible > limit:
        steps.append("Claimed amount exceeds the coverage limit; payout is capped at the limit.")
    steps.append(f"Potential payout = {format_inr(payout)}")

    return PayoutAssessment(complete=True, currency=currency, claimed_amount=claimed, eligible_loss=eligible,
                            coverage_limit_name=coverage_limit_name, coverage_limit=limit, capped_loss=capped,
                            deductible=deductible, payout=payout, steps=steps,
                            rule_refs=["Payout formula", "Policy coverage_limits", "Policy deductible"])
