from __future__ import annotations

from pathlib import Path

import pytest

from hoca.config import HocaConfig, ModelPoolConfig, ModelSlot, load_config, parse_bool
from hoca.model_pool import validate_model_pool_config


def _clear_role_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for role in ("MANAGER", "WORKER", "REVIEWER"):
        for suffix in ("NAME", "MODEL", "BASE_URL", "API_KEY"):
            monkeypatch.delenv(f"HOCA_{role}_MODEL_{suffix}", raising=False)


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
    def test_hoca_dotenv_path_is_used_outside_hoca_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / "hoca.env"
        env_file.write_text("HOCA_MAX_TOTAL_ROUNDS=4\n", encoding="utf-8")
        target_repo = tmp_path / "target"
        target_repo.mkdir()
        monkeypatch.chdir(target_repo)
        monkeypatch.setenv("HOCA_DOTENV_PATH", str(env_file))
        monkeypatch.delenv("HOCA_MAX_TOTAL_ROUNDS", raising=False)

        cfg = load_config()

        assert cfg.max_total_rounds == 4

    def test_defaults_without_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        empty_env = tmp_path / ".env"
        empty_env.write_text("")
        for key in [
            "HOCA_AUTO_MERGE",
            "HOCA_REQUIRE_TESTS",
            "HOCA_REQUIRE_REVIEW",
            "HOCA_STOP_ON_DIRTY_TREE",
            "HOCA_SYNC_DEV_BRANCH",
            "HOCA_RESTORE_DEV_BRANCH",
            "HOCA_AUTO_STAGE_REVIEWED_CHANGES",
            "HOCA_USE_KANBAN",
            "HOCA_USE_SANDBOX",
            "HOCA_USE_WORKTREE_SANDBOX",
            "HOCA_NETWORK_MODE",
            "HOCA_MAX_TOTAL_ROUNDS",
            "HOCA_WORKSPACE_ROOT",
            "OLLAMA_HOST",
            "OLLAMA_BASE_URL",
            "OLLAMA_API_BASE",
            "OLLAMA_MODEL",
            "HOCA_WEBHOOK_SECRET",
            "HOCA_WEBHOOK_URL",
            "HOCA_ALLOWED_REPOS",
            "HOCA_MAX_WEBHOOK_BYTES",
            "HOCA_NOTIFY_TELEGRAM",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
        ]:
            monkeypatch.delenv(key, raising=False)
        _clear_role_model_env(monkeypatch)

        cfg = load_config(dotenv_path=empty_env)

        assert cfg.use_kanban is False
        assert cfg.use_sandbox is True
        assert cfg.use_worktree_sandbox is True
        assert cfg.network_mode == "offline"
        assert cfg.max_total_rounds == 3
        assert cfg.model_pool.is_active is False
        assert cfg.auto_merge is False
        assert cfg.require_tests is True
        assert cfg.require_review is True
        assert cfg.stop_on_dirty_tree is True
        assert cfg.sync_dev_branch is True
        assert cfg.restore_dev_branch is True
        assert cfg.auto_stage_reviewed_changes is True
        assert cfg.workspace_root is None
        assert cfg.ollama_host == "http://127.0.0.1:11434"
        assert cfg.ollama_base_url == "http://127.0.0.1:11434"
        assert cfg.ollama_api_base == "http://127.0.0.1:11434"
        assert cfg.ollama_model == "qwen-14b-pro"
        assert cfg.webhook_secret == ""
        assert cfg.notify_telegram is False

    def test_loads_from_dotenv(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOCA_AUTO_MERGE=true\n"
            "HOCA_REQUIRE_TESTS=false\n"
            "OLLAMA_MODEL=custom-model\n"
            "HOCA_SYNC_DEV_BRANCH=false\n"
            "HOCA_RESTORE_DEV_BRANCH=false\n"
            "HOCA_AUTO_STAGE_REVIEWED_CHANGES=false\n"
            "HOCA_WORKSPACE_ROOT=~/projects\n"
            "HOCA_USE_KANBAN=true\n"
            "HOCA_USE_SANDBOX=false\n"
            "HOCA_USE_WORKTREE_SANDBOX=false\n"
            "HOCA_MAX_TOTAL_ROUNDS=5\n"
        )
        for key in [
            "HOCA_AUTO_MERGE",
            "HOCA_REQUIRE_TESTS",
            "OLLAMA_MODEL",
            "HOCA_SYNC_DEV_BRANCH",
            "HOCA_RESTORE_DEV_BRANCH",
            "HOCA_AUTO_STAGE_REVIEWED_CHANGES",
            "HOCA_WORKSPACE_ROOT",
            "HOCA_USE_KANBAN",
            "HOCA_USE_SANDBOX",
            "HOCA_USE_WORKTREE_SANDBOX",
            "HOCA_NETWORK_MODE",
            "HOCA_MAX_TOTAL_ROUNDS",
        ]:
            monkeypatch.delenv(key, raising=False)

        cfg = load_config(dotenv_path=env_file)

        assert cfg.auto_merge is True
        assert cfg.require_tests is False
        assert cfg.ollama_model == "custom-model"
        assert cfg.sync_dev_branch is False
        assert cfg.restore_dev_branch is False
        assert cfg.auto_stage_reviewed_changes is False
        assert cfg.workspace_root is not None
        assert "~" not in str(cfg.workspace_root)
        assert cfg.use_kanban is True
        assert cfg.use_sandbox is False
        assert cfg.use_worktree_sandbox is False
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
    def test_empty_model_pool_uses_ollama_fallback_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("OLLAMA_MODEL=qwen-32b-pro\nOLLAMA_BASE_URL=http://10.0.0.1:11434\n")
        for key in ["OLLAMA_MODEL", "OLLAMA_BASE_URL"]:
            monkeypatch.delenv(key, raising=False)
        _clear_role_model_env(monkeypatch)

        cfg = load_config(dotenv_path=env_file)

        assert cfg.ollama_model == "qwen-32b-pro"
        assert cfg.ollama_base_url == "http://10.0.0.1:11434"
        assert cfg.model_pool.is_active is False

    def test_loads_active_model_pool_from_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOCA_MANAGER_MODEL_NAME=manager\n"
            "HOCA_MANAGER_MODEL_MODEL=ollama/qwen-7b-pro\n"
            "HOCA_MANAGER_MODEL_BASE_URL=http://127.0.0.1:11434\n"
            "HOCA_MANAGER_MODEL_API_KEY=ollama\n"
            "HOCA_WORKER_MODEL_NAME=worker\n"
            "HOCA_WORKER_MODEL_MODEL=ollama/qwen-14b-pro\n"
            "HOCA_REVIEWER_MODEL_NAME=reviewer\n"
            "HOCA_REVIEWER_MODEL_MODEL=openai/gpt-oss-20b\n"
        )
        _clear_role_model_env(monkeypatch)

        cfg = load_config(dotenv_path=env_file)

        assert cfg.model_pool.is_active is True
        assert [slot.name for slot in cfg.model_pool.active_slots] == [
            "manager",
            "worker",
            "reviewer",
        ]
        assert cfg.model_pool.resolve_role("manager").model == "ollama/qwen-7b-pro"
        assert cfg.model_pool.resolve_role("worker").model == "ollama/qwen-14b-pro"
        assert cfg.model_pool.resolve_role("reviewer").model == "openai/gpt-oss-20b"

    def test_active_pool_uses_first_active_slot_as_default_fallback(self) -> None:
        pool = ModelPoolConfig(
            slots=(ModelSlot(name="local-coder", model="ollama/qwen-14b-pro"),),
        )

        validate_model_pool_config(pool)
        assert pool.resolve_role("reviewer").name == "local-coder"

    def test_load_config_defaults_unset_roles_to_first_active_slot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOCA_WORKER_MODEL_NAME=local-coder\nHOCA_WORKER_MODEL_MODEL=ollama/qwen-14b-pro\n"
        )
        _clear_role_model_env(monkeypatch)

        cfg = load_config(dotenv_path=env_file)

        assert cfg.model_pool.resolve_role("manager").name == "local-coder"
        assert cfg.model_pool.resolve_role("worker").name == "local-coder"

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
            "HOCA_MANAGER_MODEL_NAME=local-coder\n"
            "HOCA_MANAGER_MODEL_MODEL=ollama/qwen-14b-pro\n"
            "HOCA_WORKER_MODEL_NAME=local-coder\n"
            "HOCA_WORKER_MODEL_MODEL=ollama/qwen-7b-pro\n"
        )
        _clear_role_model_env(monkeypatch)

        with pytest.raises(ValueError, match="Duplicate model pool names"):
            load_config(dotenv_path=env_file)

    def test_loads_all_role_model_slots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lines = []
        for role in ("manager", "worker", "reviewer"):
            lines.extend(
                [
                    f"HOCA_{role.upper()}_MODEL_NAME={role}",
                    f"HOCA_{role.upper()}_MODEL_MODEL=provider/{role}",
                    f"HOCA_{role.upper()}_MODEL_BASE_URL=http://127.0.0.1:11434",
                    f"HOCA_{role.upper()}_MODEL_API_KEY=secret-{role}",
                ]
            )
        env_file = tmp_path / ".env"
        env_file.write_text("\n".join(lines) + "\n")
        _clear_role_model_env(monkeypatch)

        cfg = load_config(dotenv_path=env_file)

        assert len(cfg.model_pool.slots) == 3
        assert [slot.name for slot in cfg.model_pool.active_slots] == [
            "manager",
            "worker",
            "reviewer",
        ]
        assert cfg.model_pool.active_slots[2].api_key == "secret-reviewer"

    def test_empty_slots_do_not_fail_config_loading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOCA_WORKER_MODEL_NAME=local-coder\n"
            "HOCA_WORKER_MODEL_MODEL=ollama/qwen-14b-pro\n"
            "HOCA_REVIEWER_MODEL_NAME=reviewer-strong\n"
            "HOCA_REVIEWER_MODEL_MODEL=\n"
        )
        _clear_role_model_env(monkeypatch)

        cfg = load_config(dotenv_path=env_file)

        assert cfg.model_pool.is_active is True
        assert [slot.name for slot in cfg.model_pool.active_slots] == ["local-coder"]

    def test_unknown_role_model_env_vars_are_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOCA_WORKER_MODEL_NAME=local-coder\n"
            "HOCA_WORKER_MODEL_MODEL=ollama/qwen-14b-pro\n"
            "HOCA_SUPPORT_MODEL_NAME=extra-slot\n"
            "HOCA_SUPPORT_MODEL_MODEL=provider/extra\n"
        )
        _clear_role_model_env(monkeypatch)
        monkeypatch.setenv("HOCA_SUPPORT_MODEL_NAME", "env-extra")
        monkeypatch.setenv("HOCA_SUPPORT_MODEL_MODEL", "provider/env-extra")

        cfg = load_config(dotenv_path=env_file)

        assert len(cfg.model_pool.slots) == 3
        assert [slot.name for slot in cfg.model_pool.active_slots] == ["local-coder"]
        assert "extra-slot" not in {slot.name for slot in cfg.model_pool.slots}
        assert "env-extra" not in {slot.name for slot in cfg.model_pool.slots}


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


class TestCurrentEnvVars:
    """Current .env variables remain usable after the profile-only upgrade."""

    @staticmethod
    def _clear_current_env(monkeypatch: pytest.MonkeyPatch) -> None:
        for key in [
            "HOCA_MAX_TOTAL_ROUNDS",
            "HOCA_REQUIRE_REVIEW",
            "HOCA_AUTO_MERGE",
            "HOCA_REQUIRE_TESTS",
            "HOCA_NOTIFY_TELEGRAM",
            "HOCA_WEBHOOK_SECRET",
            "HOCA_WEBHOOK_URL",
            "HOCA_ALLOWED_REPOS",
            "HOCA_MAX_WEBHOOK_BYTES",
            "HOCA_WORKSPACE_ROOT",
            "OLLAMA_HOST",
            "OLLAMA_BASE_URL",
            "OLLAMA_API_BASE",
            "OLLAMA_MODEL",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
        ]:
            monkeypatch.delenv(key, raising=False)
        _clear_role_model_env(monkeypatch)

    def test_hoca_max_total_rounds(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("HOCA_MAX_TOTAL_ROUNDS=5\n")
        self._clear_current_env(monkeypatch)
        cfg = load_config(dotenv_path=env_file)
        assert cfg.max_total_rounds == 5

    def test_direct_llm_env_vars_are_ignored_by_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "LLM_MODEL=ollama/qwen-32b-pro\n"
            "LLM_BASE_URL=http://10.0.0.1:11434\n"
            "LLM_API_KEY=my-key\n"
        )
        self._clear_current_env(monkeypatch)
        cfg = load_config(dotenv_path=env_file)
        assert not hasattr(cfg, "llm_model")
        assert cfg.ollama_model == "qwen-14b-pro"

    def test_ollama_host_alias(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("OLLAMA_HOST=http://10.0.0.5:11434\n")
        self._clear_current_env(monkeypatch)
        cfg = load_config(dotenv_path=env_file)
        assert cfg.ollama_host == "http://10.0.0.5:11434"
        assert cfg.ollama_base_url == "http://10.0.0.5:11434"

    def test_ollama_base_url_overrides_ollama_host(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "OLLAMA_HOST=http://host-value:11434\nOLLAMA_BASE_URL=http://base-url-value:11434\n"
        )
        self._clear_current_env(monkeypatch)
        cfg = load_config(dotenv_path=env_file)
        assert cfg.ollama_base_url == "http://base-url-value:11434"
        assert cfg.ollama_host == "http://host-value:11434"

    def test_ollama_api_base_alias(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("OLLAMA_API_BASE=http://10.0.0.7:11434\n")
        self._clear_current_env(monkeypatch)
        cfg = load_config(dotenv_path=env_file)
        assert cfg.ollama_api_base == "http://10.0.0.7:11434"

    def test_ollama_api_base_falls_back_to_ollama_base_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("OLLAMA_BASE_URL=http://10.0.0.9:11434\n")
        self._clear_current_env(monkeypatch)
        cfg = load_config(dotenv_path=env_file)
        assert cfg.ollama_api_base == "http://10.0.0.9:11434"

    def test_require_review_controls_review_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("HOCA_REQUIRE_REVIEW=false\n")
        self._clear_current_env(monkeypatch)
        cfg = load_config(dotenv_path=env_file)
        assert cfg.require_review is False

    def test_notification_env_vars(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOCA_NOTIFY_TELEGRAM=true\nTELEGRAM_BOT_TOKEN=bot123\nTELEGRAM_CHAT_ID=chat456\n"
        )
        self._clear_current_env(monkeypatch)
        cfg = load_config(dotenv_path=env_file)
        assert cfg.notify_telegram is True
        assert cfg.telegram_bot_token == "bot123"
        assert cfg.telegram_chat_id == "chat456"

    def test_webhook_env_vars(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOCA_WEBHOOK_SECRET=sec123\n"
            "HOCA_WEBHOOK_URL=https://example.com/webhook\n"
            "HOCA_ALLOWED_REPOS=owner/repo\n"
            "HOCA_MAX_WEBHOOK_BYTES=32768\n"
            "HOCA_WORKSPACE_ROOT=/tmp/code\n"
        )
        self._clear_current_env(monkeypatch)
        cfg = load_config(dotenv_path=env_file)
        assert cfg.webhook_secret == "sec123"
        assert cfg.webhook_url == "https://example.com/webhook"
        assert cfg.allowed_repos == "owner/repo"
        assert cfg.max_webhook_bytes == 32768
        assert cfg.workspace_root is not None

    def test_empty_model_pool_uses_ollama_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "LLM_MODEL=ollama/qwen-32b-pro\n"
            "LLM_BASE_URL=http://127.0.0.1:11434\n"
            "LLM_API_KEY=ollama\n"
        )
        self._clear_current_env(monkeypatch)
        cfg = load_config(dotenv_path=env_file)
        assert cfg.model_pool.is_active is False
        assert cfg.ollama_model == "qwen-14b-pro"
