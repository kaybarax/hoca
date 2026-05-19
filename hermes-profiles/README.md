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

Profile installation is handled by `hoca setup-profiles` (see upgrade task 4.5).
Until that command ships, copy a template directory into your Hermes profiles
location or use `hermes profile install` when a distribution manifest is added.

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

- Shared orchestration skill: `hermes-skills/hoca.md`
- Legacy single-profile example: `.hermes/config.example.yaml`
- Upgrade flag: `HOCA_USE_HERMES_PROFILES` in `.env.example`
