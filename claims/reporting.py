"""Run reports: per-claim JSON, a summary table, and the zero-shot vs few-shot comparison."""

from __future__ import annotations

import json
from pathlib import Path

from .models import ClaimResult, ClassificationOutcome, RawEmail
from .money import format_inr


def expected_type(email: RawEmail) -> str | None:
    if not email.source_label:
        return None
    return email.source_label.split("|")[0].strip()


def write_result(result: ClaimResult, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.email_id}_{result.claim_reference}.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path


def summary_markdown(pairs: list[tuple[RawEmail, ClaimResult]]) -> str:
    lines = ["| Email | Dataset label | Type | Outcome | Internal | Payout | Key reason | Email draft |",
             "|---|---|---|---|---|---|---|---|"]
    for email, r in pairs:
        payout = format_inr(r.payout.payout) if r.payout and r.payout.payout is not None and r.decision.outcome.value == "APPROVED" else "-"
        ctype = r.classification.final_type if r.classification and r.classification.final_type else "-"
        reason = (r.decision.failed_rules and ", ".join(r.decision.failed_rules)) or "all rules passed"
        if r.errors:
            reason = "ERROR: " + "; ".join(r.errors)
        draft = f"{r.communication.email_type.value} ({r.communication.generated_by})" if r.communication else "none"
        label = (email.source_label or "-").replace("|", "/")
        lines.append(f"| {email.email_id} | {label} | {ctype} | **{r.decision.outcome.value}** | "
                     f"{r.decision.internal_status.value} | {payout} | {reason} | {draft} |")
    return "\n".join(lines)


def classification_report(rows: list[tuple[RawEmail, ClassificationOutcome]]) -> str:
    def acc(method: str) -> str:
        scored = [(e, c) for e, c in rows if expected_type(e)]
        hits = sum(getattr(c, method).insurance_type == expected_type(e) for e, c in scored)
        return f"{hits}/{len(scored)}"

    agree = sum(c.agreed for _, c in rows)
    lines = [
        "# Classification: zero-shot vs few-shot",
        "",
        f"- Zero-shot accuracy vs dataset labels: **{acc('zero_shot')}**",
        f"- Few-shot accuracy vs dataset labels: **{acc('few_shot')}**",
        f"- Methods agreed: **{agree}/{len(rows)}**",
        f"- Reliable (agree and confidence above threshold): **{sum(c.reliable for _, c in rows)}/{len(rows)}**",
        "",
        "| Email | Expected | Zero-shot (conf.) | Few-shot (conf.) | Agree | Final |",
        "|---|---|---|---|---|---|",
    ]
    for e, c in rows:
        lines.append(f"| {e.email_id} | {expected_type(e) or '-'} | {c.zero_shot.insurance_type} "
                     f"({c.zero_shot.confidence:.2f}) | {c.few_shot.insurance_type} ({c.few_shot.confidence:.2f}) | "
                     f"{'yes' if c.agreed else 'no'} | {c.final_type or 'REVIEW'} |")
    lines += ["", "## Reasons", ""]
    for e, c in rows:
        lines.append(f"- **{e.email_id}**: zero-shot: {c.zero_shot.reason} | few-shot: {c.few_shot.reason}")
    return "\n".join(lines)


def write_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
