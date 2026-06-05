"""Resolve per-role LLM environment for runners and doctor checks."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Literal

from hoca.config import HocaConfig, ModelSlot, RoleName, load_config

DoctorLineStatus = Literal["ok", "warn", "fail"]

MODEL_ROLES: tuple[RoleName, ...] = ("manager", "worker", "reviewer")
PROVIDER_API_KEY_ENV_KEYS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "TOGETHER_API_KEY",
    "XAI_API_KEY",
)
HERMES_PROVIDER_BY_MODEL_PREFIX: dict[str, str] = {
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "gemini": "google",
    "google": "google",
    "openrouter": "openrouter",
    "together": "together",
    "together_ai": "together",
    "xai": "xai",
}


@dataclass(frozen=True)
class RoleLlmSelection:
    """Resolved LLM settings for one agent role."""

    role: RoleName
    slot_name: str
    llm_model: str
    base_url: str
    api_key: str

    def env_vars(self) -> dict[str, str]:
        out = {
            "LLM_MODEL": self.llm_model,
            "LLM_BASE_URL": self.base_url,
            "LLM_API_KEY": self.api_key,
            "HOCA_SELECTED_MODEL_SLOT": self.slot_name,
        }
        out.update(_provider_api_key_env_vars(self.llm_model, self.api_key))
        if self.llm_model.startswith("ollama/"):
            out["OLLAMA_MODEL"] = self.llm_model.removeprefix("ollama/")
            out["HOCA_REQUESTED_MODEL"] = out["OLLAMA_MODEL"]
        return out


def should_resolve_role_model(config: HocaConfig, env: dict[str, str] | None = None) -> bool:
    if (env or os.environ).get("HOCA_SKIP_ROLE_MODEL_RESOLUTION", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    return config.model_pool.is_active


def resolve_role_llm(role: RoleName, config: HocaConfig) -> RoleLlmSelection:
    if config.model_pool.is_active:
        slot = config.model_pool.resolve_role(role)
        if slot is None:
            raise ValueError(f"Cannot resolve model for role {role!r}")
        return _selection_from_slot(role, slot)

    fallback_model = config.ollama_model
    if not fallback_model.startswith("ollama/"):
        fallback_model = f"ollama/{fallback_model}"
    return RoleLlmSelection(
        role=role,
        slot_name="ollama-fallback",
        llm_model=fallback_model,
        base_url=config.ollama_base_url,
        api_key="ollama",
    )


def _selection_from_slot(role: RoleName, slot: ModelSlot) -> RoleLlmSelection:
    return RoleLlmSelection(
        role=role,
        slot_name=slot.name,
        llm_model=slot.model,
        base_url=slot.base_url,
        api_key=slot.api_key,
    )


def _provider_api_key_env_vars(model: str, api_key: str) -> dict[str, str]:
    if not api_key:
        return {}
    provider = model.split("/", 1)[0].lower()
    keys_by_provider = {
        "anthropic": ("ANTHROPIC_API_KEY",),
        "deepseek": ("DEEPSEEK_API_KEY",),
        "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        "openai": ("OPENAI_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
        "together_ai": ("TOGETHER_API_KEY",),
        "together": ("TOGETHER_API_KEY",),
        "xai": ("XAI_API_KEY",),
    }
    return {key: api_key for key in keys_by_provider.get(provider, ())}


def hermes_provider_for_model(model: str) -> str:
    provider = model.split("/", 1)[0].lower()
    return HERMES_PROVIDER_BY_MODEL_PREFIX.get(provider, "")


def pool_credential_env_keys(env: dict[str, str]) -> list[str]:
    keys = [
        "LLM_MODEL",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "HOCA_SELECTED_MODEL_SLOT",
        *PROVIDER_API_KEY_ENV_KEYS,
    ]
    for role in MODEL_ROLES:
        for suffix in ("NAME", "MODEL", "BASE_URL", "API_KEY"):
            keys.append(f"HOCA_{role.upper()}_MODEL_{suffix}")
    return [key for key in keys if key in env]


def strip_pool_credentials(env: dict[str, str]) -> dict[str, str]:
    cleaned = dict(env)
    for key in pool_credential_env_keys(cleaned):
        cleaned.pop(key, None)
    return cleaned


def apply_role_to_env(
    role: RoleName,
    config: HocaConfig,
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    source = dict(env if env is not None else os.environ)
    if not should_resolve_role_model(config, source):
        return source
    selection = resolve_role_llm(role, config)
    cleaned = strip_pool_credentials(source)
    cleaned.update(selection.env_vars())
    return cleaned


def export_shell(role: RoleName, *, config: HocaConfig | None = None) -> str:
    cfg = config or load_config()
    if not should_resolve_role_model(cfg):
        return ""
    selection = resolve_role_llm(role, cfg)
    lines = [
        f"export LLM_MODEL={_shell_quote(selection.llm_model)}",
        f"export LLM_BASE_URL={_shell_quote(selection.base_url)}",
        f"export LLM_API_KEY={_shell_quote(selection.api_key)}",
        f"export HOCA_SELECTED_MODEL_SLOT={_shell_quote(selection.slot_name)}",
    ]
    for key, value in _provider_api_key_env_vars(selection.llm_model, selection.api_key).items():
        lines.append(f"export {key}={_shell_quote(value)}")
    if selection.llm_model.startswith("ollama/"):
        ollama_name = selection.llm_model.removeprefix("ollama/")
        lines.append(f"export OLLAMA_MODEL={_shell_quote(ollama_name)}")
        lines.append(f"export HOCA_REQUESTED_MODEL={_shell_quote(ollama_name)}")
    return "\n".join(lines) + "\n"


def log_line_for_selection(selection: RoleLlmSelection) -> str:
    return (
        f"Resolved {selection.role} model slot: {selection.slot_name} "
        f"(provider model: {selection.llm_model})"
    )


def model_pool_doctor_lines(config: HocaConfig) -> list[tuple[DoctorLineStatus, str]]:
    lines: list[tuple[DoctorLineStatus, str]] = []
    pool = config.model_pool

    if not pool.is_active:
        lines.append(("ok", "Model pool inactive; using Ollama fallback configuration."))
        return lines

    active = pool.active_slots
    lines.append(("ok", f"Model pool active with {len(active)} configured slot(s)."))
    for slot in active:
        lines.append(
            (
                "ok",
                f"Slot {slot.name!r}: model={slot.model!r}, api_key={slot.safe_repr()['api_key']}",
            )
        )

    for role in ("manager", "worker", "reviewer", "fallback"):
        try:
            resolved = pool.resolve_role(role)  # type: ignore[arg-type]
        except ValueError as exc:
            lines.append(("fail", f"Role {role}: {exc}"))
            continue
        if resolved is None:
            lines.append(("fail", f"Role {role}: could not resolve a model slot."))
            continue
        lines.append(("ok", f"Role {role} resolves to slot {resolved.name!r} ({resolved.model})."))

    worker_slot = pool.resolve_role("worker")
    reviewer_slot = pool.resolve_role("reviewer")
    if (
        worker_slot is not None
        and reviewer_slot is not None
        and worker_slot.name == reviewer_slot.name
    ):
        lines.append(
            (
                "warn",
                "Worker and reviewer default to the same model slot "
                f"({worker_slot.name!r}). Consider separate models for coding vs review.",
            )
        )

    for slot in active:
        if slot.model.startswith("ollama/"):
            lines.extend(_ollama_availability_lines(slot))

    return lines


def _ollama_availability_lines(slot: ModelSlot) -> list[tuple[DoctorLineStatus, str]]:
    alias = slot.model.removeprefix("ollama/")
    lines: list[tuple[DoctorLineStatus, str]] = []
    try:
        import subprocess

        completed = subprocess.run(
            ["ollama", "list"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        lines.append(
            (
                "warn",
                f"Could not verify local Ollama model {alias!r} for slot {slot.name!r} "
                "(ollama command unavailable).",
            )
        )
        return lines

    if completed.returncode != 0:
        lines.append(
            (
                "warn",
                f"Could not list Ollama models for slot {slot.name!r} ({alias!r}).",
            )
        )
        return lines

    found = False
    for row in completed.stdout.splitlines()[1:]:
        if not row.strip():
            continue
        installed_name = row.split()[0]
        if installed_name == alias or installed_name == f"{alias}:latest":
            found = True
            break
    if found:
        lines.append(("ok", f"Ollama model available for slot {slot.name!r}: {alias!r}"))
    else:
        lines.append(
            (
                "warn",
                f"Ollama model not installed for slot {slot.name!r}: {alias!r} "
                "(best-effort check).",
            )
        )
    return lines


def _shell_quote(value: str) -> str:
    if value == "":
        return "''"
    if all(ch.isalnum() or ch in "/._-:" for ch in value):
        return value
    escaped = value.replace("'", "'\"'\"'")
    return f"'{escaped}'"


def _export_main(role: str) -> int:
    try:
        print(export_shell(role))  # type: ignore[arg-type]
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _doctor_main() -> int:
    cfg = load_config()
    failed = False
    for status, message in model_pool_doctor_lines(cfg):
        tag = {"ok": "[OK]", "warn": "[WARN]", "fail": "[FAIL]"}[status]
        print(f"{tag} {message}")
        if status == "fail":
            failed = True
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve role-specific LLM environment.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Print shell export statements.")
    export_parser.add_argument("role", choices=["manager", "worker", "reviewer", "fallback"])

    subparsers.add_parser("doctor-checks", help="Print model pool doctor lines.")

    args = parser.parse_args(argv)
    if args.command == "export":
        return _export_main(args.role)
    if args.command == "doctor-checks":
        return _doctor_main()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
