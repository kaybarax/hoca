# hoca-manager

Hermes profile for the HOCA engineering manager. This profile owns task
definition, worker/reviewer delegation, deterministic validation gates,
arbitration, and the Git/PR lifecycle.

## Files

- `SOUL.md` — stable manager identity and limits
- `config.example.yaml` — example Hermes settings; copy into the profile's
  real `config.yaml` and replace placeholder paths

## Typical flow

1. Receive a human task or GitHub issue.
2. Produce or refine a `HocaTaskSpec`.
3. Delegate implementation to `hoca-worker` and review to `hoca-reviewer`.
4. Run validation scripts, arbitrate review findings, then stage/commit/PR via
   HOCA scripts.

Do not use this profile for large implementation edits except trivial mechanical
fixes. The worker profile owns implementation.
