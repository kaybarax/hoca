# HOCA Hermes Multi-Agent Upgrade PR Preparation

Prepared for Phase 18.2: PR Preparation.

## Suggested PR Title

`feat: add Hermes multi-agent HOCA workflow`

## Summary

This branch upgrades HOCA from a mostly linear manager-to-worker flow into a
Hermes-profile-based multi-agent workflow with explicit manager, worker,
reviewer, and PR-publisher responsibilities. The manager remains accountable for
Git lifecycle actions, staging, commits, PR creation, and final reporting, while
worker and reviewer execution paths are split into deterministic scripts and
role-scoped Hermes profiles.

The upgrade also adds structured task specs, review reports, manager decisions,
validation reports, final run state artifacts, round-loop arbitration, optional
Kanban orchestration, sandbox/worktree isolation, environment allowlisting, and
stronger PR-body generation so a human reviewer can understand each HOCA run
without reading every artifact file.

## New Files

Core Python modules:

- `hoca/definition_of_ready.py`
- `hoca/env_allowlist.py`
- `hoca/pr_body.py`
- `hoca/review_report_parser.py`
- `hoca/reviewer_hermes.py`
- `hoca/role_model_env.py`
- `hoca/round_loop.py`
- `hoca/sandbox_doctor.py`
- `hoca/sandbox_network.py`
- `hoca/security_cli.py`
- `hoca/task_report.py`
- `hoca/task_spec.py`
- `hoca/validation_assessment.py`
- `hoca/worker_hermes.py`
- `hoca/worktree.py`

Scripts:

- `scripts/check-definition-of-ready.sh`
- `scripts/clear-role-model-env.sh`
- `scripts/generate-task-spec.sh`
- `scripts/kanban-init.sh`
- `scripts/kanban-run.sh`
- `scripts/kanban-watch.sh`
- `scripts/lib/hoca-security.sh`
- `scripts/resolve-role-model-env.sh`
- `scripts/restore-dev-branch.sh`
- `scripts/run-reviewer-hermes.sh`
- `scripts/run-worker-hermes.sh`
- `scripts/sandbox-docker-env.sh`

Templates and docs:

- `templates/HocaRunFinalState.yaml`
- `docs/security-model.md`
- `docs/baselines/2026-05-21-live-smoke-tests.md`

Tests:

- `tests/test_create_pr_script.py`
- `tests/test_definition_of_ready.py`
- `tests/test_env_allowlist.py`
- `tests/test_env_example.py`
- `tests/test_generate_task_spec_script.py`
- `tests/test_integration_offline_workflow.py`
- `tests/test_kanban_scripts.py`
- `tests/test_pr_body.py`
- `tests/test_restore_dev_branch.py`
- `tests/test_reviewer_hermes.py`
- `tests/test_role_model_env.py`
- `tests/test_round_loop.py`
- `tests/test_run_reviewer_hermes_script.py`
- `tests/test_run_worker_hermes_script.py`
- `tests/test_sandbox.py`
- `tests/test_sandbox_doctor.py`
- `tests/test_sandbox_network.py`
- `tests/test_security_cli.py`
- `tests/test_task_spec.py`
- `tests/test_validation_report.py`
- `tests/test_worker_hermes.py`
- `tests/test_worktree.py`

## Changed Areas

- CLI and configuration support for Hermes role profiles, Kanban commands,
  report inspection, role model selection, sandbox diagnostics, and security
  checks.
- Runner scripts now separate worker, reviewer, manager, staging, PR creation,
  cleanup, branch restoration, and final reporting responsibilities.
- PR publishing now builds a structured body from task specs, changed files,
  validation artifacts, review artifacts, manager decisions, run context, risk
  notes, and auto-merge status.
- Review gating now supports deterministic checks and structured review report
  parsing so legacy text fallback is not the only quality gate.
- Tests now cover the multi-agent contracts, role runners, round loop, PR body,
  sandbox posture, environment policy, security CLI, worktree cleanup, Kanban
  scripts, and offline integration flow.

## Safety Changes

- Manager-only Git lifecycle is reinforced through scripts, tests, and profile
  guidance: worker and reviewer roles do not stage, commit, push, merge, or open
  PRs.
- Worker and reviewer execution can run in disposable worktrees and Docker
  sandbox modes with explicit network posture.
- Environment allowlists restrict which variables reach worker/reviewer and
  manager PR phases; role-specific model variables are resolved and cleared by
  dedicated scripts.
- PR metadata generation rejects secret-like task text and omits secret-like file
  paths from generated change summaries.
- Deterministic review sanity checks can downgrade unsafe reviewer LGTM results,
  including the tested Python syntax-error case.
- Safe staging, auto-merge guards, branch restoration, and final run artifacts
  make publication and cleanup auditable.

## Migration Notes

- Existing HOCA commands remain supported during the transition; the new
  multi-agent flow is additive rather than a removal of the legacy entrypoints.
- Update `.env` or CI secrets to use the role-specific model and credential
  variables documented in `.env.example` when exercising manager, worker, or
  reviewer profiles independently.
- Run `./bin/hoca setup-profiles` to install or refresh the bundled Hermes
  manager, worker, and reviewer profiles. Use `--dry-run` first if reviewing
  generated profile paths.
- Rebuild the sandbox image with `./scripts/sandbox-manager.sh build` before
  live sandboxed OpenHands runs so the image includes the current OpenHands CLI
  and HOCA wrapper expectations.
- Kanban orchestration remains optional. The default workflow continues to use
  the round-loop path unless Kanban commands are explicitly invoked.

## Validation Results

Phase 18.1 marked the branch ready for PR after these checks completed:

- Full `pytest` suite: passed.
- `ruff`: passed.
- `shellcheck` when available: passed.
- `hoca doctor`: passed with non-critical warnings.
- Fixture end-to-end test: passed.
- Generated docs review: completed.
- Sandbox command output review: completed.
- Secret scan of tracked files: passed.

Live smoke coverage is recorded in
`docs/baselines/2026-05-21-live-smoke-tests.md`, including:

- `./bin/hoca doctor`
- `./bin/hoca setup-profiles --dry-run`
- `./bin/hoca setup-profiles`
- sandbox image rebuild
- sandboxed OpenHands tiny fixture run
- reviewer known-good and known-bad diff checks
- mocked max-round behavior

## Known Limitations

- Kanban remains experimental and optional; it should not be made the default
  until it has a stability period.
- Network egress controls are stronger than before, but future hardening may add
  stricter provider-specific enforcement.
- Run artifacts are structured and auditable, but not signed.
- Role model selection is explicit and deterministic today; dynamic model
  scoring or automatic role model selection remains future work.
- There is no UI/dashboard for run history yet.
- Remote sandbox provider support is not implemented.
- The live reviewer model can still miss semantic defects; deterministic review
  checks currently cover the tested Python syntax-error case and should be
  expanded over time.

## Reviewer Guidance

Recommended review path:

1. Read `README.md`, `docs/security-model.md`, and this PR preparation note.
2. Inspect `scripts/run-hoca-task.sh`, `scripts/run-worker-hermes.sh`,
   `scripts/run-reviewer-hermes.sh`, `scripts/create-pr.sh`, and
   `hoca/round_loop.py` for the orchestration flow.
3. Inspect `hoca/pr_body.py`, `hoca/task_report.py`, and `hoca/run_artifacts.py`
   for artifact and PR-body behavior.
4. Inspect the safety modules and tests: `hoca/env_allowlist.py`,
   `hoca/sandbox_network.py`, `hoca/sandbox_doctor.py`, `hoca/worktree.py`,
   `hoca/security_cli.py`, and the matching test files.
