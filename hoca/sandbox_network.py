"""Sandbox network mode resolution and best-effort Docker network controls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from hoca.config import load_config
from hoca.contracts import HocaSandboxPolicy, NetworkMode
from hoca.run_layout import sandbox_policy_path, task_spec_path

VALID_NETWORK_MODES: frozenset[str] = frozenset(
    ("offline", "package-install", "github-only", "full")
)
DEFAULT_NETWORK_MODE: NetworkMode = "offline"

# Modes that use the default Docker bridge (egress not allowlisted).
_BRIDGE_NETWORK_MODES: frozenset[str] = frozenset(
    ("package-install", "github-only", "full")
)

NETWORK_MODE_LIMITATIONS: dict[str, str] = {
    "offline": (
        "Docker runs with --network none. No package registry or GitHub egress. "
        "Host LLM endpoints (for example host.docker.internal) are not reachable; "
        "use an in-container LLM or run the worker phase with package-install when needed."
    ),
    "package-install": (
        "Docker uses the default bridge network. HOCA does not enforce registry-only "
        "egress; treat this mode as permission to reach package registries, not as an "
        "allowlist."
    ),
    "github-only": (
        "Docker uses the default bridge network. HOCA does not enforce GitHub-only "
        "egress; treat this mode as broader-than-offline intent, not as an allowlist."
    ),
    "full": (
        "Docker uses the default bridge network with unrestricted egress. Requires "
        "explicit opt-in via HOCA_NETWORK_MODE=full or task-spec sandbox.network_mode: full."
    ),
}


class NetworkModeError(ValueError):
    """Invalid or disallowed sandbox network configuration."""


def normalize_network_mode(value: str | None, *, field: str = "network_mode") -> NetworkMode:
    if value is None or not str(value).strip():
        raise NetworkModeError(f"Missing {field}")
    mode = str(value).strip().lower()
    if mode not in VALID_NETWORK_MODES:
        raise NetworkModeError(
            f"{field} must be one of {sorted(VALID_NETWORK_MODES)}, got: {value!r}"
        )
    return mode  # type: ignore[return-value]


def _read_task_spec_sandbox_mode(run_dir: Path | None) -> NetworkMode | None:
    if run_dir is None:
        return None
    spec_path = task_spec_path(run_dir)
    if not spec_path.is_file():
        return None
    try:
        data = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    sandbox = data.get("sandbox")
    if not isinstance(sandbox, dict):
        return None
    raw_mode = sandbox.get("network_mode")
    if raw_mode is None:
        return None
    return normalize_network_mode(str(raw_mode), field="sandbox.network_mode")


def _config_network_mode() -> NetworkMode:
    cfg = load_config()
    raw = getattr(cfg, "network_mode", DEFAULT_NETWORK_MODE)
    return normalize_network_mode(str(raw or DEFAULT_NETWORK_MODE), field="HOCA_NETWORK_MODE")


def resolve_network_mode(
    *,
    role: str | None = None,
    run_dir: Path | None = None,
    explicit_mode: str | None = None,
    env_mode: str | None = None,
) -> NetworkMode:
    """Resolve the effective sandbox network mode for a worker/reviewer phase."""
    if explicit_mode is not None and str(explicit_mode).strip():
        mode = normalize_network_mode(explicit_mode, field="explicit network_mode")
    else:
        task_mode = _read_task_spec_sandbox_mode(run_dir)
        if task_mode is not None:
            mode = task_mode
        elif env_mode is not None and str(env_mode).strip():
            mode = normalize_network_mode(env_mode, field="HOCA_NETWORK_MODE")
        else:
            mode = _config_network_mode()

    normalized_role = (role or "").strip().lower()
    if normalized_role == "reviewer" and explicit_mode is None:
        # Review passes should stay offline unless a phase explicitly overrides.
        mode = DEFAULT_NETWORK_MODE

    if mode == "full":
        assert_full_network_opt_in(
            task_mode=_read_task_spec_sandbox_mode(run_dir),
            env_mode=env_mode,
            explicit_mode=explicit_mode,
        )

    return mode


def assert_full_network_opt_in(
    *,
    task_mode: NetworkMode | None = None,
    env_mode: str | None = None,
    explicit_mode: str | None = None,
) -> None:
    if explicit_mode is not None and str(explicit_mode).strip():
        normalize_network_mode(explicit_mode)
        return
    if task_mode == "full":
        return
    if env_mode is not None and normalize_network_mode(env_mode) == "full":
        return
    cfg_mode = _config_network_mode()
    if cfg_mode == "full":
        return
    raise NetworkModeError(
        "network_mode 'full' requires explicit opt-in via HOCA_NETWORK_MODE=full, "
        "task-spec sandbox.network_mode: full, or an explicit runtime override."
    )


def docker_run_network_args(mode: NetworkMode) -> list[str]:
    """Best-effort Docker CLI flags for the requested network mode."""
    normalized = normalize_network_mode(mode)
    if normalized == "offline":
        return ["--network", "none"]
    return []


def package_install_allowed(mode: NetworkMode) -> bool:
    return normalize_network_mode(mode) in _BRIDGE_NETWORK_MODES


def record_effective_sandbox_policy(
    run_dir: Path,
    *,
    role: str,
    effective_mode: NetworkMode,
) -> Path:
    """Merge effective network mode into sandbox-policy.json for the run."""
    run_dir = run_dir.resolve()
    policy_path = sandbox_policy_path(run_dir)
    if policy_path.is_file():
        try:
            existing = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    else:
        existing = {}

    if not isinstance(existing, dict):
        existing = {}

    enabled = existing.get("enabled", True)
    policy = HocaSandboxPolicy(
        enabled=bool(enabled),
        network_mode=effective_mode,
    )
    payload: dict[str, Any] = policy.to_dict()
    payload["effective_network_mode"] = effective_mode
    payload["resolved_for_role"] = role
    payload["docker_network_args"] = docker_run_network_args(effective_mode)
    payload["limitations"] = NETWORK_MODE_LIMITATIONS[effective_mode]
    policy_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return policy_path


def _cli_resolve(args: argparse.Namespace) -> int:
    mode = resolve_network_mode(
        role=args.role,
        run_dir=Path(args.run_dir).resolve() if args.run_dir else None,
        explicit_mode=args.mode,
        env_mode=args.env_mode,
    )
    print(mode)
    return 0


def _cli_docker_args(args: argparse.Namespace) -> int:
    for flag in docker_run_network_args(normalize_network_mode(args.mode)):
        print(flag)
    return 0


def _cli_record(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    mode = resolve_network_mode(
        role=args.role,
        run_dir=run_dir,
        explicit_mode=args.mode,
        env_mode=args.env_mode,
    )
    path = record_effective_sandbox_policy(run_dir, role=args.role, effective_mode=mode)
    print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HOCA sandbox network mode helpers")
    sub = parser.add_subparsers(dest="command", required=True)

    resolve_parser = sub.add_parser("resolve", help="Print effective network mode")
    resolve_parser.add_argument("--role", default="worker")
    resolve_parser.add_argument("--run-dir", default=None)
    resolve_parser.add_argument("--mode", default=None, help="Explicit override")
    resolve_parser.add_argument("--env-mode", default=None, dest="env_mode")
    resolve_parser.set_defaults(func=_cli_resolve)

    docker_parser = sub.add_parser("docker-args", help="Print docker run network flags")
    docker_parser.add_argument("--mode", required=True)
    docker_parser.set_defaults(func=_cli_docker_args)

    record_parser = sub.add_parser("record", help="Update sandbox-policy.json for a phase")
    record_parser.add_argument("--role", required=True)
    record_parser.add_argument("--run-dir", required=True)
    record_parser.add_argument("--mode", default=None)
    record_parser.add_argument("--env-mode", default=None, dest="env_mode")
    record_parser.set_defaults(func=_cli_record)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except NetworkModeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
