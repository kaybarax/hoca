# hoca-reviewer

Hermes profile for the HOCA independent reviewer. This profile inspects worker
output, coordinates OpenHands review, classifies findings by severity, and
returns structured review reports for manager arbitration.

## Files

- `SOUL.md` — stable reviewer identity and limits
- `config.example.yaml` — example Hermes settings scoped to review/QA

## Typical flow

1. Read task spec, diff, test output, and worker attempt report.
2. Run review through `scripts/review-with-openhands.sh`.
3. Return a `HocaReviewReport` with severities and required fixes.

Reviewer findings are quality signals for the manager, not commands. This
profile must not commit, push, open PRs, or expand task scope without manager
direction.
