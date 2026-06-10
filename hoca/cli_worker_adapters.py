from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from hoca.env_allowlist import filter_env_for_role
from hoca.monitor import monitor_process
from hoca.run_artifacts import record_worker_attempt
from hoca.run_layout import ensure_run_layout
from hoca.worker_direct import build_worker_direct_prompt
from hoca.worker_hermes import (
    WorkerRunResult,
    _infer_worker_status,
    _missing_profile_attempt_status,
    load_task_spec,
)


class CliWorkerAdapterUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class CliWorkerAdapterSpec:
    engine: str
    command: tuple[str, ...]
    missing_cli_name: str


def claude_worker_adapter_spec() -> CliWorkerAdapterSpec:
    return CliWorkerAdapterSpec(
        engine="claude-code",
        command=("claude", "-p"),
        missing_cli_name="claude CLI",
    )


def codex_worker_adapter_spec() -> CliWorkerAdapterSpec:
    return CliWorkerAdapterSpec(
        engine="codex",
        command=("codex", "exec", "--sandbox", "workspace-write"),
        missing_cli_name="codex CLI",
    )


def _write_monitor_result(run_dir: Path, result) -> None:
    (run_dir / "monitor-result.json").write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "openhands-exit-code.txt").write_text(str(result.exit_code) + "\n", encoding="utf-8")


def run_cli_worker_adapter(
    *,
    spec: CliWorkerAdapterSpec,
    project_path: Path,
    task_spec_path: Path,
    run_dir: Path,
    round_number: int,
    repair_brief: str | None = None,
) -> WorkerRunResult:
    binary = spec.command[0]
    if shutil.which(binary) is None:
        raise CliWorkerAdapterUnavailable(f"{spec.missing_cli_name} is not installed or not on PATH")

    project_path = project_path.resolve()
    task_spec_path = task_spec_path.resolve()
    run_dir = run_dir.resolve()
    ensure_run_layout(run_dir)
    task_spec = load_task_spec(task_spec_path)
    prompt = build_worker_direct_prompt(
        spec=task_spec,
        project_path=project_path,
        run_dir=run_dir,
        round_number=round_number,
        task_spec_path=task_spec_path,
        repair_brief=repair_brief,
    )
    prompt_path = run_dir / "prompts" / f"worker-{spec.engine}-prompt-{round_number}.txt"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")

    env = filter_env_for_role(os.environ.copy(), phase="worker")
    env["HOCA_AGENT_ROLE"] = "worker"
    env.setdefault("HOCA_PYTHON", sys.executable)
    output_path = run_dir / "openhands-output.log"
    stderr_path = run_dir / "openhands-stderr.log"
    command = [*spec.command, prompt]
    with output_path.open("w", encoding="utf-8") as output_file, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=project_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
        )
        monitor_result = monitor_process(
            process,
            project_path=str(project_path),
            run_dir=run_dir,
            timeout_seconds=int(env.get("HOCA_OPENHANDS_TIMEOUT", "600")),
            stall_seconds=int(env.get("HOCA_OPENHANDS_STALL", "300")),
            output_file=output_file,
            actor_role="worker",
        )
        if process.stdout is not None:
            process.stdout.close()
        output_file.write("")
    if monitor_result.stop_reason != "completed" and monitor_result.exit_code == 0:
        monitor_result = replace(monitor_result, exit_code=1)
    _write_monitor_result(run_dir, monitor_result)
    status = _infer_worker_status(run_dir, process_exit_code=monitor_result.exit_code)
    status = _missing_profile_attempt_status(
        run_dir,
        round_number=round_number,
        process_exit_code=monitor_result.exit_code,
        inferred_status=status,
        project_path=project_path,
    )
    attempt_path = record_worker_attempt(
        run_dir,
        round_number=round_number,
        status=status,
        mode=spec.engine,
        project_path=project_path,
        summary=[f"{spec.engine} worker adapter completed with status {status}."],
    )
    return WorkerRunResult(
        mode=spec.engine,
        exit_code=monitor_result.exit_code,
        worker_attempt_path=attempt_path,
        hermes_stdout_path=None,
        hermes_stderr_path=None,
    )


def run_claude_worker(
    *,
    project_path: Path,
    task_spec_path: Path,
    run_dir: Path,
    round_number: int,
    repair_brief: str | None = None,
) -> WorkerRunResult:
    return run_cli_worker_adapter(
        spec=claude_worker_adapter_spec(),
        project_path=project_path,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=round_number,
        repair_brief=repair_brief,
    )


def run_codex_worker(
    *,
    project_path: Path,
    task_spec_path: Path,
    run_dir: Path,
    round_number: int,
    repair_brief: str | None = None,
) -> WorkerRunResult:
    return run_cli_worker_adapter(
        spec=codex_worker_adapter_spec(),
        project_path=project_path,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=round_number,
        repair_brief=repair_brief,
    )
