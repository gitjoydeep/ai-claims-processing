"""Application settings. Secrets come only from the environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    model: str = "openai/gpt-oss-120b"
    reasoning_effort: str = "low"
    temperature: float = 0.0
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 6
    classification_confidence_threshold: float = 0.7
    currency: str = "INR"
    money_precision: Decimal = Decimal("0.01")
    max_email_chars: int = 20_000
    processing_date: date = field(default_factory=date.today)
    dataset_path: Path = PROJECT_ROOT / "insurance_claim_emails.txt"
    outputs_dir: Path = PROJECT_ROOT / "outputs"
    use_llm_for_customer_emails: bool = True


def load_settings(**overrides) -> Settings:
    """Load .env (searching upwards from the working directory) and build Settings."""
    load_dotenv(find_dotenv(usecwd=True))
    env_model = os.environ.get("CLAIMS_LLM_MODEL")
    if env_model and "model" not in overrides:
        overrides["model"] = env_model
    return Settings(**overrides)


def get_groq_api_key() -> str:
    """Return the Groq key or raise a clear error; never log or echo the value."""
    load_dotenv(find_dotenv(usecwd=True))
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set. Add it to the .env file (see .env.example).")
    return key
