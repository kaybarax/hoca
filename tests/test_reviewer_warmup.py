from __future__ import annotations

import urllib.error
from pathlib import Path

from hoca.reviewer_warmup import inspect_sandbox_container, warm_reviewer


def test_reviewer_warmup_records_unsupported_provider_without_failing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-4.1")
    monkeypatch.setenv("HOCA_USE_SANDBOX", "false")

    result = warm_reviewer(tmp_path)

    assert result["status"] == "completed"
    assert result["model_warmup"]["attempted"] is False
    assert result["sandbox"]["reason"] == "sandbox disabled"


def test_reviewer_warmup_records_ollama_failure_as_advisory(tmp_path: Path, monkeypatch) -> None:
    def fake_urlopen(*args, **kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setenv("LLM_MODEL", "ollama/qwen-14b-pro")
    monkeypatch.setenv("HOCA_USE_SANDBOX", "false")
    monkeypatch.setattr("hoca.reviewer_warmup.urllib.request.urlopen", fake_urlopen)

    result = warm_reviewer(tmp_path, timeout_seconds=0.01)

    assert result["status"] == "failed"
    assert result["model_warmup"]["attempted"] is True
    assert result["model_warmup"]["error"] == "URLError"


def test_sandbox_container_inspection_is_best_effort(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOCA_USE_SANDBOX", "true")
    monkeypatch.setattr("hoca.reviewer_warmup.shutil.which", lambda name: None)

    result = inspect_sandbox_container(tmp_path)

    assert result == {"attempted": False, "reason": "docker unavailable"}
