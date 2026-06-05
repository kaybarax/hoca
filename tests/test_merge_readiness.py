from __future__ import annotations

from pathlib import Path

import subprocess

from hoca.merge_readiness import (
    LocalReadinessInputs,
    evaluate_local_merge_readiness,
    evaluate_merge_repair_plan,
    send_merge_repair_through_adapter,
)


def init_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)


def init_repo_with_commit(path: Path) -> str:
    init_repo(path)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE)
    return repo_default_branch(path)


def repo_default_branch(path: Path) -> str:
    return (
        subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        or "main"
    )


def init_repo_with_branch_and_divergence(path: Path) -> str:
    base_branch = init_repo_with_commit(path)
    subprocess.run(
        ["git", "checkout", "-b", "feature"], cwd=path, check=True, stdout=subprocess.PIPE
    )
    (path / "feature.txt").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=path, check=True, stdout=subprocess.PIPE)

    subprocess.run(["git", "checkout", base_branch], cwd=path, check=True, stdout=subprocess.PIPE)
    (path / "main.txt").write_text("mainline\n", encoding="utf-8")
    subprocess.run(["git", "add", "main.txt"], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "mainline"], cwd=path, check=True, stdout=subprocess.PIPE
    )

    subprocess.run(["git", "checkout", "feature"], cwd=path, check=True, stdout=subprocess.PIPE)
    return base_branch


def run_ready_case(
    repo: Path,
    run_dir: Path,
    base_ref: str,
) -> str:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "changed-files.txt").write_text("feature.txt\n", encoding="utf-8")
    (run_dir / "review").mkdir()
    (run_dir / "review" / "changed-files.txt").write_text("feature.txt\n", encoding="utf-8")

    result = evaluate_local_merge_readiness(
        LocalReadinessInputs(
            lane_id="lane-1",
            run_dir=run_dir,
            repo_path=repo,
            base_ref=base_ref,
        )
    )
    return result.readiness


def test_local_merge_readiness_detects_clean_ready_branch(tmp_path: Path) -> None:
    base_branch = init_repo_with_commit(tmp_path)
    subprocess.run(
        ["git", "checkout", "-b", "feature"], cwd=tmp_path, check=True, stdout=subprocess.PIPE
    )
    (tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feature work"], cwd=tmp_path, check=True, stdout=subprocess.PIPE
    )

    result = run_ready_case(tmp_path, tmp_path / "run", base_ref=base_branch)
    assert result == "ready"


def test_local_merge_readiness_blocks_detached_head(tmp_path: Path) -> None:
    init_repo_with_commit(tmp_path)
    default_base = repo_default_branch(tmp_path)
    initial = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(["git", "checkout", initial], cwd=tmp_path, check=True, stdout=subprocess.PIPE)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    result = evaluate_local_merge_readiness(
        LocalReadinessInputs(
            lane_id="lane-2", run_dir=run_dir, repo_path=tmp_path, base_ref=default_base
        )
    )
    assert result.readiness == "blocked"
    assert "detached HEAD" in (result.reason or "")


def test_local_merge_readiness_blocks_whitespace_errors(tmp_path: Path) -> None:
    base_branch = init_repo_with_commit(tmp_path)
    subprocess.run(
        ["git", "checkout", "-b", "feature"], cwd=tmp_path, check=True, stdout=subprocess.PIPE
    )
    (tmp_path / "file.txt").write_text("bad   \n", encoding="utf-8")
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "changed-files.txt").write_text("file.txt\n", encoding="utf-8")
    (tmp_path / "run" / "review").mkdir()
    (tmp_path / "run" / "review" / "changed-files.txt").write_text("file.txt\n", encoding="utf-8")

    result = evaluate_local_merge_readiness(
        LocalReadinessInputs(
            lane_id="lane-3", run_dir=tmp_path / "run", repo_path=tmp_path, base_ref=base_branch
        )
    )
    assert result.readiness == "blocked"
    assert "git diff --check" in (result.reason or "")


def test_local_merge_readiness_blocks_unreviewed_files(tmp_path: Path) -> None:
    base_branch = init_repo_with_commit(tmp_path)
    subprocess.run(
        ["git", "checkout", "-b", "feature"], cwd=tmp_path, check=True, stdout=subprocess.PIPE
    )
    (tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "changed-files.txt").write_text("feature.txt\n", encoding="utf-8")
    (run_dir / "review").mkdir()
    (run_dir / "review" / "changed-files.txt").write_text("other.txt\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feature work"], cwd=tmp_path, check=True, stdout=subprocess.PIPE
    )

    result = evaluate_local_merge_readiness(
        LocalReadinessInputs(
            lane_id="lane-4", run_dir=run_dir, repo_path=tmp_path, base_ref=base_branch
        )
    )
    assert result.readiness == "blocked"
    assert "not present in reviewed file list" in (result.reason or "")


def test_local_merge_readiness_blocks_secret_like_paths(tmp_path: Path) -> None:
    base_branch = init_repo_with_commit(tmp_path)
    subprocess.run(
        ["git", "checkout", "-b", "feature"], cwd=tmp_path, check=True, stdout=subprocess.PIPE
    )
    (tmp_path / ".env").write_text("TOKEN=1\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "changed-files.txt").write_text(".env\n", encoding="utf-8")
    (run_dir / "review").mkdir()
    (run_dir / "review" / "changed-files.txt").write_text(".env\n", encoding="utf-8")
    subprocess.run(["git", "add", ".env"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feature work"], cwd=tmp_path, check=True, stdout=subprocess.PIPE
    )

    result = evaluate_local_merge_readiness(
        LocalReadinessInputs(
            lane_id="lane-5", run_dir=run_dir, repo_path=tmp_path, base_ref=base_branch
        )
    )
    assert result.readiness == "blocked"
    assert "Secret-like files were changed" in (result.reason or "")


def test_local_merge_readiness_blocks_divergent_merge_base(tmp_path: Path) -> None:
    base_branch = init_repo_with_branch_and_divergence(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "changed-files.txt").write_text("feature.txt\n", encoding="utf-8")
    (run_dir / "review").mkdir()
    (run_dir / "review" / "changed-files.txt").write_text("feature.txt\n", encoding="utf-8")

    result = evaluate_local_merge_readiness(
        LocalReadinessInputs(
            lane_id="lane-6", run_dir=run_dir, repo_path=tmp_path, base_ref=base_branch
        )
    )
    assert result.readiness == "blocked"
    assert "Base branch diverged" in (result.reason or "")


def test_local_merge_repair_plan_classifies_needs_rebase(tmp_path: Path) -> None:
    base_branch = init_repo_with_branch_and_divergence(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "changed-files.txt").write_text("feature.txt\n", encoding="utf-8")

    plan = evaluate_merge_repair_plan(
        LocalReadinessInputs(
            lane_id="lane-repair",
            run_dir=run_dir,
            repo_path=tmp_path,
            base_ref=base_branch,
        )
    )
    assert plan.readiness == "needs_rebase"
    assert plan.can_auto_repair is True
    assert plan.repair_brief_path is not None
    assert plan.repair_brief_path.is_file()

    called: list[tuple[str, Path]] = []

    def sender(lane_id: str, brief_path: Path) -> bool:
        called.append((lane_id, brief_path))
        return True

    assert (
        send_merge_repair_through_adapter(
            LocalReadinessInputs(
                lane_id="lane-repair",
                run_dir=run_dir,
                repo_path=tmp_path,
                base_ref=base_branch,
            ),
            sender=sender,
        )
        is True
    )
    assert called == [("lane-repair", plan.repair_brief_path)]


def test_local_merge_repair_plan_classifies_merge_conflict_and_blocks_sender(
    tmp_path: Path,
) -> None:
    base_branch = init_repo_with_branch_and_divergence(tmp_path)
    subprocess.run(["git", "checkout", "feature"], cwd=tmp_path, check=True, stdout=subprocess.PIPE)
    feature_path = tmp_path / "package.json"
    feature_path.write_text(
        '{\n  <<<<<<< HEAD\n  "name": "demo"\n=======\n  "name": "demo-conflict"\n>>>>>>> branch\n}\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "package.json"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feature work"], cwd=tmp_path, check=True, stdout=subprocess.PIPE
    )

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "changed-files.txt").write_text("package.json\n", encoding="utf-8")
    (run_dir / "review").mkdir()
    (run_dir / "review" / "changed-files.txt").write_text("package.json\n", encoding="utf-8")

    plan = evaluate_merge_repair_plan(
        LocalReadinessInputs(
            lane_id="lane-conflict",
            run_dir=run_dir,
            repo_path=tmp_path,
            base_ref=base_branch,
        )
    )
    assert plan.readiness == "merge_conflict"
    assert plan.can_auto_repair is False
    assert plan.escalate_to_human is True
    assert plan.repair_brief_path is not None
    assert plan.repair_brief_path.is_file()
    assert plan.repair_report_path is not None
    assert plan.repair_report_path.is_file()
    assert "merge_conflict" in plan.repair_report_path.name
    assert plan.reason is not None

    called: list[tuple[str, Path]] = []

    def sender(lane_id: str, brief_path: Path) -> bool:
        called.append((lane_id, brief_path))
        return True

    assert (
        send_merge_repair_through_adapter(
            LocalReadinessInputs(
                lane_id="lane-conflict",
                run_dir=run_dir,
                repo_path=tmp_path,
                base_ref=base_branch,
            ),
            sender=sender,
        )
        is False
    )
    assert called == []
