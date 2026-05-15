# HOCA

**Hermes + OpenHands Computer Automata**

HOCA is a local-first autonomous engineering automation toolkit. It coordinates
Hermes, OpenHands, Aider, and Ollama to turn a task description or GitHub issue
into a reviewed pull request — running entirely on your own machine.

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
  Aider Reviewer        ← independent code review for quality and security
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
| **Aider** | Reviewer. Provides independent quality, correctness, security, and unnecessary-edit review before changes are accepted. |
| **Ollama** | Local LLM runtime. Runs models locally to keep the system local-first. |
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
| Python 3.12+ | HOCA runtime and Aider |
| Node.js | Test runner support for JS/TS projects |
| Git | Version control |
| [GitHub CLI (`gh`)](https://cli.github.com) | PR creation and authentication |
| [Ollama](https://ollama.ai) | Local LLM runtime |
| [OpenHands CLI](https://docs.all-hands.dev) | AI worker agent |
| [Aider](https://aider.chat) | AI code reviewer |
| [Hermes Agent](https://github.com/anthropics/hermes) | Manager agent |

## Model Support

HOCA defaults to the local Ollama alias `qwen-32b-pro`, created from
`qwen2.5-coder:32b` with a custom Modelfile that sets a 32K context window. If
your machine cannot run the 32B model, HOCA supports smaller alternatives:

| HOCA Alias | Base Model | RAM Needed | Context | Modelfile |
|------------|------------|------------|---------|-----------|
| `qwen-32b-pro` | `qwen2.5-coder:32b` | ~48 GB | 32768 | `models/Modelfile` |
| `qwen-14b-pro` | `qwen2.5-coder:14b` | ~24 GB | 16384 | `models/Modelfile.14b` |
| `qwen-7b-pro` | `qwen2.5-coder:7b` | ~16 GB | 8192 | `models/Modelfile.7b` |

Other Ollama-compatible coding models (such as `deepseek-coder` variants) can
be used by setting the `LLM_MODEL`, `AIDER_MODEL`, and `OLLAMA_MODEL`
environment variables in your `.env` file.

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
dependencies, Aider, OpenHands, Ollama model pulls, and model alias creation. It
prints warnings for anything that needs manual follow-up.

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

### Pull Request Creation

When staging and commit succeed, HOCA creates a pull request using the GitHub
CLI. The PR includes a summary, validation results, Aider review status, and
risk assessment.

### Auto-Merge

Auto-merge is **disabled by default**. When enabled with `--auto-merge`, HOCA
runs additional guards before merging:

- Tests must pass.
- Aider must return LGTM.
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
| `aider` not found | Run `brew install pipx && pipx install aider-install && aider-install` |
| Model not available | Run `ollama pull qwen2.5-coder:32b`, then `ollama create qwen-32b-pro -f ./models/Modelfile` (or use the 14B/7B aliases) |
| Working tree dirty | HOCA requires a clean working tree. Commit or stash changes first. |
| Lock file exists | Another HOCA run may be active. Check `.hoca-runtime/runs/` for stale locks. |
| Tests fail | Check `.hoca-runtime/runs/<run-id>/tests.log` for details |
| Aider not LGTM | Check `.hoca-runtime/runs/<run-id>/aider-review.txt` for required fixes |

## Logs

HOCA stores run artifacts in the target repository under `.hoca-runtime/`:

```
.hoca-runtime/
├── runs/
│   └── <run-id>/
│       ├── status.json         # Run state and metadata
│       ├── openhands-output.*  # Worker output
│       ├── aider-review.txt    # Reviewer feedback
│       ├── tests.log           # Test results
│       ├── git-status.txt      # Changed files
│       ├── git-diff.patch      # Full diff
│       └── pr-body.md          # PR description
└── logs/
```

Add `.hoca-runtime/` to the target repository's `.gitignore`.

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
- **Review before merge.** Aider reviews all changes. Human review is the
  default before merge.
- **No blind commits.** Files are staged selectively. Secret-like paths are
  always rejected.
- **No blind merges.** Auto-merge is off by default and gated by strict guards
  even when enabled.
- **Clean working tree.** Runs refuse to start when uncommitted human changes
  are present.
- **Transparent.** All run artifacts, logs, and decisions are recorded in
  `.hoca-runtime/`.
