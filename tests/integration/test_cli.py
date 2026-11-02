"""CLI + reporting: full dataset run with a fake LLM writes results, summary, drafts and comparison."""

import json

import claims.__main__ as cli
from tests.conftest import FakeLLM


def test_cli_run_writes_all_outputs(settings, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda **kw: settings.__class__(**{**settings.__dict__, **kw}))
    monkeypatch.setattr(cli, "GroqLLMClient", lambda s: FakeLLM())
    assert cli.main(["run"]) == 0

    out = settings.outputs_dir
    results = sorted((out / "results").glob("*.json"))
    assert len(results) == 10
    first = json.loads(results[0].read_text())
    assert first["decision"]["outcome"] == "APPROVED" and first["payout"]["payout"] == "40000.00"
    summary = (out / "summary.md").read_text()
    assert summary.count("**APPROVED**") == 4 and summary.count("**REJECTED**") == 1
    report = (out / "classification_report.md").read_text()
    assert "Zero-shot accuracy vs dataset labels: **10/10**" in report
    assert len(list((out / "outbox").glob("*.md"))) == 10
    assert (out / "audit.jsonl").exists()


def test_cli_single_email_and_bad_input(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda **kw: settings)
    monkeypatch.setattr(cli, "GroqLLMClient", lambda s: FakeLLM())
    assert cli.main(["run", "--email", "4", "--no-compare"]) == 0
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    assert cli.main(["run", "--file", str(empty)]) == 2
    assert cli.main(["run", "--email", "99"]) == 2
