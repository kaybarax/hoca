"""Direct worker-mode prompt composition."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hoca.contracts import HocaTaskSpec
from hoca.run_layout import ensure_run_layout
from hoca.subprocess_utils import CommandResult
from hoca.worker_hermes import (
    WorkerRunResult,
    _ensure_worker_attempt_report,
    _completed_despite_finalization_stall,
    _infer_worker_status,
    _missing_profile_attempt_status,
    _redact_secret_like_lines,
    load_task_spec,
)


def _list_section(title: str, items: list[str]) -> str:
    if not items:
        return f"{title}:\n- (none)\n"
    return f"{title}:\n" + "\n".join(f"- {item}" for item in items) + "\n"


def _models_section(spec: HocaTaskSpec) -> str:
    models = spec.models
    return (
        "models:\n"
        f"- manager: {models.manager}\n"
        f"- worker: {models.worker}\n"
        f"- reviewer: {models.reviewer}\n"
        f"- fallback: {models.fallback}\n"
    )


def _sandbox_section(spec: HocaTaskSpec) -> str:
    return (
        "sandbox:\n"
        f"- enabled: {str(spec.sandbox.enabled).lower()}\n"
        f"- network_mode: {spec.sandbox.network_mode}\n"
    )


def build_worker_direct_prompt(
    *,
    spec: HocaTaskSpec,
    project_path: Path,
    run_dir: Path,
    round_number: int,
    task_spec_path: Path,
    repair_brief: str | None = None,
) -> str:
    repair_section = ""
    if repair_brief and repair_brief.strip():
        repair_section = (
            "\nRepair brief for this attempt (scope override for this round only):\n"
            f"{_redact_secret_like_lines(repair_brief.strip())}\n"
        )

    prompt = (
        "Execute one bounded HOCA worker attempt directly in OpenHands.\n\n"
        "Execution bindings:\n"
        f"- execution_project_path: {project_path.resolve()}\n"
        f"- run_dir: {run_dir.resolve()}\n"
        f"- round: {round_number}\n"
        f"- task_spec_path: {task_spec_path.resolve()}\n"
        f"{repair_section}\n"
        "Task spec bindings:\n"
        f"- run_id: {spec.run_id}\n"
        f"- repo_root_reference_only: {spec.repo_root}\n"
        f"- base_branch: {spec.base_branch}\n"
        f"- task_branch: {spec.task_branch}\n"
        f"- issue_id: {spec.issue_id or '(none)'}\n"
        f"- raw_request: {spec.raw_request.strip()}\n"
        f"- goal: {spec.goal.strip()}\n"
        f"{_list_section('non_goals', spec.non_goals)}"
        f"{_list_section('expected_areas', spec.expected_areas)}"
        f"{_list_section('acceptance_criteria', spec.acceptance_criteria)}"
        f"{_list_section('test_commands', spec.test_commands)}"
        f"- risk_level: {spec.risk_level}\n"
        f"- requires_human_approval: {str(spec.requires_human_approval).lower()}\n"
        f"- max_total_rounds: {spec.max_total_rounds}\n"
        f"{_models_section(spec)}"
        f"{_sandbox_section(spec)}"
        "\nRequired workflow:\n"
        "1. Treat execution_project_path as the only repository root. Do not cd to repo_root_reference_only.\n"
        "2. Inspect current repository state and prior round artifacts before changing files.\n"
        "3. Make only scoped edits needed for the goal and acceptance criteria.\n"
        "4. When using editing tools, send every required argument in one call: full path plus exact old/new text or insertion text. Do not retry partial edits.\n"
        "5. Verify repository diff after edits; if no diff exists, fix the edit or report a concrete blocker.\n"
        "6. Run the smallest useful validation commands from test_commands, or document why none apply.\n"
        "7. Leave Git lifecycle to the HOCA manager: do not stage, commit, push, merge, or open PRs.\n"
        "8. Prefer existing helpers and ownership boundaries; avoid one-off modes and needless abstractions.\n"
        "9. Finish with a concise implementation summary, validation performed, changed files, and any blocker.\n\n"
        "Safety constraints:\n"
        "- Do not read or modify secret-like files (.env, keys, tokens, credential stores).\n"
        "- If the task mentions .env.example, access only that exact path; never use .env* globs or inspect .env files.\n"
        "- Do not embed API keys, tokens, or passwords in prompts or reports.\n"
        "- Do not set or override HOCA_REQUESTED_MODEL, OLLAMA_MODEL, LLM_MODEL, LLM_BASE_URL, or LLM_API_KEY.\n"
        "- Stay within expected_areas unless the repair brief explicitly widens scope.\n\n"
        "Report requirements:\n"
        "- Leave enough evidence for HOCA to infer a worker attempt report.\n"
        "- State validation commands and results; if skipped, state the concrete reason.\n"
        "- If blocked, stop and name the blocker instead of broadening scope.\n"
    )
    return _redact_secret_like_lines(prompt)


def write_worker_direct_prompt(
    *,
    spec: HocaTaskSpec,
    project_path: Path,
    run_dir: Path,
    round_number: int,
    task_spec_path: Path,
    repair_brief: str | None = None,
) -> Path:
    ensure_run_layout(run_dir)
    prompt = build_worker_direct_prompt(
        spec=spec,
        project_path=project_path,
        run_dir=run_dir,
        round_number=round_number,
        task_spec_path=task_spec_path,
        repair_brief=repair_brief,
    )
    prompt_dir = run_dir / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    path = prompt_dir / f"worker-direct-prompt-{round_number}.txt"
    path.write_text(prompt, encoding="utf-8")
    return path


def _invoke_openhands_direct(
    *,
    project_path: Path,
    prompt_path: Path,
    run_dir: Path,
) -> CommandResult:
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / "worker-direct-stdout.txt"
    stderr_path = logs_dir / "worker-direct-stderr.txt"
    hoca_root = Path(__file__).resolve().parents[1]
    command = [
        str(hoca_root / "scripts" / "run-openhands-task.sh"),
        str(project_path),
        str(prompt_path),
        str(run_dir),
    ]
    env = os.environ.copy()
    env["HOCA_AGENT_ROLE"] = "worker"
    env["HOCA_LOCK_ROLE_MODEL"] = "true"
    env.setdefault("HOCA_PYTHON", sys.executable)

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=run_dir,
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    return CommandResult(
        command=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_worker_direct(
    *,
    project_path: Path,
    task_spec_path: Path,
    run_dir: Path,
    round_number: int,
    repair_brief: str | None = None,
) -> WorkerRunResult:
    if round_number < 1:
        raise ValueError("round must be greater than or equal to 1")

    project_path = project_path.resolve()
    run_dir = run_dir.resolve()
    task_spec_path = task_spec_path.resolve()
    ensure_run_layout(run_dir)
    spec = load_task_spec(task_spec_path)
    prompt_path = write_worker_direct_prompt(
        spec=spec,
        project_path=project_path,
        run_dir=run_dir,
        round_number=round_number,
        task_spec_path=task_spec_path,
        repair_brief=repair_brief,
    )

    result = _invoke_openhands_direct(
        project_path=project_path,
        prompt_path=prompt_path,
        run_dir=run_dir,
    )
    exit_code = result.returncode
    if _completed_despite_finalization_stall(
        run_dir, process_exit_code=result.returncode, project_path=project_path
    ):
        status = "completed"
        exit_code = 0
    else:
        status = _infer_worker_status(run_dir, process_exit_code=result.returncode)
    status = _missing_profile_attempt_status(
        run_dir,
        round_number=round_number,
        process_exit_code=exit_code,
        inferred_status=status,
        project_path=project_path,
    )
    attempt_path = _ensure_worker_attempt_report(
        run_dir,
        round_number=round_number,
        status=status,
        mode="direct",
        project_path=project_path,
    )
    return WorkerRunResult(
        mode="direct",
        exit_code=exit_code,
        worker_attempt_path=attempt_path,
        hermes_stdout_path=None,
        hermes_stderr_path=None,
    )
