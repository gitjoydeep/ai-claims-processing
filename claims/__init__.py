"""AI-Assisted Insurance Claims Processing System.

Pipeline: email -> extraction (LLM) -> completeness (rules) -> classification (LLM)
-> policy validation (rules + LLM evidence matching) -> payout (Decimal) -> decision (rules)
-> customer email (LLM prose, verified facts) -> outbox (drafts).
"""

__version__ = "1.0.0"
