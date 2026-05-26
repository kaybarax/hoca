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
    ]
    for role in ("MANAGER", "WORKER", "REVIEWER"):
        for suffix in ("NAME", "MODEL", "BASE_URL", "API_KEY"):
            required_entries.append(f"HOCA_{role}_MODEL_{suffix}=")

    missing = [entry for entry in required_entries if entry not in content]

    assert missing == []


def test_env_example_explains_safety_and_role_credential_forwarding() -> None:
    content = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "Set to false only for explicit host-local execution (higher risk; see README)." in content
    assert "full: unrestricted bridge egress; explicit opt-in only" in content
    assert "Only the selected role model is forwarded" in content
    assert "Raw role model credentials" in content
    assert "LLM_MODEL=" not in content
    assert "LLM_BASE_URL=" not in content
    assert "LLM_API_KEY=" not in content
