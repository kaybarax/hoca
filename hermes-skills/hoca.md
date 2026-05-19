# Hoca OpenHands Boss

## Purpose

Coordinate a bounded HOCA Manager → Worker → Reviewer engineering workflow for a
single target repository.

Use this skill when Hermes is asked to run local autonomous engineering work
through HOCA (including the instruction **"use Hoca OpenHands Boss"**). Hermes
acts as Manager: validate the workspace, delegate implementation to OpenHands,
require tests and code review, then stage, commit, and open a pull request
through HOCA scripts.

HOCA is local-first and repository-scoped. Do not treat it as unrestricted
computer automation. Keep work inside the requested Git repository and prefer
human review before merge.

## Role-specific skills (preferred)

For multi-profile HOCA, use the focused skills instead of this monolithic file:

| Skill | Profile | Scope |
|-------|---------|-------|
| `hoca-manager.md` | `hoca-manager` | Orchestration, validation, delegation |
| `hoca-worker-openhands.md` | `hoca-worker` | OpenHands implementation only |
| `hoca-reviewer-qa.md` | `hoca-reviewer` | Independent review only |
| `hoca-pr-publisher.md` | `hoca-manager` | Staging, commit, PR (manager-only) |
| `hoca-sandbox-policy.md` | all | Sandbox defaults and constraints |

This file remains the **compatibility entrypoint**: it preserves the original
"Hoca OpenHands Boss" name and end-to-end shortcut for single-profile setups.

## Parameters

- `project_path`: Required. Absolute or user-expanded path to the target Git repository.
- `task`: Required. Human-readable implementation request.
- `issue_id`: Optional. GitHub Issue number associated with the task.
- `auto_merge`: Optional boolean. Default: `false`.
- `notify_telegram`: Optional boolean. Default: `false`.

## Required defaults

- `auto_merge=false`
- `require_tests=true`
- `require_review_lgtm=true`
- `stop_on_dirty_tree=true`

Never override these defaults unless the engineer explicitly requests the
override and local HOCA configuration allows it.

## Configuration contract

Read HOCA behavior from the repository `.env` or inherited environment. See
`.env.example` for safe defaults (`HOCA_AUTO_MERGE`, `HOCA_REQUIRE_REVIEW_LGTM`,
`HOCA_REQUIRE_TESTS`, `HOCA_STOP_ON_DIRTY_TREE`, and related flags).

Use LLM settings only through HOCA wrappers. Do not pass raw provider secrets
to worker prompts or reports.

## End-to-end workflow (summary)

Follow `hoca-manager.md` for the full manager procedure. At a high level:

1. Validate workspace and read project instructions
2. Create a task branch (not on default branch)
3. Run OpenHands via `scripts/run-openhands-task.sh` (see `hoca-worker-openhands.md`)
4. Inspect changes, run `scripts/run-tests.sh`, run `scripts/review-with-openhands.sh`
5. Publish via `hoca-pr-publisher.md` scripts when gates pass
6. Notify and `scripts/generate-task-report.sh`

Apply `hoca-sandbox-policy.md` when sandboxing is enabled.

## One-command shortcut

When the engineer wants the default end-to-end HOCA path:

```bash
scripts/run-hoca-task.sh "$project_path" "$task"
scripts/run-hoca-task.sh "$project_path" "$task" --issue-id "$issue_id"
```

Optional flags: `--auto-merge`, `--notify-telegram`. The shortcut uses the same
conservative defaults and may stop for human staging, failed tests, missing
review LGTM, or merge-policy restrictions.

## Web research policy

Do not pass unsupported OpenHands browsing flags. Use `scripts/check-browsing.sh`
and capabilities recorded in the run directory. If research is required and no
browsing tool is available, stop with status `blocked` and reason
`research_unavailable`.
