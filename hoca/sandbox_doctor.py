"""Sandbox posture checks for hoca doctor."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

from hoca.config import HocaConfig, load_config
from hoca.paths import repo_root
from hoca.sandbox_network import NETWORK_MODE_LIMITATIONS

DoctorLineStatus = Literal["ok", "warn", "fail"]

DEFAULT_SANDBOX_IMAGE = "hoca-sandbox:latest"
DEFAULT_SANDBOX_MEMORY = "8g"
DEFAULT_SANDBOX_PIDS = "512"
SANDBOX_SCRIPTS = ("run-openhands-sandboxed.sh", "sandbox-manager.sh")
DOCKER_SOCK_PATTERN = re.compile(r"docker\.sock|/var/run/docker\.sock", re.IGNORECASE)


def _config_value(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def detect_container_runtime() -> str | None:
    for command in ("docker", "podman"):
        if shutil.which(command):
            return command
    return None


def container_runtime_info(runtime: str) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            [runtime, "info"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if completed.returncode == 0:
        return True, f"{runtime} daemon is running."
    detail = (completed.stderr or completed.stdout or "unknown error").strip().splitlines()
    return False, detail[0] if detail else f"{runtime} info failed."


def sandbox_doctor_lines(
    config: HocaConfig,
    *,
    root: Path | None = None,
    sandbox_image: str | None = None,
    sandbox_memory: str | None = None,
    sandbox_pids: str | None = None,
) -> list[tuple[DoctorLineStatus, str]]:
    hoca_root = root or repo_root()
    scripts_dir = hoca_root / "scripts"
    image = sandbox_image or _config_value("HOCA_SANDBOX_IMAGE", DEFAULT_SANDBOX_IMAGE)
    memory = sandbox_memory or _config_value("HOCA_SANDBOX_MEMORY", DEFAULT_SANDBOX_MEMORY)
    pids = sandbox_pids or _config_value("HOCA_SANDBOX_PIDS", DEFAULT_SANDBOX_PIDS)

    lines: list[tuple[DoctorLineStatus, str]] = []

    if config.use_sandbox:
        lines.append(("ok", "Sandbox execution enabled (HOCA_USE_SANDBOX=true)."))
    else:
        lines.append(
            (
                "warn",
                "Host execution enabled (HOCA_USE_SANDBOX=false). "
                "Worker/reviewer OpenHands runs on the host with higher risk.",
            )
        )
        lines.append(
            (
                "warn",
                "Host execution is opt-in only. Prefer sandboxed execution for autonomous rounds.",
            )
        )

    network_mode = config.network_mode.strip().lower() or "offline"
    if network_mode == "offline":
        lines.append(("ok", "Sandbox network mode: offline (default; safest egress)."))
    elif network_mode == "full":
        lines.append(
            (
                "warn",
                "Sandbox network mode: full (unrestricted egress; explicit opt-in only).",
            )
        )
        limitation = NETWORK_MODE_LIMITATIONS.get("full")
        if limitation:
            lines.append(("warn", limitation))
    elif network_mode in {"package-install", "github-only"}:
        lines.append(
            (
                "warn",
                f"Sandbox network mode: {network_mode} (bridge egress without allowlisting).",
            )
        )
        limitation = NETWORK_MODE_LIMITATIONS.get(network_mode)
        if limitation:
            lines.append(("warn", limitation))
    else:
        lines.append(
            (
                "fail",
                "HOCA_NETWORK_MODE must be offline, package-install, github-only, or "
                f"full (got: {network_mode!r}).",
            )
        )

    runtime = detect_container_runtime()
    if runtime is None:
        if config.use_sandbox:
            lines.append(
                (
                    "fail",
                    "Neither docker nor podman is available. Install Docker Desktop, Colima, or Podman.",
                )
            )
        else:
            lines.append(
                (
                    "warn",
                    "Neither docker nor podman is available; only host execution is possible.",
                )
            )
        lines.extend(_script_static_checks(scripts_dir))
        lines.extend(_resource_limit_lines(scripts_dir, memory=memory, pids=pids))
        return lines

    runtime_path = shutil.which(runtime) or runtime
    lines.append(("ok", f"Container runtime found: {runtime} ({runtime_path})."))

    daemon_ok, daemon_message = container_runtime_info(runtime)
    if daemon_ok:
        lines.append(("ok", daemon_message))
    elif config.use_sandbox:
        lines.append(("fail", f"HOCA_USE_SANDBOX=true but {daemon_message}"))
    else:
        lines.append(("warn", f"Container runtime unavailable: {daemon_message}"))

    sandbox_script = scripts_dir / "run-openhands-sandboxed.sh"
    if sandbox_script.is_file() and os.access(sandbox_script, os.X_OK):
        lines.append(("ok", "Sandbox wrapper script is executable: run-openhands-sandboxed.sh"))
    elif config.use_sandbox:
        lines.append(
            (
                "fail",
                "HOCA_USE_SANDBOX=true but run-openhands-sandboxed.sh is missing or not executable.",
            )
        )
    else:
        lines.append(("warn", "Sandbox wrapper script is missing or not executable."))

    if daemon_ok:
        lines.extend(_image_checks(runtime, image, require_image=config.use_sandbox))

    lines.extend(_script_static_checks(scripts_dir))
    lines.extend(_resource_limit_lines(scripts_dir, memory=memory, pids=pids))
    return lines


def _image_checks(
    runtime: str,
    image: str,
    *,
    require_image: bool,
) -> list[tuple[DoctorLineStatus, str]]:
    lines: list[tuple[DoctorLineStatus, str]] = []
    try:
        completed = subprocess.run(
            [runtime, "image", "inspect", image],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        lines.append(("warn", f"Could not inspect sandbox image {image!r}."))
        return lines

    if completed.returncode != 0:
        message = (
            f"Sandbox image not available: {image} "
            f"(run: scripts/sandbox-manager.sh build)."
        )
        lines.append(("fail" if require_image else "warn", message))
        return lines

    lines.append(("ok", f"Sandbox image is available: {image}"))

    try:
        user_completed = subprocess.run(
            [runtime, "image", "inspect", "--format", "{{.Config.User}}", image],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        lines.append(("warn", f"Could not read default user for sandbox image {image!r}."))
        return lines

    if user_completed.returncode != 0:
        lines.append(("warn", f"Could not read default user for sandbox image {image!r}."))
        return lines

    configured_user = user_completed.stdout.strip()
    if not configured_user or configured_user.lower() == "root":
        lines.append(
            (
                "fail",
                f"Sandbox image {image!r} defaults to root (Config.User={configured_user!r}). "
                "Rebuild from docker/Dockerfile.sandbox with USER worker.",
            )
        )
    else:
        lines.append(
            (
                "ok",
                f"Sandbox image default user is non-root: {configured_user!r}.",
            )
        )
    return lines


def _script_static_checks(scripts_dir: Path) -> list[tuple[DoctorLineStatus, str]]:
    lines: list[tuple[DoctorLineStatus, str]] = []
    for script_name in SANDBOX_SCRIPTS:
        script_path = scripts_dir / script_name
        if not script_path.is_file():
            lines.append(("warn", f"Sandbox script missing: {script_name}"))
            continue

        content = script_path.read_text(encoding="utf-8")
        if "GITHUB_TOKEN" in content:
            lines.append(("fail", f"Sandbox script forwards GITHUB_TOKEN: {script_name}"))
        else:
            lines.append(("ok", f"Sandbox script does not forward GITHUB_TOKEN: {script_name}"))

        if DOCKER_SOCK_PATTERN.search(content):
            lines.append(("fail", f"Sandbox script mounts Docker socket: {script_name}"))
        else:
            lines.append(("ok", f"Sandbox script does not mount Docker socket: {script_name}"))

        if "--cap-drop=ALL" in content:
            lines.append(("ok", f"Sandbox script drops all capabilities: {script_name}"))
        else:
            lines.append(("fail", f"Sandbox script must use --cap-drop=ALL: {script_name}"))

        if "NET_RAW" in content or "--cap-add=" in content:
            lines.append(("fail", f"Sandbox script grants extra Linux capabilities: {script_name}"))
        else:
            lines.append(("ok", f"Sandbox script does not grant NET_RAW or cap-add: {script_name}"))

    return lines


def _resource_limit_lines(
    scripts_dir: Path,
    *,
    memory: str,
    pids: str,
) -> list[tuple[DoctorLineStatus, str]]:
    lines: list[tuple[DoctorLineStatus, str]] = []
    primary = scripts_dir / "run-openhands-sandboxed.sh"
    if not primary.is_file():
        lines.append(("warn", "Cannot verify sandbox resource limits; wrapper script is missing."))
        return lines

    content = primary.read_text(encoding="utf-8")
    if "--memory=" in content or '--memory="${' in content:
        lines.append(
            (
                "ok",
                f"Sandbox memory limit configured (HOCA_SANDBOX_MEMORY={memory!r}, default {DEFAULT_SANDBOX_MEMORY!r}).",
            )
        )
    else:
        lines.append(("fail", "run-openhands-sandboxed.sh does not set a Docker memory limit."))

    if "--pids-limit=" in content or '--pids-limit="${' in content:
        lines.append(
            (
                "ok",
                f"Sandbox PID limit configured (HOCA_SANDBOX_PIDS={pids!r}, default {DEFAULT_SANDBOX_PIDS!r}).",
            )
        )
    else:
        lines.append(("fail", "run-openhands-sandboxed.sh does not set a Docker PID limit."))

    if not memory:
        lines.append(("warn", "HOCA_SANDBOX_MEMORY is empty; container memory is unconstrained."))
    if not pids:
        lines.append(("warn", "HOCA_SANDBOX_PIDS is empty; container PID count is unconstrained."))

    return lines


def _doctor_main() -> int:
    cfg = load_config()
    failed = False
    for status, message in sandbox_doctor_lines(cfg):
        tag = {"ok": "[OK]", "warn": "[WARN]", "fail": "[FAIL]"}[status]
        print(f"{tag} {message}")
        if status == "fail":
            failed = True
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sandbox posture checks for hoca doctor.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor-checks", help="Print sandbox doctor lines.")
    args = parser.parse_args(argv)
    if args.command == "doctor-checks":
        return _doctor_main()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
