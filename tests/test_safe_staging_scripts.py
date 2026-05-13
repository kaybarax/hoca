from __future__ import annotations

import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "safe-stage-after-review.sh"


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / ".gitignore").write_text(".hoca-runtime/\n", encoding="utf-8")
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", ".gitignore", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE)


def write_run_files(run_dir: Path, *, producer: str = "reviewer") -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "aider-review.txt").write_text("Looks good.\nLGTM\n", encoding="utf-8")
    (run_dir / "intended-files-source.txt").write_text(f"{producer}\n", encoding="utf-8")


def run_safe_stage(repo: Path, task: str, run_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), str(repo), task, str(run_dir), str(run_dir / "intended-files.txt")],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def staged_files(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout.splitlines()


def test_safe_stage_requires_lgtm_review(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    write_run_files(run_dir)
    (run_dir / "aider-review.txt").write_text("Needs changes.\n", encoding="utf-8")
    (run_dir / "intended-files.txt").write_text("README.md\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("updated\n", encoding="utf-8")

    result = run_safe_stage(tmp_path, "Update README", run_dir)

    assert result.returncode != 0
    assert "before an Aider review returns LGTM" in result.stderr
    assert staged_files(tmp_path) == []


def test_safe_stage_requires_manager_or_reviewer_producer(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    write_run_files(run_dir, producer="worker")
    (run_dir / "intended-files.txt").write_text("README.md\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("updated\n", encoding="utf-8")

    result = run_safe_stage(tmp_path, "Update README", run_dir)

    assert result.returncode != 0
    assert "producer must be manager or reviewer" in result.stderr
    assert staged_files(tmp_path) == []


def test_safe_stage_stops_when_changed_file_is_not_accounted_for(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    write_run_files(run_dir)
    (run_dir / "intended-files.txt").write_text("README.md\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("updated\n", encoding="utf-8")
    (tmp_path / "extra.txt").write_text("unexpected\n", encoding="utf-8")

    result = run_safe_stage(tmp_path, "Update README", run_dir)

    assert result.returncode != 0
    assert "Changed files not accounted for" in result.stderr
    assert "extra.txt" in result.stderr
    assert staged_files(tmp_path) == []


def test_safe_stage_rejects_secret_like_intended_files(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    write_run_files(run_dir)
    (run_dir / "intended-files.txt").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=value\n", encoding="utf-8")

    result = run_safe_stage(tmp_path, "Update env", run_dir)

    assert result.returncode != 0
    assert "secret-like file" in result.stderr
    assert staged_files(tmp_path) == []


def test_safe_stage_validates_intended_files_against_task(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    write_run_files(run_dir)
    (tmp_path / "docs.md").write_text("updated\n", encoding="utf-8")
    (run_dir / "intended-files.txt").write_text("docs.md\n", encoding="utf-8")

    result = run_safe_stage(tmp_path, "Update billing module", run_dir)

    assert result.returncode != 0
    assert "does not match task keywords" in result.stderr
    assert staged_files(tmp_path) == []


def test_safe_stage_requires_generated_file_justification(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    write_run_files(run_dir)
    generated = tmp_path / "api.generated.ts"
    generated.write_text("export const x = 1;\n", encoding="utf-8")
    (run_dir / "intended-files.txt").write_text("api.generated.ts\n", encoding="utf-8")

    result = run_safe_stage(tmp_path, "Update API", run_dir)

    assert result.returncode != 0
    assert "generated file change requires justification" in result.stderr
    assert staged_files(tmp_path) == []


def test_safe_stage_refuses_when_index_already_staged(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    write_run_files(run_dir)
    (run_dir / "intended-files.txt").write_text("README.md\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("updated\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "README.md"], cwd=tmp_path, check=True)

    result = run_safe_stage(tmp_path, "Update README", run_dir)

    assert result.returncode != 0
    assert "already has staged changes" in result.stderr
    assert staged_files(tmp_path) == ["README.md"]


def test_safe_stage_rejects_intended_hoca_runtime_path(tmp_path: Path) -> None:
    init_repo(tmp_path)
    runtime_file = tmp_path / ".hoca-runtime" / "bad.txt"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-f", "--", ".hoca-runtime/bad.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "track runtime"], cwd=tmp_path, check=True, stdout=subprocess.PIPE)
    runtime_file.write_text("y\n", encoding="utf-8")

    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    write_run_files(run_dir)
    (run_dir / "intended-files.txt").write_text(".hoca-runtime/bad.txt\n", encoding="utf-8")

    result = run_safe_stage(tmp_path, "Update hoca runtime file", run_dir)

    assert result.returncode != 0
    assert "Refusing intended path" in result.stderr


def test_safe_stage_stages_reviewed_accounted_files(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    write_run_files(run_dir)
    (run_dir / "intended-files.txt").write_text("README.md\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("updated\n", encoding="utf-8")

    result = run_safe_stage(tmp_path, "Update README", run_dir)

    assert result.returncode == 0, result.stderr
    assert staged_files(tmp_path) == ["README.md"]
    assert (run_dir / "staged-files.txt").read_text(encoding="utf-8") == "README.md\n"
