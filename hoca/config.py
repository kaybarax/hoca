from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off", ""})

_SECRET_PATTERN = re.compile(r"(token|secret|password|api_key|private_key)", re.IGNORECASE)
RoleName = Literal["manager", "worker", "reviewer", "fallback"]


def parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    raise ValueError(f"Cannot parse {value!r} as boolean")


def _resolve_path(value: str | None, *, default: Path | None = None) -> Path | None:
    if value is None:
        return default
    return Path(value).expanduser().resolve()


@dataclass(frozen=True)
class SafetyPolicy:
    auto_merge: bool = False
    require_pull_request: bool = True
    forbid_direct_push_to_main: bool = True
    require_clean_working_tree: bool = True
    stop_on_unrelated_changes: bool = True
    stop_on_secret_changes: bool = True
    stop_on_test_failure: bool = True
    require_review_approval: bool = True
    stop_before_commit_until_selective_staging: bool = True
    allow_high_risk_auto_merge: bool = False


DEFAULT_POLICY = SafetyPolicy()


@dataclass(frozen=True)
class ModelSlot:
    name: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = ""

    @property
    def is_active(self) -> bool:
        return bool(self.name and self.model)

    def safe_repr(self) -> dict[str, str]:
        return {
            "name": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "api_key": "***" if self.api_key else "(unset)",
        }


@dataclass(frozen=True)
class ModelPoolConfig:
    slots: tuple[ModelSlot, ...] = ()
    manager_model: str = ""
    worker_model: str = ""
    reviewer_model: str = ""
    fallback_model: str = ""

    @property
    def active_slots(self) -> tuple[ModelSlot, ...]:
        return tuple(slot for slot in self.slots if slot.is_active)

    @property
    def is_active(self) -> bool:
        return bool(self.active_slots)

    def slot_by_name(self) -> dict[str, ModelSlot]:
        return {slot.name: slot for slot in self.active_slots}

    def resolve_role(self, role: RoleName) -> ModelSlot | None:
        if not self.is_active:
            return None
        requested = {
            "manager": self.manager_model,
            "worker": self.worker_model,
            "reviewer": self.reviewer_model,
            "fallback": self.fallback_model,
        }[role]
        selected_name = requested or self.fallback_model
        if not selected_name:
            raise ValueError(
                f"HOCA_{role.upper()}_MODEL is unset and HOCA_FALLBACK_MODEL is not configured"
            )
        slots = self.slot_by_name()
        try:
            return slots[selected_name]
        except KeyError as exc:
            raise ValueError(f"Configured model name is not in the active model pool: {selected_name}") from exc

    def safe_repr(self) -> dict[str, object]:
        return {
            "slots": [slot.safe_repr() for slot in self.slots],
            "manager_model": self.manager_model,
            "worker_model": self.worker_model,
            "reviewer_model": self.reviewer_model,
            "fallback_model": self.fallback_model,
        }


@dataclass(frozen=True)
class HocaConfig:
    use_hermes_profiles: bool = False
    use_structured_reports: bool = True
    use_kanban: bool = False
    use_sandbox: bool = True
    max_total_rounds: int = 3

    auto_merge: bool = False
    require_tests: bool = True
    stop_on_dirty_tree: bool = True
    dev_branch: str = "main"
    sync_dev_branch: bool = True
    auto_stage_reviewed_changes: bool = True

    workspace_root: Path | None = None

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen-14b-pro"
    llm_model: str = "ollama/qwen-14b-pro"
    llm_base_url: str = "http://127.0.0.1:11434"
    model_pool: ModelPoolConfig = ModelPoolConfig()

    webhook_secret: str = ""
    webhook_url: str = ""
    allowed_repos: str = ""
    max_webhook_bytes: int = 65536

    notify_telegram: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    def safe_repr(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if _SECRET_PATTERN.search(field_name):
                out[field_name] = "***" if value else "(unset)"
            elif hasattr(value, "safe_repr"):
                out[field_name] = value.safe_repr()
            else:
                out[field_name] = str(value)
        return out


def _load_model_pool(config_value) -> ModelPoolConfig:
    from hoca.model_pool import validate_model_pool_config

    slots = tuple(
        ModelSlot(
            name=config_value(f"HOCA_MODEL_{index}_NAME"),
            model=config_value(f"HOCA_MODEL_{index}_MODEL"),
            base_url=config_value(f"HOCA_MODEL_{index}_BASE_URL"),
            api_key=config_value(f"HOCA_MODEL_{index}_API_KEY"),
        )
        for index in range(1, 6)
    )
    pool = ModelPoolConfig(
        slots=slots,
        manager_model=config_value("HOCA_MANAGER_MODEL"),
        worker_model=config_value("HOCA_WORKER_MODEL"),
        reviewer_model=config_value("HOCA_REVIEWER_MODEL"),
        fallback_model=config_value("HOCA_FALLBACK_MODEL"),
    )
    validate_model_pool_config(pool)
    return pool


def _resolve_max_total_rounds(config_value) -> int:
    explicit = config_value("HOCA_MAX_TOTAL_ROUNDS")
    if explicit:
        return int(explicit)
    legacy = config_value("HOCA_MAX_REPAIR_ATTEMPTS")
    if legacy:
        return int(legacy) + 1
    return 3


def load_config(*, dotenv_path: Path | None = None) -> HocaConfig:
    dotenv: dict[str, str] = {}
    if dotenv_path is not None:
        dotenv = {k: v for k, v in dotenv_values(dotenv_path).items() if v is not None}
    else:
        default_dotenv = Path(".env")
        if default_dotenv.exists():
            dotenv = {k: v for k, v in dotenv_values(default_dotenv).items() if v is not None}

    def config_value(name: str, default: str = "") -> str:
        return os.environ.get(name, dotenv.get(name, default))

    workspace_root = _resolve_path(config_value("HOCA_WORKSPACE_ROOT") or None)

    llm_model = config_value("LLM_MODEL", "ollama/qwen-14b-pro")
    if llm_model.startswith("ollama/"):
        default_base_url = "http://127.0.0.1:11434"
    elif llm_model.startswith("openai/"):
        default_base_url = "http://localhost:1234/v1"
    else:
        default_base_url = ""

    return HocaConfig(
        use_hermes_profiles=parse_bool(
            config_value("HOCA_USE_HERMES_PROFILES") or None, default=False
        ),
        use_structured_reports=parse_bool(
            config_value("HOCA_USE_STRUCTURED_REPORTS") or None, default=True
        ),
        use_kanban=parse_bool(config_value("HOCA_USE_KANBAN") or None, default=False),
        use_sandbox=parse_bool(config_value("HOCA_USE_SANDBOX") or None, default=True),
        max_total_rounds=_resolve_max_total_rounds(config_value),
        auto_merge=parse_bool(config_value("HOCA_AUTO_MERGE") or None, default=False),
        require_tests=parse_bool(config_value("HOCA_REQUIRE_TESTS") or None, default=True),
        stop_on_dirty_tree=parse_bool(
            config_value("HOCA_STOP_ON_DIRTY_TREE") or None, default=True
        ),
        dev_branch=config_value("HOCA_DEV_BRANCH", "main"),
        sync_dev_branch=parse_bool(config_value("HOCA_SYNC_DEV_BRANCH") or None, default=True),
        auto_stage_reviewed_changes=parse_bool(
            config_value("HOCA_AUTO_STAGE_REVIEWED_CHANGES") or None, default=True
        ),
        workspace_root=workspace_root,
        ollama_base_url=config_value("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        ollama_model=config_value("OLLAMA_MODEL", "qwen-14b-pro"),
        llm_model=llm_model,
        llm_base_url=config_value("LLM_BASE_URL", default_base_url),
        model_pool=_load_model_pool(config_value),
        webhook_secret=config_value("HOCA_WEBHOOK_SECRET"),
        webhook_url=config_value("HOCA_WEBHOOK_URL"),
        allowed_repos=config_value("HOCA_ALLOWED_REPOS"),
        max_webhook_bytes=int(config_value("HOCA_MAX_WEBHOOK_BYTES", "65536")),
        notify_telegram=parse_bool(config_value("HOCA_NOTIFY_TELEGRAM") or None, default=False),
        telegram_bot_token=config_value("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=config_value("TELEGRAM_CHAT_ID"),
    )


class PolicyError(RuntimeError):
    """Raised when requested behavior violates HOCA's default safety policy."""


def validate_run_options(
    *,
    auto_merge: bool = False,
    high_risk: bool = False,
    direct_main_push: bool = False,
    policy: SafetyPolicy = DEFAULT_POLICY,
) -> None:
    if high_risk and auto_merge and not policy.allow_high_risk_auto_merge:
        raise PolicyError("High-risk changes must never be auto-merged.")

    if auto_merge and not policy.auto_merge:
        raise PolicyError("Auto-merge is disabled by default.")

    if direct_main_push and policy.forbid_direct_push_to_main:
        raise PolicyError("Direct pushes to main are forbidden by default.")


def assert_tests_passed(returncode: int, *, policy: SafetyPolicy = DEFAULT_POLICY) -> None:
    if returncode != 0 and policy.stop_on_test_failure:
        raise PolicyError("Tests failed; stopping the run.")


def assert_review_approved(review_text: str, *, policy: SafetyPolicy = DEFAULT_POLICY) -> None:
    normalized = review_text.strip().lower()
    approved = normalized in {"approved", "approval: approved", "hoca-review: approved"}
    if policy.require_review_approval and not approved:
        raise PolicyError("Code review did not return approval; stopping the run.")


def assert_commit_allowed(
    *, selective_staging_ready: bool, policy: SafetyPolicy = DEFAULT_POLICY
) -> None:
    if policy.stop_before_commit_until_selective_staging and not selective_staging_ready:
        raise PolicyError("Selective staging is not fully implemented; stopping before commit.")
