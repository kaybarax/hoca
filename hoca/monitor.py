from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from hoca.security import is_secret_like_path

DANGEROUS_COMMANDS: list[re.Pattern[str]] = [
    re.compile(r"\bsudo\s+rm\b"),
    re.compile(r"\bchmod\s+(-R\s+)?777\b"),
    re.compile(r"\bchown\s+-R\b"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    re.compile(r"\bgit\s+clean\s+(-\w*f)"),
    re.compile(r"\bgit\s+push\s+(--force|-f)\b"),
    re.compile(r"\bgh\s+pr\s+merge\b"),
    re.compile(r"\bdocker\s+system\s+prune\b"),
    re.compile(r"\bbrew\s+uninstall\b"),
    re.compile(r"\bgit\s+add\s+\.\s*$"),
    re.compile(r"\bgit\s+add\s+-A\b"),
    re.compile(r"\bgit\s+commit\s+-am\b"),
    re.compile(r"\bgit\s+push\b(?!.*--set-upstream)(?!.*-u\b)"),
    re.compile(r"\bgit\s+merge\b"),
]

GIT_LIFECYCLE_MANAGER_ONLY_COMMANDS: list[re.Pattern[str]] = [
    re.compile(r"\bgit\s+add\b"),
    re.compile(r"\bgit\s+commit\b"),
    re.compile(r"\bgit\s+push\b"),
    re.compile(r"\bgh\s+pr\s+create\b"),
    re.compile(r"\bgh\s+pr\s+merge\b"),
]

MANAGER_ONLY_GIT_LIFECYCLE_ROLES = frozenset({"worker", "reviewer", "openhands"})
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_COMMAND_LINE_PREFIX = re.compile(r"^\s*(?:[$#>]\s*)?(?:`)?(?:git|gh)\s+")

# Relative paths that rm -rf is allowed to target within the project.
_SAFE_RM_TARGETS = frozenset(
    {
        "dist",
        "dist/",
        "./dist",
        "./dist/",
        "build",
        "build/",
        "./build",
        "./build/",
        "node_modules",
        "node_modules/",
        "./node_modules",
        "./node_modules/",
        ".next",
        ".next/",
        "./.next",
        "./.next/",
        ".turbo",
        ".turbo/",
        "./.turbo",
        "./.turbo/",
        "coverage",
        "coverage/",
        "./coverage",
        "./coverage/",
        ".cache",
        ".cache/",
        "./.cache",
        "./.cache/",
        "out",
        "out/",
        "./out",
        "./out/",
        "tmp",
        "tmp/",
        "./tmp",
        "./tmp/",
    }
)

_RM_RF_PATTERN = re.compile(
    r"\brm\s+(-\w*[rR]\w*\s+.*-\w*f|.*-\w*f\w*\s+.*-\w*[rR]|-rf|-Rf)\s+(.*)"
)
_RM_RF_BARE = re.compile(r"\brm\s+(-\w*[rR]\w*\s+.*-\w*f|.*-\w*f\w*\s+.*-\w*[rR]|-rf|-Rf)\b")

DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_STALL_SECONDS = 300
STALL_CHECK_INTERVAL = 30


@dataclass
class MonitorEvent:
    timestamp: float
    kind: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "kind": self.kind,
            "message": self.message,
        }


@dataclass
class MonitorResult:
    exit_code: int
    stop_reason: str
    events: list[MonitorEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "exit_code": self.exit_code,
            "stop_reason": self.stop_reason,
            "events": [e.to_dict() for e in self.events],
        }


def _now() -> float:
    return time.time()


def _record(events: list[MonitorEvent], kind: str, message: str) -> MonitorEvent:
    event = MonitorEvent(timestamp=_now(), kind=kind, message=message)
    events.append(event)
    return event


def _is_safe_rm_target(line: str) -> bool:
    """Check if an rm -rf command only targets safe build artifact directories."""
    match = _RM_RF_PATTERN.search(line)
    if not match:
        return False
    targets_str = match.group(2).strip()
    targets = targets_str.split()
    if not targets:
        return False
    for target in targets:
        base = target.rstrip("/")
        # Allow relative paths into known safe directories
        # e.g., "apps/api-gateway/dist" is safe because basename is "dist"
        parts = base.split("/")
        leaf = parts[-1] if parts else ""
        if target in _SAFE_RM_TARGETS:
            continue
        if leaf in {t.rstrip("/") for t in _SAFE_RM_TARGETS}:
            continue
        # Block anything that starts with / (absolute) or looks unsafe
        return False
    return True


def check_dangerous_command(line: str) -> str | None:
    # Check rm -rf separately — allow it for safe build artifact targets
    if _RM_RF_BARE.search(line):
        if not _is_safe_rm_target(line):
            return _RM_RF_BARE.pattern

    for pattern in DANGEROUS_COMMANDS:
        if pattern.search(line):
            return pattern.pattern
    return None


def check_manager_only_git_lifecycle_command(line: str, actor_role: str) -> str | None:
    role = actor_role.strip().lower()
    if role not in MANAGER_ONLY_GIT_LIFECYCLE_ROLES:
        return None
    normalized = _ANSI_ESCAPE.sub("", line).strip(" │")
    if not _COMMAND_LINE_PREFIX.search(normalized):
        return None
    for pattern in GIT_LIFECYCLE_MANAGER_ONLY_COMMANDS:
        if pattern.search(normalized):
            return pattern.pattern
    return None


def check_secret_access(line: str, project_path: str) -> str | None:
    structured = _structured_secret_scan_line(line)
    if structured is not None:
        line = structured
    tokens = re.findall(r"[\w./\-]+\.(?:env|pem|key|p12|pfx|kubeconfig)(?:\.[\w\-]+)*\b", line)
    for token in tokens:
        if is_secret_like_path(token):
            return token
    path_candidates = re.findall(
        r"(?:cat|less|head|tail|vim|nano|open|cp|mv|rm)\s+([\w./\-]+)", line
    )
    for candidate in path_candidates:
        if is_secret_like_path(candidate):
            return candidate
    return None


def _structured_secret_scan_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    action = event.get("action")
    if not isinstance(action, dict):
        return ""

    tool_name = str(event.get("tool_name") or "")
    kind = str(action.get("kind") or "")
    parts: list[str] = []

    path = action.get("path")
    if isinstance(path, str):
        parts.append(path)

    command = action.get("command")
    if isinstance(command, str) and tool_name not in {"file_editor"} and kind != "FileEditorAction":
        parts.append(command)

    if tool_name in {"bash", "terminal", "shell"} and isinstance(command, str):
        parts.append(command)

    return "\n".join(parts)


def _structured_command_scan_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    action = event.get("action")
    if not isinstance(action, dict):
        return ""
    command = action.get("command")
    if isinstance(command, str):
        return command
    return ""


def command_policy_scan_text(line: str) -> str:
    structured = _structured_command_scan_line(line)
    if structured is not None:
        return structured
    stripped = line.strip()
    if stripped.startswith("{") and '"source"' in stripped:
        if '"action"' not in stripped and '"command"' not in stripped:
            return ""
    return line


def check_unrelated_directory(
    line: str,
    project_path: str,
    allowed_paths: list[str] | tuple[str, ...] = (),
) -> str | None:
    abs_refs = re.findall(r"(?:cd|cat|ls|rm|cp|mv|vi|vim|nano|open)\s+(/[^\s;|&]+)", line)
    allowed_roots = [os.path.realpath(project_path)]
    allowed_roots.extend(os.path.realpath(path) for path in allowed_paths)
    tmp_prefixes = ("/tmp", "/private/tmp", "/var/tmp")
    for ref in abs_refs:
        ref_resolved = os.path.realpath(ref)
        if not any(
            ref_resolved == root or ref_resolved.startswith(root + "/") for root in allowed_roots
        ):
            if any(ref_resolved == t or ref_resolved.startswith(t + "/") for t in tmp_prefixes):
                continue
            return ref
    return None


def should_scan_line_for_policy(line: str) -> bool:
    """Skip passive OpenHands observations; scan agent actions and plain output."""
    stripped = line.strip()
    if not stripped.startswith("{"):
        return True
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return True
    kind = str(event.get("kind", ""))
    source = str(event.get("source", ""))
    if source in {"environment", "user"} or kind.endswith(("ObservationEvent", "MessageEvent")):
        return False
    return True


def _is_progress_line(line: str) -> bool:
    if not line.strip():
        return False
    noise = [
        "Thinking...",
        "Processing...",
        "Waiting",
        "Retrying",
        "retry",
        "timeout",
        "No changes",
        "Nothing to do",
    ]
    lower = line.lower()
    return not any(n.lower() in lower for n in noise)


def save_events(run_dir: Path, events: list[MonitorEvent]) -> None:
    events_file = run_dir / "monitor-events.jsonl"
    with open(events_file, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")


def save_stop_reason(run_dir: Path, reason: str, detail: str) -> None:
    status_file = run_dir / "monitor-stop.json"
    data = {
        "stop_reason": reason,
        "detail": detail,
        "timestamp": _now(),
    }
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def monitor_process_stream(
    stream,
    *,
    project_path: str,
    run_dir: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    stall_seconds: int = DEFAULT_STALL_SECONDS,
    output_file=None,
    cancel_event: threading.Event | None = None,
    actor_role: str = "worker",
) -> MonitorResult:
    """Monitor a stream (e.g. stdin piped from docker) for dangerous activity.

    If *cancel_event* is provided, a watchdog thread periodically checks
    timeout / stall even when the stream produces no output and sets the
    event to signal the stream consumer to stop.
    """
    events: list[MonitorEvent] = []
    start_time = _now()
    last_progress_time = start_time
    stop_reason = "completed"
    exit_code = 0
    watchdog_reason: list[str] = []
    lock = threading.Lock()

    _record(
        events,
        "info",
        f"Monitoring started, role={actor_role}, timeout={timeout_seconds}s, stall={stall_seconds}s",
    )

    _cancel = cancel_event or threading.Event()

    def _watchdog() -> None:
        while not _cancel.is_set():
            _cancel.wait(STALL_CHECK_INTERVAL)
            if _cancel.is_set():
                break
            elapsed = _now() - start_time
            if elapsed > timeout_seconds:
                with lock:
                    watchdog_reason.append("timeout")
                    _record(events, "timeout", f"Watchdog: hard timeout after {timeout_seconds}s")
                _cancel.set()
                return
            with lock:
                idle = _now() - last_progress_time
            if idle > stall_seconds:
                with lock:
                    watchdog_reason.append("stall")
                    _record(
                        events,
                        "stall",
                        f"Watchdog: no output for {idle:.0f}s (limit {stall_seconds}s)",
                    )
                _cancel.set()
                return

    watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
    watchdog_thread.start()

    try:
        for line in stream:
            if _cancel.is_set():
                break

            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            line = line.rstrip("\n")

            if output_file:
                output_file.write(line + "\n")
                output_file.flush()
            print(line, end="\n", flush=True)

            elapsed = _now() - start_time

            if elapsed > timeout_seconds:
                _record(events, "timeout", f"Hard timeout after {timeout_seconds}s")
                stop_reason = "timeout"
                break

            if should_scan_line_for_policy(line):
                command_scan_text = command_policy_scan_text(line)
                dangerous = check_dangerous_command(command_scan_text)
                if dangerous:
                    _record(events, "dangerous_command", f"Detected: {dangerous} in: {line[:200]}")
                    stop_reason = "dangerous_command"
                    break

                manager_only = check_manager_only_git_lifecycle_command(command_scan_text, actor_role)
                if manager_only:
                    _record(
                        events,
                        "manager_only_git_lifecycle",
                        f"Detected manager-only Git lifecycle command for {actor_role}: "
                        f"{manager_only} in: {line[:200]}",
                    )
                    stop_reason = "manager_only_git_lifecycle"
                    break

                secret = check_secret_access(line, project_path)
                if secret:
                    _record(events, "secret_access", f"Secret-like file access: {secret}")
                    stop_reason = "secret_access"
                    break

                unrelated = check_unrelated_directory(line, project_path, (str(run_dir),))
                if unrelated:
                    _record(events, "unrelated_directory", f"Access outside project: {unrelated}")
                    stop_reason = "unrelated_directory"
                    break

            if _is_progress_line(line):
                with lock:
                    last_progress_time = _now()
            elif _now() - last_progress_time > stall_seconds:
                _record(events, "stall", f"No meaningful progress for {stall_seconds}s")
                stop_reason = "stall"
                break

    except Exception as exc:
        _record(events, "error", f"Monitor error: {exc}")
        stop_reason = "monitor_error"

    _cancel.set()
    watchdog_thread.join(timeout=5)

    with lock:
        if watchdog_reason and stop_reason == "completed":
            stop_reason = watchdog_reason[0]

    _record(events, "exit", f"Stream ended with stop_reason={stop_reason}")

    save_events(run_dir, events)
    if stop_reason != "completed":
        detail = events[-2].message if len(events) >= 2 else stop_reason
        save_stop_reason(run_dir, stop_reason, detail)
        exit_code = 1

    return MonitorResult(exit_code=exit_code, stop_reason=stop_reason, events=events)


def _kill_process(process: subprocess.Popen[str]) -> None:
    try:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    except OSError:
        pass


def monitor_process(
    process: subprocess.Popen[str],
    *,
    project_path: str,
    run_dir: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    stall_seconds: int = DEFAULT_STALL_SECONDS,
    actor_role: str = "worker",
) -> MonitorResult:
    events: list[MonitorEvent] = []
    start_time = _now()
    last_progress_time = start_time
    stop_reason = "completed"
    watchdog_reason: list[str] = []
    lock = threading.Lock()
    watchdog_stop = threading.Event()

    _record(
        events,
        "info",
        f"Monitoring started, role={actor_role}, timeout={timeout_seconds}s, stall={stall_seconds}s",
    )

    def _watchdog() -> None:
        while process.poll() is None and not watchdog_stop.is_set():
            if watchdog_stop.wait(STALL_CHECK_INTERVAL):
                break
            if process.poll() is not None:
                break
            elapsed = _now() - start_time
            if elapsed > timeout_seconds:
                with lock:
                    watchdog_reason.append("timeout")
                    _record(events, "timeout", f"Watchdog: hard timeout after {timeout_seconds}s")
                _kill_process(process)
                return
            with lock:
                idle = _now() - last_progress_time
            if idle > stall_seconds:
                with lock:
                    watchdog_reason.append("stall")
                    _record(
                        events,
                        "stall",
                        f"Watchdog: no output for {idle:.0f}s (limit {stall_seconds}s)",
                    )
                _kill_process(process)
                return

    watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
    watchdog_thread.start()

    try:
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip("\n")

            with lock:
                if watchdog_reason:
                    break

            elapsed = _now() - start_time

            if elapsed > timeout_seconds:
                _record(events, "timeout", f"Hard timeout after {timeout_seconds}s")
                stop_reason = "timeout"
                break

            if should_scan_line_for_policy(line):
                command_scan_text = command_policy_scan_text(line)
                dangerous = check_dangerous_command(command_scan_text)
                if dangerous:
                    _record(events, "dangerous_command", f"Detected: {dangerous} in: {line[:200]}")
                    stop_reason = "dangerous_command"
                    break

                manager_only = check_manager_only_git_lifecycle_command(command_scan_text, actor_role)
                if manager_only:
                    _record(
                        events,
                        "manager_only_git_lifecycle",
                        f"Detected manager-only Git lifecycle command for {actor_role}: "
                        f"{manager_only} in: {line[:200]}",
                    )
                    stop_reason = "manager_only_git_lifecycle"
                    break

                secret = check_secret_access(line, project_path)
                if secret:
                    _record(events, "secret_access", f"Secret-like file access: {secret}")
                    stop_reason = "secret_access"
                    break

                unrelated = check_unrelated_directory(line, project_path, (str(run_dir),))
                if unrelated:
                    _record(events, "unrelated_directory", f"Access outside project: {unrelated}")
                    stop_reason = "unrelated_directory"
                    break

            if _is_progress_line(line):
                with lock:
                    last_progress_time = _now()
            elif _now() - last_progress_time > stall_seconds:
                _record(events, "stall", f"No meaningful progress for {stall_seconds}s")
                stop_reason = "stall"
                break

    except Exception as exc:
        _record(events, "error", f"Monitor error: {exc}")
        stop_reason = "monitor_error"

    with lock:
        if watchdog_reason and stop_reason == "completed":
            stop_reason = watchdog_reason[0]

    if stop_reason != "completed":
        _record(events, "kill", f"Terminating OpenHands process (reason: {stop_reason})")
        _kill_process(process)

    exit_code = process.wait()
    watchdog_stop.set()
    watchdog_thread.join(timeout=5)
    _record(events, "exit", f"Process exited with code {exit_code}")

    save_events(run_dir, events)
    if stop_reason != "completed":
        detail = events[-2].message if len(events) >= 2 else stop_reason
        save_stop_reason(run_dir, stop_reason, detail)

    return MonitorResult(exit_code=exit_code, stop_reason=stop_reason, events=events)
