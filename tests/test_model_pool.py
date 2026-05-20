from __future__ import annotations

import json

import pytest

from hoca.config import HocaConfig, ModelPoolConfig, ModelSlot, load_config
from hoca.contracts import HocaModelConfig, HocaModelPool, HocaRoleModelSelection
from hoca.model_pool import (
    MAX_MODEL_SLOTS,
    load_model_slots_from_env,
    model_slot_from_env,
    model_pool_from_config,
    role_model_names_for_report,
    role_model_names_for_task_spec,
    safe_model_pool_json,
    validate_model_pool,
    validate_model_pool_config,
)


def sample_roles() -> HocaRoleModelSelection:
    return HocaRoleModelSelection(
        manager="local-coder",
        worker="local-coder",
        reviewer="reviewer-strong",
        fallback="local-fast",
    )


def sample_pool(**overrides: object) -> HocaModelPool:
    defaults = {
        "models": [
            HocaModelConfig(
                name="local-coder",
                model="ollama/qwen-14b-pro",
                base_url="http://127.0.0.1:11434",
                api_key="secret-coder",
            ),
            HocaModelConfig(
                name="local-fast",
                model="ollama/qwen-7b-pro",
                base_url="http://127.0.0.1:11434",
                api_key="secret-fast",
            ),
            HocaModelConfig(
                name="reviewer-strong",
                model="openai/gpt-oss-20b",
                base_url="http://localhost:1234/v1",
                api_key="secret-reviewer",
            ),
        ],
        "roles": sample_roles(),
    }
    defaults.update(overrides)
    return HocaModelPool(**defaults)  # type: ignore[arg-type]


class TestModelPoolEnvLoading:
    def test_loads_five_slots_from_env_keys(self) -> None:
        env = {
            "HOCA_MODEL_1_NAME": "slot-1",
            "HOCA_MODEL_1_MODEL": "provider/model-1",
            "HOCA_MODEL_1_BASE_URL": "http://127.0.0.1:11434",
            "HOCA_MODEL_1_API_KEY": "secret-1",
            "HOCA_MODEL_5_NAME": "slot-5",
            "HOCA_MODEL_5_MODEL": "provider/model-5",
            "HOCA_MODEL_5_BASE_URL": "http://127.0.0.1:11435",
            "HOCA_MODEL_5_API_KEY": "secret-5",
        }

        def config_value(name: str, default: str = "") -> str:
            return env.get(name, default)

        slots = load_model_slots_from_env(config_value)

        assert len(slots) == 5
        assert slots[0].name == "slot-1"
        assert slots[0].api_key == "secret-1"
        assert slots[4].name == "slot-5"
        assert slots[1].is_active is False

    def test_rejects_slot_index_outside_supported_range(self) -> None:
        with pytest.raises(ValueError, match="between 1 and 5"):
            model_slot_from_env(lambda _name, default="": default, 6)


class TestModelPoolValidation:
    def test_empty_model_slots_are_ignored_in_safe_output(self) -> None:
        pool = sample_pool(
            models=[
                HocaModelConfig(name="local-coder", model="ollama/qwen-14b-pro"),
                HocaModelConfig(name="", model=""),
                HocaModelConfig(name="reviewer-strong", model="openai/gpt-oss-20b"),
            ]
        )

        assert len(pool.active_models()) == 2
        assert [model["name"] for model in pool.safe_dict()["models"]] == [
            "local-coder",
            "reviewer-strong",
        ]

    def test_rejects_more_than_five_active_models(self) -> None:
        models = [
            HocaModelConfig(name=f"model-{index}", model=f"provider/model-{index}")
            for index in range(1, 7)
        ]
        pool = HocaModelPool(
            models=models,
            roles=HocaRoleModelSelection(
                manager="model-1",
                worker="model-1",
                reviewer="model-1",
                fallback="model-1",
            ),
        )

        with pytest.raises(ValueError, match=f"at most {MAX_MODEL_SLOTS}"):
            validate_model_pool(pool)

    def test_rejects_duplicate_model_names(self) -> None:
        pool = sample_pool(
            models=[
                HocaModelConfig(name="local-coder", model="ollama/qwen-14b-pro"),
                HocaModelConfig(name="local-coder", model="ollama/qwen-7b-pro"),
            ]
        )

        with pytest.raises(ValueError, match="Duplicate model pool names"):
            validate_model_pool(pool)

    def test_rejects_role_selection_outside_configured_pool(self) -> None:
        pool = sample_pool(
            roles=HocaRoleModelSelection(
                manager="missing",
                worker="local-coder",
                reviewer="reviewer-strong",
                fallback="local-fast",
            )
        )

        with pytest.raises(ValueError, match="must reference a configured model name"):
            validate_model_pool(pool)

    def test_from_dict_enforces_pool_validation(self) -> None:
        payload = {
            "models": [{"name": "local-coder", "model": "ollama/qwen-14b-pro"}],
            "roles": {
                "manager": "missing",
                "worker": "local-coder",
                "reviewer": "local-coder",
                "fallback": "local-coder",
            },
        }

        with pytest.raises(ValueError, match="must reference a configured model name"):
            HocaModelPool.from_dict(payload)


class TestModelPoolConfigBridge:
    def test_builds_contract_from_active_config(self) -> None:
        config = ModelPoolConfig(
            slots=(
                ModelSlot(
                    name="local-coder",
                    model="ollama/qwen-14b-pro",
                    base_url="http://127.0.0.1:11434",
                    api_key="secret",
                ),
                ModelSlot(name="", model=""),
                ModelSlot(
                    name="local-fast",
                    model="ollama/qwen-7b-pro",
                    base_url="http://127.0.0.1:11434",
                    api_key="secret-fast",
                ),
            ),
            worker_model="local-coder",
            reviewer_model="",
            fallback_model="local-fast",
        )

        pool = model_pool_from_config(config)

        assert pool is not None
        assert [model.name for model in pool.active_models()] == ["local-coder", "local-fast"]
        assert pool.roles.worker == "local-coder"
        assert pool.roles.reviewer == "local-fast"

    def test_inactive_config_returns_none(self) -> None:
        assert model_pool_from_config(ModelPoolConfig()) is None

    def test_config_validation_rejects_unknown_role_model(self) -> None:
        config = ModelPoolConfig(
            slots=(ModelSlot(name="local-coder", model="ollama/qwen-14b-pro"),),
            worker_model="missing",
            fallback_model="local-coder",
        )

        with pytest.raises(ValueError, match="must reference a configured model name"):
            validate_model_pool_config(config)

    def test_active_pool_requires_fallback_at_load_time(self) -> None:
        config = ModelPoolConfig(
            slots=(ModelSlot(name="local-coder", model="ollama/qwen-14b-pro"),),
            worker_model="local-coder",
        )

        with pytest.raises(ValueError, match="HOCA_FALLBACK_MODEL is required"):
            validate_model_pool_config(config)

    def test_role_names_for_report_exclude_credentials(self) -> None:
        config = ModelPoolConfig(
            slots=(
                ModelSlot(name="local-coder", model="ollama/qwen-14b-pro", api_key="secret"),
                ModelSlot(name="local-fast", model="ollama/qwen-7b-pro", api_key="secret-fast"),
            ),
            worker_model="local-coder",
            fallback_model="local-fast",
        )

        names = role_model_names_for_report(config)

        assert names == {
            "manager": "local-fast",
            "worker": "local-coder",
            "reviewer": "local-fast",
            "fallback": "local-fast",
        }
        assert "secret" not in json.dumps(names)


class TestRoleModelSelection:
    def test_worker_and_reviewer_can_use_different_configured_models(self) -> None:
        config = ModelPoolConfig(
            slots=(
                ModelSlot(name="local-coder", model="ollama/qwen-14b-pro"),
                ModelSlot(name="reviewer-strong", model="openai/gpt-oss-20b"),
                ModelSlot(name="local-fast", model="ollama/qwen-7b-pro"),
            ),
            worker_model="local-coder",
            reviewer_model="reviewer-strong",
            fallback_model="local-fast",
        )

        assert config.resolve_role("worker").model == "ollama/qwen-14b-pro"
        assert config.resolve_role("reviewer").model == "openai/gpt-oss-20b"
        assert config.resolve_role("manager").name == "local-fast"

    def test_role_model_names_for_task_spec_uses_legacy_llm_model(self) -> None:
        cfg = HocaConfig(llm_model="openai/gpt-oss-20b")

        names = role_model_names_for_task_spec(cfg)

        assert names == {
            "manager": "openai/gpt-oss-20b",
            "worker": "openai/gpt-oss-20b",
            "reviewer": "openai/gpt-oss-20b",
            "fallback": "openai/gpt-oss-20b",
        }

    def test_role_model_names_for_task_spec_resolves_inherited_roles(self) -> None:
        cfg = HocaConfig(
            model_pool=ModelPoolConfig(
                slots=(
                    ModelSlot(name="local-coder", model="ollama/qwen-14b-pro"),
                    ModelSlot(name="reviewer-strong", model="openai/gpt-oss-20b"),
                    ModelSlot(name="local-fast", model="ollama/qwen-7b-pro"),
                ),
                worker_model="local-coder",
                reviewer_model="reviewer-strong",
                fallback_model="local-fast",
            )
        )

        names = role_model_names_for_task_spec(cfg)

        assert names == {
            "manager": "local-fast",
            "worker": "local-coder",
            "reviewer": "reviewer-strong",
            "fallback": "local-fast",
        }
        assert "secret" not in str(names)


class TestModelPoolSafeSerialization:
    def test_safe_json_redacts_api_keys(self) -> None:
        pool = sample_pool()
        payload = json.loads(safe_model_pool_json(pool))

        assert payload["models"][0]["api_key"] == "***"
        assert "secret-coder" not in safe_model_pool_json(pool)
        assert payload["roles"]["worker"] == "local-coder"

    def test_to_safe_json_matches_helper(self) -> None:
        pool = sample_pool()

        assert pool.to_safe_json() == safe_model_pool_json(pool)
