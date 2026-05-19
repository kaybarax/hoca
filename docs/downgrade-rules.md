# HOCA Downgrade Rules

Downgrade rules let the manager defer inconsequential reviewer findings to PR
tech debt instead of forcing another repair round. The canonical machine-readable
catalog lives in `hoca/downgrade_rules.py` as `DOWNGRADE_RULES`.

## Allowed Downgrades

| Rule ID | Summary |
| --- | --- |
| `low_maintainability_tech_debt` | Low-severity maintainability findings become PR follow-up |
| `nit_style_tech_debt` | Nit-severity style findings become PR follow-up |
| `low_nit_general_tech_debt` | Other eligible low/nit findings outside blocked categories |

Eligible findings use severity `low` or `nit` and are not security findings.
Correctness findings above low severity always require repair or explicit rejection.

## Never Downgraded By Default

| Rule ID | Summary |
| --- | --- |
| `security_never_downgraded` | All security-category findings |
| `correctness_above_low_never_downgraded` | Correctness at critical, high, or medium severity |

Security findings cannot be created at low or nit severity in structured reports;
use a non-security category for minor observations.

## Manager Reasoning And PR Preservation

| Rule ID | Summary |
| --- | --- |
| `manager_reasoning_required` | Each downgrade appends deterministic reasoning to the manager decision |
| (helper) | `merge_downgraded_findings_into_pr_notes` copies downgraded findings into `pr_notes.known_followups` |

Downgraded finding IDs are also stored on `HocaManagerDecision.downgraded_to_pr_notes`
so they are not silently discarded.

## Related Code

- `hoca/downgrade_rules.py` — catalog, eligibility, PR note merge helpers
- `hoca/arbitration.py` — manager decision logic consuming downgrade rules
- `tests/test_downgrade_rules.py` — per-rule coverage
