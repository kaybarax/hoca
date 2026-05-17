from __future__ import annotations

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


def make_fake_openhands(fake_bin: Path) -> None:
    openhands = fake_bin / "openhands"
    openhands.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "--help" ]]; then\n'
        '  echo "openhands --headless --task --override-with-envs --json"\n'
        "  exit 0\n"
        "fi\n"
        "echo 'OpenHands fake run complete.'\n",
        encoding="utf-8",
    )
    openhands.chmod(openhands.stat().st_mode | stat.S_IXUSR)


def make_fake_aider(fake_bin: Path) -> None:
    aider = fake_bin / "aider"
    aider.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\necho 'Review complete.'\necho 'LGTM'\n",
        encoding="utf-8",
    )
    aider.chmod(aider.stat().st_mode | stat.S_IXUSR)


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
    for key in ("AIDER_MODEL", "LLM_MODEL", "OLLAMA_MODEL"):
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
        args=[str(project), "Summarize project", str(run_dir)],
    )

    assert result.returncode == 0, result.stderr
    assert "MODEL=ollama/qwen-14b-pro" in result.stdout
    assert "OpenHands fake run complete." in result.stdout


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
        },
        args=[str(project), "Summarize project", str(run_dir)],
    )

    assert result.returncode == 0, result.stderr
    assert "MODEL=ollama/qwen-7b-pro" in result.stdout


def test_aider_wrapper_uses_aider_model_prefix(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-32b-pro"])
    make_fake_curl(fake_bin)
    make_fake_aider(fake_bin)
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    project.mkdir()
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")

    result = run_script(
        "review-with-aider.sh",
        fake_bin,
        args=[str(project), "Review project", str(run_dir)],
    )

    assert result.returncode == 0, result.stderr
    assert "Running Aider review with model: ollama_chat/qwen-32b-pro" in result.stdout
    assert (run_dir / "aider-review.txt").read_text(encoding="utf-8") == (
        "Review complete.\nLGTM\n"
    )
    assert (run_dir / "aider-stderr.log").read_text(encoding="utf-8") == ""
    assert (run_dir / "aider-exit-code.txt").read_text(encoding="utf-8") == "0\n"


def test_aider_wrapper_uses_yes_always_when_supported(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-32b-pro"])
    make_fake_curl(fake_bin)
    args_file = tmp_path / "aider-args.txt"
    aider = fake_bin / "aider"
    aider.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "--help" ]]; then\n'
        '  echo "aider --yes-always --read-only"\n'
        "  exit 0\n"
        "fi\n"
        'printf \'%s\\n\' "$@" > "$AIDER_ARGS_FILE"\n'
        "echo 'Review complete.'\n"
        "echo 'LGTM'\n",
        encoding="utf-8",
    )
    aider.chmod(aider.stat().st_mode | stat.S_IXUSR)
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    project.mkdir()
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")

    result = run_script(
        "review-with-aider.sh",
        fake_bin,
        extra_env={"AIDER_ARGS_FILE": str(args_file)},
        args=[str(project), "Review project", str(run_dir)],
    )

    assert result.returncode == 0, result.stderr
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert "--yes-always" in args
    assert "--read-only" in args
    assert "README.md" in args
