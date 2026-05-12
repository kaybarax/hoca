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
- Runs stop before commit until selective staging is fully implemented.
- Blind staging commands such as `git add .`, `git add -A`, and `git add --all`
  are forbidden.
- `git commit -am` is forbidden.
- High-risk changes are never auto-merged.

These defaults are encoded in `hoca.config` and `hoca.git_utils` so later CLI and
script work can call the same policy checks instead of reimplementing safety
rules in separate places.

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
