"""Run a worker attempt via the hoca-worker Hermes profile."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from hoca.config import load_config
from hoca.env_allowlist import filter_env
from hoca.role_model_env import (
    apply_role_to_env,
    hermes_provider_for_model,
    log_line_for_selection,
    openai_compatible_model_for_selection,
    resolve_role_llm,
    strip_pool_credentials,
)
from hoca.contracts import HocaTaskSpec
from hoca.paths import repo_root
from hoca.profiles import PROFILE_WORKER, hermes_installed, profile_exists
from hoca.run_artifacts import record_worker_attempt
from hoca.run_layout import ensure_run_layout, worker_attempt_path
from hoca.subprocess_utils import CommandResult

_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|private[_-]?key)\s*[:=]\s*\S+"
)

DEFAULT_HERMES_TIMEOUT_SECONDS = 1800
DEFAULT_HERMES_MAX_TURNS = 30

ITERATIVE_WORKER_RUBRIC = """Bounded iteration discipline:
- Treat this attempt as one iteration in a manager-controlled loop: inspect the
  current working tree and prior artifacts first, then continue from the existing
  state instead of restarting blindly.
- Keep the original goal stable across the attempt. Do not drift into adjacent
  cleanup or invent new requirements to make progress feel larger.
- Define completion from the task spec: every acceptance criterion satisfied,
  relevant tests run or honestly documented, changed files within scope, and no
  known unsafe or unrelated edits.
- After each implementation step, run the smallest useful validation command,
  read the failure output, and fix the cause before broadening the change.
- Only report the attempt as completed when completion is genuinely true. If a
  criterion cannot be verified, record the gap as failed or blocked instead of
  implying readiness.
- Stop and escalate when progress would require guessing product intent,
  exceeding scope, reading secrets, or using Git lifecycle commands.
"""

PSTACK_WORKER_PRINCIPLES = """Implementation quality principles:
- Name the data shape first. Before writing logic, identify the core input,
  output, state, and ownership model this change depends on.
- Subtract before adding. Remove dead branches, redundant helpers, or stale
  references that directly block the task before layering on new code.
- Minimize reader load. Collapse one-caller wrappers and avoid hidden mutable
  state unless the indirection clearly makes the touched code easier to follow.
- Keep boundary discipline. Validate external inputs at system boundaries such
  as CLI args, config, files, network payloads, and API responses; keep internal
  business logic typed, direct, and testable.
- Use the type system honestly. Prefer explicit variants and authoritative
  schemas over optional-field bags, casts, unsafe assertions, or duplicated
  parallel types.
- Make operations idempotent. For commands, lifecycle steps, retries, cleanup,
  and generated artifacts, design the change so running twice or resuming after
  a partial failure converges to the same state.
- Fix root causes. Reproduce or inspect the actual failure, ask why until the
  underlying cause is clear, and avoid guard-only patches that hide symptoms.
- Prove the real path works. Verify the actual feature, command, data flow, or
  artifact changed by the task, not just a proxy such as compilation or a
  delegate summary.
"""


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
    use_sandbox = os.environ.get("HOCA_USE_SANDBOX", "true")
    network_mode = os.environ.get("HOCA_NETWORK_MODE", "offline")
    docker_context = os.environ.get("DOCKER_CONTEXT", "")
    hoca_dotenv = Path(os.environ.get("HOCA_DOTENV_PATH", hoca_root / ".env")).expanduser()
    if not hoca_dotenv.is_absolute():
        hoca_dotenv = (hoca_root / hoca_dotenv).resolve()
    else:
        hoca_dotenv = hoca_dotenv.resolve()
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
        "   Inspect current repository state and prior round artifacts before changing files.\n"
        "   Keep inspection targeted: use git status/diff and the task's expected files; do not recursively list the repository, dependency directories, or generated artifacts.\n"
        "2. Build a precise OpenHands implementation prompt from goal, non_goals, "
        "expected_areas, acceptance_criteria, and test_commands.\n"
        "   Treat project_path as the only executable repository root. If the task spec "
        "or test_commands mention a different repo_root, rewrite validation commands to "
        "run from project_path and do not cd to the original checkout.\n"
        '   If saving the OpenHands prompt for audit, save it only as "$run_dir/openhands-task-prompt.txt".\n'
        "3. Run implementation only through:\n"
        f'   HOCA_LOCK_ROLE_MODEL=true HOCA_SKIP_ROLE_MODEL_RESOLUTION=false HOCA_USE_SANDBOX="{use_sandbox}" '
        f'HOCA_NETWORK_MODE="{network_mode}" '
        f"{f'DOCKER_CONTEXT="{docker_context}" ' if docker_context else ''}"
        f'HOCA_PYTHON="{hoca_python}" '
        f'HOCA_DOTENV_PATH="{hoca_dotenv}" '
        f"{hoca_root / 'scripts' / 'run-openhands-task.sh'} "
        '"$project_path" "$openhands_prompt" "$run_dir"\n'
        "4. Inspect repository changes read-only (git status, git diff), excluding manager-owned "
        ".hoca-runtime/ artifacts from the changed-file assessment.\n"
        "5. After one relevant validation command passes for the changed scope, stop working immediately. "
        "Do not continue exploring, re-running broader validation, or making optional edits.\n"
        "6. Apply the bounded iteration discipline before marking the attempt complete.\n"
        f"{ITERATIVE_WORKER_RUBRIC}\n"
        "7. Apply the implementation quality principles while shaping the diff.\n"
        f"{PSTACK_WORKER_PRINCIPLES}\n"
        "8. Write attempts/worker-attempt-<round>.json or run:\n"
        f'   python3 -m hoca.run_artifacts record-worker "$run_dir" '
        f"--round {round_number} --status <completed|failed|blocked>\n\n"
        "Safety constraints:\n"
        "- Manager-owned Git lifecycle only: never run git add, git commit, git push, git merge, gh pr create, or gh pr merge.\n"
        "- Do not stage, commit, push, merge, or open pull requests.\n"
        "- Do not read or modify secret-like files (.env, keys, tokens, credential stores).\n"
        "- If the task mentions .env.example, access only that exact path; never use .env* globs or inspect .env files.\n"
        "- Do not embed API keys, tokens, or passwords in prompts or reports.\n"
        "- Do not set or override HOCA_REQUESTED_MODEL, OLLAMA_MODEL, LLM_MODEL, LLM_BASE_URL, or LLM_API_KEY.\n"
        "- If you create temporary verification files, clean up only the exact files or temp directory you created; do not use broad recursive cleanup in the project tree.\n"
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


def verify_profile_prerequisites(*, hermes_home: Path | None = None) -> None:
    if not hermes_installed():
        raise RuntimeError("hermes command not found. Install Hermes before running HOCA.")
    if not profile_exists(PROFILE_WORKER, hermes_home=hermes_home):
        raise RuntimeError(
            f"Hermes profile {PROFILE_WORKER!r} is not installed. "
            "Run scripts/setup-hermes-profiles.sh before running HOCA."
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
    hermes_model = selection.llm_model.strip()
    nested_openhands_model = openai_compatible_model_for_selection(selection)
    if hermes_model:
        command.extend(["--model", hermes_model])
    provider = hermes_provider_for_model(hermes_model)
    if not provider and selection.base_url.strip():
        provider = "custom"
    if provider:
        command.extend(["--provider", provider])
    env = strip_pool_credentials(apply_role_to_env("worker", cfg, os.environ.copy()))
    env.update(selection.env_vars())
    env["LLM_MODEL"] = nested_openhands_model
    env.setdefault("HERMES_ACCEPT_HOOKS", "1")
    env["HOCA_AGENT_ROLE"] = "worker"
    env = filter_env(env, "worker")
    if provider == "custom":
        env["CUSTOM_BASE_URL"] = selection.base_url.strip()
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
        stdout = _timeout_stream_to_text(exc.stdout)
        stderr = (
            _timeout_stream_to_text(exc.stderr)
            + f"\nHermes worker timed out after {timeout_seconds}s."
        )
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        if _openhands_completed_successfully(run_dir):
            return CommandResult(
                command=tuple(command),
                returncode=0,
                stdout=stdout,
                stderr=stderr,
            )
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


def _timeout_stream_to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _infer_worker_status(run_dir: Path, *, process_exit_code: int) -> str:
    monitor_path = run_dir / "monitor-result.json"
    if monitor_path.is_file():
        try:
            import json

            monitor = json.loads(monitor_path.read_text(encoding="utf-8"))
            stop_reason = monitor.get("stop_reason")
            if isinstance(stop_reason, str) and stop_reason and stop_reason != "completed":
                return "blocked"
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


def _monitor_stop_reason(run_dir: Path) -> str | None:
    monitor_path = run_dir / "monitor-result.json"
    if not monitor_path.is_file():
        return None
    try:
        import json

        monitor = json.loads(monitor_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    stop_reason = monitor.get("stop_reason")
    return stop_reason if isinstance(stop_reason, str) and stop_reason else None


def _openhands_completed_successfully(run_dir: Path) -> bool:
    exit_code_path = run_dir / "openhands-exit-code.txt"
    if not exit_code_path.is_file():
        return False
    try:
        exit_code = exit_code_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return exit_code == "0" and _monitor_stop_reason(run_dir) == "completed"


def _completed_despite_finalization_stall(
    run_dir: Path, *, process_exit_code: int, project_path: Path
) -> bool:
    return (
        process_exit_code != 0
        and _monitor_stop_reason(run_dir) == "stall"
        and _project_has_changes(project_path)
    )


def _openhands_edit_tool_failure(run_dir: Path) -> str | None:
    output_path = run_dir / "openhands-output.jsonl"
    if not output_path.is_file():
        return None
    try:
        text = output_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if "Parameter `new_str` is required for command: insert." in text:
        return "OpenHands file_editor insert omitted required new_str and produced no changes."
    if "Parameter `old_str` is required" in text or "Parameter `new_str` is required" in text:
        return "OpenHands file_editor omitted a required edit argument and produced no changes."
    return None


def _ensure_worker_attempt_report(
    run_dir: Path,
    *,
    round_number: int,
    status: str,
    mode: str = "profile",
    project_path: Path | None = None,
) -> Path:
    attempt_path = worker_attempt_path(run_dir, round_number)
    if attempt_path.is_file():
        if status != "completed":
            try:
                import json

                existing = json.loads(attempt_path.read_text(encoding="utf-8"))
                if existing.get("status") == "completed":
                    return record_worker_attempt(
                        run_dir,
                        round_number=round_number,
                        status=status,
                        mode=mode,
                        project_path=project_path,
                    )
            except (OSError, ValueError, TypeError):
                return record_worker_attempt(
                    run_dir,
                    round_number=round_number,
                    status=status,
                    mode=mode,
                    project_path=project_path,
                )
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
    if _openhands_completed_successfully(run_dir):
        return inferred_status
    if _openhands_edit_tool_failure(run_dir):
        return "failed"
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


def _relocate_leaked_openhands_prompt(*, project_path: Path, run_dir: Path) -> None:
    leaked_prompt = project_path / "openhands-task-prompt.txt"
    if not leaked_prompt.is_file():
        return
    destination = run_dir / "openhands-task-prompt.txt"
    try:
        text = leaked_prompt.read_text(encoding="utf-8", errors="replace")
        if not destination.exists():
            destination.write_text(text, encoding="utf-8")
        leaked_prompt.unlink()
    except OSError:
        return


def _relocate_leaked_attempt_reports(*, project_path: Path, run_dir: Path) -> None:
    leaked_attempts_dir = project_path / "attempts"
    if not leaked_attempts_dir.is_dir():
        return
    destination_dir = run_dir / "attempts"
    try:
        destination_dir.mkdir(parents=True, exist_ok=True)
        for leaked_attempt in leaked_attempts_dir.glob("worker-attempt-*.json"):
            destination = destination_dir / leaked_attempt.name
            if not destination.exists():
                destination.write_text(
                    leaked_attempt.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                )
            leaked_attempt.unlink()
        leaked_attempts_dir.rmdir()
    except OSError:
        return


def run_worker_hermes(
    *,
    project_path: Path,
    task_spec_path: Path,
    run_dir: Path,
    round_number: int,
    repair_brief: str | None = None,
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
    if cfg.worker_engine == "claude-code":
        from hoca.cli_worker_adapters import run_claude_worker

        return run_claude_worker(
            project_path=project_path,
            task_spec_path=task_spec_path,
            run_dir=run_dir,
            round_number=round_number,
            repair_brief=repair_brief,
        )
    if cfg.worker_engine == "codex":
        from hoca.cli_worker_adapters import run_codex_worker

        return run_codex_worker(
            project_path=project_path,
            task_spec_path=task_spec_path,
            run_dir=run_dir,
            round_number=round_number,
            repair_brief=repair_brief,
        )
    if cfg.worker_mode == "direct":
        from hoca.worker_direct import run_worker_direct

        return run_worker_direct(
            project_path=project_path,
            task_spec_path=task_spec_path,
            run_dir=run_dir,
            round_number=round_number,
            repair_brief=repair_brief,
        )
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
    _relocate_leaked_openhands_prompt(project_path=project_path, run_dir=run_dir)
    _relocate_leaked_attempt_reports(project_path=project_path, run_dir=run_dir)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a HOCA worker attempt via the hoca-worker Hermes profile."
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
