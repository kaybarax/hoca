from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from hoca.cli import main
from hoca.fleet_contracts import HocaFleetTask, HocaLane, HocaProject
import hoca.fleet_resources as fleet_resources
from hoca.fleet_resources import collect_resource_sample, summarize_resource_samples
from hoca.fleet_registry import FleetRegistry


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "hoca@example.test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "HOCA Test"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _registry(tmp_path: Path) -> FleetRegistry:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    registry = FleetRegistry(control_root=tmp_path / "control")
    registry.create_project(
        HocaProject(
            project_id="project-1",
            repo_path=str(repo),
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )
    registry.create_task(
        HocaFleetTask(
            task_id="task-1",
            project_id="project-1",
            status="running",
            readiness="ready",
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )
    registry.create_lane(
        HocaLane(
            lane_id="lane-1",
            task_id="task-1",
            project_id="project-1",
            status="running",
            run_dir=str(repo / ".hoca-runtime" / "fleet-lanes" / "lane-1"),
            branch="hoca/lane-1",
            attempt_number=0,
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )
    return registry


def test_collect_resource_sample_groups_processes_by_lane(tmp_path: Path, monkeypatch) -> None:
    registry = _registry(tmp_path)
    monkeypatch.setattr(
        fleet_resources,
        "_process_rows",
        lambda: [
            {"pid": 1, "ppid": 0, "cpu_pct": 10.0, "rss_kb": 1024, "command": "run lane-1"},
            {"pid": 2, "ppid": 0, "cpu_pct": 5.0, "rss_kb": 2048, "command": "other"},
        ],
    )

    sample = collect_resource_sample(registry)

    assert sample["aggregate"]["process_count"] == 1
    assert sample["aggregate"]["cpu_pct"] == 10.0
    assert sample["aggregate"]["rss_mb"] == 1.0
    assert sample["lanes"]["lane-1"]["process_count"] == 1


def test_summarize_resource_samples_reports_peak_and_average() -> None:
    summary = summarize_resource_samples(
        [
            {"aggregate": {"process_count": 1, "cpu_pct": 10.0, "rss_mb": 100.0}},
            {"aggregate": {"process_count": 3, "cpu_pct": 20.0, "rss_mb": 200.0}},
        ]
    )

    assert summary["sample_count"] == 2
    assert summary["peak_cpu_pct"] == 20.0
    assert summary["average_cpu_pct"] == 15.0
    assert summary["peak_rss_mb"] == 200.0
    assert summary["average_rss_mb"] == 150.0
    assert summary["peak_process_count"] == 3


def test_fleet_monitor_resources_command_writes_report(tmp_path: Path, monkeypatch) -> None:
    _registry(tmp_path)
    monkeypatch.setattr(
        fleet_resources,
        "_process_rows",
        lambda: [
            {"pid": 1, "ppid": 0, "cpu_pct": 7.0, "rss_kb": 3072, "command": "lane-1"},
        ],
    )
    output = tmp_path / "resources.json"
    env = {"HOCA_CONTROL_ROOT": str(tmp_path / "control")}

    result = CliRunner().invoke(
        main,
        ["fleet", "monitor", "--resources", "--samples", "1", "--output", str(output)],
        env=env,
    )

    assert result.exit_code == 0
    assert "Fleet resource report written:" in result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["peak_cpu_pct"] == 7.0
    assert payload["summary"]["peak_rss_mb"] == 3.0


def test_fleet_report_can_include_resource_summary(tmp_path: Path) -> None:
    _registry(tmp_path)
    resource_report = tmp_path / "resources.json"
    resource_report.write_text(
        json.dumps(
            {
                "summary": {
                    "sample_count": 2,
                    "peak_cpu_pct": 20.0,
                    "average_cpu_pct": 15.0,
                    "peak_rss_mb": 200.0,
                    "average_rss_mb": 150.0,
                    "peak_process_count": 3,
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "fleet.md"
    env = {"HOCA_CONTROL_ROOT": str(tmp_path / "control")}

    result = CliRunner().invoke(
        main,
        [
            "fleet",
            "report",
            "--include-resources",
            "--resource-report",
            str(resource_report),
            "--output",
            str(output),
        ],
        env=env,
    )

    assert result.exit_code == 0
    content = output.read_text(encoding="utf-8")
    assert "Resource Summary:" in content
    assert "- Samples: 2" in content
    assert "- Peak CPU %: 20.0" in content
    assert "- Peak RSS MB: 200.0" in content


def test_fleet_report_can_include_validation_summary(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    lane = registry.get_lane("lane-1")
    assert lane is not None
    run_dir = Path(lane.run_dir)
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(
        json.dumps({"status": "pr_created", "current_round": 2, "pr_url": "https://x/pr/1"})
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "final-state.json").write_text(
        json.dumps(
            {
                "status": "pr_opened",
                "pr_url": "https://x/pr/1",
                "changed_files": ["README.md", "scripts/install.sh"],
                "tests_run": ["pytest"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "fleet-validation.md"
    env = {"HOCA_CONTROL_ROOT": str(tmp_path / "control")}

    result = CliRunner().invoke(
        main,
        ["fleet", "report", "--validation-summary", "--output", str(output)],
        env=env,
    )

    assert result.exit_code == 0
    content = output.read_text(encoding="utf-8")
    assert "Validation Summary:" in content
    assert "lane-1: task=task-1; status=pr_created; pr=https://x/pr/1" in content
    assert "rounds=2" in content
    assert "changed_files=2" in content
    assert "tests=1" in content
    assert "HOCA Interventions:" in content
