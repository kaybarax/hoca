# HOCA Sandbox Policy

## Purpose

Explain how HOCA isolates OpenHands worker and reviewer execution. Managers,
workers, and reviewers should read and follow this policy before running sandboxed
wrappers or approving host-local execution.

Hermes profiles are not security sandboxes. Deterministic scripts, mount policy,
credential allowlists, and the stdout monitor enforce boundaries.

## Related skills

| Skill | Role |
|-------|------|
| `hoca-manager.md` | Chooses sandbox vs host execution, escalates violations |
| `hoca-worker-openhands.md` | Implementation inside sandbox constraints |
| `hoca-reviewer-qa.md` | Review inside sandbox constraints |
| `hoca-pr-publisher.md` | Manager-only GitHub credentials after gates pass |

## Sandbox defaults

Prefer sandboxed execution whenever Docker (or equivalent) is available:

| Setting | Default | Notes |
|---------|---------|-------|
| `HOCA_USE_SANDBOX` | `true` | Recommended for headless OpenHands |
| Runtime snapshot | `.hoca-runtime/runs/<run_id>/sandbox-policy.json` | Written at run init |
| Template schema | `templates/HocaSandboxPolicy.yaml` | `enabled`, `network_mode` |
| Wrapper | `scripts/run-openhands-sandboxed.sh` | Use via `run-openhands-task.sh` / `review-with-openhands.sh` |

When `HOCA_USE_SANDBOX=true` and the sandbox script exists, wrappers route worker and
reviewer phases through the sandbox. When sandboxing is unavailable, wrappers may
fall back to host execution only with explicit engineer acceptance of higher risk.

Record the effective policy in `sandbox-policy.json` so reports and humans can
inspect what was enforced for the run.

## Network modes

`HocaSandboxPolicy.network_mode` (also on `HocaTaskSpec.sandbox`) controls outbound
access from worker/reviewer sandboxes:

| Mode | Access | When to use |
|------|--------|-------------|
| `offline` | No external network | Default; safest for most tasks |
| `package-install` | Package registries only (npm, PyPI, etc.) | Dependency installs required |
| `github-only` | GitHub API/fetch only | Tasks that must read issues or remote refs |
| `full` | Broader network | Rare; document rationale in task spec / manager notes |

Environment default: `HOCA_NETWORK_MODE` (default `offline`). Task spec field:
`sandbox.network_mode` on `HocaTaskSpec` overrides the env default for worker phases.
Review phases prefer `offline` unless an explicit runtime override is passed.

### Docker implementation (best effort)

| Mode | Docker flags | Notes |
|------|----------------|-------|
| `offline` | `--network none` | No registry/GitHub egress; host LLM via `host.docker.internal` is unavailable |
| `package-install` | default bridge + `host.docker.internal` | Registry-only egress is **not** enforced |
| `github-only` | default bridge + `host.docker.internal` | GitHub-only egress is **not** enforced |
| `full` | default bridge + `host.docker.internal` | Requires explicit `HOCA_NETWORK_MODE=full` or task-spec `full` |

Runtime records the effective mode in `.hoca-runtime/runs/<run_id>/sandbox-policy.json`
(`effective_network_mode`, `docker_network_args`, `limitations`). Helpers live in
`hoca/sandbox_network.py` and `scripts/sandbox-docker-env.sh`.

`full` is rejected unless opted in via `HOCA_NETWORK_MODE=full`, task-spec
`sandbox.network_mode: full`, or an explicit phase override. Reviewer sandboxes
default to `offline` so review can run without broad network when dependencies are
already present in the worktree.

Managers set network mode in the task spec or run init. Workers and reviewers must
not widen network access beyond the recorded policy.

## Worker and reviewer containers

Use `scripts/run-openhands-sandboxed.sh` (or a manager-selected sandbox wrapper),
not ad hoc `docker run` commands with new mounts.

Container goals:

- Operate only inside the intended project worktree (or disposable worktree when enabled)
- No host home directory mount (`~`, `/home`, user profile trees)
- No Docker socket mount
- No SSH agent, GPG agent, browser profile, or cloud credential mounts
- Bounded CPU, memory, and PIDs where the runner supports it
- Non-root execution: image defines `worker`; runtime uses project-owner
  `uid:gid` (or `HOCA_SANDBOX_USER=worker`) via `scripts/sandbox-docker-env.sh`
- OpenHands CLI is pre-installed in the image (no runtime `apt-get` as root)
- Drop all capabilities (`--cap-drop=ALL`); do not add `NET_RAW` or other Linux capabilities
- Package tooling (`pnpm install`, `pip install`, registry fetches) does not require capability exceptions

Supporting scripts:

- `scripts/run-openhands-sandboxed.sh` — headless OpenHands inside Docker with monitor
- `scripts/sandbox-manager.sh` — build/start/exec/stop helper containers
- `scripts/check-browsing.sh` — optional capability gate before enabling browsing

## Credential isolation

| Phase | Credentials |
|-------|-------------|
| Worker | LLM only (`LLM_MODEL`, `LLM_BASE_URL`, `LLM_API_KEY`) |
| Reviewer | LLM only |
| Manager PR publish | GitHub token when needed for PR operations (`gh`, push, merge) |

Never forward `GITHUB_TOKEN` into worker/reviewer sandboxes. Never embed API keys in
prompts, logs, task reports, or PR bodies.

Allowed worker/reviewer environment (allowlist principle):

```text
LLM_MODEL
LLM_BASE_URL
LLM_API_KEY
OPENHANDS_SUPPRESS_BANNER
HOME
CI
```

Workers and reviewers must refuse to use credentials that appear in the environment
even if a misconfigured host exported them.

## Forbidden mounts and access

Stop and escalate to the human when policy or the monitor detects:

- Mounts of `~`, `/home`, credential stores, or SSH/GPG agents
- Docker socket access from worker/reviewer containers
- Writes outside the task worktree
- Secret-like file reads (`.env`, keys, tokens, kubeconfigs, cloud profiles)

Do not stage, commit, or publish after a forbidden-access event until the manager
reviews monitor artifacts and run status.

## Host execution

Host-local OpenHands (`scripts/run-openhands-task.sh` without sandbox) is allowed only
when:

- `HOCA_USE_SANDBOX=false` is set explicitly, or
- The sandbox script is missing and the engineer accepts the documented fallback risk

Treat headless OpenHands on the host as high-power automation with full access to the
mounted project directory and the engineer's environment. Prefer sandboxed execution
for autonomous worker/reviewer rounds.

Wrappers emit a visible warning and write `host-execution-warning.txt` under the run
directory whenever host execution is selected or when sandboxing was requested but
unavailable.

## Nested sandboxes and Hermes-in-Docker

Prefer one explicit HOCA-controlled OpenHands container boundary for worker and
reviewer phases. Avoid confusing nested sandbox stacks (for example Hermes Docker
terminal backend plus a separate OpenHands-in-Docker layer inside it).

| Layout | Guidance |
|--------|----------|
| Default | Manager on host; worker/reviewer via `run-openhands-sandboxed.sh` |
| Hermes in Docker | Mount only the HOCA workspace and task worktree; no `~`, credential stores, or Docker socket |
| OpenHands native sandbox | Do not add a second container boundary on top of HOCA's unless policy documents a rare exception |

When Hermes runs inside Docker, the manager still owns Git lifecycle and PR credentials
outside worker/reviewer sandboxes. Keep worker and reviewer inside the HOCA sandbox
wrapper rather than widening mounts to compensate for an outer Hermes container.

## Capability checks

Browsing and other optional OpenHands features require explicit confirmation:

```bash
scripts/check-browsing.sh "$run_dir"
scripts/check-browsing.sh "$run_dir" --require
```

Enable flags only when confirmed in `openhands-capabilities.txt` or `openhands --help`.
Do not enable browsing to work around sandbox network limits.

## Stop on unsafe activity

When the monitor stops a phase, an escape is attempted, or policy is violated:

1. Stop the worker/reviewer phase immediately (do not continue OpenHands)
2. Record the reason in run status and monitor artifacts (`monitor-stop.json`, `monitor-result.json`)
3. Do not stage, commit, or publish until the manager reviews
4. Prefer run status `blocked` or `failed` over silent continuation
5. Escalate to the human when the violation is ambiguous or repeats across rounds

Managers inspect `monitor-stop.json`, `openhands-exit-code.txt`, and stderr logs before
assigning repair work or publication.

## Alignment with scripts

This skill must match:

- `scripts/run-openhands-sandboxed.sh`
- `scripts/sandbox-manager.sh`
- `hoca/run_artifacts.py` sandbox snapshots (`init_run_layout`, `sandbox-policy.json`)
- `hoca/contracts.py` (`HocaSandboxPolicy`, task spec `sandbox` field)

When script behavior and this skill differ, treat the scripts as authoritative for
runtime behavior and file a HOCA fix to restore alignment. Hermes should explain the
recorded `sandbox-policy.json` and applicable env defaults when asked.
