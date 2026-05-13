from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARDS = REPO_ROOT / "scripts" / "auto-merge-guards.sh"


def _run_guards(subcommand: str, run_dir: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        ["bash", str(GUARDS), subcommand, str(run_dir)],
        capture_output=True,
        text=True,
        env=merged,
        check=False,
    )


@pytest.fixture
def minimal_auto_merge_run(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    (run / "status.json").write_text(json.dumps({"auto_merge": "true"}), encoding="utf-8")
    (run / "tests-exit-code.txt").write_text("0\n", encoding="utf-8")
    (run / "aider-review.txt").write_text("LGTM all good\n", encoding="utf-8")
    (run / "risk-level.txt").write_text("low\n", encoding="utf-8")
    (run / "staged-files.txt").write_text("lib/widget.py\n", encoding="utf-8")
    return run


def test_wants_auto_merge_false(tmp_path: Path):
    run = tmp_path / "wants"
    run.mkdir()
    (run / "status.json").write_text(json.dumps({"auto_merge": "false"}), encoding="utf-8")
    proc = _run_guards("wants-auto-merge", run)
    assert proc.returncode == 1


def test_precheck_exit_2_when_not_requested(tmp_path: Path):
    run = tmp_path / "nr"
    run.mkdir()
    (run / "status.json").write_text(json.dumps({"auto_merge": "false"}), encoding="utf-8")
    proc = _run_guards("precheck", run)
    assert proc.returncode == 2
    assert not (run / "auto-merge-precheck-skip.txt").exists()


def test_precheck_fails_without_tests_exit_code(minimal_auto_merge_run: Path):
    (minimal_auto_merge_run / "tests-exit-code.txt").unlink()
    proc = _run_guards(
        "precheck",
        minimal_auto_merge_run,
        env={"HOCA_TEST_FORCE_REPO_AUTO_MERGE": "1"},
    )
    assert proc.returncode == 1
    skip = (minimal_auto_merge_run / "auto-merge-precheck-skip.txt").read_text(encoding="utf-8")
    assert "tests-exit-code" in skip


def test_precheck_fails_without_lgtm(minimal_auto_merge_run: Path):
    (minimal_auto_merge_run / "aider-review.txt").write_text("needs work\n", encoding="utf-8")
    proc = _run_guards(
        "precheck",
        minimal_auto_merge_run,
        env={"HOCA_TEST_FORCE_REPO_AUTO_MERGE": "1"},
    )
    assert proc.returncode == 1


def test_precheck_fails_risk_not_low(minimal_auto_merge_run: Path):
    (minimal_auto_merge_run / "risk-level.txt").write_text("medium\n", encoding="utf-8")
    proc = _run_guards(
        "precheck",
        minimal_auto_merge_run,
        env={"HOCA_TEST_FORCE_REPO_AUTO_MERGE": "1"},
    )
    assert proc.returncode == 1


def test_precheck_fails_secret_like_path(minimal_auto_merge_run: Path):
    (minimal_auto_merge_run / "staged-files.txt").write_text("config/.env.local\n", encoding="utf-8")
    proc = _run_guards(
        "precheck",
        minimal_auto_merge_run,
        env={"HOCA_TEST_FORCE_REPO_AUTO_MERGE": "1"},
    )
    assert proc.returncode == 1


def test_precheck_fails_infra_without_justification(minimal_auto_merge_run: Path):
    (minimal_auto_merge_run / "staged-files.txt").write_text(".github/workflows/ci.yml\n", encoding="utf-8")
    proc = _run_guards(
        "precheck",
        minimal_auto_merge_run,
        env={"HOCA_TEST_FORCE_REPO_AUTO_MERGE": "1"},
    )
    assert proc.returncode == 1
    skip = (minimal_auto_merge_run / "auto-merge-precheck-skip.txt").read_text(encoding="utf-8")
    assert "staging-justification" in skip


def test_precheck_passes_infra_with_justification(minimal_auto_merge_run: Path):
    (minimal_auto_merge_run / "staged-files.txt").write_text(".github/workflows/ci.yml\n", encoding="utf-8")
    (minimal_auto_merge_run / "staging-justification.txt").write_text(
        ".github/workflows/ci.yml\n", encoding="utf-8"
    )
    proc = _run_guards(
        "precheck",
        minimal_auto_merge_run,
        env={"HOCA_TEST_FORCE_REPO_AUTO_MERGE": "1"},
    )
    assert proc.returncode == 0


def test_precheck_passes_minimal(minimal_auto_merge_run: Path):
    proc = _run_guards(
        "precheck",
        minimal_auto_merge_run,
        env={"HOCA_TEST_FORCE_REPO_AUTO_MERGE": "1"},
    )
    assert proc.returncode == 0
