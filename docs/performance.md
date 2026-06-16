# HOCA Performance Guide

HOCA run time is mostly the sum of agent loops, sandbox/container work,
dependency installs, model residency, tests, and review. v1.1.0 keeps the
safety gates intact while giving each part a clearer budget or faster path.

## Run Cost Anatomy

- **Manager setup**: definition-of-ready checks, task-spec generation, branch and
  worktree setup, and role model selection.
- **Worker loop**: Hermes profile orchestration plus OpenHands execution by
  default. This is usually the largest interactive-agent cost.
- **Sandbox/container startup**: Docker image checks, container start, isolated
  home setup, worktree mount, and network policy setup.
- **Dependency installs**: package-manager install work before tests or app
  execution. Install caching skips this when the lockfile state is unchanged.
- **Tests**: validation commands from the task spec, with failure classification
  and run artifacts.
- **Reviewer loop**: reviewer prompt, review fanout when enabled, and repair
  rounds when findings require changes.
- **Model residency**: local model load and reload time. One resident local model
  is the default recommendation; multiple resident models need enough RAM for
  every loaded model plus Docker and sandbox memory.
- **Publication gates**: safe staging, commit, push, PR creation, and optional
  notification.

## Modes And Knobs

`HOCA_WORKER_MODE=direct` is the default fast path. The worker prompt is sent
directly to OpenHands while preserving HOCA's monitor policy, worktree handling,
attempt reports, tests, review, staging, and PR gates.

`HOCA_WORKER_MODE=hermes` remains selectable as an opt-in compatibility path
when the Hermes worker profile is preferred.

`HOCA_REVIEWER_MODE=direct` is the default review path. `HOCA_REVIEWER_MODE=hermes`
remains selectable and uses the Hermes reviewer profile while preserving review
artifacts and gates.

`HOCA_WORKER_ENGINE=openhands` is the default engine. `claude-code` and `codex`
use native one-shot CLI adapters with the same prompt composition, env allowlist,
monitor policy, standard worker attempt reports, and manager-owned Git lifecycle
gates. These native engines run on the host, not inside the OpenHands Docker
sandbox.

`bin/hoca run --express` requests the low-risk express lane. Eligible tasks are
low risk and narrow in expected area count. Express mode uses one total round,
direct worker/reviewer modes, and reviewer warm-up. Ineligible tasks fall back to
the standard lane and record the decision.

Run budgets are derived from task risk, expected areas, and repair round. HOCA
writes `run-budget-round-N.json` and exports phase limits such as
`HOCA_OPENHANDS_TIMEOUT`, `HOCA_OPENHANDS_STALL`, and `HOCA_HERMES_TIMEOUT`.
Explicit environment overrides still win.

Install caching records package-manager state and skips dependency installs when
the lockfile cache is current. Force an install with `HOCA_FORCE_INSTALL=true`
when validating dependency setup itself.

Reviewer warm-up is enabled by default with `HOCA_REVIEW_WARMUP=true`. Set
`HOCA_REVIEW_WARMUP=false` to disable it. It starts best-effort reviewer
model/container checks during tests, writes advisory artifacts, and never fails
the run by itself.

Fleet memory guardrails use resource budget metadata such as
`max_resident_models`, `model_residency_mb`, `docker_vm_memory_mb`, and
`sandbox_memory_mb` to avoid launching lanes that would exceed resident-model or
memory constraints.

## Benchmark Evidence

Use the benchmark command against disposable clones:

```sh
bin/hoca bench run PB-DOC-C --runs 3 --output /tmp/hoca-pb-doc-c.json
bin/hoca bench run PB-FEATURE-A --runs 3 --output /tmp/hoca-pb-feature-a.json
bin/hoca bench compare /tmp/baseline.json /tmp/candidate.json
```

Each benchmark run captures wall time, exit code, run directory, and timing
artifacts when available. Use the comparison output to show median wall-time
delta between a baseline and a candidate.

For manual evidence on a normal run, pass `--timing`:

```sh
bin/hoca run /path/to/repo "Update docs" --timing
```

Keep evidence public-safe. Use run IDs, phase names, timing summaries, and
redacted artifacts rather than absolute local paths or credentials.
