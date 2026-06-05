from __future__ import annotations

from pathlib import Path

import pytest

from hoca.config import HocaConfig, ModelPoolConfig, ModelSlot, load_config
from hoca.role_model_env import (
    apply_role_to_env,
    export_shell,
    hermes_provider_for_model,
    model_pool_doctor_lines,
    pool_credential_env_keys,
    resolve_role_llm,
    should_resolve_role_model,
    strip_pool_credentials,
)


def _active_pool_config() -> ModelPoolConfig:
    return ModelPoolConfig(
        slots=(
            ModelSlot(
                name="local-coder",
                model="ollama/qwen-14b-pro",
                base_url="http://127.0.0.1:11434",
                api_key="secret-worker",
            ),
            ModelSlot(
                name="reviewer-strong",
                model="openai/gpt-oss-20b",
                base_url="http://localhost:1234/v1",
                api_key="secret-reviewer",
            ),
            ModelSlot(
                name="local-fast",
                model="ollama/qwen-7b-pro",
                base_url="http://127.0.0.1:11434",
                api_key="secret-fast",
            ),
        ),
        worker_model="local-coder",
        reviewer_model="reviewer-strong",
        fallback_model="local-fast",
    )


class TestRoleModelResolution:
    def test_inactive_pool_uses_ollama_fallback(self) -> None:
        cfg = HocaConfig(
            ollama_model="qwen-14b-pro",
            ollama_base_url="http://127.0.0.1:11434",
        )

        selection = resolve_role_llm("worker", cfg)

        assert selection.llm_model == "ollama/qwen-14b-pro"
        assert selection.api_key == "ollama"
        assert should_resolve_role_model(cfg) is False

    def test_active_pool_resolves_worker_and_reviewer_slots(self) -> None:
        cfg = HocaConfig(model_pool=_active_pool_config())

        worker = resolve_role_llm("worker", cfg)
        reviewer = resolve_role_llm("reviewer", cfg)

        assert worker.slot_name == "local-coder"
        assert worker.api_key == "secret-worker"
        assert reviewer.slot_name == "reviewer-strong"
        assert reviewer.api_key == "secret-reviewer"
        assert should_resolve_role_model(cfg) is True

    def test_requested_model_env_does_not_skip_role_resolution(self) -> None:
        cfg = HocaConfig(model_pool=_active_pool_config())
        env = {"HOCA_REQUESTED_MODEL": "qwen-7b-pro", "LLM_MODEL": "ollama/qwen-7b-pro"}

        assert should_resolve_role_model(cfg, env) is True

    def test_apply_role_strips_other_pool_credentials(self) -> None:
        cfg = HocaConfig(model_pool=_active_pool_config())
        env = {
            "HOCA_WORKER_MODEL_API_KEY": "secret-worker",
            "HOCA_REVIEWER_MODEL_API_KEY": "secret-reviewer",
            "HOCA_MANAGER_MODEL_API_KEY": "secret-fast",
            "LLM_API_KEY": "stale",
            "OPENAI_API_KEY": "stale-openai",
        }

        worker_env = apply_role_to_env("worker", cfg, env)

        assert worker_env["LLM_MODEL"] == "ollama/qwen-14b-pro"
        assert worker_env["LLM_API_KEY"] == "secret-worker"
        assert "HOCA_WORKER_MODEL_API_KEY" not in worker_env
        assert "HOCA_REVIEWER_MODEL_API_KEY" not in worker_env
        assert "OPENAI_API_KEY" not in worker_env

        reviewer_env = apply_role_to_env("reviewer", cfg, env)

        assert reviewer_env["LLM_API_KEY"] == "secret-reviewer"
        assert reviewer_env["LLM_MODEL"] == "openai/gpt-oss-20b"
        assert reviewer_env["OPENAI_API_KEY"] == "secret-reviewer"

    def test_apply_role_adds_provider_specific_key_for_hermes_profiles(self) -> None:
        cfg = HocaConfig(
            model_pool=ModelPoolConfig(
                slots=(
                    ModelSlot(
                        name="worker-cloud",
                        model="deepseek/deepseek-v4-flash",
                        api_key="secret-worker",
                    ),
                ),
                worker_model="worker-cloud",
                fallback_model="worker-cloud",
            )
        )

        worker_env = apply_role_to_env("worker", cfg, {})

        assert worker_env["LLM_API_KEY"] == "secret-worker"
        assert worker_env["DEEPSEEK_API_KEY"] == "secret-worker"
        assert "OPENAI_API_KEY" not in worker_env

    def test_export_shell_omits_inactive_pool(self) -> None:
        cfg = HocaConfig(ollama_model="qwen-14b-pro")

        assert export_shell("worker", config=cfg) == ""

    def test_export_shell_sets_ollama_alias(self) -> None:
        cfg = HocaConfig(model_pool=_active_pool_config())
        exports = export_shell("worker", config=cfg)

        assert "export LLM_MODEL=ollama/qwen-14b-pro" in exports
        assert "export OLLAMA_MODEL=qwen-14b-pro" in exports
        assert "secret-worker" in exports
        assert "secret-reviewer" not in exports

    def test_export_shell_sets_provider_alias_for_cloud_model(self) -> None:
        cfg = HocaConfig(
            model_pool=ModelPoolConfig(
                slots=(
                    ModelSlot(
                        name="worker-cloud",
                        model="deepseek/deepseek-v4-flash",
                        api_key="secret-worker",
                    ),
                ),
                worker_model="worker-cloud",
                fallback_model="worker-cloud",
            )
        )

        exports = export_shell("worker", config=cfg)

        assert "export LLM_API_KEY=secret-worker" in exports
        assert "export DEEPSEEK_API_KEY=secret-worker" in exports


class TestModelPoolDoctorLines:
    def test_inactive_pool_reports_ollama_fallback_mode(self) -> None:
        lines = model_pool_doctor_lines(HocaConfig())

        assert any(status == "ok" and "inactive" in message for status, message in lines)

    def test_active_pool_validates_roles(self) -> None:
        lines = model_pool_doctor_lines(HocaConfig(model_pool=_active_pool_config()))

        assert any("Model pool active" in message for _, message in lines)
        assert any("worker resolves" in message for _, message in lines)
        assert not any(status == "fail" for status, _ in lines)

    def test_same_worker_and_reviewer_slot_warns(self) -> None:
        pool = ModelPoolConfig(
            slots=(ModelSlot(name="shared", model="ollama/qwen-14b-pro", api_key="x"),),
            worker_model="shared",
            reviewer_model="shared",
            fallback_model="shared",
        )
        lines = model_pool_doctor_lines(HocaConfig(model_pool=pool))

        assert any(status == "warn" and "same model slot" in message for status, message in lines)


class TestRunnerCredentialIsolation:
    def test_openhands_wrapper_logs_slot_name_not_other_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.test_model_selection_scripts import (
            init_repo,
            make_fake_curl,
            make_fake_ollama,
            make_fake_openhands,
            run_script,
        )

        fake_bin = make_fake_ollama(tmp_path, ["qwen-14b-pro", "qwen-7b-pro"])
        make_fake_curl(fake_bin)
        make_fake_openhands(fake_bin)
        project = tmp_path / "project"
        run_dir = tmp_path / "run"
        project.mkdir()
        init_repo(project)

        pool_env = {
            "HOCA_WORKER_MODEL_NAME": "local-coder",
            "HOCA_WORKER_MODEL_MODEL": "ollama/qwen-14b-pro",
            "HOCA_WORKER_MODEL_BASE_URL": "http://127.0.0.1:11434",
            "HOCA_WORKER_MODEL_API_KEY": "secret-worker",
            "HOCA_REVIEWER_MODEL_NAME": "reviewer-strong",
            "HOCA_REVIEWER_MODEL_MODEL": "ollama/qwen-7b-pro",
            "HOCA_REVIEWER_MODEL_BASE_URL": "http://127.0.0.1:11434",
            "HOCA_REVIEWER_MODEL_API_KEY": "secret-reviewer",
        }
        result = run_script(
            "run-openhands-task.sh",
            fake_bin,
            extra_env={
                "HOCA_USE_SANDBOX": "false",
                "HOCA_AGENT_ROLE": "worker",
                **pool_env,
            },
            args=[str(project), "Summarize project", str(run_dir)],
        )

        assert result.returncode == 0, result.stderr
        assert "Resolved worker model slot: local-coder" in result.stdout
        assert "MODEL=ollama/qwen-14b-pro" in result.stdout
        assert "secret-reviewer" not in result.stdout


def test_strip_pool_credentials_removes_configured_keys() -> None:
    env = {
        "LLM_API_KEY": "x",
        "DEEPSEEK_API_KEY": "x",
        "HOCA_WORKER_MODEL_API_KEY": "a",
        "PATH": "/usr/bin",
    }
    cleaned = strip_pool_credentials(env)

    assert cleaned["PATH"] == "/usr/bin"
    assert "LLM_API_KEY" not in cleaned
    assert "DEEPSEEK_API_KEY" not in cleaned
    assert pool_credential_env_keys(env) == [
        "LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "HOCA_WORKER_MODEL_API_KEY",
    ]


def test_load_config_empty_pool_ignores_direct_llm_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_MODEL=openai/gpt-oss-20b\nLLM_BASE_URL=http://localhost:1234/v1\n")
    for role in ("MANAGER", "WORKER", "REVIEWER"):
        for suffix in ("NAME", "MODEL", "BASE_URL", "API_KEY"):
            monkeypatch.delenv(f"HOCA_{role}_MODEL_{suffix}", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    cfg = load_config(dotenv_path=env_file)

    assert cfg.model_pool.is_active is False
    assert resolve_role_llm("worker", cfg).llm_model == "ollama/qwen-14b-pro"


def test_hermes_provider_for_model_maps_cloud_prefixes() -> None:
    assert hermes_provider_for_model("deepseek/deepseek-v4-flash") == "deepseek"
    assert hermes_provider_for_model("openrouter/openai/gpt-4o-mini") == "openrouter"
    assert hermes_provider_for_model("ollama/qwen-14b-pro") == ""
