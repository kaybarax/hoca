# hoca-worker

Hermes profile for the HOCA implementation worker. This profile receives a
precise task brief from `hoca-manager`, coordinates OpenHands implementation,
and returns structured attempt reports.

## Files

- `SOUL.md` — stable worker identity and limits
- `config.example.yaml` — example Hermes settings scoped to implementation

## Typical flow

1. Read the manager's `HocaTaskSpec` and attempt context.
2. Run OpenHands through `scripts/run-openhands-task.sh` (or sandboxed wrapper).
3. Return a `HocaAttemptReport` with changed files, test notes, and blockers.

This profile must not stage, commit, push, open PRs, or access secrets. Git
lifecycle remains with `hoca-manager` and deterministic HOCA scripts.
