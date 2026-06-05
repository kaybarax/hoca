from __future__ import annotations

from pathlib import Path
import subprocess


def run_init_project(repo_root: Path, project_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(repo_root / "scripts" / "init-project.sh"), str(project_path)],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )


def test_init_project_creates_templates_without_overwriting(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    project_path = tmp_path / "target"
    project_path.mkdir()

    subprocess.run(["git", "init"], cwd=project_path, check=True, capture_output=True, text=True)

    existing_openhands = project_path / ".openhands_instructions"
    existing_pr_template = project_path / "templates" / "PR_TEMPLATE.md"
    existing_pr_template.parent.mkdir()

    existing_openhands.write_text("custom openhands\n")
    existing_pr_template.write_text("custom pr template\n")
    (project_path / ".gitignore").write_text("node_modules")

    result = run_init_project(repo_root, project_path)

    assert result.returncode == 0, result.stderr
    assert existing_openhands.read_text() == "custom openhands\n"
    assert existing_pr_template.read_text() == "custom pr template\n"
    assert (project_path / ".hoca" / "config.toml").is_file()
    assert "dev_branch" in (project_path / ".hoca" / "config.toml").read_text()
    assert (project_path / ".hoca-runtime" / "runs").is_dir()
    assert (project_path / ".hoca-runtime" / "logs").is_dir()

    gitignore_lines = (project_path / ".gitignore").read_text().splitlines()
    assert "node_modules" in gitignore_lines
    assert ".hoca-runtime/" in gitignore_lines
    assert ".openhands/" in gitignore_lines
    assert "node_modules.hoca-runtime/" not in gitignore_lines


def test_init_project_is_idempotent(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    project_path = tmp_path / "target"
    project_path.mkdir()

    subprocess.run(["git", "init"], cwd=project_path, check=True, capture_output=True, text=True)

    first_result = run_init_project(repo_root, project_path)
    assert first_result.returncode == 0, first_result.stderr

    tracked_files = [
        ".openhands_instructions",
        ".hoca/config.toml",
        "templates/PR_TEMPLATE.md",
        ".gitignore",
    ]
    first_contents = {name: (project_path / name).read_text() for name in tracked_files}

    second_result = run_init_project(repo_root, project_path)
    assert second_result.returncode == 0, second_result.stderr

    second_contents = {name: (project_path / name).read_text() for name in tracked_files}
    assert second_contents == first_contents
