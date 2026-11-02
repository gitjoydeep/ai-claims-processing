"""Typed exceptions. Messages never contain customer PII."""


class ClaimsError(Exception):
    """Base class for all application errors."""


class MalformedEmailError(ClaimsError):
    """The input email is empty, too large or cannot be parsed."""


class LLMResponseError(ClaimsError):
    """The LLM was unavailable or returned output that failed schema validation after retries."""


class PolicyNotFoundError(ClaimsError):
    """No policy definition exists for the requested insurance type."""


class FinancialInputError(ClaimsError):
    """A monetary input is invalid (e.g. negative)."""
