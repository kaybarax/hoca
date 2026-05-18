from __future__ import annotations

import subprocess
import os
import stat
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run-hoca-task.sh"


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / ".gitignore").write_text(".hoca-runtime/\n", encoding="utf-8")
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", ".gitignore", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE)


def run_hoca_task(repo: Path, task: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), str(repo), task],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
        fake_bin / "docker", '#!/usr/bin/env bash\n[[ "${1:-}" == info ]] && exit 0\nexit 0\n'
    )
    write_executable(fake_bin / "curl", "#!/usr/bin/env bash\nexit 0\n")
    write_executable(
        fake_bin / "ollama",
        "#!/usr/bin/env bash\ncat <<'EOF'\nNAME ID SIZE MODIFIED\nqwen-7b-pro abc 1GB now\nEOF\n",
    )

    openhands = openhands_body or "echo 'OpenHands fake run complete.'\n"
    review_default = review_body or "echo 'Review complete.'\necho 'LGTM'\n"
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
    return subprocess.run(
        [str(SCRIPT), str(repo), task, *extra_args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def latest_status(repo: Path, run_id: str | None = None) -> str:
    runs = repo / ".hoca-runtime" / "runs"
    if run_id is None:
        run_dirs = sorted(p for p in runs.iterdir() if p.is_dir())
        status_path = run_dirs[-1] / "status.json"
    else:
        status_path = runs / run_id / "status.json"
    return status_path.read_text(encoding="utf-8")


def latest_notification_result(repo: Path) -> str:
    result_paths = sorted((repo / ".hoca-runtime" / "runs").glob("*/notification-result.txt"))
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
    fake_bin = make_fake_preflight_bin(fake_tools_root(tmp_path))
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode == 0, result.stderr
    assert "Development branch: not configured (HOCA_DEV_BRANCH is unset)" in result.stdout
    assert "Development branch sync: skipped" in result.stdout
    assert "Task branch base: current HEAD" in result.stdout
    assert "Creating branch: feat/update-readme from HEAD" in result.stdout


def test_run_hoca_task_switches_to_configured_dev_branch_before_task_branch(
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
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_DEV_BRANCH"] = "main"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    assert result.returncode == 0, result.stderr
    assert "Switching to development branch: main" in result.stdout
    assert "Development branch sync: skipped (no origin remote configured)" in result.stdout
    assert "Task branch base: main" in result.stdout
    assert "Creating branch: feat/update-readme from main" in result.stdout
    assert branch == "feat/update-readme"
    assert not (tmp_path / "previous.txt").exists()
    assert '"starting_branch": "feat/previous-task"' in latest_status(tmp_path)
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
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_DEV_BRANCH"] = "main"

    result = run_hoca_task_with_env(repo, "Update README", env)

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    assert result.returncode == 0, result.stderr
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
    (fake_bin / "docker").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

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
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode != 0
    assert "OpenHands failed with exit code" in result.stderr
    assert '"reason": "openhands_failed"' in latest_status(tmp_path)


def test_run_hoca_task_marks_openhands_conversation_error_as_failure(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body='echo \'{"kind": "ConversationErrorEvent", "code": "LLMServiceUnavailableError"}\'\n',
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode != 0
    assert "OpenHands reported a conversation error event" in result.stdout
    assert "OpenHands failed with exit code" in result.stderr
    assert '"reason": "openhands_failed"' in latest_status(tmp_path)


def test_run_hoca_task_stops_immediately_on_secret_changed_by_openhands(tmp_path: Path) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="echo 'TOKEN=value' > .env\necho 'created secret-like file'\n",
    )
    env = os.environ.copy()
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
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_MAX_REPAIR_ATTEMPTS"] = "1"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode != 0
    assert "Tests still failed after 1 repair attempt" in result.stderr
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
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["OPENHANDS_COUNT_FILE"] = str(count_file)
    env["HOCA_MAX_REPAIR_ATTEMPTS"] = "1"
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode == 0, result.stderr
    assert "Running OpenHands (test repair attempt 1)" in result.stdout
    assert count_file.read_text(encoding="utf-8") == "2\n"
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "fixed\n"
    assert '"status": "needs_human_staging"' in latest_status(tmp_path)


def test_run_hoca_task_stops_before_tests_and_review_when_openhands_makes_no_changes(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(fake_tools_root(tmp_path))
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    run_dir = sorted((tmp_path / ".hoca-runtime" / "runs").glob("run-*"))[-1]
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
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_MAX_REPAIR_ATTEMPTS"] = "1"

    rejected = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert rejected.returncode != 0
    assert '"reason": "review_not_lgtm"' in latest_status(tmp_path) or \
           '"reason": "review_failed"' in latest_status(tmp_path)


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
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["OPENHANDS_COUNT_FILE"] = str(count_file)
    env["OPENHANDS_REVIEW_COUNT_FILE"] = str(review_count_file)
    env["HOCA_MAX_REPAIR_ATTEMPTS"] = "2"
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode == 0, result.stderr
    assert "Running OpenHands (review repair attempt 1)" in result.stdout
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
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_KEEP_RUNTIME"] = "true"

    result = run_hoca_task_with_env(tmp_path, "Update README", env, "--issue-id", "42")

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
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_KEEP_RUNTIME"] = "true"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    assert result.returncode == 0, result.stderr
    assert "Generating manager intended-file list from reviewed changed files" in result.stdout
    assert "HOCA run completed through pull request creation." in result.stdout
    assert '"status": "pr_created"' in latest_status(tmp_path)
    assert '"reason": "pull_request_created"' in latest_status(tmp_path)


def test_run_hoca_task_stops_before_staging_without_intended_file_list(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body="printf 'agent edit\\n' > README.md\n",
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

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
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["HOCA_AUTO_STAGE_REVIEWED_CHANGES"] = "false"

    result = run_hoca_task_with_env(tmp_path, "Update README", env)

    changed_files = sorted((tmp_path / ".hoca-runtime" / "runs").glob("*/changed-files.txt"))[
        -1
    ].read_text(encoding="utf-8")
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
        [str(SCRIPT), str(tmp_path), "Fix issue", "--issue-id", "42"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0
    assert "Another HOCA run appears to be active" in result.stdout
    assert lock.exists()
    assert "foreign" in lock.read_text(encoding="utf-8")


def test_run_hoca_task_cleanup_does_not_remove_replaced_lock(tmp_path: Path) -> None:
    init_repo(tmp_path)
    fake_bin = make_fake_preflight_bin(
        fake_tools_root(tmp_path),
        openhands_body=(
            "cat > .hoca-runtime/runs/issue-42.lock <<'EOF'\n"
            '{"owner_token": "foreign", "pid": 99999}\n'
            "EOF\n"
        ),
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = run_hoca_task_with_env(tmp_path, "Fix issue", env, "--issue-id", "42")

    lock = tmp_path / ".hoca-runtime" / "runs" / "issue-42.lock"
    assert result.returncode == 0
    assert lock.exists()
    assert "foreign" in lock.read_text(encoding="utf-8")
