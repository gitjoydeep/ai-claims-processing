# Policy Validation and Financial Calculation

The policy terms are stored verbatim in `claims/policy/catalog.py::INSURANCE_POLICIES`.

Every rule returns a `RuleResult` with these fields: `rule_id`, `description`, `inputs`, `status` (PASSED / FAILED / NEEDS_REVIEW / NOT_APPLICABLE), `explanation`, `evidence`, `source`, `outstanding_item` and `requires_human_review`.

## 1. Rule catalogue

| Rule | Module | Logic | Outcome effect |
|---|---|---|---|
| CMP-001…006 | `completeness.py` | The six mandatory fields must be present and grounded. The amount must be > 0 and the date must not be in the future. | Any failure → **PENDING**. The follow-up lists all six fields and marks the missing ones. Classification and policy checks are skipped. |
| POL-001 | `policy/validator.py` | Policy number matches `^(HOME\|VEH\|MED\|LIFE)-\d{4}-\d{4}$` (assumption A-3, based on the sample data). | FAILED → PENDING (the customer is asked for a valid policy number). |
| POL-002 | validator | The policy-number prefix matches the classified type. | NEEDS_REVIEW → PENDING + HUMAN_REVIEW |
| POL-003 | validator | Status, effective/expiry dates and holder match. | Always NOT_APPLICABLE: no policy register was supplied (decision D-1). |
| CLS-001 | `classification.py` | Zero-shot and few-shot agree, the type is not "Uncertain", and confidence ≥ 0.7 for both. | NEEDS_REVIEW → PENDING + HUMAN_REVIEW. The policy steps are skipped. |
| COV-001 | validator | At least one matched coverage from the policy's own list, with a quote that appears verbatim in the email. | NEEDS_REVIEW → PENDING + HUMAN_REVIEW |
| EXC-001 | validator | The exclusion comes from the policy's own list and its quote appears verbatim in the email. | Determinable exclusion → **FAILED → REJECTED**. Non-determinable exclusion → NEEDS_REVIEW. Unverified suggestions are discarded. |
| DOC-001…00n | validator | One rule per `claim_requirements` entry, with event-aware applicability (decision D-2). | FAILED → PENDING, with the item requested from the customer |
| FIN-001 | validator | Selects the applicable coverage limit (see §3). | NEEDS_REVIEW → PENDING (the customer is asked for a breakdown) |
| FIN-002 | `decision.py` | The payout must be computable. | Otherwise → PENDING + HUMAN_REVIEW |

**Determinable exclusions** can be established from what the claimant states, so they can lead to an automatic rejection:
- Poor maintenance
- Normal wear and tear
- Intentional damage
- Driving without a valid licence
- Driving under the influence
- Mechanical breakdown
- Cosmetic procedures
- Non-medical expenses

**Review-only exclusions** never cause an automatic rejection:
- Fraudulent claims
- Material misrepresentation
- Suicide during the applicable exclusion period (the period is undefined)
- Flood damage without additional flood cover (add-on status unknown)
- Treatment without medical necessity (needs a clinical opinion)

**Event-aware document rules (D-2):**
- *FIR for theft claims* applies only when *Vehicle theft* matched.
- *FIR for accidental death claims* applies only when *Accidental death* matched.
- For vehicle claims, *Photos of damage* and *Repair estimate* are waived when the only match is *Vehicle theft*, because a stolen car cannot be photographed or repaired.
- Documents are matched by keywords against the documents the customer says are attached. An FIR number also satisfies an FIR requirement.

## 2. Decision priority (`claims/decision.py`)

1. Mandatory details incomplete → **PENDING** (AUTO).
2. EXC-001 FAILED → **REJECTED**. EXC-001 is the only rule that can reject a claim, and missing information never does.
3. Any other FAILED or NEEDS_REVIEW rule, or a payout that can't be computed → **PENDING**. The internal status is HUMAN_REVIEW if any of those rules requests review.
4. Otherwise → **APPROVED** with a payout.

The internal review status (`AUTO_DECIDED` / `HUMAN_REVIEW`) is kept separate from the customer-facing outcome.

## 3. Financial calculation (`claims/payout.py`)

```
Eligible Loss    = Claimed Amount
Potential Payout = max(0, min(Eligible Loss, Coverage Limit) − Deductible)
```
- All amounts are `Decimal`, INR, quantised to 0.01 with ROUND_HALF_UP. Floats and negative inputs raise `FinancialInputError`.
- A missing input gives `complete=False` with `missing_inputs`. No amount is ever estimated.
- **Why cap before the deductible:** the alternative `min(loss − deductible, limit)` differs only when loss > limit (for example 7,00,000 on Home: 4,95,000 vs 5,00,000). The test `test_formula_caps_loss_before_deductible_when_loss_exceeds_limit` covers this.
- **Home sub-limits:** building is 25,00,000 and household_items is 5,00,000, and the policy gives no rule for choosing between them. If the claim is ≤ the lowest sub-limit, every sub-limit gives the same payout, so the lowest one (household_items) is applied. Otherwise the claim goes to PENDING and the customer is asked for a building/contents breakdown.
- Amounts are parsed in Python from the verbatim text the LLM copied ("Rs. 1,20,000" → 120000; "2.5 lakh" → 250000). An unparseable or ambiguous amount, or an amount not found in the email, is INVALID and the claim goes to PENDING.

### Results on the dataset

| Email | Claimed | Limit | Deductible | Payout |
|---|---|---|---|---|
| 1 Home (burst geyser) | 45,000 | 5,00,000 (household items) | 5,000 | **40,000** |
| 5 Vehicle (theft) | 6,50,000 | 8,00,000 | 2,000 | **6,48,000** |
| 7 Medical (surgery) | 1,20,000 | 5,00,000 | 0 | **1,20,000** |
| 9 Life (natural death) | 15,00,000 | 15,00,000 | 0 | **15,00,000** |
