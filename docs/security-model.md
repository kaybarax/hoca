# HOCA Security Model

HOCA is local-first engineering automation for one repository at a time. Its
security model assumes autonomous worker and reviewer agents are useful but not
trusted with broad host access, credential stores, or irreversible Git actions.

Security does not come from Hermes profiles or agent prompts alone. Profiles
provide role identity and workflow separation. The security boundary is provided
by deterministic HOCA scripts, Docker or equivalent sandboxing, scoped
worktrees, environment allowlists, monitoring, staging checks, and a human
review stop before merge.

## Trust Boundaries

| Boundary | Trusted side | Less-trusted side | Controls |
| --- | --- | --- | --- |
| Human to HOCA manager | Human product owner | Automated manager workflow | Explicit task input, clean-tree checks, task reports, no auto-merge by default |
| Manager to worker | HOCA scripts and manager phase | Worker/OpenHands implementation phase | Task spec, sandboxed execution, role-specific credentials, monitor stops |
| Manager to reviewer | HOCA scripts and manager phase | Reviewer/OpenHands review phase | Read/review prompt, offline network by default, role-specific credentials |
| Active checkout to task worktree | User's normal checkout | Disposable task worktree | `HOCA_USE_WORKTREE_SANDBOX=true`, per-run `.hoca-runtime/worktrees/<run_id>/` |
| Host to container | Host filesystem and credentials | Worker/reviewer container | Project worktree mount only, isolated HOME, no Docker socket, no host home mount |
| Manager Git lifecycle to agents | Manager-owned staging, commit, push, PR | Worker/reviewer output | Worker/reviewer monitor blocks Git lifecycle commands |

The manager phase is more trusted than worker and reviewer phases because it
owns policy decisions, staging, commit, push, and PR publication. Worker and
reviewer phases should be treated as high-power automation and kept inside the
smallest practical filesystem, network, and credential scope.

## Credential Handling

Credentials are split by phase.

- Worker and reviewer phases receive only the selected LLM credentials for that
  phase: `LLM_MODEL`, `LLM_BASE_URL`, and `LLM_API_KEY`.
- Raw `HOCA_MODEL_<N>_*` pool credentials stay in manager-side configuration and
  are resolved before phase execution.
- GitHub credentials are manager-only. `GITHUB_TOKEN`, `GH_TOKEN`, and
  `GITHUB_REPOSITORY` are allowlisted only for the manager PR phase.
- Worker and reviewer sandboxes do not receive GitHub tokens by default and
  should not push, merge, publish, or create PRs.
- API keys and other secret-like fields are redacted in safe config
  representations, reports, and environment logging.
- Tokens should never be included in prompts, task specs, PR bodies, run
  reports, or committed files.

The canonical allowlists live in `hoca/env_allowlist.py`. Use those allowlists
when adding new phase runners so new environment variables are intentionally
reviewed instead of inherited from the host shell by accident.

HOCA rejects or stops on secret-like file paths. Current checks include `.env`
variants, private keys, kubeconfigs, local credential stores such as `.ssh`,
`.gnupg`, `.aws`, `.azure`, `.docker`, and browser cookie directories. The
canonical path policy lives in `hoca/security.py`.

## Sandbox Modes

HOCA defaults to sandboxed execution:

```env
HOCA_USE_SANDBOX=true
HOCA_USE_WORKTREE_SANDBOX=true
HOCA_NETWORK_MODE=offline
```

### Docker Sandbox

When `HOCA_USE_SANDBOX=true`, worker/reviewer OpenHands phases run in the HOCA
sandbox wrapper. The container receives explicit `-e` values for the selected
LLM credentials, an isolated `HOME`, the task worktree mounted at `/workspace`,
and the run directory mounted under `.hoca-runtime`.

The sandbox wrapper uses:

- `--security-opt=no-new-privileges`
- `--cap-drop=ALL`
- bounded memory via `HOCA_SANDBOX_MEMORY`
- bounded process count via `HOCA_SANDBOX_PIDS`
- a non-root runtime user by default, based on the task worktree owner
- no Docker socket mount
- no host home, SSH agent, GPG agent, browser profile, or cloud credential mount

Run `hoca doctor` or the sandbox doctor checks before relying on the sandbox in
a new environment. Doctor warnings are part of the security posture and should
be treated as review input, not cosmetic output.

### Worktree Sandbox

When `HOCA_USE_WORKTREE_SANDBOX=true`, HOCA creates a disposable Git worktree per
run. Worker and reviewer phases operate in that worktree, while the user's
active checkout remains outside the agent editing surface. This makes cleanup
simpler and reduces accidental modification of unrelated local work.

### Host Execution

`HOCA_USE_SANDBOX=false` is an explicit higher-risk mode. In host execution, the
worker/reviewer process runs with the same filesystem access as the current
user. Use it only for controlled repositories where that tradeoff is acceptable,
and prefer a disposable checkout with no local credential stores nearby.

### Network Modes

Network mode is controlled by `HOCA_NETWORK_MODE` or by a task spec sandbox
override.

| Mode | Behavior | Security note |
| --- | --- | --- |
| `offline` | Docker runs with `--network none` | Safest default; no package registry or GitHub egress |
| `package-install` | Docker bridge network | Allows dependency fetches; Docker does not enforce registry-only egress |
| `github-only` | Docker bridge network | Expresses intent, but Docker does not enforce GitHub-only egress |
| `full` | Docker bridge network | Unrestricted egress; requires explicit opt-in |

Reviewer runs prefer `offline` unless a phase explicitly overrides the mode.

## Deterministic Policy Gates

HOCA uses code-level gates in addition to prompts.

- `hoca/monitor.py` watches worker/reviewer output and stops dangerous commands,
  secret access attempts, unrelated absolute-directory access, and
  manager-only Git lifecycle commands from worker/reviewer roles.
- `hoca/security.py` validates staging file lists, rejects path traversal,
  rejects files outside the repository, rejects runtime files and lock files,
  and blocks secret-like paths.
- `hoca/hard_blockers.py` turns secret access, unsafe filesystem activity,
  unreviewed files, test failures, and missing PR credentials into explicit
  manager-blocking conditions.
- Safe staging scripts stage only reviewed file lists and run `git diff
  --cached --check`.
- The normal lifecycle is branch, implementation, validation, review, selective
  staging, commit, draft or ready PR, then human review. Direct merge is not the
  default.

## Known Limitations

- Docker bridge modes are not true domain allowlists. `package-install` and
  `github-only` currently document intent but do not enforce destination-level
  egress filtering by themselves.
- Host execution is not a sandbox. If enabled, filesystem isolation depends on
  the user-provided checkout and operating-system permissions.
- A mounted task worktree is writable by design. HOCA limits scope with
  worktrees, monitoring, staging checks, and review, not by making the worktree
  read-only.
- Secret detection is path-pattern based. It reduces common mistakes but cannot
  prove arbitrary file contents are safe.
- LLM providers may retain prompts or metadata according to their own policies.
  Do not place secrets in tasks, prompts, reports, or PR descriptions.
- The manager phase may have GitHub credentials for PR publication. Keep those
  credentials repo-scoped and short-lived where possible.
- MCP or plugin integrations must be allowlisted and rooted to the target
  worktree. A broadly rooted filesystem or GitHub tool can bypass the intended
  boundary.

## Emergency Stop And Cleanup

Stop a suspicious run before preserving output.

1. Interrupt the HOCA process with `Ctrl-C`.
2. If a sandbox container is still running, remove it by name or ID with
   `docker rm -f <container>`. HOCA sandbox containers are named with the run ID,
   for example `hoca-worker-<run_id>`.
3. Inspect the run directory under `.hoca-runtime/runs/<run_id>/`, especially
   `monitor-stop.json`, `monitor-events.jsonl`, stderr logs, and task reports.
4. Inspect repository state with `git status --short` and `git diff`.
5. If worktree sandboxing was used, inspect and remove only the HOCA-created
   worktree after preserving any needed logs.
6. Delete HOCA-created branches only after confirming they contain no human work.
7. Rotate any credential that may have been exposed in prompts, logs, commits,
   PR bodies, terminal output, or agent-readable files.
8. If a PR was created from a bad run, close it and delete the remote branch
   unless it contains intentional human work.

Never use broad destructive cleanup commands against the user's active checkout.
Prefer targeted cleanup of the specific run directory, container, worktree, and
branch that HOCA created.

## Review Checklist

Before treating a HOCA run as safe to publish, verify:

- Sandbox execution was enabled, or host execution was an explicit reviewed
  choice.
- The worker/reviewer received only the intended role model credentials.
- No GitHub token was forwarded to worker/reviewer phases.
- Network mode was appropriate for the task.
- The changed-file set contains no secret-like paths or unrelated files.
- Tests and reviewer gates passed, or remaining findings are explicitly recorded
  in the PR as non-blocking follow-up.
- The PR is available for human review and has not been auto-merged by default.
