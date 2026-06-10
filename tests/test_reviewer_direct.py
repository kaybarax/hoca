from __future__ import annotations

import subprocess
from pathlib import Path

from hoca.contracts import HocaReviewReport
from hoca.reviewer_direct import run_reviewer_direct
from hoca.run_layout import ensure_run_layout, review_report_path
from tests.test_worker_hermes import init_repo, sample_task_spec


def test_run_reviewer_direct_invokes_review_wrapper_and_uses_gate(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(sample_task_spec(repo_root=str(project)).to_json(), encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def fake_run(command, **kwargs):
        calls.append(tuple(command))
        (run_dir / "openhands-review.txt").write_text("LGTM\n", encoding="utf-8")
        review_report_path(run_dir, 1).parent.mkdir(parents=True, exist_ok=True)
        review_report_path(run_dir, 1).write_text(
            '{"schema_version":1,"run_id":"run-test","round":1,"role":"reviewer",'
            '"verdict":"LGTM","findings":[],"pr_notes":{"summary":["Looks good."],'
            '"known_followups":[]}}\n',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="review ok\n", stderr="")

    monkeypatch.setattr("hoca.reviewer_direct.subprocess.run", fake_run)

    result = run_reviewer_direct(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
    )

    assert result.mode == "direct"
    assert result.exit_code == 0
    assert result.review_report_path == review_report_path(run_dir, 1)
    assert calls and calls[0][0].endswith("review-with-openhands.sh")
    assert all("hermes" not in part.lower() for part in calls[0])
    assert HocaReviewReport.from_json(result.review_report_path.read_text()).verdict == "LGTM"


def test_run_reviewer_direct_fix_required_returns_repair_exit(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(sample_task_spec(repo_root=str(project)).to_json(), encoding="utf-8")

    def fake_run(command, **kwargs):
        (run_dir / "openhands-review.txt").write_text("Needs tests\n", encoding="utf-8")
        review_report_path(run_dir, 1).parent.mkdir(parents=True, exist_ok=True)
        review_report_path(run_dir, 1).write_text(
            '{"schema_version":1,"run_id":"run-test","round":1,"role":"reviewer",'
            '"verdict":"fix_required","findings":[{"id":"F1","severity":"medium",'
            '"category":"test","file":"README.md","summary":"Missing validation",'
            '"required_fix":"Add validation evidence"}],"pr_notes":{"summary":["Needs tests."],'
            '"known_followups":[]}}\n',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("hoca.reviewer_direct.subprocess.run", fake_run)

    result = run_reviewer_direct(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
    )

    assert result.exit_code == 2
    assert HocaReviewReport.from_json(result.review_report_path.read_text()).verdict == "fix_required"
