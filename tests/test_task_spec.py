from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hoca.contracts import HocaSandboxPolicy, HocaTaskSpec
from hoca.run_layout import sandbox_policy_path, task_spec_path
from hoca.run_artifacts import build_initial_task_spec
from hoca.task_spec import (
    build_enriched_task_spec,
    derive_task_branch,
    extract_explicit_test_commands,
    gather_instruction_summaries,
    gather_repository_metadata,
    generate_task_spec,
    infer_expected_areas,
    infer_test_commands,
)


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / "README.md").write_text(
        "# Demo\n\nChange src/app.py for the widget.\n", encoding="utf-8"
    )
    (path / "AGENTS.md").write_text(
        "API_KEY=super-secret-should-not-appear\nFocus changes on src/.\n",
        encoding="utf-8",
    )
    (path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE)


def clear_role_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for role in ("MANAGER", "WORKER", "REVIEWER"):
        for suffix in ("NAME", "MODEL", "BASE_URL", "API_KEY"):
            monkeypatch.delenv(f"HOCA_{role}_MODEL_{suffix}", raising=False)


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


def test_gather_instruction_summaries_with_expected_areas_returns_relevant_excerpts(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path)
    summaries = gather_instruction_summaries(tmp_path, expected_areas=("src/app.py",))
    paths = [item["path"] for item in summaries]
    assert "README.md" in paths
    assert "AGENTS.md" not in paths
    readme_excerpt = next(item["excerpt"] for item in summaries if item["path"] == "README.md")
    assert "src/app.py" in readme_excerpt


def test_infer_test_commands_for_python_repo(tmp_path: Path) -> None:
    init_repo(tmp_path)
    assert "pytest" in infer_test_commands(tmp_path)


def test_extract_explicit_test_commands_from_validation_section() -> None:
    task = """Implement the thing.

Validation commands:
- pnpm --filter @todo/api-gateway test
- `pnpm --filter @todo/api-gateway typecheck`

Reporting expectations:
- Produce artifacts.
"""

    assert extract_explicit_test_commands(task) == [
        "pnpm --filter @todo/api-gateway test",
        "pnpm --filter @todo/api-gateway typecheck",
    ]


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
    assert (run_dir / "raw-task.txt").read_text(encoding="utf-8") == "Update src/app.py\n"


def test_generate_task_spec_prefers_explicit_validation_commands(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "echo root test"}}\n', encoding="utf-8"
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "add node markers"], cwd=tmp_path, check=True)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-validation"
    task = """Update apps/api-gateway/src/config/env.ts.

Validation commands:
- pnpm --filter @todo/api-gateway test
- pnpm --filter @todo/api-gateway typecheck
"""

    path = generate_task_spec(
        run_dir,
        repo_root=tmp_path,
        raw_request=task,
        run_id="run-validation",
        base_branch="main",
        task_branch="feat/validation",
    )

    spec = HocaTaskSpec.from_json(path.read_text(encoding="utf-8"))
    assert spec.test_commands == [
        "pnpm --filter @todo/api-gateway test",
        "pnpm --filter @todo/api-gateway typecheck",
    ]


def test_generate_task_spec_rejects_non_git_path(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "bad"
    with pytest.raises(ValueError, match="Not a Git repository"):
        generate_task_spec(
            run_dir,
            repo_root=tmp_path,
            raw_request="noop",
        )


def test_build_initial_task_spec_records_resolved_role_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "HOCA_MANAGER_MODEL_NAME=local-fast\n"
        "HOCA_MANAGER_MODEL_MODEL=ollama/qwen-7b-pro\n"
        "HOCA_WORKER_MODEL_NAME=local-coder\n"
        "HOCA_WORKER_MODEL_MODEL=ollama/qwen-14b-pro\n"
        "HOCA_REVIEWER_MODEL_NAME=reviewer-strong\n"
        "HOCA_REVIEWER_MODEL_MODEL=openai/gpt-oss-20b\n"
    )
    clear_role_model_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    spec = build_initial_task_spec(
        run_id="r1",
        repo_root=str(tmp_path),
        base_branch="main",
        task_branch="feat/x",
        raw_request="task",
        issue_id=None,
        max_total_rounds=3,
        sandbox=HocaSandboxPolicy(),
    )

    assert spec.models.worker == "local-coder"
    assert spec.models.reviewer == "reviewer-strong"
    assert spec.models.manager == "local-fast"
    assert "secret" not in spec.to_json()


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
    assert (
        "README.md" in metadata["project_markers"]
        or "pyproject.toml" in metadata["project_markers"]
    )
