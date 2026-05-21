from __future__ import annotations

import subprocess
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
MODULE = "hoca.security_cli"


def test_security_cli_is_secret_like_matches_python_module() -> None:
    secret = subprocess.run(
        ["python3", "-m", MODULE, "is-secret-like", ".env"],
        cwd=SCRIPT_DIR.parent,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    allowed = subprocess.run(
        ["python3", "-m", MODULE, "is-secret-like", ".env.example"],
        cwd=SCRIPT_DIR.parent,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert secret.returncode == 0
    assert allowed.returncode == 1


def test_security_cli_validate_staging_reports_secret_paths(tmp_path: Path) -> None:
    (tmp_path / "files.txt").write_text(".env\nREADME.md\n", encoding="utf-8")
    result = subprocess.run(
        [
            "python3",
            "-m",
            MODULE,
            "validate-staging",
            str(tmp_path),
            str(tmp_path / "files.txt"),
        ],
        cwd=SCRIPT_DIR.parent,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 1
    assert "secret-like" in result.stderr
