from __future__ import annotations

import json
from typing import Any

from hoca.config import HocaConfig, ModelPoolConfig, ModelSlot, RoleName
from hoca.contracts import HocaModelConfig, HocaModelPool, HocaRoleModelSelection

MODEL_ROLES: tuple[RoleName, ...] = ("manager", "worker", "reviewer")
MAX_MODEL_SLOTS = len(MODEL_ROLES)


def model_slot_from_env(config_value, role: RoleName) -> ModelSlot:
    if role not in MODEL_ROLES:
        raise ValueError(f"Model role must be one of: {', '.join(MODEL_ROLES)}, got {role}")
    env_role = role.upper()
    prefix = f"HOCA_{env_role}_MODEL_"
    name = config_value(f"{prefix}NAME").strip() or role
    return ModelSlot(
        name=name,
        model=config_value(f"{prefix}MODEL"),
        base_url=config_value(f"{prefix}BASE_URL"),
        api_key=config_value(f"{prefix}API_KEY"),
    )


def load_model_slots_from_env(config_value) -> tuple[ModelSlot, ...]:
    return tuple(model_slot_from_env(config_value, role) for role in MODEL_ROLES)


REDACTED_API_KEY = "***"
UNSET_API_KEY = "(unset)"


def is_active_model(model: HocaModelConfig) -> bool:
    return bool(model.name.strip() and model.model.strip())


def active_models(models: list[HocaModelConfig]) -> list[HocaModelConfig]:
    return [model for model in models if is_active_model(model)]


def configured_model_names(models: list[HocaModelConfig]) -> set[str]:
    return {model.name for model in active_models(models)}


def validate_model_pool(pool: HocaModelPool) -> None:
    active = active_models(pool.models)
    if len(active) > MAX_MODEL_SLOTS:
        raise ValueError(
            f"Model pool supports at most {MAX_MODEL_SLOTS} configured models, got {len(active)}"
        )

    names = [model.name for model in active]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"Duplicate model pool names are not allowed: {', '.join(duplicates)}")

    configured = configured_model_names(pool.models)
    if not configured:
        return

    for role_name, selected in _role_selection_items(pool.roles):
        if selected and selected not in configured:
            raise ValueError(
                f"Role model selection {role_name!r} must reference a configured model name, "
                f"got: {selected!r}"
            )


def validate_model_pool_config(pool: ModelPoolConfig) -> None:
    active = pool.active_slots
    if len(active) > MAX_MODEL_SLOTS:
        raise ValueError(
            f"Model pool supports at most {MAX_MODEL_SLOTS} configured models, got {len(active)}"
        )

    names = [slot.name for slot in active]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"Duplicate model pool names are not allowed: {', '.join(duplicates)}")

    if not pool.is_active:
        return

    configured = set(pool.slot_by_name())
    for role in ("manager", "worker", "reviewer", "fallback"):
        selected_name = {
            "manager": pool.manager_model,
            "worker": pool.worker_model,
            "reviewer": pool.reviewer_model,
            "fallback": pool.fallback_model,
        }[role]
        if selected_name and selected_name not in configured:
            raise ValueError(
                f"HOCA_{role.upper()}_MODEL must reference a configured model name, "
                f"got: {selected_name!r}"
            )


def model_config_from_slot(slot: ModelSlot) -> HocaModelConfig:
    return HocaModelConfig(
        name=slot.name,
        model=slot.model,
        base_url=slot.base_url,
        api_key=slot.api_key,
    )


def role_selection_from_config(pool: ModelPoolConfig) -> HocaRoleModelSelection:
    if not pool.is_active:
        raise ValueError("Cannot build role model selection from an inactive model pool")

    return HocaRoleModelSelection(
        manager=_resolved_role_name(pool, "manager"),
        worker=_resolved_role_name(pool, "worker"),
        reviewer=_resolved_role_name(pool, "reviewer"),
        fallback=_resolved_role_name(pool, "fallback"),
    )


def model_pool_from_config(pool: ModelPoolConfig) -> HocaModelPool | None:
    if not pool.is_active:
        return None

    validate_model_pool_config(pool)
    contract = HocaModelPool(
        models=[model_config_from_slot(slot) for slot in pool.active_slots],
        roles=role_selection_from_config(pool),
    )
    validate_model_pool(contract)
    return contract


def role_model_names_for_report(pool: HocaModelPool | ModelPoolConfig) -> dict[str, str]:
    if isinstance(pool, HocaModelPool):
        return {role: selected for role, selected in _role_selection_items(pool.roles)}

    if not pool.is_active:
        return {}

    return {
        "manager": _resolved_role_name(pool, "manager"),
        "worker": _resolved_role_name(pool, "worker"),
        "reviewer": _resolved_role_name(pool, "reviewer"),
        "fallback": _resolved_role_name(pool, "fallback"),
    }


def role_model_names_for_task_spec(config: HocaConfig) -> dict[str, str]:
    """Resolved role model slot names for task specs and run artifacts."""
    if config.model_pool.is_active:
        return role_model_names_for_report(config.model_pool)

    legacy_name = config.llm_model
    return {
        "manager": legacy_name,
        "worker": legacy_name,
        "reviewer": legacy_name,
        "fallback": legacy_name,
    }


def safe_model_pool_dict(pool: HocaModelPool) -> dict[str, Any]:
    return pool.safe_dict()


def safe_model_pool_json(pool: HocaModelPool) -> str:
    return json.dumps(pool.safe_dict(), indent=2, sort_keys=True) + "\n"


def _resolved_role_name(pool: ModelPoolConfig, role: RoleName) -> str:
    slot = pool.resolve_role(role)
    if slot is None:
        return ""
    return slot.name


def _role_selection_items(
    roles: HocaRoleModelSelection,
) -> tuple[tuple[str, str], ...]:
    return (
        ("manager", roles.manager),
        ("worker", roles.worker),
        ("reviewer", roles.reviewer),
        ("fallback", roles.fallback),
    )
