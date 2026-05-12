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
