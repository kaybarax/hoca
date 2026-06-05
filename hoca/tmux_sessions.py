from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hoca.agent_adapters import AdapterCommandError, AdapterUnavailableError


TMUX_COMMAND_TIMEOUT_SECONDS = 2.0
TMUX_SESSION_NAME_MAX = 100


@dataclass(frozen=True)
class TmuxSessionMetadata:
    session_name: str
    session_id: str | None
    window_id: str | None
    pane_id: str | None
    log_path: str | None


def _sanitize_session_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]", "-", value).strip("-")
    if not normalized:
        normalized = "lane"
    return normalized[:TMUX_SESSION_NAME_MAX]


def _tmux() -> str | None:
    return shutil.which("tmux")


def is_tmux_available() -> bool:
    return _tmux() is not None


def _run_tmux(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    binary = _tmux()
    if binary is None:
        raise AdapterUnavailableError("tmux is not available")

    return subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
    )


def _first_line(output: str) -> str | None:
    for line in output.splitlines():
        value = line.strip()
        if value:
            return value
    return None


def session_exists(session_name: str) -> bool:
    if not is_tmux_available():
        return False
    safe = _sanitize_session_name(session_name)
    try:
        result = _run_tmux(["has-session", "-t", safe], check=False)
    except AdapterUnavailableError:
        return False
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def launch_tmux_session(
    *,
    session_name: str,
    command: str,
    working_dir: Path,
    log_path: Path | None = None,
) -> TmuxSessionMetadata:
    if not is_tmux_available():
        raise AdapterUnavailableError("tmux is not available")

    if not command.strip():
        raise AdapterCommandError("tmux command cannot be empty")

    safe_session = _sanitize_session_name(session_name)
    if session_exists(safe_session):
        raise AdapterCommandError(f"tmux session already exists: {safe_session}")

    working_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "new-session",
        "-d",
        "-s",
        safe_session,
        "-c",
        str(working_dir),
        command,
    ]
    created = _run_tmux(cmd)
    if created.returncode not in (0, 1):
        raise AdapterCommandError("Failed to create tmux session")

    if log_path is not None:
        pipe_cmd = f"cat > {shlex.quote(str(log_path))}"
        _run_tmux(["pipe-pane", "-t", safe_session, "-o", pipe_cmd], check=False)

    session_id = None
    window_id = None
    pane_id = None

    session_result = _run_tmux(["list-sessions", "-F", "#{session_id}", "-t", safe_session], check=False)
    if session_result.returncode == 0:
        session_id = _first_line(session_result.stdout)

    window_result = _run_tmux(["list-windows", "-t", safe_session, "-F", "#{window_id}"], check=False)
    if window_result.returncode == 0:
        window_id = _first_line(window_result.stdout)

    pane_result = _run_tmux(["list-panes", "-t", safe_session, "-F", "#{pane_id}"], check=False)
    if pane_result.returncode == 0:
        pane_id = _first_line(pane_result.stdout)

    return TmuxSessionMetadata(
        session_name=safe_session,
        session_id=session_id,
        window_id=window_id,
        pane_id=pane_id,
        log_path=str(log_path) if log_path else None,
    )


def send_to_session(session_name: str, payload: str) -> None:
    if not session_exists(session_name):
        raise AdapterCommandError(f"tmux session not running: {session_name}")

    if payload is None:
        raise ValueError("payload is required")

    safe = _sanitize_session_name(session_name)
    _run_tmux(["send-keys", "-t", safe, payload, "Enter"], check=False)


def stop_session(session_name: str) -> None:
    safe = _sanitize_session_name(session_name)
    if not session_exists(safe):
        return
    _run_tmux(["kill-session", "-t", safe], check=False)


def snapshot_session_alive(session_name: str) -> bool:
    return session_exists(session_name)
