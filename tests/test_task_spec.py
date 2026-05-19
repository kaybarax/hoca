from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hoca.contracts import HocaSandboxPolicy, HocaTaskSpec
from hoca.run_layout import sandbox_policy_path, task_spec_path
from hoca.task_spec import (
    build_enriched_task_spec,
    derive_task_branch,
    gather_instruction_summaries,
    gather_repository_metadata,
    generate_task_spec,
    infer_expected_areas,
    infer_test_commands,
    build_initial_task_spec,
)


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / "README.md").write_text(
        "# Demo\n\nChange src/app.py for the widget.\n", encoding="utf-8"
    )
    (path / "AGENTS.md").write_text(
        "API_KEY=super-secret-should-not-appear\n"
        "Focus changes on src/.\n",
        encoding="utf-8",
    )
    (path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE)


def test_derive_task_branch_from_issue() -> None:
    assert derive_task_branch("anything", "42", "main") == "fix/issue-42"


def test_derive_task_branch_from_task_slug() -> None:
    assert derive_task_branch("Add Widget API", None, "main") == "feat/add-widget-api"


def test_gather_instruction_summaries_redacts_secrets(tmp_path: Path) -> None:
    init_repo(tmp_path)
    summaries = gather_instruction_summaries(tmp_path)
    agents = next(item for item in summaries if item["path"] == "AGENTS.md")
    assert "super-secret" not in agents["excerpt"]
    assert "[redacted: possible secret]" in agents["excerpt"]


def test_infer_test_commands_for_python_repo(tmp_path: Path) -> None:
    init_repo(tmp_path)
    assert "pytest" in infer_test_commands(tmp_path)


def test_infer_expected_areas_from_task_and_instructions(tmp_path: Path) -> None:
    init_repo(tmp_path)
    areas = infer_expected_areas("Update src/app.py", tmp_path)
    assert "src/app.py" in areas


def test_generate_task_spec_writes_contract_artifacts(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-demo"
    path = generate_task_spec(
        run_dir,
        repo_root=tmp_path,
        raw_request="Update src/app.py",
        run_id="run-demo",
        base_branch="main",
        task_branch="feat/update-app",
    )

    assert path == task_spec_path(run_dir)
    spec = HocaTaskSpec.from_json(path.read_text(encoding="utf-8"))
    assert spec.run_id == "run-demo"
    assert spec.raw_request == "Update src/app.py"
    assert spec.goal == "Update src/app.py"
    assert "src/app.py" in spec.expected_areas
    assert "pytest" in spec.test_commands
    assert spec.risk_level in ("low", "medium", "high")
    assert sandbox_policy_path(run_dir).is_file()

    context = json.loads((run_dir / "task-spec-context.json").read_text(encoding="utf-8"))
    assert context["repository"]["git_inside_work_tree"] is True
    assert any(item["path"] == "README.md" for item in context["instruction_files"])


def test_generate_task_spec_rejects_non_git_path(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "bad"
    with pytest.raises(ValueError, match="Not a Git repository"):
        generate_task_spec(
            run_dir,
            repo_root=tmp_path,
            raw_request="noop",
        )


def test_build_enriched_task_spec_preserves_models_and_sandbox(tmp_path: Path) -> None:
    base = build_initial_task_spec(
        run_id="r1",
        repo_root=str(tmp_path),
        base_branch="main",
        task_branch="feat/x",
        raw_request="auth login change",
        issue_id=None,
        max_total_rounds=3,
        sandbox=HocaSandboxPolicy(),
    )
    enriched = build_enriched_task_spec(
        base_spec=base,
        instruction_summaries=[],
        test_commands=["pytest"],
        expected_areas=["src/auth.py"],
    )
    assert enriched.models == base.models
    assert enriched.sandbox == base.sandbox
    assert enriched.risk_level == "high"
    assert enriched.test_commands == ["pytest"]


def test_gather_repository_metadata(tmp_path: Path) -> None:
    init_repo(tmp_path)
    metadata = gather_repository_metadata(tmp_path)
    assert metadata["git_inside_work_tree"] is True
    assert "README.md" in metadata["project_markers"] or "pyproject.toml" in metadata["project_markers"]
