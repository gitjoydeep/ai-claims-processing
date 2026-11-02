"""Deterministic money parsing and formatting (Decimal only, Indian number system)."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_MULTIPLIERS = {"lakh": Decimal("100000"), "lakhs": Decimal("100000"), "lac": Decimal("100000"),
                "crore": Decimal("10000000"), "crores": Decimal("10000000")}
_NUMBER_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(lakhs?|lac|crores?)?", re.IGNORECASE)
AMOUNT_IN_TEXT_RE = re.compile(r"(?:Rs\.?|INR|₹)\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)


def parse_amount(text: str | None) -> Decimal | None:
    """Parse an amount phrase such as 'Rs. 1,20,000' or '2.5 lakh' into a Decimal.

    Returns None when no number is present or when the phrase contains more than one
    distinct number (ambiguous). Never guesses.
    """
    if not text:
        return None
    matches = _NUMBER_RE.findall(text)
    values = set()
    for number, unit in matches:
        try:
            value = Decimal(number.replace(",", ""))
        except InvalidOperation:
            continue
        if unit:
            value *= _MULTIPLIERS[unit.lower()]
        values.add(value)
    if len(values) != 1:
        return None
    return values.pop()


def quantize(amount: Decimal, precision: Decimal = Decimal("0.01")) -> Decimal:
    return amount.quantize(precision, rounding=ROUND_HALF_UP)


def format_inr(amount: Decimal) -> str:
    """Format using Indian digit grouping: Decimal('120000') -> 'Rs. 1,20,000.00'."""
    amount = quantize(amount)
    sign = "-" if amount < 0 else ""
    integer, _, fraction = f"{abs(amount):.2f}".partition(".")
    if len(integer) > 3:
        head, tail = integer[:-3], integer[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        integer = ",".join(groups + [tail])
    return f"{sign}Rs. {integer}.{fraction}"


def amounts_in_text(text: str) -> set[Decimal]:
    """All currency-prefixed amounts mentioned in a text (used to audit generated emails)."""
    found = set()
    for raw in AMOUNT_IN_TEXT_RE.findall(text):
        try:
            found.add(quantize(Decimal(raw.replace(",", ""))))
        except InvalidOperation:
            continue
    return found
