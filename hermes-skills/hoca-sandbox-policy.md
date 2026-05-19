# HOCA Sandbox Policy

## Purpose

Explain how HOCA isolates OpenHands worker and reviewer execution. Managers,
workers, and reviewers should follow this policy before running sandboxed
wrappers.

Hermes profiles are not security sandboxes. Deterministic scripts, mount policy,
and credential allowlists enforce boundaries.

## Defaults

Prefer sandboxed execution when available:

- `HOCA_USE_SANDBOX=true` (recommended default for headless OpenHands)
- Runtime snapshot: `.hoca-runtime/runs/<run_id>/sandbox-policy.json`
- Template schema: `templates/HocaSandboxPolicy.yaml`

Network mode (from task spec / policy):

- `offline` — no external network
- `package-install` — registries only
- `github-only` — GitHub API/fetch only
- `full` — broader access; use sparingly and document rationale

## Worker and reviewer containers

Use `scripts/run-openhands-sandboxed.sh` (or manager-selected sandbox wrapper),
not raw `docker run` inventing new mounts.

Goals:

- Operate only inside the intended project worktree (or disposable worktree when enabled)
- No host home directory mount
- No Docker socket mount
- No SSH agent, GPG agent, browser profile, or cloud credential mounts
- Bounded CPU, memory, and PIDs where the runner supports it
- Non-root execution where supported
- Drop unnecessary capabilities; avoid ad hoc `NET_RAW` unless policy requires it

## Credential isolation

| Phase | Credentials |
|-------|-------------|
| Worker | LLM only (`LLM_MODEL`, `LLM_BASE_URL`, `LLM_API_KEY`) |
| Reviewer | LLM only |
| Manager PR publish | GitHub token when needed for PR operations |

Never forward `GITHUB_TOKEN` to worker/reviewer sandboxes by default. Never embed
API keys in prompts, logs, or reports.

Allowed worker/reviewer environment (allowlist principle):

```text
LLM_MODEL
LLM_BASE_URL
LLM_API_KEY
OPENHANDS_SUPPRESS_BANNER
HOME
CI
```

## Forbidden mounts and access

Stop and escalate to the human when policy or monitor detects:

- Mounts of `~`, `/home`, credential stores, or SSH/GPG agents
- Docker socket access from worker/reviewer containers
- Writes outside the task worktree
- Secret-like file reads (`.env`, keys, tokens, kubeconfigs)

## Host execution

Host-local OpenHands (`scripts/run-openhands-task.sh` without sandbox) is allowed
only when sandboxing is disabled explicitly and the engineer accepts higher risk.
Treat headless OpenHands as high-power automation on the host.

## Capability checks

Browsing and other optional OpenHands features:

```bash
scripts/check-browsing.sh "$run_dir"
scripts/check-browsing.sh "$run_dir" --require
```

Use flags only when confirmed in `openhands-capabilities.txt` or `openhands --help`.

## Unsafe activity

On monitor stop, escape attempt, or policy violation:

1. Stop the worker/reviewer phase immediately
2. Record the reason in run status and monitor artifacts
3. Do not stage, commit, or publish until the manager reviews
4. Prefer `blocked` or `failed` over silent continuation

## Alignment with scripts

Policy must match `scripts/run-openhands-sandboxed.sh`, `scripts/sandbox-manager.sh`,
and `hoca/run_artifacts.py` sandbox snapshots. When script behavior and this
skill differ, treat the scripts as authoritative and file a HOCA fix.
