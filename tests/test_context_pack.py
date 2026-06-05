from __future__ import annotations

from hoca.context_pack import (
    append_prompt_pattern,
    append_release_policy,
    append_test_command,
    load_project_context_pack,
    update_architecture_map,
    update_context_summary,
)


def test_context_pack_redacts_secrets_and_round_trips_payloads(tmp_path) -> None:
    project_id = "project-alpha"
    update_context_summary(project_id, "api_key=abc123\nrelease strategy", control_root=tmp_path)
    update_architecture_map(project_id, "modular architecture", control_root=tmp_path)
    append_test_command(project_id, "pytest -q", control_root=tmp_path)
    append_release_policy(project_id, "always run smoke tests", control_root=tmp_path)
    append_prompt_pattern(project_id, "feature-first approach", control_root=tmp_path)

    pack = load_project_context_pack(project_id, control_root=tmp_path)
    assert "***redacted***" in pack.summary
    assert "abc123" not in pack.summary
    assert pack.architecture_map == "modular architecture"
    assert pack.test_commands == ("pytest -q",)
    assert pack.release_policies == ("always run smoke tests",)
    assert pack.prompt_patterns == ("feature-first approach",)


def test_context_pack_limits_and_deduplicates_listed_entries(tmp_path) -> None:
    project_id = "project-bravo"
    for value in ["first", "second", "second", "third", "fourth"]:
        append_prompt_pattern(
            project_id,
            value,
            control_root=tmp_path,
            max_items=2,
        )

    pack = load_project_context_pack(project_id, control_root=tmp_path)
    assert pack.prompt_patterns == ("third", "fourth")
