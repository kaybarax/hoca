# HOCA

**Hermes + OpenHands Computer Automata**

HOCA is a local-first autonomous engineering automation toolkit. It coordinates
Hermes, OpenHands, and LLMs (Ollama, LM Studio, or cloud APIs) to turn a task
description or GitHub issue into a reviewed pull request — running entirely on
your own machine or using cloud LLM providers.

HOCA is **not** an unrestricted self-operating computer agent. It does not
wander across unrelated folders, commit without inspection, or merge without
approval. Every run is scoped to a single repository, gated by tests and code
review, and stopped before merge by default.

## Architecture

```text
Human or GitHub Issue
        │
        ▼
  Hermes Manager        ← plans work, delegates, coordinates review
        │
        ▼
  OpenHands Worker      ← implements changes inside the target repo
        │
        ▼
  Tests + Diff Check    ← runs project tests, inspects changes
        │
        ▼
  OpenHands Reviewer    ← independent code review for quality and security
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
| **Hermes** | Manager. Plans work, delegates bounded tasks, inspects state, coordinates review, manages the Git and PR lifecycle. |
| **OpenHands** | Worker. Performs implementation inside the target repository under the Manager's constraints. |
| **OpenHands** (review mode) | Reviewer. Provides independent quality, correctness, security, and unnecessary-edit review before changes are accepted. |
| **Ollama / LM Studio / Cloud** | LLM runtime. Runs models locally (Ollama, LM Studio) or via cloud APIs (DeepSeek, Gemini, etc.). |
| **Git + GitHub CLI** | Version control and pull request layer. |

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
| [Ollama](https://ollama.ai) | Local LLM runtime (or LM Studio / cloud API) |
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

Other Ollama-compatible coding models (such as `deepseek-coder` variants) can
be used by setting the `LLM_MODEL` and `OLLAMA_MODEL` environment variables in
your `.env` file.

### LM Studio (Local OpenAI-compatible)

HOCA supports [LM Studio](https://lmstudio.ai) as a local LLM provider via its
OpenAI-compatible API:

```env
LLM_MODEL=openai/gpt-oss-20b
LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=lm-studio
```

### Cloud / Enterprise LLMs

HOCA supports cloud LLMs through litellm provider prefixes:

```env
# DeepSeek
LLM_MODEL=deepseek/deepseek-chat
LLM_API_KEY=<your-api-key>

# Gemini
LLM_MODEL=gemini/gemini-2.0-flash
LLM_API_KEY=<your-api-key>

# Together AI
LLM_MODEL=together_ai/meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo
LLM_API_KEY=<your-api-key>
```

**Model fallback:** `scripts/select-model.sh` checks which models are available.
For Ollama, it tries `OLLAMA_MODEL` first (default `qwen-14b-pro`), then falls
back through `qwen-14b-pro`, `qwen-7b-pro`, and `qwen-32b-pro`. For LM Studio,
it queries the `/v1/models` endpoint. For cloud providers, no validation is
needed. If no provider is available, the run fails with a clear diagnostic.

For a single run, choose the model explicitly:

```sh
bin/hoca run /path/to/repo "Implement feature X" --model qwen-14b-pro
bin/hoca issue /path/to/repo 123 "Fix the login bug" --model qwen-14b-pro
```

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
dependencies, OpenHands, Ollama model pulls, and model alias creation. It prints
warnings for anything that needs manual follow-up.

After installation:

1. Start Ollama: `ollama serve`
2. Start Docker: open Docker Desktop, or run `colima start --cpu 6 --memory 16`
3. Authenticate GitHub: `gh auth login`

## Health Check

Verify that all dependencies are installed and configured:

```sh
bin/hoca doctor
```

Doctor checks for required commands, Docker availability, Ollama connectivity,
model presence, GitHub authentication, and environment configuration.

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
- `--model MODEL` — use a specific Ollama alias for this run, such as `qwen-14b-pro`

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

HOCA stops for human intervention when the failure is classified as
environmental or pre-existing, when the review tool itself crashes, or when
all rounds are exhausted. Configure the round limit with
`HOCA_MAX_TOTAL_ROUNDS` (default `3`): round 1 is the initial implementation
plus review, and rounds 2-3 are repair plus review cycles. The legacy
`HOCA_MAX_REPAIR_ATTEMPTS` variable is still accepted (value + 1 = total
rounds).

### Docker Sandbox (recommended default)

HOCA runs worker and reviewer OpenHands phases inside one HOCA-controlled Docker
container by default (`HOCA_USE_SANDBOX=true` in `.env.example`). The sandbox
comes pre-built with bun, node, pnpm, git, GitHub CLI, and a Python 3.12
OpenHands virtualenv — the worker has
network access for package installation but is filesystem-isolated from the
host (only the project worktree is mounted).

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
- Network access for `pnpm install`, registry fetches, etc.
- Host Ollama access via `host.docker.internal`
- Memory and PID limits (configurable via `HOCA_SANDBOX_MEMORY`, `HOCA_SANDBOX_PIDS`)
- Dropped capabilities (`--cap-drop=ALL --security-opt=no-new-privileges`)
- Automatic container cleanup after each run

### Pull Request Creation

When staging and commit succeed, HOCA creates a pull request using the GitHub
CLI on the manager host (`scripts/create-pr.sh`). The PR includes a summary,
validation results, code review status, and risk assessment. Worker and
reviewer sandboxes do not receive `GITHUB_TOKEN`; only the manager PR phase
uses GitHub credentials.

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
  preferred when it needs to launch Hermes, Docker, or Ollama.

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
| `ollama` not found | Install with `brew install ollama` |
| Ollama server not responding | Start it with `ollama serve` |
| Docker not running | Start Docker Desktop or run `colima start` |
| `gh` not authenticated | Run `gh auth login` |
| `openhands` not found | Run `curl -fsSL https://install.openhands.dev/install.sh \| sh` |
| Model not available | Run `ollama pull qwen2.5-coder:14b`, then `ollama create qwen-14b-pro -f ./models/Modelfile.14b` (or use the 7B/32B aliases) |
| Working tree dirty | HOCA requires a clean working tree. Commit or stash changes first. |
| Lock file exists | Another HOCA run may be active. Check `.hoca-runtime/runs/` for stale locks. |
| Tests fail | Check `.hoca-runtime/runs/<run-id>/tests.log` for details |
| Review not LGTM | Check `.hoca-runtime/runs/<run-id>/openhands-review.txt` for required fixes |

## Logs

HOCA stores run artifacts in the target repository under `.hoca-runtime/`:

```
.hoca-runtime/
├── runs/
│   └── <run-id>/
│       ├── status.json         # Run state and metadata
│       ├── openhands-output.*  # Worker output
│       ├── openhands-review.txt # Reviewer feedback
│       ├── tests.log           # Test results
│       ├── git-status.txt      # Changed files
│       ├── git-diff.patch      # Full diff
│       └── pr-body.md          # PR description
└── logs/
```

Add `.hoca-runtime/` to the target repository's `.gitignore`.
`bin/hoca init-project /path/to/repo` adds that ignore rule and copies the
OpenHands and PR templates for you when they are missing.

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
