# HOCA PR Publisher

## Purpose

Manager-only Git publication after validation and review gates pass. Use this
skill with the `hoca-manager` Hermes profile only — never with worker or
reviewer profiles.

Publication is not a separate Hermes sub-agent. The manager decides whether to
publish; HOCA scripts perform safe staging, commit, push, pull request creation,
optional auto-merge, and run finalization.

## Related skills

| Skill | Role |
|-------|------|
| `hoca-manager.md` | Gates, arbitration, publication decision |
| `hoca-worker-openhands.md` | Implementation (no Git lifecycle) |
| `hoca-reviewer-qa.md` | Review and PR notes (no Git lifecycle) |
| `hoca-sandbox-policy.md` | Credential isolation for worker/reviewer vs manager |

## Parameters

The manager provides these at publication time:

- `project_path`: Repository root (must match `HocaTaskSpec.repo_root`)
- `task`: Human-readable task text (used for commit/PR metadata; must not contain secrets)
- `run_dir`: `.hoca-runtime/runs/<run_id>/`
- `issue_id`: Optional GitHub issue number
- `auto_merge`: Optional boolean (default `false`; must pass `auto-merge-guards.sh` prechecks)

Read publication policy from `.env` / environment:

- `HOCA_AUTO_MERGE`, `HOCA_REQUIRE_TESTS`, `HOCA_REQUIRE_REVIEW_LGTM`
- `HOCA_AUTO_STAGE_REVIEWED_CHANGES` — when true, shortcut may build intended-file lists from reviewed changes
- `HOCA_SYNC_DEV_BRANCH` — when true, sync the manager-resolved development branch before task-base checkout
- `HOCA_DEV_BRANCH` — optional override; prefer target repo `.hoca/config.toml` or `origin/HEAD`
- `HOCA_KEEP_RUNTIME` — when false, `.hoca-runtime` may be removed after successful PR creation

## Manager-only publication

PR publishing remains manager-owned:

- Only `hoca-manager` (or the unified `hoca.md` shortcut under manager policy) may
  invoke staging, commit, push, and PR scripts.
- Worker and reviewer profiles prepare artifacts (changed files, review reports,
  PR note suggestions) but must not run publication scripts.
- The manager must record `proceed_to_pr` or equivalent in `HocaManagerDecision`
  before publication when running step-by-step.

Worker and reviewer must refuse requests to stage, commit, push, or open PRs and
stop with `blocked` / `blocked_reason` pointing back to the manager.

## Prerequisites (safe staging)

Do not publish until all applicable gates pass:

| Gate | Requirement |
|------|-------------|
| Working tree | Reflects reviewed worker changes on the task branch (not default branch) |
| Tests | `require_tests=true` and current-task validation passed (`tests-summary.md`) |
| Review | `require_review_lgtm=true` and structured review returned `LGTM` when enabled |
| Monitor | No monitor stop signals or secret-path detections in run artifacts |
| Scope | Intended files enumerated; no hard blockers (ambiguous Git state, unexplained infra/lockfile changes) |
| Review gate | `safe-stage-after-review.sh` calls `hoca.review_gate` — staging refuses without approved review |

Before staging, ensure run artifacts exist:

- `changed-files.txt`, `git-diff.patch`, validation and review outputs
- `decisions/manager-decision-<round>.json` with publication decision when manual
- Reviewer `pr_notes` and `known_followups` for PR body follow-up sections

## Publication procedures

Follow these procedures in order when the manager publishes manually.

### 1. Confirm publication decision

Read `HocaManagerDecision` and validation/review artifacts. Publish only when:

- Decision is `proceed_to_pr`, or policy allows `draft_pr_with_blockers` with
  explicit residual risk documented in the PR body
- No hard blockers remain (secrets, scope violations, current-task test failures,
  severe security/correctness findings, missing `gh` auth)

Otherwise set run status to `blocked`, `failed`, `no_changes`, or
`needs_human_staging` and stop without pushing.

### 2. Build intended-file list

Never use blind staging:

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

Rules for `intended-files.txt`:

- One repo-relative path per line; comments allowed with `#`
- Every path must be directly relevant, reviewed, non-secret, and accounted for
- Must not include `.hoca-runtime/*`, `.git/*`, or other runtime/metadata paths
- Must match actual changed files (scripts detect unaccounted or unexpected paths)

`intended-files-source.txt` must be exactly `manager` or `reviewer` (reviewer may
suggest paths; manager still owns the publication decision).

Add `staging-justification.txt` when policy requires extra justification for
lockfiles, generated files, migrations, or infrastructure changes.

### 3. Safe stage

```bash
scripts/safe-stage-after-review.sh "$project_path" "$task" "$run_dir" "$run_dir/intended-files.txt"
git diff --cached
```

On failure: set status `needs_human_staging`, preserve `git-status.txt` and diff
artifacts, and escalate — do not fall back to blind `git add`.

Expect outputs: `staged-files.txt`, `intended-files.normalized.txt`, and staging
audit files under `$run_dir`.

### 4. Commit

After safe staging succeeds and `git diff --cached` looks correct:

```bash
scripts/commit-after-staging.sh "$project_path" "$task" "$run_dir"
```

Pass `--issue-id "$issue_id"` when present. The script verifies the staged index
matches `intended-files.normalized.txt`, keeps the commit message to a concise
task summary, refuses unsafe identifiers, and adds a `HOCA-Run: <run_id>` trailer
when the run id is available. Record the commit hash in run artifacts.

### 5. Create pull request

```bash
scripts/create-pr.sh "$project_path" "$task" "$run_dir"
```

Pass `--issue-id "$issue_id"` when present. Requires authenticated `gh` CLI.
Refuses PRs from detached HEAD or the repository default branch.

### 6. Merge policy

Default: do not merge automatically.

- `auto_merge=false`: leave the PR open for human review
- `auto_merge=true`: queue GitHub auto-merge only when `scripts/auto-merge-guards.sh`
  prechecks pass

Leave the PR open when any of these apply: high-risk changes, failed tests,
missing review LGTM, secret-like staged paths, missing risk approval, or GitHub
mergeability failures.

### 7. Cleanup and branch restoration

After publication or a terminal non-publish outcome:

1. Update run status: `completed`, `pr_created`, `draft_pr_created_with_blockers`,
   `blocked`, `failed`, `no_changes`, or `needs_human_staging`
2. Record final artifacts:

   ```bash
   python3 -m hoca.run_artifacts record-final "$run_dir"
   scripts/generate-task-report.sh "$project_path" "$run_dir"
   scripts/notify.sh "$project_path" "$run_dir"    # when notify_telegram=true
   ```

3. Remove active lock files only after finalization (owner token must match)
4. Keep run logs under `$run_dir` for audit unless `HOCA_KEEP_RUNTIME=true`
5. When `HOCA_KEEP_RUNTIME` is not true and publication completed via the shortcut,
   `.hoca-runtime` may be removed after PR creation — do not delete mid-run artifacts
   needed for human staging
6. Branch restoration: when the manager resolved a development branch and policy allows,
   check out that branch so the engineer returns to their normal working branch.
   Do not delete `main`, `master`, or the default branch. Task branches may be deleted
   by GitHub only when auto-merge completes with `--delete-branch` and repository
   settings allow it

Notification failure must not hide the task outcome. Reports and PR bodies must not
include secrets or huge log dumps.

## PR body requirements

`scripts/create-pr.sh` builds the PR body from `templates/PR_TEMPLATE.md` (project
copy preferred, then bundled HOCA template). Each section is filled from run
artifacts:

| Section | Source |
|---------|--------|
| Summary | Task one-liner (sanitized — no secrets in task text) |
| Changes | Commit log and diff stat vs base branch |
| Validation | `tests-summary.md` |
| Code Review | `openhands-review.txt` / structured review status |
| Risk | `risk-notes.txt` or explicit none |
| Linked Issue | `--issue-id` when provided |
| Auto-merge | Footer from `auto-merge-guards.sh` precheck result |

The manager (or reviewer suggestions absorbed by the manager) should ensure the PR
communicates:

- Summary and changed areas
- Validation commands and results
- Review verdict and round count
- Accepted reviewer findings fixed
- Findings intentionally not fixed (with rationale)
- Known low-priority tech debt (`known_followups` / downgraded findings)
- Risk notes and sandbox mode used
- Whether human review is required before merge

Reviewer supplies title suggestions, summary bullets, validation notes, risk notes,
and tech-debt items in `HocaReviewReport.pr_notes`; the manager decides what ships
in the final PR.

## Token handling

- GitHub tokens are manager-only for this phase (`gh` auth, push, PR create/merge)
- Never include tokens in prompts, logs, task reports, PR bodies, or commit messages
- Worker and reviewer sandboxes must not receive `GITHUB_TOKEN` by default
- Refuse to build PR metadata when task text matches secret-like patterns
- Do not read `.env`, credential stores, or API keys into PR fragments

See `hoca-sandbox-policy.md` for the credential isolation table.

## Publication boundary

The PR publisher phase owns:

- Intended-file list validation and safe staging
- Commit after staging (index must match normalized intended list)
- Push and PR creation via `gh`
- Auto-merge queueing when guards pass
- Run finalization, lock cleanup, and optional runtime cleanup

The PR publisher does not own:

- Task spec authoring, branch creation, or repair loops (manager)
- Implementation or repair (worker)
- Independent review or LGTM (reviewer)

## Must never

- Delegate staging, commit, push, or PR creation to `hoca-worker` or `hoca-reviewer`
- Allow worker or reviewer profiles to invoke this skill or its scripts
- Use `git add .`, `git add -A`, or `git commit -am`
- Publish from default branch or detached HEAD
- Publish when review gate, tests, or hard blockers fail (unless explicit draft-PR
  policy documents residual risk)
- Expose tokens or secrets in PR metadata
- Delete `main`, `master`, or the repository default branch
