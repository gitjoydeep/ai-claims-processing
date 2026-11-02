# Requirement Traceability Matrix (as built)

| Req | Description | Component | Rule | Tests |
|---|---|---|---|---|
| FR-01 | Read incoming email | `ingestion.py` | — | `test_money_ingestion.py::test_dataset_parsing_*`, `test_malformed_*`, `test_oversized_*` |
| FR-02/03 | Extract 9 fields into Pydantic | `extraction.py`, `models.ExtractedClaimData`, `llm/client.py` | — | `test_extractor_returns_parsed_amount_and_date`, `test_strict_schema_*`, live e2e |
| FR-04 | Do not invent information | `extraction.ground_extraction` | grounding | `test_hallucinated_policy_number_is_flagged`, `test_hallucinated_amount_not_parsed`, `test_date_not_in_email_is_dropped` |
| FR-05 | Mandatory-field check | `completeness.py` | CMP-001…006 | `test_completeness_on_dataset_cases`, `test_all_six_mandatory_fields_checked`, `test_optional_fields_do_not_block`, `test_future_event_date_invalid`, `test_zero_amount_invalid` |
| FR-06 | Follow-up email listing all six details | `composer.py` | — | `test_pending_missing_details_lists_all_six_and_marks_missing`, `test_end_to_end_each_dataset_email[EMAIL-03/06/10]` |
| FR-07 | Incomplete claims stop before classification | `workflow.py` | — | `test_incomplete_claims_skip_classification_and_policy` |
| FR-08/09 | Zero-shot and few-shot classification, compared | `classification.py`, `reporting.classification_report` | CLS-001 | `test_classifier_on_all_four_categories`, `test_unreliable_classification_is_flagged_not_forced`, `test_cli_run_writes_all_outputs`, live report |
| FR-10 | Policy dictionaries | `policy/catalog.py` | — | `test_policy_dictionary_matches_supplied_policy_file` |
| FR-11/12 | Coverage, exclusions, limits, deductibles | `policy/matcher.py`, `policy/validator.py` | POL-001/2/3, COV-001, EXC-001, DOC-*, FIN-001 | `test_validator_decision.py` (16 tests) |
| FR-13/14 | Payout formula and table | `payout.py` | FIN-001/002 | `test_payout.py` (24 cases, V6 items 1–11) |
| FR-15/16/17 | Approval and rejection emails, RTCO prompts | `composer.py`, `llm/prompts.py` | — | `test_approval_email_*`, `test_rejection_email_*`, `test_llm_draft_*` |
| NFR-DEC | Decision priority and review flag | `decision.py` | REJECTION_RULES | `test_decision_*`, `test_missing_mandatory_info_takes_priority_over_potential_rejection`, `test_review_flag_kept_separate_from_outcome` |
| NFR-IDEM | No duplicate emails | `outbox.py`, `idempotency_key` | — | `test_outbox_suppresses_duplicates`, `test_retry_does_not_duplicate_customer_email` |
| C3 | Prompt-injection protection | `prompts.INJECTION_GUARD`, `wrap_for_prompt` | — | `test_wrap_neutralises_delimiter_injection`, `test_prompt_injection_cannot_approve`, live `test_live_prompt_injection` |
| C8 | PII-free logs | `audit.py`, `classification.claim_summary` | — | `test_audit_trail_covers_every_stage_without_pii`, `test_classification_input_excludes_pii` |
| C9 | Fail-safe errors | `workflow.py`, `llm/client.py` | — | `test_llm_outage_is_fail_safe[*]`, `test_error_reports_the_failing_step`, `test_llm_client_*` |
| V11 | End-to-end for all 4 categories | `workflow.py` | all | `test_end_to_end_each_dataset_email[*]` (offline), `test_live_dataset_outcomes[*]` (live) |
