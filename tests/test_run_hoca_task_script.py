from __future__ import annotations

import json
import subprocess
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run-hoca-task.sh"
_TEMPLATE_REPO: Path | None = None


def test_run_hoca_task_exports_hoca_dotenv_path() -> None:
    content = SCRIPT.read_text(encoding="utf-8")

    assert 'export HOCA_DOTENV_PATH="${HOCA_DOTENV_PATH:-$HOCA_ROOT/.env}"' in content


def test_hoca_scripts_honor_hoca_python_for_hoca_modules() -> None:
    root = Path(__file__).resolve().parents[1]
    scripts = [
        root / "scripts" / "run-hoca-task.sh",
        root / "scripts" / "run-openhands-task.sh",
        root / "scripts" / "create-pr.sh",
        root / "scripts" / "generate-task-report.sh",
        root / "scripts" / "review-with-openhands.sh",
        root / "scripts" / "safe-stage-after-review.sh",
        root / "scripts" / "auto-merge-guards.sh",
    ]

    for script in scripts:
        content = script.read_text(encoding="utf-8")
        assert 'PYTHON_BIN="${HOCA_PYTHON:-python3}"' in content, script
        assert "python3 -m hoca." not in content, script
        assert 'PYTHONPATH="$HOCA_ROOT' not in content or '"$PYTHON_BIN"' in content, script


def test_openhands_wrapper_prepends_execution_root_guard() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "run-openhands-task.sh"
    content = script.read_text(encoding="utf-8")

    assert "HOCA execution root: $PROJECT_PATH" in content
    assert "the only repository root you may read, write, inspect, or run commands in" in content
    assert "rewrite the command to" in content
    assert "Do not cd to the original checkout" in content


def test_openhands_wrapper_does_not_embed_api_key_in_python_command() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "run-openhands-task.sh"
    content = script.read_text(encoding="utf-8")

    assert "HOCA_OPENHANDS_API_KEY" in content
    assert "api_key = os.environ['HOCA_OPENHANDS_API_KEY']" in content
    assert "env_override['LLM_API_KEY'] = '${API_KEY}'" not in content
    assert "env_override['LLM_MODEL'] = '${MODEL}'" not in content


def base_env() -> dict[str, str]:
    env = os.environ.copy()
    env["HOCA_DOCTOR_SCRIPT"] = "true"
    env["HOCA_USE_SANDBOX"] = "false"
    env["HOCA_USE_WORKTREE_SANDBOX"] = "false"
    hermes_home = Path(tempfile.mkdtemp(prefix="hoca-test-hermes-home-"))
    (hermes_home / "profiles" / "hoca-worker").mkdir(parents=True)
    (hermes_home / "profiles" / "hoca-reviewer").mkdir(parents=True)
    env["HERMES_HOME"] = str(hermes_home)
    return env


def init_repo(path: Path) -> None:
    template = _template_repo()
    shutil.copytree(template, path, dirs_exist_ok=True)


def _template_repo() -> Path:
    global _TEMPLATE_REPO
    if _TEMPLATE_REPO is not None:
        return _TEMPLATE_REPO

    root = Path(tempfile.mkdtemp(prefix="hoca-run-task-template-"))
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=root, check=True)
    (root / ".gitignore").write_text(".hoca-runtime/\n", encoding="utf-8")
    (root / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", ".gitignore", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    _TEMPLATE_REPO = root
    return root


def run_hoca_task(repo: Path, task: str) -> subprocess.CompletedProcess[str]:
    env = base_env()
    env["HOCA_RUNTIME_ARCHIVE_ROOT"] = str(archive_root(repo))
    return subprocess.run(
        [str(SCRIPT), str(repo), task],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def make_fake_preflight_bin(
    tmp_path: Path,
    *,
    openhands_body: str | None = None,
    review_body: str | None = None,
    pytest_body: str | None = None,
) -> Path:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(parents=True)

    write_executable(
        fake_bin / "gh",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "auth" && "${2:-}" == "status" ]]; then exit 0; fi\n'
        'if [[ "${1:-}" == "pr" && "${2:-}" == "create" ]]; then\n'
        "  echo 'https://github.com/example/repo/pull/1'\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "${1:-}" == "pr" && "${2:-}" == "view" ]]; then\n'
        "  echo 'https://github.com/example/repo/pull/1'\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "${1:-}" == "repo" && "${2:-}" == "view" ]]; then echo "example/repo"; exit 0; fi\n'
        'if [[ "${1:-}" == "api" ]]; then echo "false"; exit 0; fi\n'
        "exit 0\n",
    )
    write_executable(fake_bin / "node", "#!/usr/bin/env bash\necho v20.0.0\n")
    write_executable(
        fake_bin / "docker",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "info" ]]; then exit 0; fi\n'
        'if [[ "${1:-}" == "image" && "${2:-}" == "inspect" ]]; then\n'
        '  if [[ "${3:-}" == "--format" ]]; then echo "worker"; fi\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )
    write_executable(fake_bin / "curl", "#!/usr/bin/env bash\nexit 0\n")
    write_executable(
        fake_bin / "ollama",
        "#!/usr/bin/env bash\ncat <<'EOF'\nNAME ID SIZE MODIFIED\nqwen-7b-pro abc 1GB now\nEOF\n",
    )

    openhands = openhands_body or "echo 'OpenHands fake run complete.'\n"
    review_default = review_body or "echo 'Review complete.'\necho 'LGTM'\n"
    write_executable(
        fake_bin / "openhands-body.sh",
        "#!/usr/bin/env bash\nset -euo pipefail\n" + openhands,
    )
    write_executable(
        fake_bin / "review-body.sh",
        "#!/usr/bin/env bash\nset -euo pipefail\n" + review_default,
    )
    write_executable(
        fake_bin / "openhands",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "--help" ]]; then\n'
        '  echo "openhands --headless --task --override-with-envs --json"\n'
        "  exit 0\n"
        "fi\n"
        'TASK_ARG=""\n'
        'prev=""\n'
        'for arg in "$@"; do\n'
        '  if [[ "$prev" == "--task" ]]; then TASK_ARG="$arg"; fi\n'
        '  prev="$arg"\n'
        "done\n"
        'if [[ "$TASK_ARG" == *"Review the current repository changes"* ]]; then\n'
        f"  {review_default}"
        "  exit 0\n"
        "fi\n"
        f"{openhands}",
    )
    write_executable(
        fake_bin / "hermes",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'PROFILE=""\n'
        'if [[ "${1:-}" == "-p" ]]; then PROFILE="${2:-}"; shift 2; fi\n'
        '[[ "${1:-}" == "chat" ]] || { echo "missing chat subcommand" >&2; exit 2; }\n'
        "shift\n"
        'PROMPT=""\n'
        'while [[ $# -gt 0 ]]; do\n'
        '  case "$1" in\n'
        '    --query|-q) PROMPT="${2:-}"; shift 2;;\n'
        '    --model) [[ "${2:-}" == */* ]] || { echo "missing model override" >&2; exit 2; }; shift 2;;\n'
        '    *) shift;;\n'
        '  esac\n'
        'done\n'
        'RUN_DIR="$PWD"\n'
        'PROJECT="$(printf "%s" "$PROMPT" | sed -n "s/^- project_path: //p" | head -n 1)"\n'
        '[ -n "$PROJECT" ] || PROJECT="${HERMES_TEST_PROJECT:-}"\n'
        '[ -n "$PROJECT" ] || { echo "project missing" >&2; exit 2; }\n'
        'ROUND="$(printf "%s" "$PROMPT" | sed -n "s/^- round: \\([0-9][0-9]*\\)$/\\1/p" | head -n 1)"\n'
        '[ -n "$ROUND" ] || ROUND="${HERMES_TEST_ROUND:-1}"\n'
        'mkdir -p "$RUN_DIR/attempts" "$RUN_DIR/reviews" "$RUN_DIR/logs"\n'
        'if [[ "$PROFILE" == "hoca-reviewer" ]]; then\n'
        '  export OPENHANDS_REVIEW_COUNT_FILE="${OPENHANDS_REVIEW_COUNT_FILE:-$PROJECT/openhands-review-count}"\n'
        f'  review_output="$(cd "$PROJECT" && "{fake_bin / "review-body.sh"}")"\n'
        '  verdict="fix_required"\n'
        '  findings=\'[{"id":"F1","severity":"medium","category":"correctness","file":null,"summary":"Review requested changes","required_fix":"Address reviewer feedback"}]\'\n'
        '  if [[ "$review_output" == *"LGTM"* ]]; then verdict="LGTM"; findings="[]"; fi\n'
        '  cat > "$RUN_DIR/reviews/review-report-${ROUND}.json" <<EOF\n'
        '{"schema_version":1,"run_id":"run-test","round":'"${ROUND}"',"role":"reviewer",'
        '"verdict":"'"${verdict}"'","findings":'"${findings}"',"pr_notes":{"summary":["Hermes reviewer completed"],"known_followups":[]}}\n'
        "EOF\n"
        '  printf "%s\\n" "$PROMPT" > "$RUN_DIR/logs/reviewer-hermes-invoked-round-${ROUND}.txt"\n'
        '  exit 0\n'
        'fi\n'
        'cd "$PROJECT"\n'
        'export OPENHANDS_COUNT_FILE="${OPENHANDS_COUNT_FILE:-$PROJECT/openhands-count}"\n'
        f'worker_output="$("{fake_bin / "openhands-body.sh"}")"\n'
        'printf "%s\\n" "$worker_output"\n'
        'if [[ "$worker_output" == *"ConversationErrorEvent"* ]]; then\n'
        '  echo "OpenHands reported a conversation error event."\n'
        '  exit 1\n'
        'fi\n'
        'cat > "$RUN_DIR/attempts/worker-attempt-${ROUND}.json" <<EOF\n'
        '{"schema_version":1,"run_id":"run-test","round":'"${ROUND}"',"role":"worker",'
        '"status":"completed","changed_files":[],"summary":["Hermes worker completed"],'
        '"commands_run":["run-openhands-task.sh"],"tests_run":[],"known_risks":[],'
        '"blocked_reason":null,"artifact_paths":{}}\n'
        "EOF\n"
        'printf "%s\\n" "$PROMPT" > "$RUN_DIR/logs/worker-hermes-invoked-round-${ROUND}.txt"\n',
    )

    if pytest_body is not None:
        write_executable(
            fake_bin / "pytest", "#!/usr/bin/env bash\nset -euo pipefail\n" + pytest_body
        )

    return fake_bin


def run_hoca_task_with_env(
    repo: Path,
    task: str,
    env: dict[str, str],
    *extra_args: str,
) -> subprocess.CompletedProcess[str]:
    run_env = env.copy()
    run_env.setdefault("HOCA_RUNTIME_ARCHIVE_ROOT", str(archive_root(repo)))
    return subprocess.run(
        [str(SCRIPT), str(repo), task, *extra_args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=run_env,
    )


def archive_root(repo: Path) -> Path:
    return repo.parent / f"{repo.name}-hoca-archives"


def archived_runs_dir(repo: Path) -> Path:
    return archive_root(repo) / repo.name


def latest_run_dir(repo: Path) -> Path:
    target_runs = repo / ".hoca-runtime" / "runs"
    if target_runs.exists():
        run_dirs = sorted(p for p in target_runs.iterdir() if p.is_dir())
        if run_dirs:
            return run_dirs[-1]
    archive_runs = archived_runs_dir(repo)
    run_dirs = sorted(p for p in archive_runs.iterdir() if p.is_dir())
    return run_dirs[-1]


def run_dir(repo: Path, run_id: str) -> Path:
    target = repo / ".hoca-runtime" / "runs" / run_id
    if target.exists():
        return target
    return archived_runs_dir(repo) / run_id


def latest_status(repo: Path, run_id: str | None = None) -> str:
    if run_id is None:
        status_path = latest_run_dir(repo) / "status.json"
    else:
        status_path = run_dir(repo, run_id) / "status.json"
    return status_path.read_text(encoding="utf-8")


def latest_notification_result(repo: Path) -> str:
    result_paths = sorted((repo / ".hoca-runtime" / "runs").glob("*/notification-result.txt"))
    if not result_paths:
        result_paths = sorted(archived_runs_dir(repo).glob("*/notification-result.txt"))
    return result_paths[-1].read_text(encoding="utf-8")


def fake_tools_root(repo: Path, name: str = "tools") -> Path:
    return repo.parent / f"{repo.name}-{name}"


def prepare_pr_ready_repo(repo: Path) -> None:
    (repo / "templates").mkdir(exist_ok=True)
    (repo / "templates" / "PR_TEMPLATE.md").write_text(
        "## Summary\n\n## Changes\n\n## Validation\n\n## Code Review\n\n"
        "## Risk\n\n## Linked Issue\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "--", "templates/PR_TEMPLATE.md"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "commit", "-m", "add PR template"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )
    remote = repo.parent / f"{repo.name}-origin.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True)
    subprocess.run(
        ["git", "push", "-u", "origin", "HEAD"], cwd=repo, check=True, stdout=subprocess.PIPE
    )


def test_run_hoca_task_uses_worker_profile(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="printf 'agent edit\\n' > README.md\n",
    )
    hermes_home = tmp_path / "hermes-home"
    setup_fake_hermes_worker(fake_bin, hermes_home)
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HERMES_HOME"] = str(hermes_home)
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"
    env["HERMES_TEST_PROJECT"] = str(tmp_path)

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode == 0, result.stderr
    assert "Running worker profile (implementation)" in result.stdout


def test_basic_run_does_not_require_kanban_when_flag_is_default_false(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="printf 'agent edit\\n' > README.md\n",
    )
    hermes_home = tmp_path / "hermes-home"
    setup_fake_hermes_worker(fake_bin, hermes_home)
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HERMES_HOME"] = str(hermes_home)
    env["HOCA_USE_KANBAN"] = "false"
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"
    env["HERMES_TEST_PROJECT"] = str(tmp_path)

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode == 0, result.stderr
    assert "Running worker profile (implementation)" in result.stdout
    assert "kanban" not in result.stderr.lower()


def setup_fake_hermes_worker(fake_bin: Path, hermes_home: Path) -> None:
    (hermes_home / "profiles" / "hoca-worker").mkdir(parents=True)
    (hermes_home / "profiles" / "hoca-reviewer").mkdir(parents=True)
    hermes = fake_bin / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'PROFILE=""\n'
        'if [[ "${1:-}" == "-p" ]]; then PROFILE="${2:-}"; shift 2; fi\n'
        '[[ "${1:-}" == "chat" ]] || { echo "missing chat subcommand" >&2; exit 2; }\n'
        "shift\n"
        'while [[ $# -gt 0 ]]; do\n'
        '  case "$1" in\n'
        "    --query|-q)\n"
        '      PROMPT="${2:-}"\n'
        "      shift 2\n"
        "      ;;\n"
        "    --model)\n"
        '      [[ "${2:-}" == */* ]] || { echo "missing model override" >&2; exit 2; }\n'
        "      shift 2\n"
        "      ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        'PROJECT="${HERMES_TEST_PROJECT:?}"\n'
        'RUN_DIR="$(find "$PROJECT/.hoca-runtime/runs" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)"\n'
        '[ -n "$RUN_DIR" ] || { echo "run dir missing" >&2; exit 1; }\n'
        'if [[ "$PROFILE" == "hoca-reviewer" ]]; then\n'
        '  ROUND="$(printf "%s" "$PROMPT" | sed -n "s/^- round: \\([0-9][0-9]*\\)$/\\1/p" | head -n 1)"\n'
        '  [ -n "$ROUND" ] || ROUND="${HERMES_TEST_ROUND:-1}"\n'
        '  mkdir -p "$RUN_DIR/reviews" "$RUN_DIR/logs"\n'
        '  cat > "$RUN_DIR/reviews/review-report-${ROUND}.json" <<EOF\n'
        '{"schema_version":1,"run_id":"run-test","round":'"${ROUND}"',"role":"reviewer",'
        '"verdict":"LGTM","findings":[],"pr_notes":{"summary":["Hermes reviewer completed"],"known_followups":[]}}\n'
        "EOF\n"
        '  printf "%s\\n" "$PROMPT" > "$RUN_DIR/logs/reviewer-hermes-invoked-round-${ROUND}.txt"\n'
        '  exit 0\n'
        'fi\n'
        'ROUND="$(printf "%s" "$PROMPT" | sed -n "s/^- round: \\([0-9][0-9]*\\)$/\\1/p" | head -n 1)"\n'
        '[ -n "$ROUND" ] || ROUND="${HERMES_TEST_ROUND:-1}"\n'
        'mkdir -p "$RUN_DIR/attempts" "$RUN_DIR/logs"\n'
        'printf "agent edit\\n" > "$PROJECT/README.md"\n'
        'cat > "$RUN_DIR/attempts/worker-attempt-${ROUND}.json" <<EOF\n'
        "{\n"
        '  "schema_version": 1,\n'
        '  "run_id": "run-test",\n'
        '  "round": '"${ROUND}"',\n'
        '  "role": "worker",\n'
        '  "status": "completed",\n'
        '  "changed_files": ["README.md"],\n'
        '  "summary": ["Hermes worker completed"],\n'
        '  "commands_run": ["run-openhands-task.sh"],\n'
        '  "tests_run": [],\n'
        '  "known_risks": [],\n'
        '  "blocked_reason": null,\n'
        '  "artifact_paths": {}\n'
        "}\n"
        "EOF\n"
        'printf "%s\\n" "$PROMPT" > "$RUN_DIR/logs/worker-hermes-invoked-round-${ROUND}.txt"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    hermes.chmod(hermes.stat().st_mode | stat.S_IXUSR)


def test_run_hoca_task_routes_implementation_through_worker_hermes_when_profiles_enabled(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(fake_tools_root(tmp_path))
    hermes_home = tmp_path / "hermes-home"
    setup_fake_hermes_worker(fake_bin, hermes_home)
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HERMES_HOME"] = str(hermes_home)
    env["HOCA_PYTHON"] = sys.executable
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"
    env["HERMES_TEST_PROJECT"] = str(tmp_path)

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    run_dir = latest_run_dir(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Running worker profile (implementation)" in result.stdout
    assert (run_dir / "attempts" / "worker-attempt-1.json").is_file()
    assert (run_dir / "logs" / "worker-hermes-invoked-round-1.txt").is_file()
    assert (run_dir / "worker-hermes-prompt-round-1.txt").is_file()


def test_run_hoca_task_routes_repair_through_worker_hermes_when_profiles_enabled(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "pyproject.toml"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add pyproject"], cwd=tmp_path, check=True, stdout=subprocess.PIPE
    )
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="printf 'broken\\n' > README.md\n",
        pytest_body="grep -q '^fixed$' README.md\n",
    )
    hermes_home = tmp_path / "hermes-home"
    hermes = fake_bin / "hermes"
    (hermes_home / "profiles" / "hoca-worker").mkdir(parents=True)
    (hermes_home / "profiles" / "hoca-reviewer").mkdir(parents=True)
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'PROFILE=""\n'
        'if [[ "${1:-}" == "-p" ]]; then PROFILE="${2:-}"; shift 2; fi\n'
        '[[ "${1:-}" == "chat" ]] || { echo "missing chat subcommand" >&2; exit 2; }\n'
        "shift\n"
        'while [[ $# -gt 0 ]]; do case "$1" in --query|-q) PROMPT="${2:-}"; shift 2;; --model) [[ "${2:-}" == */* ]] || { echo "missing model override" >&2; exit 2; }; shift 2;; *) shift;; esac; done\n'
        'PROJECT="${HERMES_TEST_PROJECT:?}"\n'
        'RUN_DIR="$(find "$PROJECT/.hoca-runtime/runs" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)"\n'
        '[ -n "$RUN_DIR" ] || { echo "run dir missing" >&2; exit 1; }\n'
        'if [[ "$PROFILE" == "hoca-reviewer" ]]; then\n'
        '  ROUND="$(printf "%s" "$PROMPT" | sed -n "s/^- round: \\([0-9][0-9]*\\)$/\\1/p" | head -n 1)"\n'
        '  [ -n "$ROUND" ] || ROUND="${HERMES_TEST_ROUND:-1}"\n'
        '  mkdir -p "$RUN_DIR/reviews" "$RUN_DIR/logs"\n'
        '  cat > "$RUN_DIR/reviews/review-report-${ROUND}.json" <<EOF\n'
        '{"schema_version":1,"run_id":"run-test","round":'"${ROUND}"',"role":"reviewer",'
        '"verdict":"LGTM","findings":[],"pr_notes":{"summary":["Hermes reviewer completed"],"known_followups":[]}}\n'
        "EOF\n"
        '  printf "%s\\n" "$PROMPT" > "$RUN_DIR/logs/reviewer-hermes-invoked-round-${ROUND}.txt"\n'
        '  exit 0\n'
        'fi\n'
        'ROUND="$(printf "%s" "$PROMPT" | sed -n "s/^- round: \\([0-9][0-9]*\\)$/\\1/p" | head -n 1)"\n'
        '[ -n "$ROUND" ] || ROUND="${HERMES_TEST_ROUND:-1}"\n'
        'mkdir -p "$RUN_DIR/attempts" "$RUN_DIR/logs"\n'
        'if [[ "$ROUND" == "2" ]]; then printf "fixed\\n" > "$PROJECT/README.md"; '
        'else printf "broken\\n" > "$PROJECT/README.md"; fi\n'
        'cat > "$RUN_DIR/attempts/worker-attempt-${ROUND}.json" <<EOF\n'
        '{"schema_version":1,"run_id":"run-test","round":'"${ROUND}"',"role":"worker",'
        '"status":"completed","changed_files":["README.md"],"summary":["Hermes worker completed"],'
        '"commands_run":["run-openhands-task.sh"],"tests_run":[],"known_risks":[],'
        '"blocked_reason":null,"artifact_paths":{}}\n'
        "EOF\n"
        'printf "%s\\n" "$PROMPT" > "$RUN_DIR/logs/worker-hermes-invoked-round-${ROUND}.txt"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    hermes.chmod(hermes.stat().st_mode | stat.S_IXUSR)

    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HERMES_HOME"] = str(hermes_home)
    env["HOCA_PYTHON"] = sys.executable
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["HOCA_MAX_TOTAL_ROUNDS"] = "2"
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"
    env["HERMES_TEST_PROJECT"] = str(tmp_path)

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    run_dir = latest_run_dir(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Running worker profile (repair round 2 of 2)" in result.stdout
    assert (run_dir / "repair-attempt-1.md").is_file()
    assert (run_dir / "attempts" / "worker-attempt-2.json").is_file()
    assert (run_dir / "logs" / "worker-hermes-invoked-round-2.txt").is_file()
    repair_prompt = (run_dir / "logs" / "worker-hermes-invoked-round-2.txt").read_text(
        encoding="utf-8"
    )
    assert "Repair reason: tests_failed" in repair_prompt


def test_run_hoca_task_generates_task_spec_artifacts(tmp_path: Path) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(fake_tools_root(tmp_path))
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    run_dir = latest_run_dir(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Generating task spec..." in result.stdout
    assert (run_dir / "raw-task.txt").read_text(encoding="utf-8") == "Update README\n"
    assert (run_dir / "task-spec.json").is_file()
    assert (run_dir / "task-spec-context.json").is_file()
    assert (run_dir / "sandbox-policy.json").is_file()


def test_run_hoca_task_reports_workspace_validation_before_dirty_stop(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "README.md").write_text("human edit\n", encoding="utf-8")

    result = run_hoca_task(tmp_path, "Update README")

    assert result.returncode != 0
    assert "Validating target repository..." in result.stdout
    assert f"Repository root: {tmp_path}" in result.stdout
    assert "Current branch:" in result.stdout
    assert "Working tree status before run:" in result.stdout
    assert "README.md" in result.stdout
    assert "Stopping to avoid mixing unrelated human changes" in result.stdout


def test_run_hoca_task_logs_current_head_base_when_no_dev_branch_configured(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    current_branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    fake_bin = make_fake_preflight_bin(fake_tools_root(tmp_path))
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode == 0, result.stderr
    assert "Development branch source: current branch" in result.stdout
    assert f"Development branch: {current_branch}" in result.stdout
    assert "Development branch sync: skipped (no origin remote configured)" in result.stdout
    assert f"Task branch base: {current_branch}" in result.stdout
    assert f"Creating branch: feat/update-readme from {current_branch}" in result.stdout


def test_run_hoca_task_uses_cli_dev_branch_before_task_branch(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-b", "feat/previous-task"], cwd=tmp_path, check=True)
    (tmp_path / "previous.txt").write_text("previous branch only\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "previous.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "previous task"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
    )
    fake_bin = make_fake_preflight_bin(fake_tools_root(tmp_path))
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(tmp_path, "Update README", env, "--dev-branch", "main")

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    assert result.returncode == 0, result.stderr
    assert "Development branch source: CLI override" in result.stdout
    assert "Switching to development branch: main" in result.stdout
    assert "Development branch sync: skipped (no origin remote configured)" in result.stdout
    assert "Task branch base: main" in result.stdout
    assert "Creating branch: feat/update-readme from main" in result.stdout
    assert branch == "feat/update-readme"
    assert not (tmp_path / "previous.txt").exists()
    assert '"starting_branch": "feat/previous-task"' in latest_status(tmp_path)
    assert '"task_base_branch": "main"' in latest_status(tmp_path)


def test_run_hoca_task_uses_unique_branch_when_task_branch_exists(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "branch", "feat/update-readme"],
        cwd=tmp_path,
        check=True,
    )
    fake_bin = make_fake_preflight_bin(fake_tools_root(tmp_path))
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(tmp_path, "Update README", env, "--dev-branch", "main")

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    assert result.returncode == 0, result.stderr
    assert "Task branch already exists: feat/update-readme; using feat/update-readme-" in (
        result.stderr
    )
    assert branch.startswith("feat/update-readme-")
    assert branch != "feat/update-readme"
    assert f"Creating branch: {branch} from main" in result.stdout
    assert f'"task_branch": "{branch}"' in (
        latest_run_dir(tmp_path) / "task-spec.json"
    ).read_text(encoding="utf-8")


def test_run_hoca_task_uses_project_config_dev_branch(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    (tmp_path / ".hoca").mkdir()
    (tmp_path / ".hoca" / "config.toml").write_text('dev_branch = "main"\n', encoding="utf-8")
    subprocess.run(["git", "add", "--", ".hoca/config.toml"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add hoca project config"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
    )
    subprocess.run(["git", "checkout", "-b", "feat/previous-task"], cwd=tmp_path, check=True)
    fake_bin = make_fake_preflight_bin(fake_tools_root(tmp_path))
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode == 0, result.stderr
    assert "Development branch source: .hoca/config.toml" in result.stdout
    assert "Switching to development branch: main" in result.stdout
    assert "Task branch base: main" in result.stdout
    assert "Creating branch: feat/update-readme from main" in result.stdout
    assert '"task_base_branch": "main"' in latest_status(tmp_path)


def test_run_hoca_task_bases_task_branch_on_latest_origin_dev_branch(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True)
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "remote", "set-head", "origin", "main"], cwd=repo, check=True)

    (repo / "local-only.txt").write_text("local main only\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "local-only.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "local main only"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )

    remote_work = tmp_path / "remote-work"
    subprocess.run(["git", "clone", str(remote), str(remote_work)], check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", "main"], cwd=remote_work, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "config", "user.email", "hoca@example.test"], cwd=remote_work, check=True
    )
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=remote_work, check=True)
    (remote_work / "origin-only.txt").write_text("origin main only\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "origin-only.txt"], cwd=remote_work, check=True)
    subprocess.run(
        ["git", "commit", "-m", "origin main only"],
        cwd=remote_work,
        check=True,
        stdout=subprocess.PIPE,
    )
    subprocess.run(["git", "push", "origin", "main"], cwd=remote_work, check=True, stdout=subprocess.PIPE)

    subprocess.run(["git", "checkout", "-b", "feat/previous-task"], cwd=repo, check=True)
    fake_bin = make_fake_preflight_bin(fake_tools_root(repo))
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(repo, "Update README", env)

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    assert result.returncode == 0, result.stderr
    assert "Development branch source: origin/HEAD" in result.stdout
    assert "Development branch sync: enabled" in result.stdout
    assert "Fetching latest development branch from origin: main" in result.stdout
    assert "Fetched development branch: origin/main" in result.stdout
    assert "Task branch base: origin/main" in result.stdout
    assert "Creating branch: feat/update-readme from origin/main" in result.stdout
    assert '"task_base_branch": "origin/main"' in latest_status(repo)
    assert branch == "feat/update-readme"
    assert (repo / "origin-only.txt").exists()
    assert not (repo / "local-only.txt").exists()


def test_run_hoca_task_stops_when_doctor_preflight_fails(tmp_path: Path) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(fake_tools_root(tmp_path))
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_DOCTOR_SCRIPT"] = "false"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode != 0
    assert "HOCA doctor failed" in result.stderr
    assert '"reason": "doctor_failed"' in latest_status(tmp_path)
    assert "type=failed" in latest_notification_result(tmp_path)


def test_run_hoca_task_marks_openhands_failure_and_saves_logs(tmp_path: Path) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="echo 'boom' >&2\nexit 42\n",
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode != 0
    assert "OpenHands failed with exit code" in result.stderr
    assert "Worker failure: boom" in result.stderr
    assert '"reason": "openhands_failed"' in latest_status(tmp_path)
    assert "Worker failure: boom" in (
        latest_run_dir(tmp_path) / "failure-detail.txt"
    ).read_text(encoding="utf-8")


def test_run_hoca_task_cleans_target_runtime_and_archives_evidence_by_default(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="echo 'boom' >&2\nexit 42\n",
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    archived = latest_run_dir(tmp_path)
    assert result.returncode != 0
    assert not (tmp_path / ".hoca-runtime").exists()
    assert archived.is_dir()
    assert archived.is_relative_to(archive_root(tmp_path))
    assert (archived / "task-report.md").is_file()
    assert '"reason": "openhands_failed"' in (archived / "status.json").read_text(
        encoding="utf-8"
    )


def test_run_hoca_task_marks_openhands_conversation_error_as_failure(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body='echo \'{"kind": "ConversationErrorEvent", "code": "LLMServiceUnavailableError"}\'\n',
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode != 0
    assert "OpenHands failed with exit code" in result.stderr
    assert '"reason": "openhands_failed"' in latest_status(tmp_path)


def test_run_hoca_task_stops_immediately_on_secret_changed_by_openhands(tmp_path: Path) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="echo 'TOKEN=value' > .env\necho 'created secret-like file'\n",
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode != 0
    assert "Secret-like changed file detected" in result.stderr
    assert '"reason": "secret_detected"' in latest_status(tmp_path)


def test_run_hoca_task_stops_before_review_when_tests_fail(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "pyproject.toml"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add pyproject"], cwd=tmp_path, check=True, stdout=subprocess.PIPE
    )
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="printf 'agent edit\\n' > README.md\n",
        pytest_body="echo 'tests failed'\nexit 3\n",
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_MAX_TOTAL_ROUNDS"] = "2"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode != 0
    assert "Tests still failed after round 2 of 2" in result.stderr
    assert '"reason": "tests_failed"' in latest_status(tmp_path)


def test_run_hoca_task_repairs_current_task_test_failures(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "pyproject.toml"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add pyproject"], cwd=tmp_path, check=True, stdout=subprocess.PIPE
    )
    count_file = tmp_path / "openhands-count"
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body=(
            'count="$(cat "$OPENHANDS_COUNT_FILE" 2>/dev/null || echo 0)"\n'
            'count="$((count + 1))"\n'
            'printf "%s\\n" "$count" > "$OPENHANDS_COUNT_FILE"\n'
            'if [[ "$count" == "1" ]]; then printf "broken\\n" > README.md; '
            'else printf "fixed\\n" > README.md; fi\n'
        ),
        pytest_body="grep -q '^fixed$' README.md\n",
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["OPENHANDS_COUNT_FILE"] = str(count_file)
    env["HOCA_MAX_TOTAL_ROUNDS"] = "2"
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode == 0, result.stderr
    assert "Running worker profile (repair round 2 of 2)" in result.stdout
    assert count_file.read_text(encoding="utf-8") == "2\n"
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "fixed\n"
    assert '"status": "needs_human_staging"' in latest_status(tmp_path)


def test_run_hoca_task_applies_worktree_changes_after_review(tmp_path: Path) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body=(
            "printf 'agent edit\\n' > README.md\n"
            "mkdir -p src/__tests__\n"
            "printf 'new test\\n' > src/__tests__/env.test.ts\n"
        ),
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_USE_WORKTREE_SANDBOX"] = "true"
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    assert result.returncode == 0, result.stderr
    assert "Applying worktree changes to main checkout for staging" in result.stdout
    assert "fatal:" not in result.stderr
    assert branch == "feat/update-readme"
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "agent edit\n"
    assert (tmp_path / "src/__tests__/env.test.ts").read_text(encoding="utf-8") == "new test\n"
    assert '"status": "needs_human_staging"' in latest_status(tmp_path)


def test_run_hoca_task_stops_before_tests_and_review_when_openhands_makes_no_changes(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(fake_tools_root(tmp_path))
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    run_dir = latest_run_dir(tmp_path)
    assert result.returncode == 0
    assert "No changes produced." in result.stdout
    assert '"status": "no_changes"' in latest_status(tmp_path)
    assert not (run_dir / "tests-summary.md").exists()
    assert not (run_dir / "openhands-review.txt").exists()


def test_run_hoca_task_distinguishes_review_failure_from_rejection(tmp_path: Path) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="printf 'agent edit\\n' > README.md\n",
        review_body="echo 'Needs changes.'\n",
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_MAX_TOTAL_ROUNDS"] = "1"
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)
    run_dir = latest_run_dir(tmp_path)
    decision_path = run_dir / "decisions" / "manager-decision-1.json"

    assert decision_path.is_file()
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision["decision"] == "draft_pr_with_blockers"
    assert (run_dir / "draft-pr-with-blockers.flag").is_file()
    assert result.stdout.count("round 1 of 1") >= 2


def test_run_hoca_task_repairs_review_rejections(tmp_path: Path) -> None:
    init_repo(tmp_path)
    count_file = tmp_path / "openhands-count"
    review_count_file = tmp_path / "openhands-review-count"
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body=(
            'count="$(cat "$OPENHANDS_COUNT_FILE" 2>/dev/null || echo 0)"\n'
            'count="$((count + 1))"\n'
            'printf "%s\\n" "$count" > "$OPENHANDS_COUNT_FILE"\n'
            'if [[ "$count" == "1" ]]; then printf "needs review\\n" > README.md; '
            'else printf "ready\\nLGTM\\n" > README.md; fi\n'
        ),
        review_body=(
            'count="$(cat "$OPENHANDS_REVIEW_COUNT_FILE" 2>/dev/null || echo 0)"\n'
            'count="$((count + 1))"\n'
            'printf "%s\\n" "$count" > "$OPENHANDS_REVIEW_COUNT_FILE"\n'
            'if [[ "$count" == "1" ]]; then echo "Needs changes."; '
            'else echo "Review complete."; echo "LGTM"; fi\n'
        ),
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["OPENHANDS_COUNT_FILE"] = str(count_file)
    env["OPENHANDS_REVIEW_COUNT_FILE"] = str(review_count_file)
    env["HOCA_MAX_TOTAL_ROUNDS"] = "3"
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode == 0, result.stderr
    assert "Running worker profile (repair round 2 of 3)" in result.stdout
    assert count_file.read_text(encoding="utf-8") == "2\n"
    assert review_count_file.read_text(encoding="utf-8") == "2\n"
    assert '"status": "needs_human_staging"' in latest_status(tmp_path)


def test_run_hoca_task_runs_safe_staging_with_intended_file_list(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    prepare_pr_ready_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "issue-42"
    run_dir.mkdir(parents=True)
    (run_dir / "intended-files.txt").write_text("README.md\n", encoding="utf-8")
    (run_dir / "intended-files-source.txt").write_text("manager\n", encoding="utf-8")
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="printf 'agent edit\\n' > README.md\n",
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_KEEP_RUNTIME"] = "true"

    result = run_hoca_task_with_env(
        tmp_path,
        "Fix GitHub issue #42: Update README with setup notes",
        env,
        "--issue-id",
        "42",
    )

    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert result.returncode == 0
    assert "Safe staging artifacts detected" in result.stdout
    assert "HOCA run completed through pull request creation." in result.stdout
    assert '"status": "pr_created"' in latest_status(tmp_path, "issue-42")
    assert '"reason": "pull_request_created"' in latest_status(tmp_path, "issue-42")
    assert staged.stdout == ""
    assert (run_dir / "staged-files.txt").read_text(encoding="utf-8") == "README.md\n"
    assert (run_dir / "commit-hash.txt").is_file()
    assert (run_dir / "pr-url.txt").read_text(encoding="utf-8").strip() == (
        "https://github.com/example/repo/pull/1"
    )


def test_run_hoca_task_auto_stages_reviewed_changes_and_creates_pr(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    prepare_pr_ready_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="printf 'agent edit\\n' > README.md\n",
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_KEEP_RUNTIME"] = "true"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode == 0, result.stderr
    assert "Generating manager intended-file list from reviewed changed files" in result.stdout
    assert "HOCA run completed through pull request creation." in result.stdout
    assert '"status": "pr_created"' in latest_status(tmp_path)
    assert '"reason": "pull_request_created"' in latest_status(tmp_path)


def test_run_hoca_task_restores_dev_branch_after_pr_creation(tmp_path: Path) -> None:
    init_repo(tmp_path)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    prepare_pr_ready_repo(tmp_path)
    subprocess.run(["git", "checkout", "-b", "feat/previous-task"], cwd=tmp_path, check=True)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="printf 'agent edit\\n' > README.md\n",
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_KEEP_RUNTIME"] = "true"

    result = run_hoca_task_with_env(tmp_path, "Update README", env, "--dev-branch", "main")

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    assert result.returncode == 0, result.stderr
    assert "Restoring development branch: main" in result.stdout
    assert "HOCA run completed through pull request creation." in result.stdout
    assert branch == "main"


def test_run_hoca_task_stops_before_staging_without_intended_file_list(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="printf 'agent edit\\n' > README.md\n",
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)
    run_dir = latest_run_dir(tmp_path)

    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert result.returncode == 0
    assert "Stopping before staging" in result.stdout
    assert '"status": "needs_human_staging"' in latest_status(tmp_path)
    assert '"reason": "selective_staging_required"' in latest_status(tmp_path)
    assert staged.stdout == ""
    assert "type=needs-review" in latest_notification_result(tmp_path)
    assert (run_dir / "attempts" / "worker-attempt-1.json").is_file()
    assert (run_dir / "reviews" / "review-report-1.json").is_file()
    assert (run_dir / "decisions" / "manager-decision-1.json").is_file()
    assert (run_dir / "validation" / "validation-report-1.json").is_file()
    assert (run_dir / "final-state.json").is_file()
    assert '"current_round": 1' in latest_status(tmp_path)


def test_run_hoca_task_ignores_own_runtime_artifacts_when_not_gitignored(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", ".gitignore"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "do not ignore hoca runtime"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
    )
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="printf 'agent edit\\n' > README.md\n",
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    changed_files = (latest_run_dir(tmp_path) / "changed-files.txt").read_text(encoding="utf-8")
    assert result.returncode == 0
    assert "Working tree has existing changes:" not in result.stdout
    assert "README.md" in changed_files
    assert ".hoca-runtime" not in changed_files


def test_duplicate_issue_lock_exits_successfully_with_notice(tmp_path: Path) -> None:
    init_repo(tmp_path)
    lock_dir = tmp_path / ".hoca-runtime" / "runs"
    lock_dir.mkdir(parents=True)
    lock = lock_dir / "issue-42.lock"
    lock.write_text('{"owner_token": "foreign"}\n', encoding="utf-8")

    result = subprocess.run(
        [str(SCRIPT), str(tmp_path), "Fix GitHub issue #42: resolve login bug", "--issue-id", "42"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=base_env(),
    )

    assert result.returncode == 0
    assert "Another HOCA run appears to be active" in result.stdout
    assert lock.exists()
    assert "foreign" in lock.read_text(encoding="utf-8")


def test_run_hoca_task_cleanup_removes_runtime_even_when_lock_was_replaced(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body=(
            "cat > .hoca-runtime/runs/issue-42.lock <<'EOF'\n"
            '{"owner_token": "foreign", "pid": 99999}\n'
            "EOF\n"
        ),
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(
        tmp_path,
        "Fix GitHub issue #42: resolve login bug",
        env,
        "--issue-id",
        "42",
    )

    assert result.returncode == 0
    assert not (tmp_path / ".hoca-runtime").exists()
    assert (run_dir(tmp_path, "issue-42") / "status.json").is_file()


def test_run_hoca_task_cleanup_removes_unpublished_worktree_branch_on_failure(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="echo 'OpenHands failed before publication.' >&2\nexit 1\n",
    )
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_USE_WORKTREE_SANDBOX"] = "true"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode != 0
    assert "Deleting unpublished disposable task branch: feat/update-readme" in result.stdout
    branch_check = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", "refs/heads/feat/update-readme"],
        cwd=tmp_path,
        check=False,
    )
    assert branch_check.returncode != 0
    assert not (tmp_path / ".hoca-runtime").exists()
    assert (latest_run_dir(tmp_path) / "status.json").is_file()


def test_run_hoca_task_blocks_dangerous_task_before_run_dir(tmp_path: Path) -> None:
    init_repo(tmp_path)

    result = run_hoca_task(tmp_path, "run git push --force to main")

    assert result.returncode == 1
    assert "Checking definition of ready..." in result.stdout
    assert "failed definition-of-ready checks" in result.stderr
    runs_dir = tmp_path / ".hoca-runtime" / "runs"
    assert not runs_dir.exists() or not list(runs_dir.glob("run-*"))


def test_run_hoca_task_escalates_broad_task_before_run_dir(tmp_path: Path) -> None:
    init_repo(tmp_path)

    result = run_hoca_task(tmp_path, "fix everything")

    assert result.returncode == 2
    assert "Checking definition of ready..." in result.stdout
    assert "needs clarification" in result.stderr.lower()


def test_run_hoca_task_writes_definition_of_ready_artifact(tmp_path: Path) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(fake_tools_root(tmp_path))
    env = base_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    run_dir = latest_run_dir(tmp_path)
    assert result.returncode == 0, result.stderr
    dor_path = run_dir / "definition-of-ready.json"
    assert dor_path.is_file()
    payload = json.loads(dor_path.read_text(encoding="utf-8"))
    assert payload["outcome"] == "ready"
