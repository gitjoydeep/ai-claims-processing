"""CLI.

    python -m claims run                 # process all dataset emails, write outputs/
    python -m claims run --email 4       # process one dataset email
    python -m claims run --file my.txt   # process a single raw email file
    python -m claims run --template-emails   # deterministic customer emails (no LLM prose)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .audit import AuditLog
from .classification import ClaimClassifier
from .communication.outbox import DraftOutbox
from .config import load_settings
from .errors import ClaimsError
from .ingestion import load_dataset, parse_email
from .llm.client import GroqLLMClient
from .reporting import classification_report, summary_markdown, write_result
from .workflow import ClaimsProcessor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claims", description="AI-assisted insurance claims processing")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Process claim emails")
    run.add_argument("--email", type=int, help="Dataset email number (1-based)")
    run.add_argument("--file", type=Path, help="Path to a single raw email text file")
    run.add_argument("--template-emails", action="store_true", help="Use deterministic email templates only")
    run.add_argument("--no-compare", action="store_true", help="Skip zero-shot vs few-shot report")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings(use_llm_for_customer_emails=not args.template_emails)
    out = settings.outputs_dir

    try:
        if args.file:
            emails = [parse_email(args.file.read_text(encoding="utf-8"), max_chars=settings.max_email_chars)]
        else:
            emails = load_dataset(settings.dataset_path, settings.max_email_chars)
            if args.email:
                emails = [emails[args.email - 1]]
    except (ClaimsError, OSError, IndexError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2

    llm = GroqLLMClient(settings)
    processor = ClaimsProcessor(llm, settings, outbox=DraftOutbox(out / "outbox"), audit=AuditLog(out / "audit.jsonl"))

    pairs = []
    for email in emails:
        result = processor.process(email)
        write_result(result, out / "results")
        pairs.append((email, result))
        print(f"{email.email_id}: {result.decision.outcome.value} ({result.decision.internal_status.value})")

    summary = summary_markdown(pairs)
    (out / "summary.md").write_text("# Claims processing summary\n\n" + summary + "\n", encoding="utf-8")
    print("\n" + summary)

    if not args.no_compare:
        classifier = ClaimClassifier(llm, settings.classification_confidence_threshold)
        rows = []
        for email, result in pairs:
            if result.classification:
                rows.append((email, result.classification))
            elif result.extraction:  # incomplete claims: classified for the comparison report only
                rows.append((email, classifier.classify(result.extraction)))
        report = classification_report(rows)
        (out / "classification_report.md").write_text(report + "\n", encoding="utf-8")
        print("\n" + report.split("\n## Reasons")[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
