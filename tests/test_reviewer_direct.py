from __future__ import annotations

from pathlib import Path

from hoca.contracts import HocaReviewReport
from hoca.reviewer_direct import run_reviewer_direct
from hoca.run_layout import ensure_run_layout, review_report_path
from tests.test_worker_hermes import init_repo, sample_task_spec


class FakeReviewProcess:
    def __init__(self, command, *, run_dir: Path, round_number: int, verdict: str):
        self.command = tuple(command)
        self.returncode: int | None = None
        (run_dir / "openhands-review.txt").write_text(
            "LGTM\n" if verdict == "LGTM" else "Needs tests\n",
            encoding="utf-8",
        )
        if verdict == "LGTM":
            findings = "[]"
        else:
            findings = (
                '[{"id":"F1","severity":"medium","category":"test","file":"README.md",'
                '"summary":"Missing validation","required_fix":"Add validation evidence"}]'
            )
        review_report_path(run_dir, round_number).parent.mkdir(parents=True, exist_ok=True)
        review_report_path(run_dir, round_number).write_text(
            '{"schema_version":1,"run_id":"run-test","round":'
            f"{round_number}"
            ',"role":"reviewer","verdict":"'
            f"{verdict}"
            '","findings":'
            f"{findings}"
            ',"pr_notes":{"summary":["Looks good."],"known_followups":[]}}\n',
            encoding="utf-8",
        )

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class FakeNoisyReviewProcess:
    def __init__(self, command, *, stderr_path: Path):
        self.command = tuple(command)
        self.returncode: int | None = None
        stderr_path.write_text(
            "InternalServerError around final message\n"
            "```json\n"
            '{"schema_version":1,"run_id":"run-test","round":1,"role":"reviewer",'
            '"verdict":"LGTM","findings":[],"pr_notes":{"summary":["Recovered."],'
            '"known_followups":[]}}\n'
            "```\n",
            encoding="utf-8",
        )

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class FakeHangingReviewProcess:
    def __init__(self, command):
        self.command = tuple(command)
        self.returncode: int | None = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = -15
        return -15

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


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

    def fake_popen(command, **kwargs):
        calls.append(tuple(command))
        return FakeReviewProcess(command, run_dir=run_dir, round_number=1, verdict="LGTM")

    monkeypatch.setattr("hoca.reviewer_direct.subprocess.Popen", fake_popen)

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


def test_run_reviewer_direct_fix_required_returns_repair_exit(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(sample_task_spec(repo_root=str(project)).to_json(), encoding="utf-8")

    def fake_popen(command, **kwargs):
        return FakeReviewProcess(command, run_dir=run_dir, round_number=1, verdict="fix_required")

    monkeypatch.setattr("hoca.reviewer_direct.subprocess.Popen", fake_popen)

    result = run_reviewer_direct(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
    )

    assert result.exit_code == 2
    assert (
        HocaReviewReport.from_json(result.review_report_path.read_text()).verdict == "fix_required"
    )


def test_run_reviewer_direct_recovers_fenced_report_from_stderr(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(sample_task_spec(repo_root=str(project)).to_json(), encoding="utf-8")

    def fake_popen(command, **kwargs):
        return FakeNoisyReviewProcess(
            command,
            stderr_path=run_dir / "logs" / "reviewer-direct-stderr.txt",
        )

    monkeypatch.setattr("hoca.reviewer_direct.subprocess.Popen", fake_popen)

    result = run_reviewer_direct(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
    )

    assert result.exit_code == 0
    assert HocaReviewReport.from_json(result.review_report_path.read_text()).verdict == "LGTM"


def test_run_reviewer_direct_times_out_without_structured_report(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(sample_task_spec(repo_root=str(project)).to_json(), encoding="utf-8")

    def fake_popen(command, **kwargs):
        return FakeHangingReviewProcess(command)

    ticks = iter([0.0, 2.0])
    monkeypatch.setenv("HOCA_OPENHANDS_TIMEOUT", "1")
    monkeypatch.setattr("hoca.reviewer_direct.subprocess.Popen", fake_popen)
    monkeypatch.setattr("hoca.reviewer_direct.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("hoca.reviewer_direct.time.sleep", lambda _seconds: None)

    result = run_reviewer_direct(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
    )

    report = HocaReviewReport.from_json(result.review_report_path.read_text())
    assert result.exit_code == 4
    assert report.verdict == "blocked"
    assert "did not write a structured HocaReviewReport" in report.findings[0].required_fix
