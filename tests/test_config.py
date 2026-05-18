from __future__ import annotations

from pathlib import Path

import pytest

from hoca.config import HocaConfig, load_config, parse_bool


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

        cfg = load_config(dotenv_path=empty_env)

        assert cfg.auto_merge is False
        assert cfg.require_tests is True
        assert cfg.stop_on_dirty_tree is True
        assert cfg.dev_branch == "main"
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
            "HOCA_WORKSPACE_ROOT=~/projects\n"
        )
        for key in [
            "HOCA_AUTO_MERGE",
            "HOCA_REQUIRE_TESTS",
            "OLLAMA_MODEL",
            "HOCA_DEV_BRANCH",
            "HOCA_WORKSPACE_ROOT",
        ]:
            monkeypatch.delenv(key, raising=False)

        cfg = load_config(dotenv_path=env_file)

        assert cfg.auto_merge is True
        assert cfg.require_tests is False
        assert cfg.ollama_model == "custom-model"
        assert cfg.dev_branch == "develop"
        assert cfg.workspace_root is not None
        assert "~" not in str(cfg.workspace_root)

    def test_env_var_overrides_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("HOCA_AUTO_MERGE=false\n")
        monkeypatch.setenv("HOCA_AUTO_MERGE", "true")

        cfg = load_config(dotenv_path=env_file)

        assert cfg.auto_merge is True


class TestSafeRepr:
    def test_secrets_are_masked(self) -> None:
        cfg = HocaConfig(
            webhook_secret="super-secret-value",
            telegram_bot_token="tok123",
        )
        safe = cfg.safe_repr()
        assert safe["webhook_secret"] == "***"
        assert safe["telegram_bot_token"] == "***"

    def test_empty_secrets_show_unset(self) -> None:
        cfg = HocaConfig()
        safe = cfg.safe_repr()
        assert safe["webhook_secret"] == "(unset)"
        assert safe["telegram_bot_token"] == "(unset)"

    def test_non_secret_fields_shown(self) -> None:
        cfg = HocaConfig(ollama_model="test-model")
        safe = cfg.safe_repr()
        assert safe["ollama_model"] == "test-model"
