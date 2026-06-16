from __future__ import annotations

from pathlib import Path

import pytest

from hoca.config import load_config
from hoca.role_model_env import model_pool_doctor_lines


def write_env(path: Path, body: str) -> None:
    path.write_text(body.strip() + "\n", encoding="utf-8")


def test_doctor_residency_passes_quietly_on_single_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    write_env(
        env_path,
        """
        HOCA_MANAGER_MODEL_NAME=manager
        HOCA_MANAGER_MODEL_MODEL=ollama/qwen-14b-pro
        HOCA_WORKER_MODEL_NAME=worker
        HOCA_WORKER_MODEL_MODEL=ollama/qwen-14b-pro
        HOCA_REVIEWER_MODEL_NAME=reviewer
        HOCA_REVIEWER_MODEL_MODEL=ollama/qwen-14b-pro
        """,
    )
    monkeypatch.setenv("HOCA_SYSTEM_RAM_GB", "64")
    monkeypatch.delenv("HOCA_STRICT_MODEL_RESIDENCY", raising=False)

    lines = model_pool_doctor_lines(load_config(dotenv_path=env_path))

    assert any("Model residency check fits" in message for _, message in lines)
    assert not any(status == "warn" and "over-committed" in message for status, message in lines)


def test_doctor_residency_warns_on_overcommitted_multi_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    write_env(
        env_path,
        """
        HOCA_MANAGER_MODEL_NAME=manager
        HOCA_MANAGER_MODEL_MODEL=ollama/qwen-7b-pro
        HOCA_WORKER_MODEL_NAME=worker
        HOCA_WORKER_MODEL_MODEL=ollama/qwen-14b-pro
        HOCA_REVIEWER_MODEL_NAME=reviewer
        HOCA_REVIEWER_MODEL_MODEL=ollama/qwen-32b-pro
        """,
    )
    monkeypatch.setenv("HOCA_SYSTEM_RAM_GB", "64")
    monkeypatch.delenv("HOCA_STRICT_MODEL_RESIDENCY", raising=False)

    lines = model_pool_doctor_lines(load_config(dotenv_path=env_path))

    assert any(status == "warn" and "swap churn" in message for status, message in lines)
    assert any(status == "warn" and "over-committed" in message for status, message in lines)


def test_doctor_residency_strict_mode_fails_overcommit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    write_env(
        env_path,
        """
        HOCA_MANAGER_MODEL_NAME=manager
        HOCA_MANAGER_MODEL_MODEL=ollama/qwen-7b-pro
        HOCA_WORKER_MODEL_NAME=worker
        HOCA_WORKER_MODEL_MODEL=ollama/qwen-14b-pro
        HOCA_REVIEWER_MODEL_NAME=reviewer
        HOCA_REVIEWER_MODEL_MODEL=ollama/qwen-32b-pro
        """,
    )
    monkeypatch.setenv("HOCA_SYSTEM_RAM_GB", "64")
    monkeypatch.setenv("HOCA_STRICT_MODEL_RESIDENCY", "true")

    lines = model_pool_doctor_lines(load_config(dotenv_path=env_path))

    assert any(status == "fail" and "over-committed" in message for status, message in lines)
