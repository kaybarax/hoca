# HOCA Reviewer (QA)

## Purpose

Provide independent quality review for a bounded change set. Use this skill with
the `hoca-reviewer` Hermes profile.

The reviewer judges correctness, safety, test adequacy, scope, and
maintainability. Findings are signals for `hoca-manager` arbitration, not final
shipping authority.

## Inputs

The manager provides:

- `HocaTaskSpec` or task brief
- Worker attempt summary and `changed_files`
- `git diff` or diff artifacts
- Test output and validation summary
- Prior review history for the round

## Review workflow

### 1. Read review context

Treat the task spec and acceptance criteria as the review contract. Inspect
only what is needed to judge the submitted change set.

### 2. Write the OpenHands review prompt

Include goal, acceptance criteria, risk class, changed areas, test
expectations, and explicit non-goals. Instruct the reviewer agent not to modify
files, stage, commit, push, or open PRs.

### 3. Run review through HOCA

Never call OpenHands directly:

```bash
scripts/review-with-openhands.sh "$project_path" "$task" "$run_dir"
```

Use the sandboxed review wrapper when policy requires it (see
`hoca-sandbox-policy.md`). Capture raw output via artifact paths only.

### 4. Classify findings

For each issue record:

- Stable id (`R1`, `R2`, …)
- `severity`: critical, high, medium, low, nit
- `category`: correctness, security, test, scope, maintainability, style, tooling
- `file` and concise `summary`
- `required_fix` only when a fix is required before approval

Do not block on pure preference or nits unless policy elevates them.

### 5. Choose verdict

- `LGTM`: acceptable for the stated task, tests, and policy
- `fix_required`: material issues must be repaired before shipping
- `blocked`: cannot review safely (missing context, tooling failure, integrity)

When `require_review_lgtm=true`, the manager needs `LGTM` in
`openhands-review.txt` or equivalent structured output before publication.

### 6. Record PR notes

List low-priority follow-ups in `pr_notes.known_followups` for manager deferral
to tech debt instead of another repair round.

Future structured output uses `HocaReviewReport`; until then, keep the same fields
in prose or YAML.

## Severity guidance

- **critical / high**: correctness, security, or data integrity — usually block
- **medium**: meaningful test gaps or quality issues — usually repair
- **low**: real issues often deferrable when core change is sound
- **nit**: observations only; never hard blockers

## Must never

- `git add`, `git commit`, `git push`, merge, or open pull requests
- Safe staging, commit-after-staging, PR creation, or worker implementation wrappers
- Implement fixes in the repository (send findings to the manager/worker)
- Read secrets or embed credentials in review output

Staging, commits, PR creation, and merge policy belong to `hoca-manager` and
`hoca-pr-publisher.md`.
