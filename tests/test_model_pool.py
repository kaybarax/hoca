from __future__ import annotations

import json

import pytest

from hoca.config import ModelPoolConfig, ModelSlot
from hoca.contracts import HocaModelConfig, HocaModelPool, HocaRoleModelSelection
from hoca.model_pool import (
    MAX_MODEL_SLOTS,
    model_pool_from_config,
    role_model_names_for_report,
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
        )

        with pytest.raises(ValueError, match="must reference a configured model name"):
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
