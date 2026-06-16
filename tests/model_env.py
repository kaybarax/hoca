from __future__ import annotations

import os
from collections.abc import MutableMapping

import pytest


DUMMY_ROLE_MODEL_ENV = {
    "HOCA_MANAGER_MODEL_MODEL": "test-provider/manager",
    "HOCA_MANAGER_MODEL_BASE_URL": "http://127.0.0.1:65531/v1",
    "HOCA_WORKER_MODEL_MODEL": "test-provider/worker",
    "HOCA_WORKER_MODEL_BASE_URL": "http://127.0.0.1:65532/v1",
    "HOCA_REVIEWER_MODEL_MODEL": "test-provider/reviewer",
    "HOCA_REVIEWER_MODEL_BASE_URL": "http://127.0.0.1:65533/v1",
}


def set_dummy_role_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in DUMMY_ROLE_MODEL_ENV.items():
        monkeypatch.setenv(name, value)


def with_dummy_role_model_env(
    env: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    merged = dict(os.environ if env is None else env)
    merged.update(DUMMY_ROLE_MODEL_ENV)
    return merged
