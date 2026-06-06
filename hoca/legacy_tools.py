from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


REMOVED_TOOL_TERMS = ("aid" + "er",)
EXCLUDED_DIRS = frozenset({".git", ".idea", ".pytest_cache", ".ruff_cache", ".venv", "venv"})


@dataclass(frozen=True)
class LegacyToolFinding:
    path: str
    line_number: int
    term: str


def scan_removed_tool_references(
    root: Path, *, terms: tuple[str, ...] = REMOVED_TOOL_TERMS
) -> list[LegacyToolFinding]:
    findings: list[LegacyToolFinding] = []
    lowered_terms = tuple(term.lower() for term in terms)
    for path in _candidate_files(root):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            for term, lowered_term in zip(terms, lowered_terms, strict=True):
                if lowered_term in lowered:
                    findings.append(
                        LegacyToolFinding(
                            path=str(relative),
                            line_number=line_number,
                            term=term,
                        )
                    )
    return findings


def _candidate_files(root: Path) -> list[Path]:
    git_dir = root / ".git"
    if git_dir.exists():
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return [
                root / line
                for line in result.stdout.splitlines()
                if line.strip() and (root / line).is_file()
            ]
    return [path for path in sorted(root.rglob("*")) if path.is_file()]
