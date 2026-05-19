# HOCA Hard Blocker Rules

Hard blockers prevent a HOCA run from opening a normal ready PR. The canonical
machine-readable catalog lives in `hoca/hard_blockers.py` as `HARD_BLOCKER_RULES`.

## Validation Hard Blockers

| ID | Disposition | Summary |
| --- | --- | --- |
| `secret_file_change` | absolute | Secret-like file changes in the task diff |
| `secret_access_attempt` | absolute | Attempted read/write of secret-like files |
| `unsafe_filesystem_access` | absolute | Unsafe filesystem or command activity |
| `unreviewed_changed_files` | repairable | Changed files not reviewed this round |
| `unaccounted_staged_files` | repairable | Staged files outside the intended list |
| `test_failure` | repairable | Current-task validation tests failed |
| `dirty_unrelated_work` | repairable | Dirty unrelated workspace changes |
| `detached_head` | absolute | Detached HEAD without explicit allowance |
| `missing_pr_credentials` | absolute | PR creation required but credentials missing |
| `scope_risk` | repairable | Unexplained scope or infra churn |
| `staging_risk` | repairable | Staging plan risk before manager staging |

Absolute validation blockers stop the run immediately. Repairable validation
blockers may return `repair_required` before the round cap, but still block at
round 3.

## Review Finding Hard Blockers

| ID | Summary |
| --- | --- |
| `severe_correctness_finding` | Critical correctness defect |
| `security_regression_finding` | Security finding at critical or high severity |

Any critical-severity finding is treated as a hard blocker. Security regressions
also hard-block at high severity.

## Monitor Stop Reason Mapping

| Monitor `stop_reason` | Hard blocker ID |
| --- | --- |
| `secret_access` | `secret_access_attempt` |
| `unrelated_directory` | `unsafe_filesystem_access` |
| `dangerous_command` | `unsafe_filesystem_access` |

## Related Code

- `hoca/hard_blockers.py` — catalog, `ValidationStatus`, collection helpers
- `hoca/arbitration.py` — manager decision logic consuming hard blockers
- `tests/test_hard_blockers.py` — per-rule coverage
