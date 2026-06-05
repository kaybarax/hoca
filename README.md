# HOCA

**Hermes + OpenHands Computer Automata**

HOCA is a local-first autonomous engineering automation toolkit. It coordinates
Hermes, OpenHands, and LLMs to turn a task description or GitHub issue into a
reviewed pull request. You can run it entirely on your own machine with Ollama,
LM Studio, llama.cpp, MLX, or another LiteLLM/OpenAI-compatible local runtime,
or use cloud LLM providers.

HOCA is **not** an unrestricted self-operating computer agent. It does not
wander across unrelated folders, commit without inspection, or merge without
approval. Every run is scoped to a single repository, gated by tests and code
review, and stopped before merge by default.

## Architecture

```text
Human or GitHub Issue
        │
        ▼
  Hermes Manager        ← plans work, delegates, arbitrates review
        │
        ▼
  Hermes Worker         ← coordinates OpenHands implementation
        │
        ▼
  Tests + Diff Check    ← runs project tests, inspects changes
        │
        ▼
  Hermes Reviewer       ← coordinates independent QA/security review
        │
        ▼
  Safe Git Staging      ← selective staging, rejects secrets and blind adds
        │
        ▼
  Commit + PR           ← creates branch, commits, opens pull request
        │
        ▼
  Human Review          ← default: no auto-merge, human approves
```

### Roles

| Component | Role |
|-----------|------|
| **`hoca-manager` Hermes profile** | Engineering manager, team lead, and product-owner delegate. Plans work, creates the task spec, assigns implementation and review, arbitrates findings, and owns staging, commit, PR publication, and final reporting. |
| **`hoca-worker` Hermes profile** | Principal engineer lane. Converts the task spec into an OpenHands implementation prompt, monitors the worker run, and returns a structured attempt report. |
| **`hoca-reviewer` Hermes profile** | QA, security, and release-quality lane. Reviews the diff and validation output, classifies findings, and returns a structured review report. |
| **OpenHands** | Execution engine used by worker and reviewer profiles for code changes and independent review inside the scoped target repository. |
| **Local or cloud LLM runtime** | Model backend. Runs models locally through Ollama or an OpenAI-compatible server such as LM Studio, llama.cpp, MLX, LocalAI, or vLLM, or via cloud APIs such as OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, and similar providers. |
| **Git + GitHub CLI** | Version control and pull request layer. |

HOCA runs through the profile-backed Manager -> Worker -> Reviewer workflow
using the resolved manager/worker/reviewer role model settings.

## Requirements

### Hardware

- Apple Silicon Mac or comparable local machine
- 48 GB RAM recommended for 32B models (smaller models need less — see
  [Model Support](#model-support))

### Software

| Dependency | Purpose |
|------------|---------|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) or [Colima](https://github.com/abiosoft/colima) | OpenHands sandbox backend |
| [Homebrew](https://brew.sh) | Package manager for macOS dependencies |
| Python 3.12+ | HOCA runtime |
| Node.js | Test runner support for JS/TS projects |
| Git | Version control |
| [GitHub CLI (`gh`)](https://cli.github.com) | PR creation and authentication |
| LLM backend | Ollama for the default local setup, or another LiteLLM/OpenAI-compatible local runtime such as LM Studio, llama.cpp, MLX, LocalAI, or vLLM; cloud providers are also supported |
| [OpenHands CLI](https://docs.all-hands.dev) | AI worker agent and code reviewer |
| [Hermes Agent](https://github.com/anthropics/hermes) | Manager agent |

## Model Support

HOCA defaults to the local Ollama alias `qwen-14b-pro`, created from
`qwen2.5-coder:14b` with a custom Modelfile that sets a 16K context window. If
your machine can comfortably run the 32B model, HOCA also supports it:

| HOCA Alias | Base Model | RAM Needed | Context | Modelfile |
|------------|------------|------------|---------|-----------|
| `qwen-32b-pro` | `qwen2.5-coder:32b` | ~48 GB | 32768 | `models/Modelfile` |
| `qwen-14b-pro` | `qwen2.5-coder:14b` | ~24 GB | 16384 | `models/Modelfile.14b` |
| `qwen-7b-pro` | `qwen2.5-coder:7b` | ~16 GB | 8192 | `models/Modelfile.7b` |

The built-in fallback path is Ollama, but the role model blocks below can point
at any LiteLLM/OpenAI-compatible model backend. For local runtimes such as LM
Studio, llama.cpp server, MLX server, LocalAI, or vLLM, set the model name,
local `*_MODEL_BASE_URL`, and whatever API key placeholder that server expects.
For cloud providers, leave `*_MODEL_BASE_URL` empty unless the provider or
gateway requires a custom endpoint.

### Role Model Pool

HOCA can route manager, worker, and reviewer phases through role-scoped model
configuration in `.env`:

```env
HOCA_MANAGER_MODEL_NAME=manager
HOCA_MANAGER_MODEL_MODEL=ollama/qwen-7b-pro
HOCA_MANAGER_MODEL_BASE_URL=http://127.0.0.1:11434
HOCA_MANAGER_MODEL_API_KEY=ollama

HOCA_WORKER_MODEL_NAME=worker
HOCA_WORKER_MODEL_MODEL=ollama/qwen-14b-pro
HOCA_WORKER_MODEL_BASE_URL=http://127.0.0.1:11434
HOCA_WORKER_MODEL_API_KEY=ollama

HOCA_REVIEWER_MODEL_NAME=reviewer
HOCA_REVIEWER_MODEL_MODEL=openai/gpt-oss-20b
HOCA_REVIEWER_MODEL_BASE_URL=http://localhost:1234/v1
HOCA_REVIEWER_MODEL_API_KEY=local
```

The manager can use a balanced planning model, the worker can use a
coding-specialized model, and the reviewer can use a stronger reasoning model
when available. Configure all three roles explicitly; use the same values in
multiple role blocks when they should share one model. If a role is empty while
another role is active, HOCA uses the first active role model as the fallback.
Only the selected role model's credentials are forwarded to that phase, and API
keys are redacted from reports and logs.

### Local OpenAI-Compatible And Cloud Models

Use the same role blocks for local OpenAI-compatible servers and cloud models.
Local examples include LM Studio, llama.cpp's OpenAI-compatible server, MLX
servers, LocalAI, and vLLM:

```env
HOCA_REVIEWER_MODEL_NAME=reviewer
HOCA_REVIEWER_MODEL_MODEL=openai/gpt-oss-20b
HOCA_REVIEWER_MODEL_BASE_URL=http://127.0.0.1:8080/v1
HOCA_REVIEWER_MODEL_API_KEY=local

HOCA_WORKER_MODEL_NAME=worker
HOCA_WORKER_MODEL_MODEL=deepseek/deepseek-chat
HOCA_WORKER_MODEL_BASE_URL=
HOCA_WORKER_MODEL_API_KEY=<your-api-key>
```

For local OpenAI-compatible runtimes, the `openai/` model prefix tells LiteLLM
to use the OpenAI protocol, while `*_MODEL_BASE_URL` points at your local
server. For example, llama.cpp commonly serves at
`http://127.0.0.1:8080/v1`; LM Studio often serves at
`http://localhost:1234/v1`.

**Ollama fallback:** when no role model blocks are active, HOCA uses
`OLLAMA_MODEL` and `OLLAMA_BASE_URL` as the local fallback.

Smaller models trade capability for speed and lower memory use. For high-risk
work, human review is recommended regardless of model size.

## Installation

```sh
git clone <repo-url> hoca
cd hoca
cp .env.example .env
# Edit .env with your local values
./scripts/install.sh
```

The install script handles Homebrew packages, a repo-local `.venv` for Python
dependencies, OpenHands, and the default Ollama model pulls/aliases. If you use
LM Studio, llama.cpp, MLX, another local OpenAI-compatible server, or a cloud
provider, configure the role model blocks in `.env` instead of relying on the
Ollama fallback.

After installation:

1. Start your selected model backend: for the default path, `ollama serve`; for
   LM Studio, llama.cpp, MLX, or another local server, start its
   OpenAI-compatible `/v1` endpoint.
2. Start Docker: open Docker Desktop, or run `colima start --cpu 6 --memory 16`
3. Authenticate GitHub: `gh auth login`

### Hermes Profiles

Install or refresh the bundled Hermes profiles:

```sh
scripts/setup-hermes-profiles.sh
```

The setup creates profile scaffolding for:

- `hoca-manager`
- `hoca-worker`
- `hoca-reviewer`

Profiles give each role its own instructions, identity, and default behavior.
They are not security sandboxes by themselves; HOCA still relies on scoped
working directories, environment allowlists, Docker sandboxing, safe staging,
and manager-only PR publication for safety.

## Health Check

Verify that all dependencies are installed and configured:

```sh
bin/hoca doctor
```

Doctor checks for required commands, Docker availability, GitHub
authentication, environment configuration, the default Ollama fallback when
used, and role model configuration for local or cloud providers.

## Usage

### Initialize a Target Project

Prepare a repository for HOCA runs by copying worker instructions and reviewer
configuration:

```sh
bin/hoca init-project /path/to/repo
```

### Manual Run

Run a task against a repository:

```sh
bin/hoca run /path/to/repo "Implement feature X"
```

### Issue Run

Run a task linked to a GitHub issue:

```sh
bin/hoca issue /path/to/repo 123 "Fix the login bug"
```

Both `run` and `issue` accept optional flags:

- `--auto-merge` — enable guarded auto-merge (disabled by default)
- `--notify-telegram` — send Telegram notifications on completion
- `--dev-branch BRANCH` — manager override for the target repo development branch

## Default Behavior

HOCA is intentionally conservative by default.

### Stop Before Commit

HOCA stops before committing when it cannot safely determine which files to
stage. Changed files are recorded for human review. This prevents accidental
inclusion of unrelated changes or sensitive files.

### Safe Staging

HOCA never runs `git add .`, `git add -A`, or `git commit -am`. Files are
staged selectively. The staging process rejects secret-like files including
`.env`, private keys, kubeconfigs, package registry credentials, Docker
credentials, and local credential stores.

### Repair Loop

When OpenHands produces changes but tests fail because of the current task, or
the code review requests fixes, HOCA gives OpenHands a repair brief containing
the failure summary, recent logs, review feedback, and current diff. It repeats
validation after each repair pass.

The manager is the final arbiter for each reviewer finding. It can accept a
finding and send a repair brief, reject an invalid finding with a recorded
reason, downgrade low-impact follow-up work, or stop the run when the task is no
longer safe to continue automatically.

HOCA stops for human intervention when the failure is classified as
environmental or pre-existing, when the review tool itself crashes, or when
all rounds are exhausted. Configure the round limit with
`HOCA_MAX_TOTAL_ROUNDS` (default `3`): round 1 is the initial implementation
plus review, and rounds 2-3 are repair plus review cycles.

### Optional Durable Kanban Mode

Kanban orchestration is intentionally off by default:

```env
HOCA_USE_KANBAN=false
```

The standard `bin/hoca run /path/to/repo "Task"` workflow does not require a
Hermes Kanban board or Kanban setup. Keep `HOCA_USE_KANBAN=false` for the
profile-backed Manager -> Worker -> Reviewer pipeline. Future Kanban commands can
opt in to durable multi-agent coordination incrementally for longer-running work
that needs restartable state, role handoffs, and an auditable task board.

### Docker Sandbox (recommended default)

HOCA runs worker and reviewer OpenHands phases inside one HOCA-controlled Docker
container by default (`HOCA_USE_SANDBOX=true` in `.env.example`). The sandbox
comes pre-built with bun, node, pnpm, git, GitHub CLI, and a Python 3.12
OpenHands virtualenv. It is filesystem-isolated from the host by mounting only
the project worktree.

Sandbox network access is offline by default (`HOCA_NETWORK_MODE=offline`).
When a task needs dependency downloads, opt into `HOCA_NETWORK_MODE=package-install`
for that run or task spec and record the reason in the run notes.

No extra setup is required when Docker is running; `bin/hoca run` uses the
sandbox automatically:

```sh
bin/hoca run /path/to/repo "Implement feature X"
```

Host-local OpenHands is higher risk and requires an explicit opt-in:

```sh
export HOCA_USE_SANDBOX=false
bin/hoca run /path/to/repo "Implement feature X"
```

Wrappers print a visible warning and record `host-execution-warning.txt` in the
run directory when host execution is used.

Avoid nested sandbox layers: prefer this single HOCA OpenHands container boundary
rather than stacking Hermes Docker terminal backends with an additional OpenHands
sandbox inside. If Hermes itself runs in Docker, mount only the HOCA workspace and
the task worktree — not your home directory, credential stores, or the Docker socket.

Build the sandbox image manually:

```sh
docker compose --profile sandbox build
# or
scripts/sandbox-manager.sh build
```

The sandbox provides:

- Pre-installed tools (bun, node, pnpm, git, gh, Python, OpenHands)
- Credential isolation: `GITHUB_TOKEN` is not forwarded into worker/reviewer
  containers; PR creation uses manager-side `gh` authentication only
- Offline network isolation by default, with explicit opt-in modes for package
  installation or broader egress
- Host-local LLM access via `host.docker.internal` when a bridge network mode is
  selected and the role model base URL points at a local server
- Memory and PID limits (configurable via `HOCA_SANDBOX_MEMORY`, `HOCA_SANDBOX_PIDS`)
- Dropped capabilities (`--cap-drop=ALL --security-opt=no-new-privileges`)
- Automatic container cleanup after each run

### Pull Request Creation

When staging and commit succeed, HOCA creates a pull request using the GitHub
CLI on the manager host (`scripts/create-pr.sh`). The PR includes a summary,
validation results, code review status, and risk assessment. Worker and
reviewer sandboxes do not receive `GITHUB_TOKEN`; only the manager PR phase
uses GitHub credentials.

PR publication is deliberately manager-owned rather than delegated to another
Hermes profile. Worker and reviewer lanes may provide summaries, validation
notes, risk notes, and follow-up suggestions, but deterministic HOCA scripts do
the mechanical staging, commit, push, and PR creation after the manager accepts
the run state.

### Auto-Merge

Auto-merge is **disabled by default**. When enabled with `--auto-merge`, HOCA
runs additional guards before merging:

- Tests must pass.
- Code review must return LGTM.
- Risk level must be low.
- No secret-like files in the changeset.
- The repository must allow auto-merge in GitHub settings.
- The PR must be mergeable.

Auto-merge is **never** allowed for authentication, authorization, payment,
database migration, infrastructure, or security-sensitive changes.

Human review remains the default release gate. A successful HOCA run opens a PR
for inspection; it does not merge that PR unless `--auto-merge` is explicitly
requested and all guarded merge checks pass.

## GitHub Issue Automation

HOCA can be triggered from GitHub issues using a webhook workflow.

### Webhook Setup

1. Start the local webhook listener:

   ```sh
   source .env
   python scripts/webhook-listener.py
   ```

2. Expose the listener through a secure tunnel (e.g., Cloudflare Tunnel):

   ```sh
   cloudflared tunnel --url http://127.0.0.1:5000
   ```

3. Configure GitHub repository secrets:

   | Secret | Value |
   |--------|-------|
   | `AGENT_WEBHOOK_URL` | Your tunnel URL + `/webhook` (e.g., `https://example.trycloudflare.com/webhook`) |
   | `HOCA_WEBHOOK_SECRET` | A shared secret for HMAC signature verification |

4. Create a `fix-me` label in your GitHub repository.

5. Add the `fix-me` label to any issue to trigger a HOCA run.

The included GitHub Actions workflow (`.github/workflows/agent-trigger.yml`)
sends a signed webhook payload to your local listener, which dispatches Hermes
to run HOCA against the issue.

### Webhook Security

- The listener binds to `127.0.0.1` only — it is not directly exposed to the
  internet.
- Every incoming webhook is verified against an HMAC-SHA256 signature using
  `HOCA_WEBHOOK_SECRET`.
- Issue titles are never passed directly into shell commands.
- The listener can also run as a Docker service, but running on the host is
  preferred when it needs to launch Hermes, Docker, or a host-local LLM backend.

## Telegram Notifications

HOCA can send Telegram messages on task completion or when a run is blocked.

Set these in your `.env` file:

```
TELEGRAM_BOT_TOKEN=<your-bot-token>
TELEGRAM_CHAT_ID=<your-chat-id>
```

Use the `--notify-telegram` flag with `run` or `issue` commands.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ollama` not found | Install with `brew install ollama` if using the default Ollama fallback, or configure role model blocks for another local/cloud provider |
| Ollama server not responding | Start it with `ollama serve`, or configure active role model blocks so HOCA does not rely on the Ollama fallback |
| Docker not running | Start Docker Desktop or run `colima start` |
| `gh` not authenticated | Run `gh auth login` |
| `openhands` not found | Run `curl -fsSL https://install.openhands.dev/install.sh \| sh` |
| Model not available | For Ollama, run `ollama pull qwen2.5-coder:14b`, then `ollama create qwen-14b-pro -f ./models/Modelfile.14b` (or use the 7B/32B aliases). For LM Studio, llama.cpp, MLX, or cloud providers, confirm the role model name, base URL, and API key in `.env`. |
| Working tree dirty | HOCA requires a clean working tree. Commit or stash changes first. |
| Lock file exists | Another HOCA run may be active. Check the runtime archive or rerun with `HOCA_KEEP_RUNTIME=true` for immediate debugging. |
| Tests fail | Check the archived run directory under `~/.hoca/runtime-archives/<repo-name>/<run-id>/` for `tests-summary.md` and test logs. |
| Review not LGTM | Check the archived run directory under `~/.hoca/runtime-archives/<repo-name>/<run-id>/` for review artifacts. |

## Logs

HOCA writes run artifacts under the target repository's `.hoca-runtime/` during
execution, then archives the current run outside the target checkout and removes
the target `.hoca-runtime/` on exit. By default archives are stored under:

```text
~/.hoca/runtime-archives/<repo-name>/<run-id>/
```

Set `HOCA_RUNTIME_ARCHIVE_ROOT=/path/to/archive-root` to choose a different
archive location. Set `HOCA_KEEP_RUNTIME=true` only for immediate debugging
when you intentionally want to leave `.hoca-runtime/` in the target repository.

Archived run layout:

```
<archive-root>/<repo-name>/<run-id>/
│       ├── status.json         # Run state and metadata
│       ├── openhands-output.*  # Worker output
│       ├── openhands-review.txt # Reviewer feedback
│       ├── tests.log           # Test results
│       ├── git-status.txt      # Changed files
│       ├── git-diff.patch      # Full diff
│       └── pr-body.md          # PR description
```

`bin/hoca init-project /path/to/repo` still adds `.hoca-runtime/` to the target
repository's `.gitignore` because the directory exists during active runs. It
also copies the OpenHands and PR templates for you when they are missing.

## Development

```sh
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

## Known Limitations

- HOCA currently targets macOS. Linux support is possible but untested.
- Local models are not as reliable as frontier hosted models. Review all output
  carefully.
- OpenHands runs in headless always-approve mode. HOCA's safety controls
  operate at the staging and review layer, not inside the worker sandbox.
- The webhook listener requires a secure tunnel for GitHub to reach your local
  machine. The tunnel must stay running for issue automation to work.
- Duplicate GitHub Actions runs can occur when issue events (opened + labeled)
  fire together. The lock mechanism prevents concurrent HOCA runs for the same
  issue.

## Safety Principles

HOCA is built around the idea that automation is most useful when it is
constrained:

- **Repository-scoped.** Every run is confined to a single repository working
  tree.
- **Review before merge.** OpenHands reviews all changes. Human review is the
  default before merge.
- **No blind commits.** Files are staged selectively. Secret-like paths are
  always rejected.
- **No blind merges.** Auto-merge is off by default and gated by strict guards
  even when enabled.
- **Clean working tree.** Runs refuse to start when uncommitted human changes
  are present.
- **Transparent.** All run artifacts, logs, and decisions are recorded in
  `.hoca-runtime/`.
