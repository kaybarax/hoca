from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_env_example_documents_hermes_upgrade_variables() -> None:
    content = (ROOT / ".env.example").read_text(encoding="utf-8")
    required_entries = [
        "HOCA_USE_KANBAN=false",
        "HOCA_MAX_TOTAL_ROUNDS=3",
        "HOCA_NETWORK_MODE=offline",
        "HOCA_USE_WORKTREE_SANDBOX=true",
        "HOCA_USE_SANDBOX=true",
        "HOCA_WORKER_MODE=direct",
        "HOCA_REVIEWER_MODE=direct",
        "HOCA_WORKER_ENGINE=openhands",
        "HOCA_REVIEW_WARMUP=true",
        "HOCA_FORCE_INSTALL=false",
        "HOCA_BUDGET_MIN_TOTAL_ROUNDS=1",
        "HOCA_BUDGET_MAX_TOTAL_ROUNDS=3",
        "HOCA_BUDGET_MIN_OPENHANDS_TIMEOUT=240",
        "HOCA_BUDGET_MAX_OPENHANDS_TIMEOUT=1200",
        "HOCA_BUDGET_MIN_OPENHANDS_STALL=90",
        "HOCA_BUDGET_MAX_OPENHANDS_STALL=600",
        "HOCA_BUDGET_MIN_HERMES_TIMEOUT=600",
        "HOCA_BUDGET_MAX_HERMES_TIMEOUT=2400",
    ]
    for role in ("MANAGER", "WORKER", "REVIEWER"):
        for suffix in ("NAME", "MODEL", "BASE_URL", "API_KEY"):
            required_entries.append(f"HOCA_{role}_MODEL_{suffix}=")

    missing = [entry for entry in required_entries if entry not in content]

    assert missing == []


def test_env_example_explains_safety_and_role_credential_forwarding() -> None:
    content = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert (
        "Set to false only for explicit host-local execution (higher risk; see README)." in content
    )
    assert "full: unrestricted bridge egress; explicit opt-in only" in content
    assert "Only the selected role model is forwarded" in content
    assert "Raw role model credentials" in content
    assert "LLM_MODEL=" not in content
    assert "LLM_BASE_URL=" not in content
    assert "LLM_API_KEY=" not in content


def test_env_example_defaults_all_roles_to_one_model() -> None:
    content = (ROOT / ".env.example").read_text(encoding="utf-8")

    uncommented_model_lines = [
        line
        for line in content.splitlines()
        if line.startswith("HOCA_") and "_MODEL_MODEL=" in line
    ]

    assert uncommented_model_lines == [
        "HOCA_MANAGER_MODEL_MODEL=ollama/qwen-14b-pro",
        "HOCA_WORKER_MODEL_MODEL=ollama/qwen-14b-pro",
        "HOCA_REVIEWER_MODEL_MODEL=ollama/qwen-14b-pro",
    ]
    assert "Explicit multi-model opt-in" in content


def test_readme_documents_single_model_residency_default() -> None:
    content = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "documented default is one resident model for all\nroles" in content
    assert "one `qwen-14b-pro` residency is about 24 GB" in content
    assert "swap churn" in content
    assert "Multi-model routing remains supported as an explicit opt-in" in content


def test_docs_explain_backend_keep_alive_settings() -> None:
    env_content = (ROOT / ".env.example").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "# OLLAMA_KEEP_ALIVE=30m" in env_content
    assert "# OLLAMA_MAX_LOADED_MODELS=1" in env_content
    assert "disable idle unload" in env_content
    assert "### Backend Keep-Alive" in readme
    assert "export OLLAMA_KEEP_ALIVE=30m" in readme
    assert "export OLLAMA_MAX_LOADED_MODELS=1" in readme
    assert "disable idle model unloading" in readme
    assert "idle-timeout" in readme


def test_readme_links_performance_guide_and_fast_defaults() -> None:
    content = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "[Performance guide](docs/performance.md)" in content
    assert "### v1.1 Fast Defaults" in content
    assert "HOCA_WORKER_MODE=direct" in content
    assert "HOCA_REVIEWER_MODE=direct" in content
    assert "HOCA_WORKER_ENGINE=openhands" in content
    assert "HOCA_REVIEW_WARMUP=true" in content
    assert "run-budget-round-N.json" in content
