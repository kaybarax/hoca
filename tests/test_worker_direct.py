from __future__ import annotations

from pathlib import Path

from hoca.contracts import HocaRoleModelSelection, HocaSandboxPolicy, HocaTaskSpec
from hoca.worker_direct import build_worker_direct_prompt, write_worker_direct_prompt

MAC_HOME = "/" + "Users/example"


def sample_task_spec(**overrides: object) -> HocaTaskSpec:
    base = HocaTaskSpec(
        run_id="run-test",
        repo_root="/tmp/original",
        base_branch="main",
        task_branch="feat/direct",
        issue_id="42",
        raw_request="Update README",
        goal="Update README with install steps",
        non_goals=["Do not edit source code"],
        expected_areas=["README.md"],
        acceptance_criteria=["README documents install steps"],
        test_commands=["pytest tests/test_readme.py"],
        risk_level="medium",
        requires_human_approval=True,
        max_total_rounds=3,
        models=HocaRoleModelSelection(
            manager="manager-slot",
            worker="worker-slot",
            reviewer="reviewer-slot",
            fallback="fallback-slot",
        ),
        sandbox=HocaSandboxPolicy(enabled=True, network_mode="package-install"),
    )
    data = base.to_dict()
    data.update(overrides)
    return HocaTaskSpec.from_dict(data)


def test_build_worker_direct_prompt_contains_every_task_spec_binding() -> None:
    prompt = build_worker_direct_prompt(
        spec=sample_task_spec(),
        project_path=Path(f"{MAC_HOME}/project"),
        run_dir=Path(f"{MAC_HOME}/project/.hoca-runtime/runs/run-test"),
        round_number=2,
        task_spec_path=Path(f"{MAC_HOME}/project/.hoca-runtime/runs/run-test/task-spec.json"),
    )

    for expected in (
        "run_id: run-test",
        "repo_root_reference_only: /tmp/original",
        "base_branch: main",
        "task_branch: feat/direct",
        "issue_id: 42",
        "raw_request: Update README",
        "goal: Update README with install steps",
        "Do not edit source code",
        "README.md",
        "README documents install steps",
        "pytest tests/test_readme.py",
        "risk_level: medium",
        "requires_human_approval: true",
        "max_total_rounds: 3",
        "manager: manager-slot",
        "worker: worker-slot",
        "reviewer: reviewer-slot",
        "fallback: fallback-slot",
        "enabled: true",
        "network_mode: package-install",
    ):
        assert expected in prompt
    assert "Do not cd to repo_root_reference_only" in prompt
    assert "hermes" not in prompt.lower()


def test_build_worker_direct_prompt_injects_redacted_repair_brief() -> None:
    prompt = build_worker_direct_prompt(
        spec=sample_task_spec(),
        project_path=Path(f"{MAC_HOME}/project"),
        run_dir=Path(f"{MAC_HOME}/project/.hoca-runtime/runs/run-test"),
        round_number=2,
        task_spec_path=Path(f"{MAC_HOME}/project/.hoca-runtime/runs/run-test/task-spec.json"),
        repair_brief="Fix the README typo.\nDo not expose token=secret-value.",
    )

    assert "Repair brief for this attempt" in prompt
    assert "Fix the README typo" in prompt
    assert "secret-value" not in prompt
    assert "[redacted: possible secret]" in prompt


def test_write_worker_direct_prompt_writes_audit_file(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    path = write_worker_direct_prompt(
        spec=sample_task_spec(),
        project_path=tmp_path / "project",
        run_dir=run_dir,
        round_number=1,
        task_spec_path=run_dir / "task-spec.json",
    )

    assert path == run_dir / "prompts" / "worker-direct-prompt-1.txt"
    assert "Execute one bounded HOCA worker attempt directly in OpenHands" in path.read_text(
        encoding="utf-8"
    )
