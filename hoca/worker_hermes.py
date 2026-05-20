"""Run a worker attempt via Hermes profile or legacy OpenHands wrapper."""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from hoca.config import HocaConfig, load_config
from hoca.env_allowlist import filter_env, redact_env_for_logging
from hoca.role_model_env import apply_role_to_env, log_line_for_selection, resolve_role_llm
from hoca.contracts import HocaTaskSpec
from hoca.paths import repo_root
from hoca.profiles import PROFILE_WORKER, hermes_installed, profile_exists
from hoca.run_artifacts import record_worker_attempt
from hoca.run_layout import ensure_run_layout, worker_attempt_path
from hoca.subprocess_utils import CommandResult, run_command

_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|private[_-]?key)\s*[:=]\s*\S+"
)

DEFAULT_HERMES_TIMEOUT_SECONDS = 1800
DEFAULT_HERMES_MAX_TURNS = 30


@dataclass(frozen=True)
class WorkerRunResult:
    mode: str
    exit_code: int
    worker_attempt_path: Path | None
    hermes_stdout_path: Path | None = None
    hermes_stderr_path: Path | None = None


def _redact_secret_like_lines(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if _SECRET_VALUE_PATTERN.search(line):
            lines.append("[redacted: possible secret]")
        else:
            lines.append(line)
    return "\n".join(lines)


def load_task_spec(task_spec_path: Path) -> HocaTaskSpec:
    path = task_spec_path.resolve()
    if not path.is_file():
        raise ValueError(f"Task spec not found: {path}")
    return HocaTaskSpec.from_json(path.read_text(encoding="utf-8"))


def _format_list_section(title: str, items: list[str]) -> str:
    if not items:
        return f"{title}:\n- (none)\n"
    body = "\n".join(f"- {item}" for item in items)
    return f"{title}:\n{body}\n"


def build_worker_hermes_prompt(
    *,
    spec: HocaTaskSpec,
    project_path: Path,
    run_dir: Path,
    round_number: int,
    task_spec_path: Path,
    repair_brief: str | None = None,
) -> str:
    hoca_root = repo_root()
    repair_section = ""
    if repair_brief and repair_brief.strip():
        repair_section = (
            "\nRepair brief for this attempt (scope override for this round only):\n"
            f"{_redact_secret_like_lines(repair_brief.strip())}\n"
        )

    prompt = (
        "Execute one bounded HOCA worker attempt using the hoca-worker-openhands skill.\n\n"
        "Assignment parameters:\n"
        f"- project_path: {project_path.resolve()}\n"
        f"- run_dir: {run_dir.resolve()}\n"
        f"- round: {round_number}\n"
        f"- task_spec_path: {task_spec_path.resolve()}\n"
        f"- hoca_root: {hoca_root}\n"
        f"{repair_section}\n"
        "Required steps:\n"
        "1. Read the manager task spec at task_spec_path.\n"
        "2. Build a precise OpenHands implementation prompt from goal, non_goals, "
        "expected_areas, acceptance_criteria, and test_commands.\n"
        "3. Run implementation only through:\n"
        f'   {hoca_root / "scripts" / "run-openhands-task.sh"} '
        '"$project_path" "$openhands_prompt" "$run_dir"\n'
        "4. Inspect repository changes read-only (git status, git diff).\n"
        "5. Write attempts/worker-attempt-<round>.json or run:\n"
        f'   python3 -m hoca.run_artifacts record-worker "$run_dir" '
        f'--round {round_number} --status <completed|failed|blocked>\n\n'
        "Safety constraints:\n"
        "- Manager-owned Git lifecycle only: never run git add, git commit, git push, git merge, gh pr create, or gh pr merge.\n"
        "- Do not stage, commit, push, merge, or open pull requests.\n"
        "- Do not read or modify secret-like files (.env, keys, tokens, credential stores).\n"
        "- Do not embed API keys, tokens, or passwords in prompts or reports.\n"
        "- Stay within expected_areas unless the repair brief explicitly widens scope.\n\n"
        "Task spec summary (read the JSON file for full fields):\n"
        f"- goal: {spec.goal.strip()}\n"
        f"{_format_list_section('non_goals', spec.non_goals)}"
        f"{_format_list_section('expected_areas', spec.expected_areas)}"
        f"{_format_list_section('acceptance_criteria', spec.acceptance_criteria)}"
        f"{_format_list_section('test_commands', spec.test_commands)}"
        f"- risk_level: {spec.risk_level}\n"
        f"- repo_root: {spec.repo_root}\n"
        f"- task_branch: {spec.task_branch}\n"
    )
    return _redact_secret_like_lines(prompt)


def build_legacy_openhands_task(*, spec: HocaTaskSpec, repair_brief: str | None = None) -> str:
    if repair_brief and repair_brief.strip():
        return _redact_secret_like_lines(repair_brief.strip())
    return _redact_secret_like_lines(spec.goal.strip())


def verify_profile_prerequisites(*, hermes_home: Path | None = None) -> None:
    if not hermes_installed():
        raise RuntimeError(
            "hermes command not found. Install Hermes or disable HOCA_USE_HERMES_PROFILES."
        )
    if not profile_exists(PROFILE_WORKER, hermes_home=hermes_home):
        raise RuntimeError(
            f"Hermes profile {PROFILE_WORKER!r} is not installed. "
            "Run scripts/setup-hermes-profiles.sh before enabling profile mode."
        )


def _resolve_timeout_seconds() -> int:
    raw = os.environ.get("HOCA_HERMES_TIMEOUT", str(DEFAULT_HERMES_TIMEOUT_SECONDS))
    try:
        timeout = int(raw)
    except ValueError as exc:
        raise ValueError(f"HOCA_HERMES_TIMEOUT must be an integer, got: {raw!r}") from exc
    if timeout <= 0:
        raise ValueError("HOCA_HERMES_TIMEOUT must be greater than 0")
    return timeout


def _resolve_max_turns() -> int:
    raw = os.environ.get("HOCA_HERMES_MAX_TURNS", str(DEFAULT_HERMES_MAX_TURNS))
    try:
        max_turns = int(raw)
    except ValueError as exc:
        raise ValueError(f"HOCA_HERMES_MAX_TURNS must be an integer, got: {raw!r}") from exc
    if max_turns <= 0:
        raise ValueError("HOCA_HERMES_MAX_TURNS must be greater than 0")
    return max_turns


def _worker_execution_env(cfg: HocaConfig | None = None) -> dict[str, str]:
    config = cfg or load_config()
    env = apply_role_to_env("worker", config)
    env["HOCA_AGENT_ROLE"] = "worker"
    return env


def _run_openhands_wrapper(
    *,
    project_path: Path,
    task: str,
    run_dir: Path,
    cfg: HocaConfig | None = None,
) -> CommandResult:
    config = cfg or load_config()
    env = _worker_execution_env(config)
    if config.model_pool.is_active:
        selection = resolve_role_llm("worker", config)
        print(log_line_for_selection(selection), file=sys.stderr)
    script = repo_root() / "scripts" / "run-openhands-task.sh"
    return run_command(
        [str(script), str(project_path), task, str(run_dir)],
        cwd=project_path,
        env=env,
    )


def _invoke_hermes_worker(
    *,
    prompt: str,
    run_dir: Path,
    timeout_seconds: int,
    max_turns: int,
) -> CommandResult:
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / "worker-hermes-stdout.txt"
    stderr_path = logs_dir / "worker-hermes-stderr.txt"

    command = [
        "hermes",
        "-p",
        PROFILE_WORKER,
        "-z",
        prompt,
        "--accept-hooks",
        "-s",
        "hoca-worker-openhands",
        "--max-turns",
        str(max_turns),
    ]

    import subprocess

    cfg = load_config()
    env = apply_role_to_env("worker", cfg, os.environ.copy())
    env.setdefault("HERMES_ACCEPT_HOOKS", "1")
    env["HOCA_AGENT_ROLE"] = "worker"
    env = filter_env(env, "worker")
    if cfg.model_pool.is_active:
        print(log_line_for_selection(resolve_role_llm("worker", cfg)), file=sys.stderr)

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
            cwd=run_dir,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + f"\nHermes worker timed out after {timeout_seconds}s."
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        return CommandResult(
            command=tuple(command),
            returncode=124,
            stdout=stdout,
            stderr=stderr,
        )

    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    return CommandResult(
        command=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _infer_worker_status(run_dir: Path, *, process_exit_code: int) -> str:
    monitor_path = run_dir / "monitor-result.json"
    if monitor_path.is_file():
        try:
            import json

            monitor = json.loads(monitor_path.read_text(encoding="utf-8"))
            stop_reason = monitor.get("stop_reason")
            if isinstance(stop_reason, str) and stop_reason and stop_reason != "completed":
                if stop_reason in {"secret_detected", "dangerous_command", "scope_violation"}:
                    return "blocked"
                return "failed"
        except (OSError, ValueError, TypeError):
            pass

    if process_exit_code == 0:
        return "completed"
    return "failed"


def _ensure_worker_attempt_report(
    run_dir: Path,
    *,
    round_number: int,
    status: str,
    mode: str = "legacy",
    project_path: Path | None = None,
) -> Path:
    attempt_path = worker_attempt_path(run_dir, round_number)
    if attempt_path.is_file():
        return attempt_path
    return record_worker_attempt(
        run_dir,
        round_number=round_number,
        status=status,
        mode=mode,
        project_path=project_path,
    )


def run_worker_hermes(
    *,
    project_path: Path,
    task_spec_path: Path,
    run_dir: Path,
    round_number: int,
    repair_brief: str | None = None,
    use_hermes_profiles: bool | None = None,
    hermes_home: Path | None = None,
) -> WorkerRunResult:
    if round_number < 1:
        raise ValueError("round must be greater than or equal to 1")

    project_path = project_path.resolve()
    run_dir = run_dir.resolve()
    task_spec_path = task_spec_path.resolve()
    ensure_run_layout(run_dir)

    spec = load_task_spec(task_spec_path)
    cfg = load_config()
    profile_mode = cfg.use_hermes_profiles if use_hermes_profiles is None else use_hermes_profiles

    if profile_mode:
        verify_profile_prerequisites(hermes_home=hermes_home)
        prompt = build_worker_hermes_prompt(
            spec=spec,
            project_path=project_path,
            run_dir=run_dir,
            round_number=round_number,
            task_spec_path=task_spec_path,
            repair_brief=repair_brief,
        )
        prompt_path = run_dir / f"worker-hermes-prompt-round-{round_number}.txt"
        prompt_path.write_text(prompt + "\n", encoding="utf-8")

        result = _invoke_hermes_worker(
            prompt=prompt,
            run_dir=run_dir,
            timeout_seconds=_resolve_timeout_seconds(),
            max_turns=_resolve_max_turns(),
        )
        status = _infer_worker_status(run_dir, process_exit_code=result.returncode)
        attempt_path = _ensure_worker_attempt_report(
            run_dir,
            round_number=round_number,
            status=status,
            mode="profile",
            project_path=project_path,
        )
        return WorkerRunResult(
            mode="profile",
            exit_code=result.returncode,
            worker_attempt_path=attempt_path,
            hermes_stdout_path=run_dir / "logs" / "worker-hermes-stdout.txt",
            hermes_stderr_path=run_dir / "logs" / "worker-hermes-stderr.txt",
        )

    openhands_task = build_legacy_openhands_task(spec=spec, repair_brief=repair_brief)
    task_path = run_dir / f"openhands-task-round-{round_number}.txt"
    task_path.write_text(openhands_task + "\n", encoding="utf-8")

    result = _run_openhands_wrapper(
        project_path=project_path,
        task=openhands_task,
        run_dir=run_dir,
        cfg=cfg,
    )
    status = _infer_worker_status(run_dir, process_exit_code=result.returncode)
    attempt_path = _ensure_worker_attempt_report(
        run_dir,
        round_number=round_number,
        status=status,
        mode="legacy",
        project_path=project_path,
    )
    return WorkerRunResult(
        mode="legacy",
        exit_code=result.returncode,
        worker_attempt_path=attempt_path,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a HOCA worker attempt via Hermes profile or legacy OpenHands wrapper."
    )
    parser.add_argument("project_path", help="Path to the target Git repository")
    parser.add_argument("task_spec_path", help="Path to task-spec.json")
    parser.add_argument("run_dir", help="HOCA run directory (.hoca-runtime/runs/<run_id>)")
    parser.add_argument("round", type=int, help="Worker attempt round number (>= 1)")
    parser.add_argument(
        "--repair-brief",
        default="",
        help="Optional repair brief text file for rounds after the first attempt",
    )

    args = parser.parse_args(argv)
    repair_brief = None
    if args.repair_brief:
        repair_path = Path(args.repair_brief)
        if not repair_path.is_file():
            print(f"Repair brief not found: {repair_path}", file=sys.stderr)
            return 1
        repair_brief = repair_path.read_text(encoding="utf-8")

    try:
        result = run_worker_hermes(
            project_path=Path(args.project_path),
            task_spec_path=Path(args.task_spec_path),
            run_dir=Path(args.run_dir),
            round_number=args.round,
            repair_brief=repair_brief,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if result.worker_attempt_path is not None:
        print(result.worker_attempt_path)
    if result.exit_code != 0:
        return result.exit_code if result.exit_code > 0 else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
