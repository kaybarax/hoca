"""Deterministic scope and staging risk checks for validation reports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from hoca.contracts import HocaTaskSpec
from hoca.risk import (
    is_dependency_lockfile,
    is_generated_file,
    is_infrastructure_file,
    is_migration_file,
    justified_files_from_run_dir,
)
from hoca.run_layout import task_spec_path
from hoca.run_state import read_optional_json

_TASK_TOKEN_STOPWORDS = frozenset(
    {
        "task",
        "this",
        "that",
        "with",
        "from",
        "into",
        "file",
        "files",
        "change",
        "changes",
        "update",
        "implement",
        "create",
        "make",
        "fix",
        "safe",
        "stage",
        "staging",
    }
)

@dataclass(frozen=True)
class ValidationRiskAssessment:
    scope_risk: bool
    staging_risk: bool
    out_of_scope_files: tuple[str, ...]
    staging_risk_files: tuple[str, ...]


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def task_tokens_from_text(text: str) -> frozenset[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if len(token) >= 4 and token not in _TASK_TOKEN_STOPWORDS:
            tokens.add(token)
    return frozenset(tokens)


def path_matches_expected_area(path: str, area: str) -> bool:
    lower_file = _normalize_path(path).lower()
    lower_area = _normalize_path(area).lower()
    if not lower_area:
        return False
    return (
        lower_file == lower_area
        or lower_file.startswith(f"{lower_area}/")
        or lower_file.endswith(f"/{lower_area}")
        or f"/{lower_area}/" in f"/{lower_file}/"
    )


def path_matches_task_context(
    path: str,
    *,
    expected_areas: list[str],
    task_tokens: frozenset[str],
    justified_files: set[str],
) -> bool:
    if path in justified_files:
        return True
    if expected_areas and any(
        path_matches_expected_area(path, area) for area in expected_areas if area.strip()
    ):
        return True
    if not task_tokens:
        return not expected_areas
    lower_file = _normalize_path(path).lower()
    return any(token in lower_file for token in task_tokens)


def _load_task_spec(run_dir: Path) -> HocaTaskSpec | None:
    spec_path = task_spec_path(run_dir)
    if not spec_path.is_file():
        return None
    data = read_optional_json(spec_path)
    if data is None:
        return None
    try:
        return HocaTaskSpec.from_dict(data)
    except ValueError:
        return None


def _staging_risk_reason(path: str, justified_files: set[str]) -> str | None:
    if path in justified_files:
        return None
    if is_dependency_lockfile(path):
        return "dependency lockfile change"
    if is_generated_file(path):
        return "generated file change"
    if is_migration_file(path):
        return "migration change"
    if is_infrastructure_file(path):
        return "infrastructure change"
    return None


def assess_validation_risks(
    run_dir: Path,
    changed_files: list[str],
) -> ValidationRiskAssessment:
    spec = _load_task_spec(run_dir)
    expected_areas = list(spec.expected_areas) if spec else []
    task_tokens = task_tokens_from_text(spec.goal if spec else "")
    justified_files = justified_files_from_run_dir(run_dir)

    out_of_scope: list[str] = []
    staging_risk_files: list[str] = []

    for path in changed_files:
        if not path_matches_task_context(
            path,
            expected_areas=expected_areas,
            task_tokens=task_tokens,
            justified_files=justified_files,
        ):
            out_of_scope.append(path)
        staging_reason = _staging_risk_reason(path, justified_files)
        if staging_reason:
            staging_risk_files.append(path)

    monitor = read_optional_json(run_dir / "monitor-result.json") or {}
    stop_reason = monitor.get("stop_reason")
    scope_from_monitor = stop_reason == "scope_violation"

    return ValidationRiskAssessment(
        scope_risk=bool(out_of_scope) or scope_from_monitor,
        staging_risk=bool(staging_risk_files),
        out_of_scope_files=tuple(out_of_scope),
        staging_risk_files=tuple(staging_risk_files),
    )
