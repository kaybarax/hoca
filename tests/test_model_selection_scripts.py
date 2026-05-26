from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def make_fake_ollama(tmp_path: Path, models: list[str]) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ollama = fake_bin / "ollama"
    rows = "\n".join(f"{model} 1 GB 2026-05-13" for model in models)
    ollama.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" != "list" ]]; then exit 2; fi\n'
        "cat <<'EOF'\n"
        "NAME ID SIZE MODIFIED\n"
        f"{rows}\n"
        "EOF\n",
        encoding="utf-8",
    )
    ollama.chmod(ollama.stat().st_mode | stat.S_IXUSR)
    return fake_bin


def make_fake_curl(fake_bin: Path, *, succeeds: bool = True) -> None:
    curl = fake_bin / "curl"
    curl.write_text(
        f"#!/usr/bin/env bash\nset -euo pipefail\nexit {0 if succeeds else 7}\n",
        encoding="utf-8",
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)


def make_fake_openhands(fake_bin: Path, *, env_capture: Path | None = None) -> None:
    capture_line = ""
    if env_capture is not None:
        capture_line = f'env | sort > "{env_capture}"\n'
    openhands = fake_bin / "openhands"
    openhands.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "--help" ]]; then\n'
        '  echo "openhands --headless --task --override-with-envs --json"\n'
        "  exit 0\n"
        "fi\n"
        f"{capture_line}"
        "echo 'OpenHands fake run complete.'\n",
        encoding="utf-8",
    )
    openhands.chmod(openhands.stat().st_mode | stat.S_IXUSR)



def init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE)


def run_script(
    script_name: str,
    fake_bin: Path,
    extra_env: dict[str, str] | None = None,
    args: list[str] | None = None,
):
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    for key in ("LLM_MODEL", "OLLAMA_MODEL"):
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(REPO_ROOT / "scripts" / script_name), *(args or [])],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_select_model_prefers_configured_ollama_model(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-14b-pro", "custom-coder"])
    make_fake_curl(fake_bin)

    result = run_script("select-model.sh", fake_bin, {"OLLAMA_MODEL": "custom-coder"})

    assert result.returncode == 0
    assert result.stdout.strip() == "custom-coder"


def test_select_model_requires_explicit_requested_model(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-14b-pro", "qwen-7b-pro"])
    make_fake_curl(fake_bin)

    result = run_script(
        "select-model.sh",
        fake_bin,
        {"HOCA_REQUESTED_MODEL": "qwen-7b-pro", "OLLAMA_MODEL": "qwen-14b-pro"},
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "qwen-7b-pro"


def test_select_model_errors_when_requested_model_is_missing(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-14b-pro"])
    make_fake_curl(fake_bin)

    result = run_script("select-model.sh", fake_bin, {"HOCA_REQUESTED_MODEL": "qwen-7b-pro"})

    assert result.returncode == 1
    assert "Requested HOCA model not found" in result.stderr


def test_select_model_falls_back_to_supported_models(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-7b-pro"])
    make_fake_curl(fake_bin)

    result = run_script("select-model.sh", fake_bin, {"OLLAMA_MODEL": "missing-model"})

    assert result.returncode == 0
    assert result.stdout.strip() == "qwen-7b-pro"


def test_select_model_accepts_latest_tagged_aliases(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-14b-pro:latest"])
    make_fake_curl(fake_bin)

    result = run_script("select-model.sh", fake_bin)

    assert result.returncode == 0
    assert result.stdout.strip() == "qwen-14b-pro"


def test_select_model_errors_when_no_compatible_model_exists(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["unrelated-model"])
    make_fake_curl(fake_bin)

    result = run_script("select-model.sh", fake_bin)

    assert result.returncode == 1
    assert "No HOCA-compatible Ollama model found" in result.stderr


def test_select_model_errors_when_ollama_server_is_unreachable(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-7b-pro"])
    make_fake_curl(fake_bin, succeeds=False)

    result = run_script("select-model.sh", fake_bin)

    assert result.returncode == 1
    assert "Start it with: ollama serve" in result.stderr


def test_openhands_wrapper_uses_selected_model(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-14b-pro"])
    make_fake_curl(fake_bin)
    make_fake_openhands(fake_bin)
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    project.mkdir()
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")

    result = run_script(
        "run-openhands-task.sh",
        fake_bin,
        extra_env={"HOCA_USE_SANDBOX": "false"},
        args=[str(project), "Summarize project", str(run_dir)],
    )

    assert result.returncode == 0, result.stderr
    assert "MODEL=ollama/qwen-14b-pro" in result.stdout
    assert "ROLE=worker" in result.stdout
    assert "OpenHands fake run complete." in result.stdout
    assert "git add" in (run_dir / "agent-role-policy.txt").read_text(encoding="utf-8")


def test_openhands_wrapper_accepts_task_file_path(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-14b-pro"])
    make_fake_curl(fake_bin)
    openhands = fake_bin / "openhands"
    openhands.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "--help" ]]; then\n'
        '  echo "openhands --headless --task --override-with-envs --json"\n'
        "  exit 0\n"
        "fi\n"
        'prev=""\n'
        'for arg in "$@"; do\n'
        '  if [[ "$prev" == "--task" ]]; then echo "$arg"; fi\n'
        '  prev="$arg"\n'
        "done\n",
        encoding="utf-8",
    )
    openhands.chmod(openhands.stat().st_mode | stat.S_IXUSR)
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    task_file = tmp_path / "task.txt"
    project.mkdir()
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    task_file.write_text("Summarize project from file\n", encoding="utf-8")

    result = run_script(
        "run-openhands-task.sh",
        fake_bin,
        extra_env={"HOCA_USE_SANDBOX": "false"},
        args=[str(project), str(task_file), str(run_dir)],
    )

    assert result.returncode == 0, result.stderr
    assert "Summarize project from file" in result.stdout


def test_openhands_wrapper_uses_requested_model_env(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-14b-pro", "qwen-7b-pro"])
    make_fake_curl(fake_bin)
    make_fake_openhands(fake_bin)
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    project.mkdir()
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")

    result = run_script(
        "run-openhands-task.sh",
        fake_bin,
        extra_env={
            "HOCA_REQUESTED_MODEL": "qwen-7b-pro",
            "OLLAMA_MODEL": "qwen-7b-pro",
            "LLM_MODEL": "ollama/qwen-7b-pro",
            "HOCA_USE_SANDBOX": "false",
        },
        args=[str(project), "Summarize project", str(run_dir)],
    )

    assert result.returncode == 0, result.stderr
    assert "MODEL=ollama/qwen-7b-pro" in result.stdout


def test_openhands_wrapper_lock_ignores_agent_requested_model(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-14b-pro", "qwen-7b-pro"])
    make_fake_curl(fake_bin)
    make_fake_openhands(fake_bin)
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    project.mkdir()
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")

    result = run_script(
        "run-openhands-task.sh",
        fake_bin,
        extra_env={
            "HOCA_LOCK_ROLE_MODEL": "true",
            "HOCA_REQUESTED_MODEL": "qwen-7b-pro",
            "OLLAMA_MODEL": "qwen-7b-pro",
            "LLM_MODEL": "ollama/qwen-7b-pro",
            "LLM_BASE_URL": "http://bad.example.invalid",
            "LLM_API_KEY": "agent-supplied-key",
            "HOCA_USE_SANDBOX": "false",
        },
        args=[str(project), "Summarize project", str(run_dir)],
    )

    assert result.returncode == 0, result.stderr
    assert "MODEL=ollama/qwen-14b-pro" in result.stdout
    assert "qwen-7b-pro" not in result.stdout


def test_openhands_wrapper_strips_github_token_for_worker(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-14b-pro"])
    make_fake_curl(fake_bin)
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    project.mkdir()
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    env_capture = run_dir / "openhands-env.txt"
    make_fake_openhands(fake_bin, env_capture=env_capture)

    result = run_script(
        "run-openhands-task.sh",
        fake_bin,
        extra_env={
            "HOCA_USE_SANDBOX": "false",
            "GITHUB_TOKEN": "ghp_test_token_must_not_leak",
        },
        args=[str(project), "Summarize project", str(run_dir)],
    )

    assert result.returncode == 0, result.stderr
    captured = env_capture.read_text(encoding="utf-8")
    assert "GITHUB_TOKEN=" not in captured


def test_openhands_wrapper_strips_github_token_for_reviewer(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-14b-pro"])
    make_fake_curl(fake_bin)
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    project.mkdir()
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    env_capture = run_dir / "openhands-env.txt"
    make_fake_openhands(fake_bin, env_capture=env_capture)

    result = run_script(
        "run-openhands-task.sh",
        fake_bin,
        extra_env={
            "HOCA_AGENT_ROLE": "reviewer",
            "HOCA_USE_SANDBOX": "false",
            "GITHUB_TOKEN": "ghp_test_token_must_not_leak",
        },
        args=[str(project), "Review changes", str(run_dir)],
    )

    assert result.returncode == 0, result.stderr
    captured = env_capture.read_text(encoding="utf-8")
    assert "GITHUB_TOKEN=" not in captured


def test_review_with_openhands_calls_run_openhands_task(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-32b-pro"])
    make_fake_curl(fake_bin)
    openhands = fake_bin / "openhands"
    openhands.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'if [[ "${1:-}" == "--help" ]]; then\n'
        '  echo "openhands --headless --task --override-with-envs --json"\n'
        "  exit 0\n"
        "fi\n"
        "cat <<'JSON'\n"
        '{"schema_version":1,"run_id":"run","round":1,"role":"reviewer",'
        '"verdict":"LGTM","findings":[],"pr_notes":{"summary":["Looks good."],'
        '"known_followups":[]}}\n'
        "JSON\n",
        encoding="utf-8",
    )
    openhands.chmod(openhands.stat().st_mode | stat.S_IXUSR)
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    project.mkdir()
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")

    result = run_script(
        "review-with-openhands.sh",
        fake_bin,
        extra_env={"HOCA_USE_SANDBOX": "false"},
        args=[str(project), "Review project", str(run_dir)],
    )

    assert result.returncode == 0, result.stderr
    assert "Running OpenHands review" in result.stdout
    assert "ROLE=reviewer" in result.stdout
    assert (run_dir / "openhands-review.txt").exists()
    assert "role: reviewer" in (run_dir / "review" / "agent-role-policy.txt").read_text(
        encoding="utf-8"
    )
    assert (run_dir / "review" / "changed-files.txt").read_text(encoding="utf-8") == "README.md\n"
    assert (run_dir / "review" / "git-diff.patch").is_file()
    assert (run_dir / "reviews" / "review-report-1.json").is_file()
    prompt = (run_dir / "review" / "openhands-review-prompt.txt").read_text(encoding="utf-8")
    assert "HocaReviewReport" in prompt
    assert "Do not implement fixes" in prompt
    assert "Severity rubric:" in prompt
    assert "PR tech debt" in prompt
    assert "structured JSON" in prompt


def test_review_with_openhands_materializes_structured_json_from_output(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-32b-pro"])
    make_fake_curl(fake_bin)
    openhands = fake_bin / "openhands"
    openhands.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'if [[ "${1:-}" == "--help" ]]; then\n'
        '  echo "openhands --headless --task --override-with-envs --json"\n'
        "  exit 0\n"
        "fi\n"
        "cat <<'EOF'\n"
        "Review complete.\n"
        "```json\n"
        "{\n"
        '  "schema_version": 1,\n'
        '  "run_id": "run",\n'
        '  "round": 1,\n'
        '  "role": "reviewer",\n'
        '  "verdict": "LGTM",\n'
        '  "findings": [],\n'
        '  "pr_notes": {"summary": ["Looks good."], "known_followups": ["Rename helper later"]}\n'
        "}\n"
        "```\n"
        "LGTM\n"
        "EOF\n",
        encoding="utf-8",
    )
    openhands.chmod(openhands.stat().st_mode | stat.S_IXUSR)
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    project.mkdir()
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")

    result = run_script(
        "review-with-openhands.sh",
        fake_bin,
        extra_env={"HOCA_USE_SANDBOX": "false"},
        args=[str(project), "Review project", str(run_dir)],
    )

    assert result.returncode == 0, result.stderr
    assert "source: structured" in result.stdout
    report = json.loads((run_dir / "reviews" / "review-report-1.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "LGTM"
    assert report["pr_notes"]["known_followups"] == ["Rename helper later"]
    assert "LGTM" in (run_dir / "openhands-review.txt").read_text(encoding="utf-8")


def test_review_with_openhands_prefers_structured_report(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-32b-pro"])
    make_fake_curl(fake_bin)
    openhands = fake_bin / "openhands"
    openhands.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'if [[ "${1:-}" == "--help" ]]; then\n'
        '  echo "openhands --headless --task --override-with-envs --json"\n'
        "  exit 0\n"
        "fi\n"
        "echo 'LGTM'\n",
        encoding="utf-8",
    )
    openhands.chmod(openhands.stat().st_mode | stat.S_IXUSR)
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    structured_report = tmp_path / "review-report.json"
    project.mkdir()
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    structured_report.write_text(
        "{\n"
        '  "schema_version": 1,\n'
        '  "run_id": "run",\n'
        '  "round": 1,\n'
        '  "role": "reviewer",\n'
        '  "verdict": "fix_required",\n'
        '  "findings": [],\n'
        '  "pr_notes": {"summary": ["Needs work."], "known_followups": []}\n'
        "}\n",
        encoding="utf-8",
    )

    result = run_script(
        "review-with-openhands.sh",
        fake_bin,
        extra_env={
            "HOCA_USE_SANDBOX": "false",
            "HOCA_REVIEW_REPORT_PATH": str(structured_report),
        },
        args=[str(project), "Review project", str(run_dir)],
    )

    assert result.returncode == 2
    assert "source: structured" in result.stdout
    assert "OpenHands review did not return LGTM" in result.stdout
