"""Processing audit trail (JSONL). Records step, status and rule IDs only, never email text,
names, policy numbers or medical details."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, path: Path | None = None):
        self._path = path
        self.records: list[dict[str, Any]] = []
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, claim_reference: str, step: str, status: str, **detail: Any) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "claim": claim_reference,
                 "step": step, "status": status, **detail}
        self.records.append(entry)
        if self._path:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
