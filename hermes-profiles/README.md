# HOCA Hermes Profiles

Role-specific Hermes profile templates for the HOCA multi-agent upgrade. Each
subdirectory is a distributable profile skeleton with a stable identity (`SOUL.md`)
and example Hermes configuration (`config.example.yaml`).

| Profile | Role |
|---------|------|
| `hoca-manager` | Engineering manager: task clarity, safety policy, arbitration, Git/PR lifecycle |
| `hoca-worker` | Principal engineer: implementation via OpenHands, no Git lifecycle |
| `hoca-reviewer` | Principal reviewer: quality gate via OpenHands review, no Git lifecycle |

## Installation

Profile installation is handled by `scripts/setup-hermes-profiles.sh` or
`hoca setup-profiles`. Run from the HOCA repo:

```bash
hoca setup-profiles
hoca setup-profiles --dry-run
./scripts/setup-hermes-profiles.sh
./scripts/setup-hermes-profiles.sh --dry-run
```

You can also copy a template directory into your Hermes profiles location or use
`hermes profile install` when a distribution manifest is added.

```bash
hermes profile create hoca-manager   # if creating manually
# Then copy SOUL.md and merge config.example.yaml into the profile config.
```

## Safety

Hermes profiles provide identity and defaults; they are **not** security
sandboxes. Filesystem access, credentials, Docker mounts, and MCP exposure still
depend on terminal backend settings and host policy. Keep secrets in profile
`.env` files, never in tracked templates.

## Related paths

- Historical entrypoint: `hermes-skills/hoca.md` ("Hoca OpenHands Boss")
- Role skills: `hermes-skills/hoca-manager.md`, `hoca-worker-openhands.md`,
  `hoca-reviewer-qa.md`, `hoca-pr-publisher.md`, `hoca-sandbox-policy.md`
- Single-profile experiments are no longer part of supported HOCA operation.
