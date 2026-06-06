from pathlib import Path

from click.testing import CliRunner

from hoca.cli import main
from hoca.legacy_tools import scan_removed_tool_references


def _removed_tool_name() -> str:
    return "aid" + "er"


def test_scan_removed_tool_references_finds_legacy_text(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text(f"Remove {_removed_tool_name()} notes\n", encoding="utf-8")

    findings = scan_removed_tool_references(tmp_path)

    assert len(findings) == 1
    assert findings[0].path == "note.txt"
    assert findings[0].line_number == 1


def test_fleet_legacy_check_passes_when_clean(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("clean\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["fleet", "legacy-check", "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert "Legacy removed-tool check passed." in result.output


def test_fleet_legacy_check_fails_when_reference_exists(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text(f"legacy {_removed_tool_name()}\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["fleet", "legacy-check", "--root", str(tmp_path)])

    assert result.exit_code != 0
    assert "note.txt:1: removed tool reference" in result.output
