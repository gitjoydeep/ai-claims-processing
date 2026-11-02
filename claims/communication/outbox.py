"""Email delivery module: draft outbox with idempotency.

No email-sending integration or credentials were supplied, so messages are stored as drafts for
authorised review. A message whose idempotency key already exists is not written again.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import CustomerEmail


class DraftOutbox:
    def __init__(self, directory: Path | None):
        self._dir = directory
        self._index: dict[str, str] = {}
        if directory:
            directory.mkdir(parents=True, exist_ok=True)
            index_file = directory / "index.json"
            if index_file.exists():
                self._index = json.loads(index_file.read_text(encoding="utf-8"))

    def submit(self, email: CustomerEmail) -> CustomerEmail:
        if email.idempotency_key in self._index:
            return email.model_copy(update={"delivery_status": "DUPLICATE_SUPPRESSED"})
        filename = f"{email.claim_reference}_{email.email_type.value.lower()}.md"
        self._index[email.idempotency_key] = filename
        if self._dir:
            (self._dir / filename).write_text(
                f"<!-- status: DRAFT | generated_by: {email.generated_by} | key: {email.idempotency_key} -->\n"
                f"**Subject:** {email.subject}\n\n{email.body}\n", encoding="utf-8")
            (self._dir / "index.json").write_text(json.dumps(self._index, indent=2), encoding="utf-8")
        return email
