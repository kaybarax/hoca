"""Run a worker attempt via Hermes profile or legacy OpenHands wrapper."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from hoca.config import HocaConfig, load_config
from hoca.env_allowlist import filter_env
from hoca.role_model_env import (
    apply_role_to_env,
    hermes_provider_for_model,
    log_line_for_selection,
    resolve_role_llm,
    strip_pool_credentials,
)
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
    hoca_python = sys.executable
    hoca_dotenv = hoca_root / ".env"
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
        "   Treat project_path as the only executable repository root. If the task spec "
        "or test_commands mention a different repo_root, rewrite validation commands to "
        "run from project_path and do not cd to the original checkout.\n"
        "3. Run implementation only through:\n"
        f'   HOCA_LOCK_ROLE_MODEL=true HOCA_PYTHON="{hoca_python}" '
        f'HOCA_DOTENV_PATH="{hoca_dotenv}" '
        f'{hoca_root / "scripts" / "run-openhands-task.sh"} '
        '"$project_path" "$openhands_prompt" "$run_dir"\n'
        "4. Inspect repository changes read-only (git status, git diff).\n"
        "5. Write attempts/worker-attempt-<round>.json or run:\n"
        f'   python3 -m hoca.run_artifacts record-worker "$run_dir" '
        f'--round {round_number} --status <completed|failed|blocked>\n\n'
        "Safety constraints:\n"
        "- Manager-owned Git lifecycle only: never run git add, git commit, git push, git merge, gh pr create, or gh pr merge.\n"
        "- Do not stage, commit, push, merge, or open pull requests.\n"
        "- Do not read or modify secret-like files (.env, keys, tokens, credential stores).\n"
        "- If the task mentions .env.example, access only that exact path; never use .env* globs or inspect .env files.\n"
        "- Do not embed API keys, tokens, or passwords in prompts or reports.\n"
        "- Do not set or override HOCA_REQUESTED_MODEL, HOCA_CLI_MODEL_OVERRIDE, LLM_MODEL, LLM_BASE_URL, or LLM_API_KEY.\n"
        "- Do not read, write, or run commands in any repository path other than project_path.\n"
        "- Stay within expected_areas unless the repair brief explicitly widens scope.\n\n"
        "Task spec summary (read the JSON file for full fields):\n"
        f"- execution_project_path: {project_path.resolve()}\n"
        f"- task_spec_repo_root_for_reference_only: {spec.repo_root}\n"
        f"- goal: {spec.goal.strip()}\n"
        f"{_format_list_section('non_goals', spec.non_goals)}"
        f"{_format_list_section('expected_areas', spec.expected_areas)}"
        f"{_format_list_section('acceptance_criteria', spec.acceptance_criteria)}"
        f"{_format_list_section('test_commands', spec.test_commands)}"
        f"- risk_level: {spec.risk_level}\n"
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
        "chat",
        "--query",
        prompt,
        "--accept-hooks",
        "--skills",
        "hoca-worker-openhands",
        "--max-turns",
        str(max_turns),
        "--quiet",
    ]

    import subprocess

    cfg = load_config()
    selection = resolve_role_llm("worker", cfg)
    if selection.llm_model.strip():
        command.extend(["--model", selection.llm_model])
    provider = hermes_provider_for_model(selection.llm_model)
    if provider:
        command.extend(["--provider", provider])
    env = strip_pool_credentials(apply_role_to_env("worker", cfg, os.environ.copy()))
    env.update(selection.env_vars())
    env.setdefault("HERMES_ACCEPT_HOOKS", "1")
    env["HOCA_AGENT_ROLE"] = "worker"
    env = filter_env(env, "worker")
    if cfg.model_pool.is_active:
        print(log_line_for_selection(selection), file=sys.stderr)

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
        stdout_path = run_dir / "logs" / "worker-hermes-stdout.txt"
        if stdout_path.is_file():
            stdout = stdout_path.read_text(encoding="utf-8", errors="replace").lower()
            if "status: `blocked`" in stdout or "status: blocked" in stdout:
                return "blocked"
            if "status: `failed`" in stdout or "status: failed" in stdout:
                return "failed"
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


def _missing_profile_attempt_status(
    run_dir: Path,
    *,
    round_number: int,
    process_exit_code: int,
    inferred_status: str,
    project_path: Path | None = None,
) -> str:
    if process_exit_code != 0:
        return inferred_status
    if worker_attempt_path(run_dir, round_number).is_file():
        return inferred_status
    if project_path is not None and _project_has_changes(project_path):
        return inferred_status
    return "blocked"


def _project_has_changes(project_path: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "status", "--short"],
            cwd=project_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    return any(
        line.strip() and not line[3:].startswith(".hoca-runtime/")
        for line in completed.stdout.splitlines()
    )


def _attempt_report_status(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    status = data.get("status") if isinstance(data, dict) else None
    return status if isinstance(status, str) else None


def _profile_attempt_needs_openhands_fallback(
    *,
    run_dir: Path,
    round_number: int,
    process_exit_code: int,
    project_path: Path,
) -> bool:
    if process_exit_code != 0:
        return False
    if os.environ.get("HOCA_PROFILE_OPENHANDS_FALLBACK", "true").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False
    if _project_has_changes(project_path):
        return False

    attempt_status = _attempt_report_status(worker_attempt_path(run_dir, round_number))
    return attempt_status in {"failed", "blocked"} or attempt_status is None


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
        if _profile_attempt_needs_openhands_fallback(
            run_dir=run_dir,
            round_number=round_number,
            process_exit_code=result.returncode,
            project_path=project_path,
        ):
            openhands_task = build_legacy_openhands_task(
                spec=spec,
                repair_brief=repair_brief,
            )
            fallback_task_path = run_dir / f"openhands-profile-fallback-round-{round_number}.txt"
            fallback_task_path.write_text(openhands_task + "\n", encoding="utf-8")
            fallback_result = _run_openhands_wrapper(
                project_path=project_path,
                task=openhands_task,
                run_dir=run_dir,
                cfg=cfg,
            )
            fallback_status = _infer_worker_status(
                run_dir,
                process_exit_code=fallback_result.returncode,
            )
            attempt_path = record_worker_attempt(
                run_dir,
                round_number=round_number,
                status=fallback_status,
                mode="legacy",
                project_path=project_path,
                summary=[
                    "Hermes profile worker exited without producing project changes; "
                    "HOCA reran the implementation through the direct OpenHands wrapper.",
                    f"Profile worker exit code: {result.returncode}.",
                    f"Fallback OpenHands exit code: {fallback_result.returncode}.",
                ],
            )
            return WorkerRunResult(
                mode="profile-fallback",
                exit_code=fallback_result.returncode,
                worker_attempt_path=attempt_path,
                hermes_stdout_path=run_dir / "logs" / "worker-hermes-stdout.txt",
                hermes_stderr_path=run_dir / "logs" / "worker-hermes-stderr.txt",
            )

        status = _infer_worker_status(run_dir, process_exit_code=result.returncode)
        status = _missing_profile_attempt_status(
            run_dir,
            round_number=round_number,
            process_exit_code=result.returncode,
            inferred_status=status,
            project_path=project_path,
        )
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
