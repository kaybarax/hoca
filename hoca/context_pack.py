from __future__ import annotations

import re
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from hoca.control_paths import make_fleet_control_paths
from hoca.run_state import now_iso, write_json_atomic


@dataclass(frozen=True)
class ProjectContext:
    project_id: str
    summary: str
    architecture_map: str
    test_commands: tuple[str, ...]
    release_policies: tuple[str, ...]
    prompt_patterns: tuple[str, ...]
    failure_patterns: tuple[str, ...]


SECRET_HINTS = ("api_key", "api-key", "secret", "token", "password", "private_key", "credential")
MAX_SUMMARY_BYTES = 4096
MAX_LIST_ITEMS = 20


def _context_dir(project_id: str, *, control_root: Path | None = None) -> Path:
    return make_fleet_control_paths(override=control_root).memory_dir / project_id / "context-pack"


def _path(project_id: str, filename: str, *, control_root: Path | None = None) -> Path:
    return _context_dir(project_id, control_root=control_root) / filename


def _safe_text(value: str) -> str:
    scrubbed = value
    for token in SECRET_HINTS:
        pattern = re.compile(rf"({re.escape(token)})\s*[:=]\s*[^\s]+", re.IGNORECASE)
        scrubbed = pattern.sub(r"\1=***redacted***", scrubbed)
    return scrubbed if scrubbed == value else scrubbed


def _read_lines(payload_path: Path) -> tuple[str, ...]:
    if not payload_path.is_file():
        return ()
    try:
        raw = payload_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        if isinstance(payload, dict):
            values = payload.get("items", ())
            if isinstance(values, list):
                return tuple(
                    _safe_text(str(value).strip())
                    for value in values
                    if value is not None and str(value).strip()
                )
        return tuple(
            _safe_text(str(line).strip()) for line in raw.splitlines() if str(line).strip()
        )
    except OSError:
        return ()

    except json.JSONDecodeError:
        return ()


def _write_lines(payload_path: Path, values: Iterable[str], *, max_items: int) -> None:
    bounded = [_safe_text(value) for value in values][:max_items]
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        payload_path,
        {
            "items": bounded,
            "updated_at": now_iso(),
        },
    )


def _append_line(payload_path: Path, value: str, *, max_items: int) -> None:
    existing = list(_read_lines(payload_path))
    next_values = existing + [_safe_text(value)]
    deduped: list[str] = []
    for item in next_values:
        if item not in deduped:
            deduped.append(item)
    _write_lines(payload_path, deduped[-max_items:], max_items=max_items)


def update_context_summary(
    project_id: str,
    summary: str,
    *,
    control_root: Path | None = None,
) -> Path:
    target = _path(project_id, "project-summary.txt", control_root=control_root)
    data = _safe_text(summary.strip())
    if len(data.encode("utf-8")) > MAX_SUMMARY_BYTES:
        data = data[:MAX_SUMMARY_BYTES]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data, encoding="utf-8")
    return target


def update_architecture_map(
    project_id: str,
    architecture_map: str,
    *,
    control_root: Path | None = None,
) -> Path:
    target = _path(project_id, "architecture-map.txt", control_root=control_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_safe_text(architecture_map.strip()), encoding="utf-8")
    return target


def append_test_command(
    project_id: str,
    command: str,
    *,
    control_root: Path | None = None,
    max_items: int = MAX_LIST_ITEMS,
) -> Path:
    target = _path(project_id, "test-command-memory.json", control_root=control_root)
    _append_line(target, command, max_items=max_items)
    return target


def append_release_policy(
    project_id: str,
    policy: str,
    *,
    control_root: Path | None = None,
    max_items: int = MAX_LIST_ITEMS,
) -> Path:
    target = _path(project_id, "release-policy.json", control_root=control_root)
    _append_line(target, policy, max_items=max_items)
    return target


def append_prompt_pattern(
    project_id: str,
    pattern: str,
    *,
    control_root: Path | None = None,
    max_items: int = MAX_LIST_ITEMS,
) -> Path:
    target = _path(project_id, "prompt-patterns.json", control_root=control_root)
    _append_line(target, pattern, max_items=max_items)
    return target


def append_failure_pattern(
    project_id: str,
    pattern: str,
    *,
    control_root: Path | None = None,
    max_items: int = MAX_LIST_ITEMS,
) -> Path:
    target = _path(project_id, "failure-patterns.json", control_root=control_root)
    _append_line(target, pattern, max_items=max_items)
    return target


def load_project_context_pack(
    project_id: str,
    *,
    control_root: Path | None = None,
) -> ProjectContext:
    return ProjectContext(
        project_id=project_id,
        summary=(
            _path(project_id, "project-summary.txt", control_root=control_root).read_text(
                encoding="utf-8"
            )
            if _path(project_id, "project-summary.txt", control_root=control_root).is_file()
            else ""
        ),
        architecture_map=(
            _path(project_id, "architecture-map.txt", control_root=control_root).read_text(
                encoding="utf-8"
            )
            if _path(project_id, "architecture-map.txt", control_root=control_root).is_file()
            else ""
        ),
        test_commands=_read_lines(
            _path(project_id, "test-command-memory.json", control_root=control_root)
        ),
        release_policies=_read_lines(
            _path(project_id, "release-policy.json", control_root=control_root)
        ),
        prompt_patterns=_read_lines(
            _path(project_id, "prompt-patterns.json", control_root=control_root)
        ),
        failure_patterns=_read_lines(
            _path(project_id, "failure-patterns.json", control_root=control_root)
        ),
    )
