from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoca.env_allowlist import PhaseRole, filter_env_for_role, redact_env_for_logging
from hoca.fleet_contracts import HocaAgentAdapterSpec
from hoca.security import is_secret_like_path


class AdapterError(RuntimeError):
    pass


class AdapterCommandError(AdapterError):
    pass


class AdapterUnavailableError(AdapterError):
    pass


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


_ALLOWED_COMMAND_PATTERN = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*?)}")
_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=$")
_PATHY_PLACEHOLDER_HINTS = (
    "path",
    "dir",
    "worktree",
    "project",
    "repo",
    "runtime",
    "root",
    "root_dir",
)


def _now() -> str:
    return str(int(time.time()))


@dataclass(frozen=True)
class AdapterRunArtifact:
    return_code: int
    stdout: str
    stderr: str
    command: str
    session_id: str
    run_dir: str
    lane_id: str | None = None
    task_id: str | None = None
    task: str | None = None
    project_id: str | None = None
    project_path: str | None = None
    metadata: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "return_code": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "command": self.command,
            "session_id": self.session_id,
            "run_dir": self.run_dir,
            "lane_id": self.lane_id,
            "task_id": self.task_id,
            "task": self.task,
            "project_id": self.project_id,
            "project_path": self.project_path,
            "metadata": self.metadata or {},
        }


@dataclass(frozen=True)
class LiveAdapterSession:
    session_id: str
    lane_id: str
    adapter_id: str
    status: str
    command: str
    metadata: dict[str, str]
    process: subprocess.Popen[str]


def _is_path_like_placeholder(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _PATHY_PLACEHOLDER_HINTS)


def _placeholder_sample(name: str) -> str:
    if _is_path_like_placeholder(name):
        return "/tmp/work"
    return "value"


def fake_session_id() -> str:
    return f"lane-session-{_now()}"


def redact_env_for_session(env: dict[str, str]) -> dict[str, str]:
    return redact_env_for_logging(env)


def _template_fields(template: str) -> set[str]:
    return set(_ALLOWED_COMMAND_PATTERN.findall(template))


def _strip_placeholders(value: str) -> str:
    return _ALLOWED_COMMAND_PATTERN.sub("", value)


def _coerce_template_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _fake_template_values(template: str) -> dict[str, str]:
    return {field: _placeholder_sample(field) for field in _template_fields(template)}


def format_command(
    template: str,
    *,
    values: dict[str, object],
) -> str:
    allowed = _template_fields(template)
    sanitized = {key: _coerce_template_value(values[key]) for key in allowed}
    return template.format(**sanitized)


def _available_binary(binary: str) -> bool:
    if not binary:
        return False
    if os.path.isabs(binary):
        return Path(binary).is_file() and os.access(binary, os.X_OK)
    return shutil.which(binary) is not None


def _first_command_and_aliases(template_command: str) -> list[str]:
    tokens = shlex.split(template_command)
    if not tokens:
        return []

    command_candidates: list[str] = []
    for token in tokens:
        if _ENV_KEY_PATTERN.match(token):
            continue
        base_command = token
        # remove placeholders if they were embedded in path components
        stripped = _strip_placeholders(base_command)

        for candidate in (base_command, stripped):
            normalized = os.path.normpath(candidate)
            if normalized:
                command_candidates.append(normalized)
            if "/" in normalized:
                base = normalized.rsplit("/", 1)[-1]
                if base:
                    command_candidates.append(base)
        break

    return command_candidates


def _normalize_allowed_command(command: str) -> str:
    if not command:
        return ""
    return os.path.basename(os.path.normpath(command))


def _is_command_allowed(command: str, allowlist: list[str]) -> bool:
    allowed = {_normalize_allowed_command(item) for item in allowlist if item}
    if not allowed:
        return False

    normalized = _normalize_allowed_command(command)
    if command in allowlist or normalized in allowed:
        return True
    if not os.path.isabs(command):
        return normalized in allowed
    if any(
        os.path.isabs(item) and os.path.normpath(item) == os.path.normpath(command)
        for item in allowlist
    ):
        return True
    return False


def required_commands_from_template(template: str) -> list[str]:
    """Return candidate commands required by ``template``.

    We intentionally use placeholder samples for detection only; this keeps checks
    deterministic when paths are rendered from variables.
    """
    filled = format_command(template, values=_fake_template_values(template))
    return _first_command_and_aliases(filled)


def missing_required_commands(spec: HocaAgentAdapterSpec) -> list[str]:
    missing: list[str] = []
    command_groups: list[list[str]] = []
    for command in _first_command_and_aliases(
        format_command(spec.command_template, values=_fake_template_values(spec.command_template))
    ):
        if any(command in group for group in command_groups):
            continue
        normalized = _normalize_allowed_command(command)
        matching_group = next(
            (
                group
                for group in command_groups
                if normalized and normalized in {_normalize_allowed_command(item) for item in group}
            ),
            None,
        )
        if matching_group is None:
            command_groups.append([command])
        else:
            matching_group.append(command)

    for group in command_groups:
        if not any(_available_binary(command) for command in group):
            missing.extend(group)
    return missing


def required_commands_ok(spec: HocaAgentAdapterSpec) -> bool:
    return not missing_required_commands(spec)


def adapter_doctor_lines(spec: HocaAgentAdapterSpec | None = None) -> list[tuple[str, str]]:
    active_spec = spec or default_openhands_adapter_spec()
    missing = missing_required_commands(active_spec)
    required = required_commands_from_template(active_spec.command_template)
    if missing:
        return [
            (
                "fail",
                f"Adapter {active_spec.adapter_id!r} is missing required commands: {', '.join(sorted(set(missing)))}",
            )
        ]
    return [
        (
            "ok",
            f"Adapter {active_spec.adapter_id!r} required commands available: {', '.join(required)}",
        )
    ]


def session_metadata_from_spec(spec: HocaAgentAdapterSpec) -> dict[str, str]:
    return {
        "provider": spec.provider,
        "command_template": spec.command_template,
        "max_concurrency": str(spec.max_concurrency),
        "runtime_home": spec.runtime_home or "",
        "default_for_tasks": ",".join(spec.default_for_tasks or []),
        "capabilities": ",".join(spec.capabilities or []),
    }


def default_openhands_adapter_spec(
    *,
    adapter_id: str = "openhands-hermes",
    script_path: Path | None = None,
) -> HocaAgentAdapterSpec:
    script = str(
        (
            script_path
            if script_path is not None
            else Path(__file__).resolve().parents[1] / "scripts" / "run-lane-agent.sh"
        )
    )
    return HocaAgentAdapterSpec(
        adapter_id=adapter_id,
        provider="openhands",
        command_template=(
            f"{shlex.quote(script)} --project-path {{project_path}} --task {{task}} "
            "--lane-id {lane_id} --task-id {task_id} --project-id {project_id} "
            "--run-dir {run_dir}"
        ),
        max_concurrency=1,
        default_for_tasks=["coding", "review"],
        capabilities=["coding", "mid_task_send", "status", "collect", "stop"],
        created_at=_now_iso(),
    )


def custom_command_adapter_spec(
    *,
    adapter_id: str,
    provider: str,
    command_template: str,
    max_concurrency: int = 1,
    command_allowlist: list[str] | None = None,
    capabilities: list[str] | None = None,
    default_for_tasks: list[str] | None = None,
) -> HocaAgentAdapterSpec:
    normalized = [
        item.strip()
        for item in (command_allowlist or required_commands_from_template(command_template))
        if item.strip()
    ]
    return HocaAgentAdapterSpec(
        adapter_id=adapter_id,
        provider=provider,
        command_template=command_template,
        command_allowlist=normalized,
        max_concurrency=max_concurrency,
        default_for_tasks=default_for_tasks or [],
        capabilities=capabilities or [],
        created_at=_now_iso(),
    )


class AgentAdapter:
    def __init__(self, *, spec: HocaAgentAdapterSpec) -> None:
        self.spec = spec

        if spec.provider != "openhands":
            if not spec.command_allowlist:
                raise AdapterUnavailableError(
                    f"Adapter '{spec.adapter_id}' requires command_allowlist for non-openhands adapters"
                )
            for required in required_commands_from_template(spec.command_template):
                if not _is_command_allowed(required, spec.command_allowlist):
                    raise AdapterUnavailableError(
                        f"Adapter '{spec.adapter_id}' command '{required}' must not be allow-listed"
                    )

        missing = missing_required_commands(spec)
        if missing:
            raise AdapterUnavailableError(
                f"Adapter '{spec.adapter_id}' is unavailable: missing {', '.join(sorted(set(missing)))}"
            )

    @property
    def adapter_id(self) -> str:
        return self.spec.adapter_id

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(self.spec.capabilities or ())

    @property
    def required_commands(self) -> tuple[str, ...]:
        return tuple(required_commands_from_template(self.spec.command_template))

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def _make_env(
        self,
        *,
        extra_env: dict[str, str] | None = None,
        role: PhaseRole = "worker",
    ) -> dict[str, str]:
        merged = dict(os.environ)
        if extra_env:
            merged.update(extra_env)
        filtered = filter_env_for_role(merged, phase=role)
        filtered["HOCA_ADAPTER_ID"] = self.spec.adapter_id
        filtered["HOCA_ADAPTER_PROVIDER"] = self.spec.provider
        filtered["HOCA_ADAPTER_STARTED_AT"] = _now_iso()
        return filtered

    def _render(self, **values: object) -> str:
        for key in ["worktree_path", "project_path", "run_dir", "task"]:
            if key in values:
                path = Path(_coerce_template_value(values[key]))
                if str(path).strip() and is_secret_like_path(path):
                    raise AdapterCommandError(f"Refusing to use secret-like path for {key}: {path}")
        return format_command(self.spec.command_template, values=values)

    def start(
        self,
        *,
        session_id: str,
        lane_id: str,
        project_path: Path,
        task: str,
        run_dir: Path,
        task_id: str | None = None,
        project_id: str | None = None,
        worktree_path: Path | None = None,
        extra_env: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> LiveAdapterSession:
        if not project_path.is_dir():
            raise AdapterCommandError(f"Project path does not exist: {project_path}")

        run_dir.mkdir(parents=True, exist_ok=True)
        worktree = worktree_path or project_path
        if not Path(worktree).exists():
            raise AdapterCommandError(f"Worktree path does not exist: {worktree}")
        if not str(Path(worktree).resolve()).startswith(str(Path(project_path).resolve())):
            raise AdapterCommandError("worktree_path must be inside project_path")

        command_text = self._render(
            project_path=project_path,
            worktree_path=worktree,
            run_dir=run_dir,
            task=task,
            task_id=task_id or "",
            project_id=project_id or "",
            lane_id=lane_id,
        )

        try:
            command = shlex.split(command_text)
        except ValueError as exc:
            raise AdapterCommandError(f"Invalid adapter command: {command_text}") from exc

        if not command:
            raise AdapterCommandError("Adapter command is empty")

        stdout_log = run_dir / "adapter-stdout.log"
        stderr_log = run_dir / "adapter-stderr.log"
        with (
            stdout_log.open("w", encoding="utf-8") as out_f,
            stderr_log.open("w", encoding="utf-8") as err_f,
        ):
            process = subprocess.Popen(
                command,
                cwd=str(worktree),
                env=self._make_env(extra_env=extra_env),
                stdout=out_f,
                stderr=err_f,
                stdin=subprocess.PIPE,
                text=True,
            )

        session_metadata = {
            "start_time": _now_iso(),
            "command": command_text,
            "lane_id": lane_id,
            "task_id": task_id or "",
            "project_id": project_id or "",
            "task": task,
            "project_path": str(project_path),
            "worktree_path": str(worktree),
            "run_dir": str(run_dir),
            "adapter_id": self.spec.adapter_id,
            "status": "running",
        }
        session_metadata.update(session_metadata_from_spec(self.spec))
        if extra_env and "OPENAI_API_KEY" in extra_env:
            session_metadata["openai"] = extra_env["OPENAI_API_KEY"]
        if metadata:
            session_metadata.update(metadata)

        return LiveAdapterSession(
            session_id=session_id,
            lane_id=lane_id,
            adapter_id=self.spec.adapter_id,
            status="running",
            command=command_text,
            metadata=session_metadata,
            process=process,
        )

    def status(self, session: LiveAdapterSession) -> str:
        if session.process.poll() is None:
            return "running"
        return "completed" if session.process.returncode == 0 else "failed"

    def stop(self, session: LiveAdapterSession) -> bool:
        if session.process.poll() is not None:
            return False
        if session.process.stdin is not None and not session.process.stdin.closed:
            try:
                session.process.stdin.close()
            except OSError:
                pass
        try:
            session.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            session.process.terminate()
            try:
                session.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                session.process.kill()
                session.process.wait(timeout=5)
        try:
            session.process.wait(timeout=0)
        except subprocess.TimeoutExpired:
            pass
        return True

    def send(self, session: LiveAdapterSession, payload: str) -> None:
        if session.process.stdin is None:
            raise AdapterCommandError("Session does not accept input redirection")
        run_dir = Path(session.metadata.get("run_dir") or "")
        stderr_log = run_dir / "adapter-stderr.log" if run_dir else None
        if stderr_log is not None:
            stderr_log.parent.mkdir(parents=True, exist_ok=True)
            with stderr_log.open("a", encoding="utf-8") as err_f:
                err_f.write(f"{payload}\n")

    def collect(
        self,
        session: LiveAdapterSession,
        run_dir: Path,
        *,
        session_dir: Path | None = None,
    ) -> AdapterRunArtifact:
        session.process.wait()
        out_path = run_dir / "adapter-stdout.log"
        err_path = run_dir / "adapter-stderr.log"
        status_json = run_dir / "status.json"
        artifact_json = run_dir / "adapter-artifact.json"
        metadata = dict(session.metadata)

        if artifact_json.is_file():
            try:
                loaded = artifact_json.read_text(encoding="utf-8")
                artifact_data = json.loads(loaded)
            except Exception:
                artifact_data = {}
            if isinstance(artifact_data, dict):
                artifact_metadata = {
                    str(key): str(value)
                    for key, value in artifact_data.items()
                    if isinstance(key, str)
                }
                metadata.update(artifact_metadata)

        if status_json.is_file():
            try:
                status = json.loads(status_json.read_text(encoding="utf-8"))
            except Exception:
                status = {}
            if isinstance(status, dict):
                for key in ["status", "pr_url", "task", "lane_id", "project_id"]:
                    value = status.get(key)
                    if isinstance(value, str):
                        metadata[key] = value

        if (
            session_dir is not None
            and session_dir.is_dir()
            and (session_dir / "lane.json").is_file()
        ):
            metadata["session_dir"] = str(session_dir)

        return AdapterRunArtifact(
            return_code=session.process.returncode or 0,
            stdout=out_path.read_text(encoding="utf-8") if out_path.is_file() else "",
            stderr=err_path.read_text(encoding="utf-8") if err_path.is_file() else "",
            command=session.command,
            session_id=session.session_id,
            run_dir=str(run_dir),
            lane_id=session.lane_id,
            task_id=session.metadata.get("task_id") or None,
            task=session.metadata.get("task") or None,
            project_id=session.metadata.get("project_id") or None,
            project_path=session.metadata.get("project_path") or None,
            metadata=metadata,
        )


def _doctor_main() -> int:
    failed = False
    for status, message in adapter_doctor_lines():
        print(f"[{status.upper()}] {message}")
        if status == "fail":
            failed = True
    return 1 if failed else 0


def _main() -> int:
    parser = argparse.ArgumentParser(description="Adapter command checks for HOCA doctor.")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor-checks", help="Print adapter command availability checks.")
    args = parser.parse_args()

    if args.command == "doctor-checks":
        return _doctor_main()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
