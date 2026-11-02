"""LLM access behind a small protocol so business logic can be tested with a fake client.

Follows the reference notebook: Groq + strict JSON-schema structured output + Pydantic validation.
Adds bounded retries and typed errors instead of bare excepts.
"""

from __future__ import annotations

import copy
import logging
import time
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import Settings, get_groq_api_key
from ..errors import LLMResponseError

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    def complete_structured(self, *, system: str, user: str, response_model: type[T], schema_name: str) -> T:
        """Return an instance of `response_model` or raise LLMResponseError."""
        ...


def to_strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema -> strict-mode schema: refs inlined, all properties required, no extras."""
    schema = copy.deepcopy(model.model_json_schema())
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            node = {k: resolve(v) for k, v in node.items() if k != "default"}
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"].keys())
                node["additionalProperties"] = False
            return node
        if isinstance(node, list):
            return [resolve(n) for n in node]
        return node

    return resolve(schema)


def _backoff_seconds(error: Exception | None, attempt: int) -> float:
    """Honour the server's retry-after header (rate limits); otherwise exponential backoff."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or {}
    retry_after = headers.get("retry-after") if hasattr(headers, "get") else None
    try:
        if retry_after is not None:
            return min(float(retry_after) + 1.0, 90.0)
    except (TypeError, ValueError):
        pass
    return float(min(2 ** attempt, 30))


class GroqLLMClient:
    def __init__(self, settings: Settings, client=None):
        self._settings = settings
        if client is None:
            from groq import Groq

            client = Groq(api_key=get_groq_api_key(), timeout=settings.llm_timeout_seconds, max_retries=0)
        self._client = client

    def complete_structured(self, *, system: str, user: str, response_model: type[T], schema_name: str) -> T:
        schema = to_strict_schema(response_model)
        last_error: Exception | None = None
        for attempt in range(1, self._settings.llm_max_retries + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self._settings.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=self._settings.temperature,
                    reasoning_effort=self._settings.reasoning_effort,
                    include_reasoning=False,
                    response_format={"type": "json_schema",
                                     "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
                )
                content = response.choices[0].message.content or ""
                return response_model.model_validate_json(content)
            except ValidationError as exc:
                last_error = exc
                logger.warning("LLM output failed schema '%s' validation (attempt %d)", schema_name, attempt)
            except Exception as exc:  # network / API / rate-limit errors from the SDK
                last_error = exc
                logger.warning("LLM call '%s' failed (attempt %d): %s", schema_name, attempt, type(exc).__name__)
            if attempt < self._settings.llm_max_retries:
                time.sleep(_backoff_seconds(last_error, attempt))
        raise LLMResponseError(
            f"LLM step '{schema_name}' failed after {self._settings.llm_max_retries} attempts: "
            f"{type(last_error).__name__}") from last_error
