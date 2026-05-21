from __future__ import annotations

import pytest

from hoca.env_allowlist import (
    MANAGER_PR_ALLOWLIST,
    WORKER_REVIEWER_ALLOWLIST,
    allowlist_for_phase,
    blocked_keys,
    filter_env,
    filter_env_for_role,
    redact_env_for_logging,
)


class TestWorkerReviewerAllowlist:
    def test_llm_vars_are_allowed(self) -> None:
        env = {
            "LLM_MODEL": "ollama/qwen-14b-pro",
            "LLM_BASE_URL": "http://127.0.0.1:11434",
            "LLM_API_KEY": "ollama",
        }
        result = filter_env(env, "worker")
        assert result == env

    def test_github_token_is_blocked(self) -> None:
        env = {
            "GITHUB_TOKEN": "ghp_secret123",
            "LLM_MODEL": "ollama/qwen-14b-pro",
            "HOME": "/home/user",
        }
        result = filter_env(env, "worker")
        assert "GITHUB_TOKEN" not in result
        assert result["LLM_MODEL"] == "ollama/qwen-14b-pro"
        assert result["HOME"] == "/home/user"

    def test_ssh_and_cloud_tokens_are_blocked(self) -> None:
        env = {
            "SSH_AUTH_SOCK": "/tmp/ssh-agent",
            "AWS_ACCESS_KEY_ID": "AKIA...",
            "AWS_SECRET_ACCESS_KEY": "wJalrX...",
            "AZURE_CLIENT_SECRET": "secret",
            "NPM_TOKEN": "npm_xxx",
            "DOCKER_HOST": "tcp://localhost:2375",
            "GH_TOKEN": "ghp_abc",
            "HOMEBREW_GITHUB_API_TOKEN": "ghp_xyz",
            "LLM_MODEL": "test",
        }
        result = filter_env(env, "reviewer")
        assert result == {"LLM_MODEL": "test"}

    def test_openhands_suppress_banner_allowed(self) -> None:
        env = {"OPENHANDS_SUPPRESS_BANNER": "1"}
        result = filter_env(env, "worker")
        assert result == env

    def test_ci_and_basic_shell_vars_allowed(self) -> None:
        env = {
            "CI": "true",
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "LANG": "en_US.UTF-8",
            "TERM": "xterm",
            "USER": "dev",
        }
        result = filter_env(env, "worker")
        assert result == env

    def test_hoca_agent_role_allowed(self) -> None:
        env = {"HOCA_AGENT_ROLE": "worker", "HOCA_SELECTED_MODEL_SLOT": "local-coder"}
        result = filter_env(env, "worker")
        assert result == env

    def test_worker_and_reviewer_use_same_allowlist(self) -> None:
        assert allowlist_for_phase("worker") is allowlist_for_phase("reviewer")

    def test_hermes_prefixed_vars_are_allowed(self) -> None:
        env = {
            "HERMES_HOME": "/home/user/.hermes",
            "HERMES_ACCEPT_HOOKS": "1",
            "HERMES_CUSTOM_SETTING": "value",
        }
        result = filter_env(env, "worker")
        assert result == env

    def test_hermes_prefix_does_not_leak_to_manager_pr(self) -> None:
        env = {"HERMES_HOME": "/home/user/.hermes", "GITHUB_TOKEN": "ghp_x"}
        result = filter_env(env, "manager-pr")
        assert "HERMES_HOME" not in result
        assert result["GITHUB_TOKEN"] == "ghp_x"


class TestManagerPrAllowlist:
    def test_github_token_is_allowed(self) -> None:
        env = {
            "GITHUB_TOKEN": "ghp_secret123",
            "GITHUB_REPOSITORY": "owner/repo",
            "GH_TOKEN": "ghp_fallback",
        }
        result = filter_env(env, "manager-pr")
        assert result == env

    def test_llm_keys_are_blocked_in_pr_phase(self) -> None:
        env = {
            "LLM_API_KEY": "secret-model-key",
            "LLM_MODEL": "ollama/qwen-14b-pro",
            "GITHUB_TOKEN": "ghp_x",
        }
        result = filter_env(env, "manager-pr")
        assert "LLM_API_KEY" not in result
        assert "LLM_MODEL" not in result
        assert result["GITHUB_TOKEN"] == "ghp_x"


class TestExtraAllow:
    def test_extra_allow_extends_allowlist(self) -> None:
        env = {"CUSTOM_VAR": "value", "LLM_MODEL": "test"}
        result = filter_env(env, "worker", extra_allow=frozenset({"CUSTOM_VAR"}))
        assert result == env

    def test_extra_allow_does_not_affect_base(self) -> None:
        env = {"GITHUB_TOKEN": "ghp_x", "CUSTOM_VAR": "y"}
        result = filter_env(env, "worker", extra_allow=frozenset({"CUSTOM_VAR"}))
        assert "GITHUB_TOKEN" not in result
        assert result["CUSTOM_VAR"] == "y"


class TestBlockedKeys:
    def test_reports_blocked_keys(self) -> None:
        env = {
            "LLM_MODEL": "test",
            "GITHUB_TOKEN": "ghp_x",
            "AWS_SECRET_ACCESS_KEY": "y",
        }
        blocked = blocked_keys(env, "worker")
        assert "GITHUB_TOKEN" in blocked
        assert "AWS_SECRET_ACCESS_KEY" in blocked
        assert "LLM_MODEL" not in blocked


class TestRedactEnvForLogging:
    def test_redacts_secret_like_keys(self) -> None:
        env = {
            "LLM_API_KEY": "secret-value",
            "GITHUB_TOKEN": "ghp_abc",
            "LLM_MODEL": "ollama/qwen-14b-pro",
            "HOME": "/home/user",
            "AWS_SECRET_ACCESS_KEY": "wJalrX",
        }
        redacted = redact_env_for_logging(env)
        assert redacted["LLM_API_KEY"] == "***"
        assert redacted["GITHUB_TOKEN"] == "***"
        assert redacted["AWS_SECRET_ACCESS_KEY"] == "***"
        assert redacted["LLM_MODEL"] == "ollama/qwen-14b-pro"
        assert redacted["HOME"] == "/home/user"

    def test_unset_secrets_show_unset(self) -> None:
        env = {"LLM_API_KEY": "", "GITHUB_TOKEN": ""}
        redacted = redact_env_for_logging(env)
        assert redacted["LLM_API_KEY"] == "(unset)"
        assert redacted["GITHUB_TOKEN"] == "(unset)"


class TestFilterEnvForRole:
    def test_defaults_to_os_environ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_MODEL", "test-model")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
        result = filter_env_for_role(phase="worker")
        assert result["LLM_MODEL"] == "test-model"
        assert "GITHUB_TOKEN" not in result


class TestCriticalSecurityVars:
    """Verify that high-risk environment variables are never forwarded to worker/reviewer."""

    @pytest.mark.parametrize(
        "var",
        [
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "GITHUB_ENTERPRISE_TOKEN",
            "SSH_AUTH_SOCK",
            "SSH_AGENT_PID",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AZURE_CLIENT_SECRET",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "NPM_TOKEN",
            "DOCKER_HOST",
            "DOCKER_CONFIG",
            "HOMEBREW_GITHUB_API_TOKEN",
            "TELEGRAM_BOT_TOKEN",
            "HOCA_WEBHOOK_SECRET",
        ],
    )
    def test_dangerous_var_blocked_for_worker(self, var: str) -> None:
        assert var not in WORKER_REVIEWER_ALLOWLIST

    @pytest.mark.parametrize(
        "var",
        [
            "LLM_API_KEY",
            "LLM_MODEL",
            "LLM_BASE_URL",
            "OLLAMA_MODEL",
            "SSH_AUTH_SOCK",
            "AWS_ACCESS_KEY_ID",
        ],
    )
    def test_non_pr_vars_blocked_for_manager_pr(self, var: str) -> None:
        assert var not in MANAGER_PR_ALLOWLIST
