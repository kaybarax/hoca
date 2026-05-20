from __future__ import annotations

from pathlib import Path

import pytest

from hoca.config import HocaConfig, ModelPoolConfig, ModelSlot, load_config, parse_bool
from hoca.model_pool import validate_model_pool_config


class TestParseBool:
    @pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"])
    def test_truthy_values(self, value: str) -> None:
        assert parse_bool(value, default=False) is True

    @pytest.mark.parametrize(
        "value", ["0", "false", "False", "FALSE", "no", "NO", "off", "OFF", ""]
    )
    def test_falsy_values(self, value: str) -> None:
        assert parse_bool(value, default=True) is False

    def test_none_returns_default_true(self) -> None:
        assert parse_bool(None, default=True) is True

    def test_none_returns_default_false(self) -> None:
        assert parse_bool(None, default=False) is False

    def test_whitespace_is_stripped(self) -> None:
        assert parse_bool("  true  ", default=False) is True

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse"):
            parse_bool("maybe", default=False)


class TestLoadConfigDefaults:
    def test_defaults_without_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        empty_env = tmp_path / ".env"
        empty_env.write_text("")
        for key in [
            "HOCA_AUTO_MERGE",
            "HOCA_REQUIRE_TESTS",
            "HOCA_STOP_ON_DIRTY_TREE",
            "HOCA_DEV_BRANCH",
            "HOCA_SYNC_DEV_BRANCH",
            "HOCA_AUTO_STAGE_REVIEWED_CHANGES",
            "HOCA_USE_HERMES_PROFILES",
            "HOCA_USE_STRUCTURED_REPORTS",
            "HOCA_USE_KANBAN",
            "HOCA_USE_SANDBOX",
            "HOCA_USE_WORKTREE_SANDBOX",
            "HOCA_MAX_TOTAL_ROUNDS",
            "HOCA_MANAGER_MODEL",
            "HOCA_WORKER_MODEL",
            "HOCA_REVIEWER_MODEL",
            "HOCA_FALLBACK_MODEL",
            "HOCA_WORKSPACE_ROOT",
            "OLLAMA_BASE_URL",
            "OLLAMA_MODEL",
            "LLM_MODEL",
            "LLM_BASE_URL",
            "HOCA_WEBHOOK_SECRET",
            "HOCA_WEBHOOK_URL",
            "HOCA_ALLOWED_REPOS",
            "HOCA_MAX_WEBHOOK_BYTES",
            "HOCA_NOTIFY_TELEGRAM",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
        ]:
            monkeypatch.delenv(key, raising=False)
        for index in range(1, 6):
            for suffix in ("NAME", "MODEL", "BASE_URL", "API_KEY"):
                monkeypatch.delenv(f"HOCA_MODEL_{index}_{suffix}", raising=False)

        cfg = load_config(dotenv_path=empty_env)

        assert cfg.use_hermes_profiles is False
        assert cfg.use_structured_reports is True
        assert cfg.use_kanban is False
        assert cfg.use_sandbox is True
        assert cfg.use_worktree_sandbox is True
        assert cfg.max_total_rounds == 3
        assert cfg.model_pool.is_active is False
        assert cfg.auto_merge is False
        assert cfg.require_tests is True
        assert cfg.stop_on_dirty_tree is True
        assert cfg.dev_branch == "main"
        assert cfg.sync_dev_branch is True
        assert cfg.auto_stage_reviewed_changes is True
        assert cfg.workspace_root is None
        assert cfg.ollama_base_url == "http://127.0.0.1:11434"
        assert cfg.ollama_model == "qwen-14b-pro"
        assert cfg.webhook_secret == ""
        assert cfg.notify_telegram is False

    def test_loads_from_dotenv(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOCA_AUTO_MERGE=true\n"
            "HOCA_REQUIRE_TESTS=false\n"
            "OLLAMA_MODEL=custom-model\n"
            "HOCA_DEV_BRANCH=develop\n"
            "HOCA_SYNC_DEV_BRANCH=false\n"
            "HOCA_AUTO_STAGE_REVIEWED_CHANGES=false\n"
            "HOCA_WORKSPACE_ROOT=~/projects\n"
            "HOCA_USE_HERMES_PROFILES=true\n"
            "HOCA_USE_STRUCTURED_REPORTS=false\n"
            "HOCA_USE_KANBAN=true\n"
            "HOCA_USE_SANDBOX=false\n"
            "HOCA_USE_WORKTREE_SANDBOX=false\n"
            "HOCA_MAX_TOTAL_ROUNDS=5\n"
        )
        for key in [
            "HOCA_AUTO_MERGE",
            "HOCA_REQUIRE_TESTS",
            "OLLAMA_MODEL",
            "HOCA_DEV_BRANCH",
            "HOCA_SYNC_DEV_BRANCH",
            "HOCA_AUTO_STAGE_REVIEWED_CHANGES",
            "HOCA_WORKSPACE_ROOT",
            "HOCA_USE_HERMES_PROFILES",
            "HOCA_USE_STRUCTURED_REPORTS",
            "HOCA_USE_KANBAN",
            "HOCA_USE_SANDBOX",
            "HOCA_USE_WORKTREE_SANDBOX",
            "HOCA_MAX_TOTAL_ROUNDS",
        ]:
            monkeypatch.delenv(key, raising=False)

        cfg = load_config(dotenv_path=env_file)

        assert cfg.auto_merge is True
        assert cfg.require_tests is False
        assert cfg.ollama_model == "custom-model"
        assert cfg.dev_branch == "develop"
        assert cfg.sync_dev_branch is False
        assert cfg.auto_stage_reviewed_changes is False
        assert cfg.workspace_root is not None
        assert "~" not in str(cfg.workspace_root)
        assert cfg.use_hermes_profiles is True
        assert cfg.use_structured_reports is False
        assert cfg.use_kanban is True
        assert cfg.use_sandbox is False
        assert cfg.use_worktree_sandbox is False
        assert cfg.max_total_rounds == 5

    def test_legacy_max_repair_attempts_alias(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("HOCA_MAX_REPAIR_ATTEMPTS=2\n")
        monkeypatch.delenv("HOCA_MAX_TOTAL_ROUNDS", raising=False)
        monkeypatch.delenv("HOCA_MAX_REPAIR_ATTEMPTS", raising=False)

        cfg = load_config(dotenv_path=env_file)

        assert cfg.max_total_rounds == 3

    def test_max_total_rounds_takes_precedence_over_legacy_alias(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOCA_MAX_TOTAL_ROUNDS=5\n"
            "HOCA_MAX_REPAIR_ATTEMPTS=1\n"
        )
        monkeypatch.delenv("HOCA_MAX_TOTAL_ROUNDS", raising=False)
        monkeypatch.delenv("HOCA_MAX_REPAIR_ATTEMPTS", raising=False)

        cfg = load_config(dotenv_path=env_file)

        assert cfg.max_total_rounds == 5

    def test_env_var_overrides_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("HOCA_AUTO_MERGE=false\n")
        monkeypatch.setenv("HOCA_AUTO_MERGE", "true")

        cfg = load_config(dotenv_path=env_file)

        assert cfg.auto_merge is True


class TestModelPoolConfig:
    def test_empty_model_pool_preserves_legacy_single_model_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "LLM_MODEL=openai/gpt-oss-20b\n"
            "LLM_BASE_URL=http://localhost:1234/v1\n"
            "LLM_API_KEY=lm-studio\n"
        )
        for key in [
            "LLM_MODEL",
            "LLM_BASE_URL",
            "LLM_API_KEY",
            "HOCA_MANAGER_MODEL",
            "HOCA_WORKER_MODEL",
            "HOCA_REVIEWER_MODEL",
            "HOCA_FALLBACK_MODEL",
        ]:
            monkeypatch.delenv(key, raising=False)

        cfg = load_config(dotenv_path=env_file)

        assert cfg.llm_model == "openai/gpt-oss-20b"
        assert cfg.llm_base_url == "http://localhost:1234/v1"
        assert cfg.model_pool.is_active is False
        assert cfg.model_pool.resolve_role("worker") is None

    def test_loads_active_model_pool_from_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOCA_MODEL_1_NAME=local-coder\n"
            "HOCA_MODEL_1_MODEL=ollama/qwen-14b-pro\n"
            "HOCA_MODEL_1_BASE_URL=http://127.0.0.1:11434\n"
            "HOCA_MODEL_1_API_KEY=ollama\n"
            "HOCA_MODEL_2_NAME=local-fast\n"
            "HOCA_MODEL_2_MODEL=ollama/qwen-7b-pro\n"
            "HOCA_WORKER_MODEL=local-coder\n"
            "HOCA_FALLBACK_MODEL=local-fast\n"
        )
        for index in range(1, 6):
            for suffix in ("NAME", "MODEL", "BASE_URL", "API_KEY"):
                monkeypatch.delenv(f"HOCA_MODEL_{index}_{suffix}", raising=False)
        for key in [
            "HOCA_WORKER_MODEL",
            "HOCA_FALLBACK_MODEL",
        ]:
            monkeypatch.delenv(key, raising=False)

        cfg = load_config(dotenv_path=env_file)

        assert cfg.model_pool.is_active is True
        assert [slot.name for slot in cfg.model_pool.active_slots] == [
            "local-coder",
            "local-fast",
        ]
        assert cfg.model_pool.resolve_role("worker").model == "ollama/qwen-14b-pro"
        assert cfg.model_pool.resolve_role("reviewer").model == "ollama/qwen-7b-pro"

    def test_active_pool_requires_fallback_for_unset_roles(self) -> None:
        pool = ModelPoolConfig(
            slots=(ModelSlot(name="local-coder", model="ollama/qwen-14b-pro"),),
        )

        with pytest.raises(ValueError, match="HOCA_FALLBACK_MODEL is required"):
            validate_model_pool_config(pool)

    def test_load_config_fails_when_active_pool_has_no_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOCA_MODEL_1_NAME=local-coder\n"
            "HOCA_MODEL_1_MODEL=ollama/qwen-14b-pro\n"
            "HOCA_WORKER_MODEL=local-coder\n"
        )
        self._clear_model_pool_env(monkeypatch)

        with pytest.raises(ValueError, match="HOCA_FALLBACK_MODEL is required"):
            load_config(dotenv_path=env_file)

    def test_active_pool_requires_role_name_to_exist(self) -> None:
        pool = ModelPoolConfig(
            slots=(ModelSlot(name="local-coder", model="ollama/qwen-14b-pro"),),
            worker_model="missing",
        )

        with pytest.raises(ValueError, match="active model pool"):
            pool.resolve_role("worker")

    def test_duplicate_model_names_fail_config_load(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOCA_MODEL_1_NAME=local-coder\n"
            "HOCA_MODEL_1_MODEL=ollama/qwen-14b-pro\n"
            "HOCA_MODEL_2_NAME=local-coder\n"
            "HOCA_MODEL_2_MODEL=ollama/qwen-7b-pro\n"
        )
        for index in range(1, 6):
            for suffix in ("NAME", "MODEL", "BASE_URL", "API_KEY"):
                monkeypatch.delenv(f"HOCA_MODEL_{index}_{suffix}", raising=False)

        with pytest.raises(ValueError, match="Duplicate model pool names"):
            load_config(dotenv_path=env_file)

    def test_loads_all_five_model_slots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lines = []
        for index in range(1, 6):
            lines.extend(
                [
                    f"HOCA_MODEL_{index}_NAME=slot-{index}",
                    f"HOCA_MODEL_{index}_MODEL=provider/model-{index}",
                    f"HOCA_MODEL_{index}_BASE_URL=http://127.0.0.1:{11430 + index}",
                    f"HOCA_MODEL_{index}_API_KEY=secret-{index}",
                ]
            )
        lines.append("HOCA_FALLBACK_MODEL=slot-1")
        env_file = tmp_path / ".env"
        env_file.write_text("\n".join(lines) + "\n")
        self._clear_model_pool_env(monkeypatch)

        cfg = load_config(dotenv_path=env_file)

        assert len(cfg.model_pool.slots) == 5
        assert [slot.name for slot in cfg.model_pool.active_slots] == [
            f"slot-{index}" for index in range(1, 6)
        ]
        assert cfg.model_pool.active_slots[4].api_key == "secret-5"

    def test_empty_slots_do_not_fail_config_loading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOCA_MODEL_1_NAME=local-coder\n"
            "HOCA_MODEL_1_MODEL=ollama/qwen-14b-pro\n"
            "HOCA_MODEL_3_NAME=reviewer-strong\n"
            "HOCA_MODEL_3_MODEL=\n"
            "HOCA_MODEL_4_NAME=\n"
            "HOCA_MODEL_4_MODEL=\n"
            "HOCA_MODEL_5_NAME=\n"
            "HOCA_MODEL_5_MODEL=\n"
            "HOCA_FALLBACK_MODEL=local-coder\n"
        )
        self._clear_model_pool_env(monkeypatch)

        cfg = load_config(dotenv_path=env_file)

        assert cfg.model_pool.is_active is True
        assert [slot.name for slot in cfg.model_pool.active_slots] == ["local-coder"]

    def test_hoca_model_6_env_vars_are_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOCA_MODEL_1_NAME=local-coder\n"
            "HOCA_MODEL_1_MODEL=ollama/qwen-14b-pro\n"
            "HOCA_MODEL_6_NAME=extra-slot\n"
            "HOCA_MODEL_6_MODEL=provider/extra\n"
            "HOCA_FALLBACK_MODEL=local-coder\n"
        )
        self._clear_model_pool_env(monkeypatch)
        monkeypatch.setenv("HOCA_MODEL_6_NAME", "env-extra")
        monkeypatch.setenv("HOCA_MODEL_6_MODEL", "provider/env-extra")

        cfg = load_config(dotenv_path=env_file)

        assert len(cfg.model_pool.slots) == 5
        assert [slot.name for slot in cfg.model_pool.active_slots] == ["local-coder"]
        assert "extra-slot" not in {slot.name for slot in cfg.model_pool.slots}
        assert "env-extra" not in {slot.name for slot in cfg.model_pool.slots}

    @staticmethod
    def _clear_model_pool_env(monkeypatch: pytest.MonkeyPatch) -> None:
        for index in range(1, 7):
            for suffix in ("NAME", "MODEL", "BASE_URL", "API_KEY"):
                monkeypatch.delenv(f"HOCA_MODEL_{index}_{suffix}", raising=False)


class TestSafeRepr:
    def test_secrets_are_masked(self) -> None:
        cfg = HocaConfig(
            webhook_secret="super-secret-value",
            telegram_bot_token="tok123",
            model_pool=ModelPoolConfig(
                slots=(
                    ModelSlot(
                        name="local-coder",
                        model="ollama/qwen-14b-pro",
                        api_key="secret-key",
                    ),
                )
            ),
        )
        safe = cfg.safe_repr()
        assert safe["webhook_secret"] == "***"
        assert safe["telegram_bot_token"] == "***"
        assert safe["model_pool"]["slots"][0]["api_key"] == "***"

    def test_empty_secrets_show_unset(self) -> None:
        cfg = HocaConfig()
        safe = cfg.safe_repr()
        assert safe["webhook_secret"] == "(unset)"
        assert safe["telegram_bot_token"] == "(unset)"

    def test_non_secret_fields_shown(self) -> None:
        cfg = HocaConfig(ollama_model="test-model")
        safe = cfg.safe_repr()
        assert safe["ollama_model"] == "test-model"
