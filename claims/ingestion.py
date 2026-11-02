"""Email ingestion and preprocessing.

* Parses the supplied dataset file (blocks separated by '====' lines).
* Removes the evaluation header ('EMAIL n | Category | Label') so the LLM never sees ground truth.
* Normalises and sanitises text, and wraps it in a delimited block for prompt-injection safety.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

from .errors import MalformedEmailError
from .models import RawEmail

_SEPARATOR_RE = re.compile(r"^\s*={10,}\s*$", re.MULTILINE)
_HEADER_RE = re.compile(r"^\s*EMAIL\s+(\d+)\s*\|(.+)$", re.IGNORECASE)
_SUBJECT_RE = re.compile(r"^\s*Subject:\s*(.*)$", re.IGNORECASE)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
EMAIL_TAG = "customer_email"


def normalise_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_CHARS_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_email(text: str, email_id: str | None = None, max_chars: int = 20_000) -> RawEmail:
    """Parse one raw email (optionally preceded by a dataset header line)."""
    if text is None or not text.strip():
        raise MalformedEmailError("Email is empty.")
    text = normalise_text(text)
    if len(text) > max_chars:
        raise MalformedEmailError(f"Email exceeds the maximum length of {max_chars} characters.")

    lines = text.split("\n")
    source_label = None
    header = _HEADER_RE.match(lines[0])
    if header:
        email_id = email_id or f"EMAIL-{int(header.group(1)):02d}"
        source_label = header.group(2).strip()
        lines = lines[1:]

    subject = ""
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        match = _SUBJECT_RE.match(line)
        if match:
            subject = match.group(1).strip()
            lines = lines[i + 1:]
        break

    body = "\n".join(lines).strip()
    if not body:
        raise MalformedEmailError("Email has no body text.")
    return RawEmail(email_id=email_id or content_id(text), subject=subject, body=body, source_label=source_label)


def load_dataset(path: Path, max_chars: int = 20_000) -> list[RawEmail]:
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")
    blocks = [b for b in _SEPARATOR_RE.split(path.read_text(encoding="utf-8")) if b.strip()]
    return [parse_email(block, max_chars=max_chars) for block in blocks]


def content_id(text: str) -> str:
    return hashlib.sha256(normalise_text(text).encode("utf-8")).hexdigest()[:10]


def claim_reference(email: RawEmail) -> str:
    """Deterministic claim reference: retries of the same email map to the same claim."""
    return "CLM-" + hashlib.sha256(email.full_text.encode("utf-8")).hexdigest()[:8].upper()


def wrap_for_prompt(email: RawEmail) -> str:
    """Delimit customer content; neutralise any attempt to close the delimiter early."""
    safe = email.full_text.replace(f"</{EMAIL_TAG}>", f"&lt;/{EMAIL_TAG}&gt;").replace(
        f"<{EMAIL_TAG}>", f"&lt;{EMAIL_TAG}&gt;")
    return f"<{EMAIL_TAG}>\n{safe}\n</{EMAIL_TAG}>"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[‘’`]", "'", s)
    s = re.sub(r"[“”]", '"', s)
    s = re.sub(r"[‐-―]", "-", s)
    return re.sub(r"\s+", " ", s).strip(" .,;:'\"")


def text_contains(haystack: str, needle: str | None) -> bool:
    """Whitespace/case-insensitive containment, used to verify the LLM quoted real text.

    A quote shortened with an ellipsis is accepted only if every fragment appears, in order.
    """
    if not needle:
        return False
    fragments = [_norm(f) for f in re.split(r"\.\.\.|…", needle) if _norm(f)]
    if not fragments:
        return False
    text = _norm(haystack)
    position = 0
    for fragment in fragments:
        found = text.find(fragment, position)
        if found < 0:
            return False
        position = found + len(fragment)
    return True
