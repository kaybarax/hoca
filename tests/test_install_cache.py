from __future__ import annotations

from pathlib import Path

from hoca.install_cache import install_current, install_fingerprint, marker_path, write_marker


def write_project(project: Path, lockfile: str = "lock-v1") -> None:
    project.mkdir(exist_ok=True)
    (project / "package.json").write_text('{"scripts":{"test":"vitest"}}\n', encoding="utf-8")
    (project / "pnpm-lock.yaml").write_text(lockfile + "\n", encoding="utf-8")


def test_install_cache_marker_lives_under_hoca_runtime(tmp_path: Path) -> None:
    write_project(tmp_path)

    marker = marker_path(tmp_path, "pnpm")

    assert marker == tmp_path / ".hoca-runtime" / "install-cache" / "pnpm.sha256"


def test_install_cache_requires_node_modules_and_matching_marker(tmp_path: Path) -> None:
    write_project(tmp_path)
    write_marker(tmp_path, "pnpm")

    assert install_current(tmp_path, "pnpm") is False

    (tmp_path / "node_modules").mkdir()

    assert install_current(tmp_path, "pnpm") is True


def test_install_cache_changes_when_lockfile_changes(tmp_path: Path) -> None:
    write_project(tmp_path)
    before = install_fingerprint(tmp_path, "pnpm")

    (tmp_path / "pnpm-lock.yaml").write_text("lock-v2\n", encoding="utf-8")

    assert install_fingerprint(tmp_path, "pnpm") != before
