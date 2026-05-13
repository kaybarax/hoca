# HOCA

HOCA means **Hermes + OpenHands Computer Automata**.

HOCA is a local-first autonomous engineering automation toolkit. It is designed to
help a developer run structured software engineering work on their own machine,
inside a bounded repository workspace, with explicit review and safety controls.

HOCA is not an unrestricted self-operating computer agent. It should not receive
open-ended control over a machine, wander across unrelated folders, or commit and
merge changes without inspection. HOCA is an experimental but structured AI
developer workspace: useful because it is automated, and useful because that
automation is constrained.

## Product Priorities

HOCA must prioritize:

- Local execution.
- Repository-scoped work.
- Explicit safety controls.
- Git hygiene.
- Review-before-merge.
- Human review by default.
- No blind commits.
- No blind merges.
- No uncontrolled filesystem access.

These priorities are non-negotiable defaults for the project. Later workflow,
script, and CLI implementations should make the safe path the normal path.

## Required Default Behavior

HOCA's default behavior is intentionally conservative:

- `auto_merge` is disabled.
- Pull requests are required for normal completion.
- Direct pushes to `main` and `master` are forbidden.
- Each run requires a clean working tree before it starts.
- Runs stop when unrelated human changes are present.
- Runs stop when secret-like files are modified or created.
- Runs stop when tests fail.
- Runs stop unless Aider returns explicit approval.
- The task runner commits only after safe staging succeeds, using exactly one `git commit` against the staged index (never blind `git commit -am`).
- Blind staging commands such as `git add .`, `git add -A`, and `git add --all`
  are forbidden.
- Explicit staging also refuses secret-like files such as `.env`, private keys,
  kubeconfigs, package registry credentials, Docker credentials, browser
  cookies, and local credential stores.
- `git commit -am` is forbidden.
- High-risk changes are never auto-merged.
- Optional **guarded auto-merge** (milestone 18.2): with `--auto-merge`, `status.json` sets `auto_merge` true. When you later run `create-pr.sh`, HOCA may run `gh pr merge --auto --merge --delete-branch` only if `scripts/auto-merge-guards.sh` passes: tests exited 0, Aider output contains `LGTM`, `risk-level.txt` starts with `low`, `staged-files.txt` has no secret-like paths, infrastructure-sensitive paths appear in `staging-justification.txt`, the repo has GitHub **Allow auto-merge**, and the new PR reports **MERGEABLE**. Otherwise the PR stays open for human review.

These defaults are encoded in `hoca.config` and `hoca.git_utils` so later CLI and
script work can call the same policy checks instead of reimplementing safety
rules in separate places.

## Secrets Policy

HOCA must never commit local credentials. Keep real secrets in local environment
or credential-store tooling outside of committed project files. The safety policy
rejects secret-like paths before staging, including `.env` files, private key
formats, `.github/secrets`, kubeconfigs, `.npmrc`, `.pypirc`, Docker registry
credentials, browser cookies, and local credential stores such as `.ssh`,
`.aws`, `.azure`, `.gnupg`, and macOS Keychains.

## Core Workflow Model

HOCA uses a bounded engineering workflow that turns a human request or GitHub
Issue into reviewed repository changes:

```text
Human or GitHub Issue
        |
        v
Hermes Manager
        |
        v
OpenHands Worker
        |
        v
Tests and Diff Inspection
        |
        v
Aider Reviewer
        |
        v
Selective Git Staging
        |
        v
Commit
        |
        v
Pull Request
        |
        v
Human Review by Default
        |
        v
Optional Merge
        |
        v
Notification
```

The workflow roles are fixed:

- Hermes is the Manager. It plans work, delegates bounded implementation tasks,
  inspects workflow state, coordinates review, and manages the Git and pull
  request lifecycle.
- OpenHands is the Worker. It performs implementation inside the target
  repository workspace under the Manager's constraints.
- Aider is the Reviewer. It provides an independent quality, correctness,
  security, test, and unnecessary-edit review before changes are accepted.
- Ollama is the local LLM runtime. It provides local model execution where
  practical and keeps the default system local-first.
- Git and GitHub CLI are the version-control and pull-request layer. They create
  branches, inspect diffs, stage selected files, commit changes, and open pull
  requests.
- GitHub Actions and the local webhook listener are the optional issue-trigger
  layer. They can wake a local HOCA run from labeled or opened GitHub Issues
  after webhook security checks.
- macOS notifications and Telegram are the optional notification layer. They
  report completion, blocked runs, or required human action without making
  notification delivery a critical path.

This model keeps automated work inside a repository-scoped, review-oriented
pipeline instead of treating the computer as an unrestricted operating surface.

## Docker

Docker is optional. HOCA uses Docker primarily as the OpenHands sandbox backend.
A `docker-compose.yml` is provided for optional local services.

Start Docker Desktop or Colima before running HOCA:

```sh
# Colima example
colima start --cpu 6 --memory 16
```

The webhook listener can run as a Docker service, but running it directly on the
host is preferred when it needs to launch host-level commands (hoca CLI, Docker,
Ollama):

```sh
# Preferred: run on host
python scripts/webhook-listener.py

# Alternative: run in Docker (isolated, cannot spawn host commands)
docker compose --profile webhook up
```

Ollama is not included in Docker Compose. Run it natively:

```sh
ollama serve
```
