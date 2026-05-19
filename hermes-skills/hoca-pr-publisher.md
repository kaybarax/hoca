# HOCA PR Publisher

## Purpose

Manager-only Git publication after validation and review gates pass. Use this
skill with the `hoca-manager` Hermes profile only — never with worker or
reviewer profiles.

Publication includes safe staging, commit, pull request creation, optional
auto-merge, and run finalization.

## Prerequisites

Before publishing:

- Working tree reflects reviewed worker changes
- `require_tests=true` tests passed for current-task failures (or policy allows)
- `require_review_lgtm=true` review returned LGTM when enabled
- Intended files are enumerated and non-secret
- No hard blockers (secret paths, monitor stops, scope violations, ambiguous Git state)

## Safe staging

Never use:

```bash
git add .
git add -A
git add --all
git commit -am
```

Write:

```text
.hoca-runtime/runs/<run_id>/intended-files.txt
.hoca-runtime/runs/<run_id>/intended-files-source.txt
```

`intended-files-source.txt` must be `manager` or `reviewer`. Then:

```bash
scripts/safe-stage-after-review.sh "$project_path" "$task" "$run_dir" "$run_dir/intended-files.txt"
git diff --cached
```

Only stage files that are directly relevant, reviewed, non-secret, and
accounted for. Add `staging-justification.txt` when policy requires extra
justification for lockfiles, generated files, migrations, or infrastructure.

## Commit

After safe staging succeeds:

```bash
scripts/commit-after-staging.sh "$project_path" "$task" "$run_dir"
```

Pass `--issue-id "$issue_id"` when present. Confirm the commit hash is recorded in
the run directory.

## Create PR

```bash
scripts/create-pr.sh "$project_path" "$task" "$run_dir"
```

Pass `--issue-id "$issue_id"` when present. The PR body should include:

- Summary and changed areas
- Validation commands and results
- Review verdict and round count
- Accepted reviewer findings fixed
- Findings intentionally not fixed (with rationale)
- Known low-priority tech debt
- Risk notes and sandbox mode used
- Whether human review is required before merge

## Merge policy

Default: do not merge automatically.

- `auto_merge=false`: leave the PR open for human review
- `auto_merge=true`: queue GitHub auto-merge only when guarded prechecks pass

High-risk changes, failed tests, missing review LGTM, secret-like staged paths,
missing risk approval, or mergeability failures must leave the PR open.

## Token handling

- GitHub tokens are manager-only for this phase
- Never include tokens in prompts, logs, task reports, or PR bodies
- Worker and reviewer phases must not receive `GITHUB_TOKEN` by default

## Finalize

Update run status (`completed`, `blocked`, `failed`, `no_changes`,
`needs_human_staging`). Remove active lock files only after finalization. Keep
run logs for audit. Restore the configured development branch when policy allows.

## Must never

Delegate staging, commit, push, or PR creation to `hoca-worker` or
`hoca-reviewer`. Those profiles must not invoke this skill or its scripts.
