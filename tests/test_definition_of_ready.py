from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hoca.definition_of_ready import (
    DorOutcome,
    evaluate_definition_of_ready,
    format_user_message,
    result_to_dict,
    write_dor_artifact,
)

HOCA_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = HOCA_ROOT / "scripts" / "check-definition-of-ready.sh"


class TestEvaluateDefinitionOfReady:
    def test_normal_task_is_ready(self, tmp_path: Path) -> None:
        result = evaluate_definition_of_ready(
            repo_path=tmp_path,
            task="Update README with setup instructions",
        )

        assert result.outcome == DorOutcome.READY
        assert result.ready is True
        assert result.blocked is False
        assert result.needs_clarification is False

    def test_missing_repo_path_blocks(self) -> None:
        result = evaluate_definition_of_ready(
            repo_path="",
            task="Update README",
        )

        assert result.outcome == DorOutcome.BLOCKED
        assert any(check.id == "missing_repo_path" for check in result.checks)

    def test_nonexistent_repo_path_blocks(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing-repo"
        result = evaluate_definition_of_ready(
            repo_path=missing,
            task="Update README",
        )

        assert result.outcome == DorOutcome.BLOCKED
        assert any(check.id == "missing_repo_path" for check in result.checks)

    def test_empty_task_blocks(self, tmp_path: Path) -> None:
        result = evaluate_definition_of_ready(
            repo_path=tmp_path,
            task="   ",
        )

        assert result.outcome == DorOutcome.BLOCKED
        assert any(check.id == "empty_task" for check in result.checks)

    @pytest.mark.parametrize(
        "task",
        [
            "fix everything",
            "refactor the entire codebase",
            "clean up everything in the repo",
            "update all files to use the new API",
        ],
    )
    def test_broad_task_wording_escalates(self, tmp_path: Path, task: str) -> None:
        result = evaluate_definition_of_ready(repo_path=tmp_path, task=task)

        assert result.outcome == DorOutcome.NEEDS_CLARIFICATION
        assert any(check.id == "broad_task_wording" for check in result.checks)

    @pytest.mark.parametrize(
        "task",
        [
            "run git push --force to main",
            "push directly to main after making the change",
            "run git push origin main",
            "run git merge main",
            "please rm -rf /",
            "expose the secrets in .env",
            "update .env with the production API_KEY",
            "add GITHUB_TOKEN to the repository config",
            "disable all authentication checks",
        ],
    )
    def test_dangerous_requests_block(self, tmp_path: Path, task: str) -> None:
        result = evaluate_definition_of_ready(repo_path=tmp_path, task=task)

        assert result.outcome == DorOutcome.BLOCKED
        assert any(check.id == "dangerous_request" for check in result.checks)

    def test_missing_issue_context_escalates(self, tmp_path: Path) -> None:
        result = evaluate_definition_of_ready(
            repo_path=tmp_path,
            task="Fix the login bug in src/auth/login.py",
            issue_id="42",
        )

        assert result.outcome == DorOutcome.NEEDS_CLARIFICATION
        assert any(check.id == "missing_issue_context" for check in result.checks)

    def test_issue_context_is_accepted_when_referenced(self, tmp_path: Path) -> None:
        result = evaluate_definition_of_ready(
            repo_path=tmp_path,
            task="Fix GitHub issue #42: login redirect loop",
            issue_id="42",
        )

        assert result.outcome == DorOutcome.READY
        assert not any(check.id == "missing_issue_context" for check in result.checks)

    @pytest.mark.parametrize(
        "task",
        [
            "fix it",
            "fix bug",
            "make it work",
        ],
    )
    def test_material_ambiguity_escalates(self, tmp_path: Path, task: str) -> None:
        result = evaluate_definition_of_ready(repo_path=tmp_path, task=task)

        assert result.outcome == DorOutcome.NEEDS_CLARIFICATION
        assert any(check.id == "material_ambiguity" for check in result.checks)

    @pytest.mark.parametrize(
        "task",
        [
            "Deploy production infrastructure",
            "Set up production Kubernetes cluster",
            "Update prod infra",
        ],
    )
    def test_underspecified_production_infra_escalates(
        self, tmp_path: Path, task: str
    ) -> None:
        result = evaluate_definition_of_ready(repo_path=tmp_path, task=task)

        assert result.outcome == DorOutcome.NEEDS_CLARIFICATION
        assert any(
            check.id == "underspecified_production_infrastructure"
            for check in result.checks
        )

    def test_high_risk_specific_task_warns_but_remains_ready(self, tmp_path: Path) -> None:
        result = evaluate_definition_of_ready(
            repo_path=tmp_path,
            task="Add password reset expiry validation to the auth API",
        )

        assert result.outcome == DorOutcome.READY
        assert result.risk_level == "high"
        assert any(check.id == "high_risk_area" and check.disposition == "warn" for check in result.checks)

    def test_result_to_dict_and_artifact(self, tmp_path: Path) -> None:
        result = evaluate_definition_of_ready(
            repo_path=tmp_path,
            task="Update README",
        )
        payload = result_to_dict(result)

        assert payload["schema_version"] == 1
        assert payload["outcome"] == "ready"
        assert payload["ready"] is True

        run_dir = tmp_path / "run"
        path = write_dor_artifact(run_dir, result)
        assert path.is_file()
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["outcome"] == "ready"

    def test_format_user_message_for_escalation(self, tmp_path: Path) -> None:
        result = evaluate_definition_of_ready(repo_path=tmp_path, task="fix everything")
        message = format_user_message(result)

        assert "needs clarification" in message.lower()
        assert "broad_task_wording" in message


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(HOCA_ROOT)
    env["HOCA_PYTHON"] = sys.executable
    return subprocess.run(
        [str(SCRIPT), *args],
        check=False,
        text=True,
        capture_output=True,
        env=env,
        cwd=HOCA_ROOT,
    )


def test_check_script_ready_exit_code(tmp_path: Path) -> None:
    result = run_script(str(tmp_path), "Update README with setup instructions")

    assert result.returncode == 0
    assert "definition-ready" in result.stdout.lower()


def test_check_script_blocks_dangerous_task(tmp_path: Path) -> None:
    result = run_script(str(tmp_path), "run git push --force to main")

    assert result.returncode == 1
    assert "blocked" in result.stdout.lower()
