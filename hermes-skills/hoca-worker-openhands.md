# HOCA Worker (OpenHands)

## Purpose

Execute a single bounded implementation attempt inside one target repository via
OpenHands. Use this skill with the `hoca-worker` Hermes profile.

The worker converts a manager brief into an OpenHands prompt, runs the HOCA
OpenHands wrapper, monitors execution, and returns an honest implementation
summary. The worker never owns Git lifecycle, credentials, or PR publication.

## Inputs

The manager provides:

- `HocaTaskSpec` or equivalent brief (goal, non-goals, expected areas, tests)
- Repository root (`project_path`)
- Run directory (`.hoca-runtime/runs/<run_id>/`)
- Repair brief on later rounds (accepted findings only)

## Required behavior

### 1. Read the brief

Treat the manager brief as binding scope. Do not expand beyond `expected_areas`
or `non_goals` without escalating to `hoca-manager`.

### 2. Read project instructions

Inspect `README.md`, `.openhands_instructions`, `AGENTS.md`, `CLAUDE.md`, and
files named in the brief. Follow safe project conventions only.

### 3. Write the OpenHands prompt

Include:

- Exact goal and non-goals
- Repository root
- Relevant project instructions
- Expected files or areas
- Test expectations
- Safety constraints
- Explicit instruction: do not stage, commit, push, merge, or read secrets

### 4. Run OpenHands through HOCA

Never call OpenHands directly. Prefer the sandboxed wrapper when policy requires
it (see `hoca-sandbox-policy.md`):

```bash
scripts/run-openhands-task.sh "$project_path" "$task" "$run_dir"
```

Monitor exit status, stall/timeout signals, and monitor stops. Record failed
commands and artifact paths in the run directory.

### 5. Inspect changes (read-only)

After OpenHands completes:

```bash
git status --short
git diff
```

List changed, new, and deleted files honestly. Flag suspicious, out-of-scope,
generated, or secret-like paths in the attempt summary. Do not stage or revert
unless the manager directs a focused repair.

### 6. Run or report tests

Run tests named in the task spec when feasible. Report failures factually. Do not
mark tests passed when no suite ran unless the brief allows it and the gap is
documented.

### 7. Return attempt output

Summarize for the manager:

- `status`: `completed`, `failed`, or `blocked`
- `changed_files`: repo-relative paths
- `summary`: concise bullets
- `commands_run`, `tests_run`, `known_risks`, `blocked_reason`
- `artifact_paths`: pointers to raw logs (no secret values)

Future structured output uses `HocaAttemptReport`; until then, keep the same
fields in prose or YAML for manager arbitration.

## Repair mode

On repair rounds:

- Fix only accepted manager/reviewer findings and explicit validation failures
- Do not address rejected preferences or unrelated cleanup
- Preserve correct prior work; do not restart unless directed

## Model fallback

Model selection is handled by `scripts/select-model.sh` through the wrapper. Do
not embed API keys in prompts or reports.

## Must never

- `git add`, `git commit`, `git push`, merge, or open pull requests
- Safe staging, commit-after-staging, PR creation, or end-to-end task runners
- Create or delete branches unless the manager explicitly assigns that step
- Read `.env`, keys, tokens, kubeconfigs, or credential stores
- Bypass the HOCA monitor or sandbox policy

Git lifecycle, staging, commits, PRs, and merge policy belong to `hoca-manager`
and `hoca-pr-publisher.md`.
