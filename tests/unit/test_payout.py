"""V6: financial calculation verification. Formula: max(0, min(loss, limit) - deductible)."""

from decimal import Decimal as D

import pytest

from claims.errors import FinancialInputError
from claims.payout import calculate_payout
from claims.policy.catalog import get_policy
from claims.policy.validator import select_coverage_limit


def calc(claimed, limit="500000", deductible="5000", name="household_items"):
    return calculate_payout(claimed_amount=None if claimed is None else D(claimed), coverage_limit_name=name,
                            coverage_limit=None if limit is None else D(limit),
                            deductible=None if deductible is None else D(deductible))


@pytest.mark.parametrize("claimed, expected", [
    ("3000", "0.00"),         # 1. below deductible -> floored at zero
    ("5000", "0.00"),         # 2. equal to deductible
    ("85000", "80000.00"),    # 3. above deductible (worked example)
    ("600000", "495000.00"),  # 4. above limit: min(600000, 500000) - 5000
    ("500000", "495000.00"),  # 5. equal to limit
])
def test_deductible_and_limit_boundaries(claimed, expected):
    result = calc(claimed)
    assert result.complete
    assert result.payout == D(expected)
    assert result.payout >= 0


def test_below_deductible_explains_floor():
    assert any("floored" in s for s in calc("1000").steps)


def test_above_limit_explains_cap():
    assert any("capped" in s for s in calc("900000").steps)


def test_zero_deductible_medical_and_life():
    assert calc("120000", limit="500000", deductible="0").payout == D("120000.00")
    assert calc("1500000", limit="1500000", deductible="0").payout == D("1500000.00")


def test_formula_caps_loss_before_deductible_when_loss_exceeds_limit():
    # min(loss, limit) - deductible gives 495000; min(loss - deductible, limit) would give 500000.
    assert calc("700000").payout == D("495000.00")


@pytest.mark.parametrize("missing", ["claimed", "limit", "deductible"])
def test_missing_financial_input_marks_incomplete(missing):
    args = {"claimed": "1000", "limit": "500000", "deductible": "5000"}
    args[missing] = None
    result = calc(args["claimed"], args["limit"], args["deductible"])
    assert not result.complete and result.payout is None and result.missing_inputs


@pytest.mark.parametrize("field", ["claimed", "limit", "deductible"])
def test_negative_inputs_rejected(field):
    args = {"claimed": "1000", "limit": "500000", "deductible": "5000"}
    args[field] = "-1"
    with pytest.raises(FinancialInputError):
        calc(args["claimed"], args["limit"], args["deductible"])


def test_float_inputs_rejected():
    with pytest.raises(FinancialInputError):
        calculate_payout(claimed_amount=1000.0, coverage_limit_name="x", coverage_limit=D("1"), deductible=D("0"))


def test_rounding_half_up_to_paise():
    assert calc("10000.005", deductible="0").payout == D("10000.01")
    assert calc("10000.004", deductible="0").payout == D("10000.00")


def test_calculation_records_inputs_and_steps():
    r = calc("45000")
    assert (r.eligible_loss, r.coverage_limit, r.capped_loss, r.deductible) == (D("45000.00"), D("500000.00"),
                                                                               D("45000.00"), D("5000.00"))
    assert r.steps[-1] == "Potential payout = Rs. 40,000.00"
    assert r.rule_refs


def test_home_sub_limit_selected_when_claim_within_lowest():
    name, value, rule = select_coverage_limit(get_policy("Home Insurance"), D("85000"))
    assert (name, value, rule.status.value) == ("household_items", D("500000"), "PASSED")


def test_home_sub_limit_ambiguous_above_lowest_needs_breakdown():
    name, value, rule = select_coverage_limit(get_policy("Home Insurance"), D("700000"))
    assert name is None and rule.status.value == "NEEDS_REVIEW" and "breakdown" in rule.outstanding_item


def test_single_limit_policies():
    for t, expected in [("Vehicle Insurance", "vehicle"), ("Medical Insurance", "hospitalisation"),
                        ("Life Insurance", "sum_assured")]:
        assert select_coverage_limit(get_policy(t), D("1"))[0] == expected
