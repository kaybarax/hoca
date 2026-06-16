from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_ci_workflow_runs_standard_quality_and_test_checks() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "name: CI" in workflow
    assert "pull_request:" in workflow
    assert 'python-version: "3.12"' in workflow
    assert 'python -m pip install -e ".[dev]"' in workflow
    assert "ruff check ." in workflow
    assert "ruff format --check ." in workflow
    assert "pytest -q" in workflow
