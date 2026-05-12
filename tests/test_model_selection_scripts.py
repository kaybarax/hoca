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


def run_script(script_name: str, fake_bin: Path, extra_env: dict[str, str] | None = None):
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(REPO_ROOT / "scripts" / script_name)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_select_model_prefers_configured_ollama_model(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-14b-pro", "custom-coder"])

    result = run_script("select-model.sh", fake_bin, {"OLLAMA_MODEL": "custom-coder"})

    assert result.returncode == 0
    assert result.stdout.strip() == "custom-coder"


def test_select_model_falls_back_to_supported_models(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-7b-pro"])

    result = run_script("select-model.sh", fake_bin, {"OLLAMA_MODEL": "missing-model"})

    assert result.returncode == 0
    assert result.stdout.strip() == "qwen-7b-pro"


def test_select_model_errors_when_no_compatible_model_exists(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["unrelated-model"])

    result = run_script("select-model.sh", fake_bin)

    assert result.returncode == 1
    assert "No HOCA-compatible Ollama model found" in result.stderr


def test_openhands_wrapper_uses_selected_model(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-14b-pro"])

    result = run_script("run-openhands-task.sh", fake_bin)

    assert result.returncode == 0
    assert "Selected model: ollama/qwen-14b-pro" in result.stdout


def test_aider_wrapper_uses_aider_model_prefix(tmp_path: Path) -> None:
    fake_bin = make_fake_ollama(tmp_path, ["qwen-32b-pro"])

    result = run_script("review-with-aider.sh", fake_bin)

    assert result.returncode == 0
    assert "Selected model: ollama_chat/qwen-32b-pro" in result.stdout
