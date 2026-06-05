from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from hoca.control_paths import make_fleet_control_paths
from hoca.fleet_contracts import HocaLaneLease
from hoca.git_utils import is_git_repo
from hoca.worktree import (
    create_worktree,
    remove_worktree,
    validate_worktree_path,
    worktree_base,
    worktree_changed_files,
    worktree_path,
)


DEFAULT_TTL_SECONDS = 3600


def _now_iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_datetime(value: str, *, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid {field} timestamp: {value!r}") from exc


class WorktreeLeasePool:
    """Persisted lease tracking for worktrees created by HOCA.

    The implementation stores only lease metadata and keeps the actual worktree
    creation/removal flow in the existing :mod:`hoca.worktree` module.
    """

    def __init__(
        self,
        *,
        control_root: Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.paths = make_fleet_control_paths(override=control_root)
        self.ttl_seconds = ttl_seconds

    @property
    def leases_path(self) -> Path:
        return self.paths.resource_state_json.with_name("lane-leases.json")

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if not self.leases_path.is_file():
            return {}
        raw = self.leases_path.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        data = payload.get("leases")
        if not isinstance(data, dict):
            return {}
        return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}

    def _write_index(self, data: dict[str, dict[str, Any]]) -> None:
        self.leases_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"leases": data}
        temp_path = self.leases_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp_path.replace(self.leases_path)

    def list_leases(self) -> list[HocaLaneLease]:
        return [HocaLaneLease.from_dict(item) for item in self._load_index().values()]

    def get_lease(self, lease_id: str) -> HocaLaneLease | None:
        raw = self._load_index().get(lease_id)
        if raw is None:
            return None
        return HocaLaneLease.from_dict(raw)

    def create_lease(
        self,
        *,
        lane_id: str,
        project_id: str,
        task_id: str,
        branch: str,
        base_ref: str,
        project_path: Path,
        lease_id: str,
        process_id: int | None = None,
    ) -> HocaLaneLease:
        if lease_id in self._load_index():
            raise ValueError(f"Lease already exists: {lease_id}")

        if not is_git_repo(project_path):
            raise ValueError("Project repository must exist and be a git repository")

        # Store every lease path under project runtime worktrees and let the existing
        # implementation handle low-level creation.
        wt = worktree_path(project_path, lease_id)
        if not validate_worktree_path(project_path, wt):
            raise ValueError(f"Worktree path escapes runtime directory: {wt}")

        create_worktree(project_path, lease_id, branch)

        now = _now_iso_now()
        lease = HocaLaneLease(
            lease_id=lease_id,
            lane_id=lane_id,
            project_id=project_id,
            task_id=task_id,
            branch=branch,
            base_ref=base_ref,
            worktree_path=str(wt),
            acquired_at=now,
            heartbeat_at=now,
            expires_at=(
                (datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            ),
            process_id=process_id,
        )

        leases = self._load_index()
        leases[lease_id] = lease.to_dict()
        self._write_index(leases)
        return lease

    def renew_lease(self, lease_id: str) -> HocaLaneLease:
        leases = self._load_index()
        raw = leases.get(lease_id)
        if raw is None:
            raise ValueError(f"Lease not found: {lease_id}")
        lease = HocaLaneLease.from_dict(raw)
        now = _now_iso_now()
        updated = replace(
            lease,
            heartbeat_at=now,
            expires_at=(datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        )
        leases[lease_id] = updated.to_dict()
        self._write_index(leases)
        return updated

    def release_lease(
        self,
        lease_id: str,
        *,
        project_path: Path,
        force: bool = False,
    ) -> bool:
        leases = self._load_index()
        raw = leases.get(lease_id)
        if raw is None:
            raise ValueError(f"Lease not found: {lease_id}")
        lease = HocaLaneLease.from_dict(raw)
        wt = Path(lease.worktree_path).resolve()
        if not validate_worktree_path(project_path, wt):
            raise ValueError(f"Worktree path escapes runtime directory: {wt}")

        run_id = wt.name
        if wt.exists() and not force:
            changed_files = _list_changed_files(project_path, run_id)
            if changed_files:
                raise ValueError("Refusing to remove unclean worktree without force")

        cleaned = remove_worktree(project_path, run_id)
        leases.pop(lease_id, None)
        self._write_index(leases)
        return cleaned

    def stale_leases(
        self,
        *,
        reference: str | None = None,
    ) -> list[HocaLaneLease]:
        now = _ensure_datetime(reference or _now_iso_now(), field="now")
        stale: list[HocaLaneLease] = []
        for lease in self.list_leases():
            expiry = lease.expires_at
            if not expiry:
                continue
            try:
                expires_at = _ensure_datetime(expiry, field="lease.expires_at")
            except ValueError:
                stale.append(lease)
                continue
            if expires_at <= now:
                stale.append(lease)
        return stale

    def stale_worktree_report(
        self,
        *,
        reference: str | None = None,
    ) -> list[dict[str, str]]:
        return [lease.to_dict() for lease in self.stale_leases(reference=reference)]

    def cleanup_stale_worktrees(
        self,
        *,
        project_path: Path,
        reference: str | None = None,
        dry_run: bool = False,
        remove_completed: bool = False,
        completed_lane_ids: list[str] | None = None,
        remove_abandoned: bool = False,
        confirm_abandoned: bool = False,
    ) -> list[str]:
        """
        Remove worktrees with stale leases and optionally by lane status.

        - completed_lane_ids: list of lane IDs known as completed.
        - remove_abandoned requires an explicit confirmation flag.
        """
        completed_lane_ids = completed_lane_ids or []
        stale = {lease.lease_id for lease in self.stale_leases(reference=reference)}
        active = self._load_index().copy()

        remove_candidates: list[str] = []

        for lease_id, raw in list(active.items()):
            lease = HocaLaneLease.from_dict(raw)
            if lease_id in stale:
                remove_candidates.append(lease_id)
                continue
            if remove_completed and lease.lane_id in completed_lane_ids:
                remove_candidates.append(lease_id)
                continue
            if remove_abandoned and lease.lane_id not in completed_lane_ids:
                if confirm_abandoned:
                    remove_candidates.append(lease_id)

        if dry_run:
            return sorted(remove_candidates)

        removed: list[str] = []
        for lease_id in remove_candidates:
            try:
                self.release_lease(lease_id, project_path=project_path, force=True)
            except ValueError:
                continue
            removed.append(lease_id)
        return sorted(removed)


def _list_changed_files(project_path: Path, run_id: str) -> list[str]:
    return worktree_changed_files(project_path, run_id)


def slugify(text: str, *, max_length: int = 48) -> str:
    """Create a compact branch-safe slug."""
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in text.strip().lower())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-")
    return cleaned[:max_length] or "task"


def lane_short_id(lane_id: str, *, fallback: str = "lane") -> str:
    short = "".join(ch if ch.isalnum() else "" for ch in lane_id)[-12:]
    return short or fallback


def _ref_exists(project_path: Path, ref: str, *, remote: bool = False) -> bool:
    check = [
        "git",
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/{'heads' if not remote else 'remotes/origin'}/{ref}",
    ]
    result = subprocess.run(check, cwd=str(project_path), capture_output=True, text=True)
    return result.returncode == 0


def generate_lane_branch(
    project_path: Path,
    task_slug: str,
    lane_id: str,
    *,
    prefix: str = "hoca",
    check_remote: bool = True,
) -> str:
    if not task_slug:
        raise ValueError("Task slug must not be empty")
    base = slugify(task_slug)
    short = lane_short_id(lane_id)
    base_name = f"{prefix}/{base}-{short}"

    candidate = base_name
    counter = 1
    while _ref_exists(project_path, candidate, remote=False) or (
        check_remote and _ref_exists(project_path, candidate, remote=True)
    ):
        candidate = f"{base_name}-{counter}"
        counter += 1
        if counter > 1000:
            raise ValueError(f"Unable to allocate unique branch name: {base_name}")
    return candidate


def _worktree_safe_relative_to_root(project_path: Path, candidate: Path) -> bool:
    base = worktree_base(project_path)
    try:
        return (
            candidate == base
            or candidate.is_relative_to(base)
            or str(candidate).startswith(str(base) + "/")
        )
    except OSError:
        return False


def prune_orphaned_worktrees(
    project_path: Path,
    *,
    managed_roots: list[str],
    dry_run: bool = False,
) -> list[str]:
    """
    Remove managed worktrees that are not tracked by active leases.
    """
    base = worktree_base(project_path)
    if not base.exists():
        return []
    managed = {
        Path(path).resolve()
        for path in managed_roots
        if _worktree_safe_relative_to_root(project_path, Path(path))
    }
    candidates: list[Path] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        if child.resolve() in managed:
            continue
        candidates.append(child)

    removed: list[str] = []
    for child in candidates:
        if dry_run:
            removed.append(str(child))
            continue
        try:
            shutil.rmtree(child)
        except OSError:
            pass
        removed.append(str(child))
    return sorted(removed)
