from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_install_script_uses_repo_virtualenv_for_hoca_dependencies() -> None:
    install_script = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert "python@3.12" in install_script
    assert "venv_python_bin()" in install_script
    assert '"$py_bin" -m venv "$REPO_ROOT/.venv"' in install_script
    assert '"$venv_py" -m pip install --upgrade -e "$REPO_ROOT"' in install_script
    assert '"$py_bin" -m pip install --upgrade -e "$REPO_ROOT"' not in install_script
    assert "--break-system-packages" not in install_script


def test_install_script_accepts_latest_tagged_ollama_aliases() -> None:
    install_script = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert '$1 == model || $1 == model ":latest"' in install_script
