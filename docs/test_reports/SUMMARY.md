# Test Execution Summary (2026-11-01)

| Suite | Command | Result |
|---|---|---|
| Offline unit + integration (fake LLM) | `python -m pytest -m "not live" --cov=claims` | **157 passed**, 1 skipped (optional check against the source policy file), 97% line coverage. See `offline_test_report.txt` |
| Live end-to-end (Groq `openai/gpt-oss-120b`) | `RUN_LIVE_LLM=1 python -m pytest -m live` | **11 passed** in 3 min 50 s (10 dataset emails + 1 prompt injection). See `live_e2e_report.txt` |
| Live CLI run | `python -m claims run` | Outcomes match the expected results for 10/10 emails. Classification: zero-shot 10/10, few-shot 10/10 |
| Notebook | `jupyter nbconvert --execute notebooks/claims_walkthrough.ipynb` | Executed with 0 errors |

## Coverage of the verification checklist
- **V2–V4 (email processing and missing information):** extraction grounding, the six mandatory fields, invalid, future and zero values, optional fields, and malformed input.
- **V3 (classification):** all 4 categories, disagreement, the Uncertain answer, low confidence and confidence clamping.
- **V5 (policy validation):** every rule ID, fabricated or out-of-policy exclusion quotes, review-only exclusions, event-aware documents, and determinism.
- **V6 (financial):** all 11 listed cases. "Previously consumed limits" is N/A because no data was supplied, which is documented.
- **V7 (decisions):** all 3 outcomes, missing information vs. rejection priority, and the review flag kept separate from the outcome.
- **V8 (emails):** the correct email type per outcome, invented amounts, contradictory wording, LLM outage fallback, duplicate suppression and restart persistence.
- **V10 (security):** prompt injection (offline and live), delimiter escaping, PII-free audit and classification input, and no hardcoded secrets.
- **V11 (end to end):** every dataset email, offline and live.

## Issues found during testing (and fixed)
1. Groq free-tier rate limits failed 5 of 10 claims in the first live run. The fail-safe worked: those claims went to PENDING with HUMAN_REVIEW and no email was sent. Fix: retries now honour `retry-after` and there are more retry attempts.
2. The LLM shortened a coverage quote with "...", so verification rejected it and email 2 went to review. Fix: ellipsis fragments are now verified in order, and the prompt asks for continuous quotes.
3. Errors were reported under the wrong step name. Fixed with step tracking.
4. The verifier for pending emails rejected a valid LLM draft because its outcome-word list was too narrow. The template fallback produced a correct email in the meantime. Fix: the accepted wording was widened.
