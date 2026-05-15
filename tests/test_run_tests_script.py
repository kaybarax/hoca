from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run-tests.sh"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run_tests(
    project: Path, run_dir: Path, fake_bin: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if fake_bin is not None:
        env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [str(SCRIPT), str(project), str(run_dir)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE)


def test_run_tests_runs_node_script_without_jq(tmp_path: Path) -> None:
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    fake_bin = tmp_path / "bin"
    project.mkdir()
    fake_bin.mkdir()
    (project / "package.json").write_text(
        '{"scripts": {"test": "echo node ok"}}\n', encoding="utf-8"
    )
    write_executable(fake_bin / "jq", "#!/usr/bin/env bash\nexit 127\n")
    write_executable(fake_bin / "npm", '#!/usr/bin/env bash\necho npm "$@"\n')

    result = run_tests(project, run_dir, fake_bin)

    assert result.returncode == 0, result.stderr
    assert "Running: npm test" in result.stdout
    assert "- **Status**: passed" in (run_dir / "tests-summary.md").read_text(encoding="utf-8")


def test_run_tests_runs_python_pytest(tmp_path: Path) -> None:
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    fake_bin = tmp_path / "bin"
    project.mkdir()
    fake_bin.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    write_executable(fake_bin / "pytest", "#!/usr/bin/env bash\necho pytest ok\n")

    result = run_tests(project, run_dir, fake_bin)

    assert result.returncode == 0, result.stderr
    assert "Running: pytest" in result.stdout
    assert "pytest ok" in (run_dir / "tests-output.log").read_text(encoding="utf-8")


def test_run_tests_runs_go_and_rust(tmp_path: Path) -> None:
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    fake_bin = tmp_path / "bin"
    project.mkdir()
    fake_bin.mkdir()
    (project / "go.mod").write_text("module example.test/demo\n", encoding="utf-8")
    (project / "Cargo.toml").write_text("[package]\nname = 'demo'\n", encoding="utf-8")
    write_executable(fake_bin / "go", '#!/usr/bin/env bash\necho go "$@"\n')
    write_executable(fake_bin / "cargo", '#!/usr/bin/env bash\necho cargo "$@"\n')

    result = run_tests(project, run_dir, fake_bin)

    assert result.returncode == 0, result.stderr
    assert "Running: go test ./..." in result.stdout
    assert "Running: cargo test" in result.stdout
    output = (run_dir / "tests-output.log").read_text(encoding="utf-8")
    assert "go test ./..." in output
    assert "cargo test" in output


def test_run_tests_uses_no_test_fallback(tmp_path: Path) -> None:
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    project.mkdir()
    (project / "README.md").write_text("no tests here\n", encoding="utf-8")

    result = run_tests(project, run_dir)

    assert result.returncode == 0, result.stderr
    assert "No automated tests detected." in result.stdout
    assert "no-tests-detected" in (run_dir / "tests-summary.md").read_text(encoding="utf-8")
    assert (run_dir / "tests-exit-code.txt").read_text(encoding="utf-8") == "0\n"


def test_run_tests_records_first_failed_command(tmp_path: Path) -> None:
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    fake_bin = tmp_path / "bin"
    project.mkdir()
    fake_bin.mkdir()
    init_repo(project)
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    write_executable(fake_bin / "pytest", "#!/usr/bin/env bash\necho boom >&2\nexit 3\n")

    result = run_tests(project, run_dir, fake_bin)

    assert result.returncode == 3
    assert (run_dir / "tests-exit-code.txt").read_text(encoding="utf-8") == "3\n"
    assert (run_dir / "failed-command.txt").read_text(encoding="utf-8") == "pytest\n"
    summary = (run_dir / "tests-summary.md").read_text(encoding="utf-8")
    assert "- **Status**: failed" in summary
    assert "- **Failed command**: `pytest`" in summary
