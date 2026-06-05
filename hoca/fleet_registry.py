from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from hoca.control_paths import make_fleet_control_paths
from hoca.fleet_contracts import HocaFleetTask, HocaLane, HocaProject
from hoca.git_utils import is_git_repo
from hoca.run_state import read_optional_json, write_json_atomic


class FleetRegistry:
    def __init__(self, *, control_root: Path | None = None) -> None:
        self.paths = make_fleet_control_paths(override=control_root)

    def _load_index(self, path: Path) -> dict[str, dict[str, Any]]:
        raw = read_optional_json(path)
        if not isinstance(raw, dict):
            return {}
        return {
            str(key): value
            for key, value in raw.items()
            if isinstance(key, str) and isinstance(value, dict)
        }

    def _write_index(self, path: Path, data: dict[str, Any]) -> None:
        write_json_atomic(path, data)

    def list_projects(self) -> list[HocaProject]:
        return [
            HocaProject.from_dict(payload)
            for payload in self._load_index(self.paths.projects_json).values()
        ]

    def get_project(self, project_id: str) -> HocaProject | None:
        data = self._load_index(self.paths.projects_json).get(project_id)
        if data is None:
            return None
        return HocaProject.from_dict(data)

    def create_project(self, project: HocaProject) -> None:
        if not is_git_repo(Path(project.repo_path)):
            raise ValueError("Project repository must exist and be a git repository")

        data = self._load_index(self.paths.projects_json)
        if project.project_id in data:
            raise ValueError("Project ID already exists")

        if any(item.get("repo_path") == project.repo_path for item in data.values()):
            raise ValueError("Project repository path already registered")

        payload = project.to_dict()
        data[project.project_id] = payload
        self._write_index(self.paths.projects_json, data)

    def update_project(self, project_id: str, project: HocaProject) -> None:
        data = self._load_index(self.paths.projects_json)
        existing = data.get(project_id)
        if existing is None:
            raise ValueError("Project not found")

        next_project = replace(project, project_id=project_id)

        if next_project.repo_path != existing.get("repo_path"):
            if any(
                existing_project_id != project_id and item.get("repo_path") == next_project.repo_path
                for existing_project_id, item in data.items()
            ):
                raise ValueError("Project repository path already registered")
            if not is_git_repo(Path(next_project.repo_path)):
                raise ValueError("Project repository must exist and be a git repository")

        data[project_id] = next_project.to_dict()
        self._write_index(self.paths.projects_json, data)

    def delete_project(self, project_id: str) -> None:
        data = self._load_index(self.paths.projects_json)
        if project_id not in data:
            raise ValueError("Project not found")
        del data[project_id]
        self._write_index(self.paths.projects_json, data)

        # Best-effort cleanup related tasks/lanes.
        tasks = self._load_index(self.paths.tasks_json)
        tasks = {k: v for k, v in tasks.items() if v.get("project_id") != project_id}
        self._write_index(self.paths.tasks_json, tasks)

        lanes = self._load_index(self.paths.lanes_json)
        lanes = {k: v for k, v in lanes.items() if v.get("project_id") != project_id}
        self._write_index(self.paths.lanes_json, lanes)

    def list_tasks(self, *, project_id: str | None = None) -> list[HocaFleetTask]:
        tasks = [HocaFleetTask.from_dict(value) for value in self._load_index(self.paths.tasks_json).values()]
        if project_id is None:
            return tasks
        return [task for task in tasks if task.project_id == project_id]

    def create_task(self, task: HocaFleetTask) -> None:
        projects = self._load_index(self.paths.projects_json)
        tasks = self._load_index(self.paths.tasks_json)
        if task.project_id not in projects:
            raise ValueError("Task references unknown project")

        if task.task_id in tasks:
            raise ValueError("Task ID already exists")

        for dependency_id in task.dependencies or []:
            if dependency_id not in tasks:
                raise ValueError(f"Unknown dependency task ID: {dependency_id}")

        tasks[task.task_id] = task.to_dict()
        self._write_index(self.paths.tasks_json, tasks)

    def get_task(self, task_id: str) -> HocaFleetTask | None:
        raw = self._load_index(self.paths.tasks_json).get(task_id)
        if raw is None:
            return None
        return HocaFleetTask.from_dict(raw)

    def update_task(self, task_id: str, task: HocaFleetTask) -> None:
        tasks = self._load_index(self.paths.tasks_json)
        if task_id not in tasks:
            raise ValueError("Task not found")
        if task.project_id not in self._load_index(self.paths.projects_json):
            raise ValueError("Task references unknown project")
        for dependency_id in task.dependencies or []:
            if dependency_id not in tasks and dependency_id != task_id:
                raise ValueError(f"Unknown dependency task ID: {dependency_id}")

        tasks[task_id] = replace(task, task_id=task_id).to_dict()
        self._write_index(self.paths.tasks_json, tasks)

    def list_lanes(self, *, task_id: str | None = None, project_id: str | None = None) -> list[HocaLane]:
        lanes = [HocaLane.from_dict(value) for value in self._load_index(self.paths.lanes_json).values()]
        if task_id is not None:
            lanes = [lane for lane in lanes if lane.task_id == task_id]
        if project_id is not None:
            lanes = [lane for lane in lanes if lane.project_id == project_id]
        return lanes

    def get_lane(self, lane_id: str) -> HocaLane | None:
        raw = self._load_index(self.paths.lanes_json).get(lane_id)
        if raw is None:
            return None
        return HocaLane.from_dict(raw)

    def create_lane(self, lane: HocaLane) -> None:
        lanes = self._load_index(self.paths.lanes_json)
        if lane.lane_id in lanes:
            raise ValueError("Lane ID already exists")

        project_exists = lane.project_id in self._load_index(self.paths.projects_json)
        task_exists = lane.task_id in self._load_index(self.paths.tasks_json)
        if not project_exists or not task_exists:
            raise ValueError("Lane references unknown project or task")

        lanes[lane.lane_id] = lane.to_dict()
        self._write_index(self.paths.lanes_json, lanes)

        # Keep task -> lane relationship in the task snapshot.
        tasks = self._load_index(self.paths.tasks_json)
        task = tasks.get(lane.task_id)
        if task is not None:
            lane_ids = list(task.get("lane_ids") or [])
            if lane.lane_id not in lane_ids:
                lane_ids.append(lane.lane_id)
                task["lane_ids"] = lane_ids
                tasks[lane.task_id] = task
                self._write_index(self.paths.tasks_json, tasks)

    def update_lane(self, lane_id: str, lane: HocaLane) -> None:
        lanes = self._load_index(self.paths.lanes_json)
        if lane_id not in lanes:
            raise ValueError("Lane not found")

        if lane.task_id not in self._load_index(self.paths.tasks_json):
            raise ValueError("Lane references unknown task")
        if lane.project_id not in self._load_index(self.paths.projects_json):
            raise ValueError("Lane references unknown project")

        lanes[lane_id] = replace(lane, lane_id=lane_id).to_dict()
        self._write_index(self.paths.lanes_json, lanes)
