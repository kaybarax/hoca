from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from hoca.security import is_secret_like_path

DANGEROUS_COMMANDS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+(-\w*[rR]\w*\s+.*-\w*f|.*-\w*f\w*\s+.*-\w*[rR]|-rf|-Rf)\b"),
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


def check_dangerous_command(line: str) -> str | None:
    for pattern in DANGEROUS_COMMANDS:
        if pattern.search(line):
            return pattern.pattern
    return None


def check_secret_access(line: str, project_path: str) -> str | None:
    tokens = re.findall(r'[\w./\-]+\.(?:env|pem|key|p12|pfx|kubeconfig)\b', line)
    for token in tokens:
        if is_secret_like_path(token):
            return token
    path_candidates = re.findall(r'(?:cat|less|head|tail|vim|nano|open|cp|mv|rm)\s+([\w./\-]+)', line)
    for candidate in path_candidates:
        if is_secret_like_path(candidate):
            return candidate
    return None


def check_unrelated_directory(line: str, project_path: str) -> str | None:
    abs_refs = re.findall(r'(?:cd|cat|ls|rm|cp|mv|vi|vim|nano|open)\s+(/[^\s;|&]+)', line)
    project_resolved = os.path.realpath(project_path)
    tmp_prefixes = ("/tmp", "/private/tmp", "/var/tmp")
    for ref in abs_refs:
        ref_resolved = os.path.realpath(ref)
        if not ref_resolved.startswith(project_resolved + "/") and ref_resolved != project_resolved:
            if any(ref_resolved == t or ref_resolved.startswith(t + "/") for t in tmp_prefixes):
                continue
            return ref
    return None


def _is_progress_line(line: str) -> bool:
    if not line.strip():
        return False
    noise = [
        "Thinking...", "Processing...", "Waiting",
        "Retrying", "retry", "timeout",
        "No changes", "Nothing to do",
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


def monitor_process(
    process: subprocess.Popen[str],
    *,
    project_path: str,
    run_dir: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    stall_seconds: int = DEFAULT_STALL_SECONDS,
) -> MonitorResult:
    events: list[MonitorEvent] = []
    start_time = _now()
    last_progress_time = start_time
    stop_reason = "completed"

    _record(events, "info", f"Monitoring started, timeout={timeout_seconds}s, stall={stall_seconds}s")

    try:
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip("\n")
            elapsed = _now() - start_time

            if elapsed > timeout_seconds:
                _record(events, "timeout", f"Hard timeout after {timeout_seconds}s")
                stop_reason = "timeout"
                break

            dangerous = check_dangerous_command(line)
            if dangerous:
                _record(events, "dangerous_command", f"Detected: {dangerous} in: {line[:200]}")
                stop_reason = "dangerous_command"
                break

            secret = check_secret_access(line, project_path)
            if secret:
                _record(events, "secret_access", f"Secret-like file access: {secret}")
                stop_reason = "secret_access"
                break

            unrelated = check_unrelated_directory(line, project_path)
            if unrelated:
                _record(events, "unrelated_directory", f"Access outside project: {unrelated}")
                stop_reason = "unrelated_directory"
                break

            if _is_progress_line(line):
                last_progress_time = _now()
            elif _now() - last_progress_time > stall_seconds:
                _record(events, "stall", f"No meaningful progress for {stall_seconds}s")
                stop_reason = "stall"
                break

    except Exception as exc:
        _record(events, "error", f"Monitor error: {exc}")
        stop_reason = "monitor_error"

    if stop_reason != "completed":
        _record(events, "kill", f"Terminating OpenHands process (reason: {stop_reason})")
        try:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        except OSError:
            pass

    exit_code = process.wait()
    _record(events, "exit", f"Process exited with code {exit_code}")

    save_events(run_dir, events)
    if stop_reason != "completed":
        detail = events[-2].message if len(events) >= 2 else stop_reason
        save_stop_reason(run_dir, stop_reason, detail)

    return MonitorResult(exit_code=exit_code, stop_reason=stop_reason, events=events)
