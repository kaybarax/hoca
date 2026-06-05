from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hoca.agent_adapters import (
    AdapterCommandError,
    AdapterUnavailableError,
    AdapterRunArtifact,
    AgentAdapter,
    adapter_doctor_lines,
    custom_command_adapter_spec,
    default_openhands_adapter_spec,
    fake_session_id,
    format_command,
    missing_required_commands,
    required_commands_from_template,
    required_commands_ok,
)
from hoca.fleet_contracts import HocaAgentAdapterSpec


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)


def test_format_and_required_commands_detect_missing() -> None:
    spec = HocaAgentAdapterSpec(
        adapter_id="custom",
        provider="custom",
        command_template="{worktree_path}/bin/custom-agent --task={task} --lane={lane_id}",
        command_allowlist=["/tmp/work/bin/custom-agent"],
        max_concurrency=1,
    )
    rendered = format_command(
        spec.command_template,
        values={"worktree_path": Path("/tmp/work"), "task": "build", "lane_id": "lane-1"},
    )
    assert rendered.startswith("/tmp/work")

    cmds = required_commands_from_template(spec.command_template)
    assert cmds[0] == "/tmp/work/bin/custom-agent"
    assert "custom-agent" in cmds
    assert "/tmp/work/bin/custom-agent" in missing_required_commands(spec)


def test_adapter_start_collect_round_trip_with_fake_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    agent = fake_bin / "hoca-fake-agent"
    _write_executable(
        agent,
        '#!/usr/bin/env bash\necho agent-run\necho "openai=$OPENAI_API_KEY"\ncat\n',
    )
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    spec = HocaAgentAdapterSpec(
        adapter_id="fake",
        provider="fake",
        command_template="hoca-fake-agent --worktree={worktree_path} --task={task}",
        command_allowlist=["hoca-fake-agent"],
        max_concurrency=1,
        capabilities=["coding"],
    )

    adapter = AgentAdapter(spec=spec)
    run_dir = tmp_path / "run"
    session = adapter.start(
        session_id=fake_session_id(),
        lane_id="lane-1",
        project_path=tmp_path,
        worktree_path=tmp_path,
        task="Hello",
        run_dir=run_dir,
        extra_env={"OPENAI_API_KEY": "done"},
    )
    adapter.send(session, "from-manager")
    assert adapter.stop(session) is True
    artifact = adapter.collect(session=session, run_dir=run_dir)

    assert artifact.return_code == 0
    assert artifact.stdout.splitlines()[0] == "agent-run"
    assert "openai=done" in artifact.stdout
    assert "from-manager" in artifact.stderr
    assert artifact.command.startswith("hoca-fake-agent")
    assert artifact.metadata is not None
    assert artifact.metadata["openai"] == "done"


def test_adapter_start_filters_github_tokens_from_worker_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    agent = fake_bin / "hoca-fake-agent"
    _write_executable(
        agent,
        "#!/usr/bin/env bash\necho github=$GITHUB_TOKEN\necho gh=$GH_TOKEN\ncat\n",
    )
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    spec = HocaAgentAdapterSpec(
        adapter_id="fake",
        provider="fake",
        command_template="hoca-fake-agent --worktree={worktree_path} --task={task}",
        command_allowlist=["hoca-fake-agent"],
        max_concurrency=1,
        capabilities=["coding"],
    )

    adapter = AgentAdapter(spec=spec)
    run_dir = tmp_path / "run"
    session = adapter.start(
        session_id=fake_session_id(),
        lane_id="lane-1",
        project_path=tmp_path,
        worktree_path=tmp_path,
        task="Hello",
        run_dir=run_dir,
        extra_env={
            "GITHUB_TOKEN": "keep-out",
            "GH_TOKEN": "keep-out",
            "OPENAI_API_KEY": "still-allowed",
        },
    )
    adapter.send(session, "from-manager")
    assert adapter.stop(session) is True
    artifact = adapter.collect(session=session, run_dir=run_dir)

    assert "github=keep-out" not in artifact.stdout
    assert "gh=keep-out" not in artifact.stdout
    assert "github=" in artifact.stdout
    assert "gh=" in artifact.stdout


def test_start_rejects_secret_like_extra_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    agent = fake_bin / "hoca-fake-agent"
    _write_executable(
        agent,
        "#!/usr/bin/env bash\necho openai=$OPENAI_API_KEY\necho secret=$SECRET_TOKEN\ncat\n",
    )
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    spec = HocaAgentAdapterSpec(
        adapter_id="fake",
        provider="fake",
        command_template="hoca-fake-agent --worktree={worktree_path} --task={task}",
        command_allowlist=["hoca-fake-agent"],
        max_concurrency=1,
        capabilities=["coding"],
    )

    adapter = AgentAdapter(spec=spec)
    run_dir = tmp_path / "run"
    session = adapter.start(
        session_id=fake_session_id(),
        lane_id="lane-1",
        project_path=tmp_path,
        worktree_path=tmp_path,
        task="Hello",
        run_dir=run_dir,
        extra_env={"OPENAI_API_KEY": "allowed", "SECRET_TOKEN": "forbidden"},
    )
    adapter.send(session, "from-manager")
    assert adapter.stop(session) is True
    artifact = adapter.collect(session=session, run_dir=run_dir)

    assert "openai=allowed" in artifact.stdout
    assert "secret=forbidden" not in artifact.stdout


def test_missing_binary_is_detected() -> None:
    spec = HocaAgentAdapterSpec(
        adapter_id="missing",
        provider="missing",
        command_template="not-a-real-command --foo",
        command_allowlist=["not-a-real-command"],
        max_concurrency=1,
    )
    assert not required_commands_ok(spec)
    assert "not-a-real-command" in missing_required_commands(spec)

    with pytest.raises(AdapterUnavailableError):
        AgentAdapter(spec=spec)


def test_has_capability_and_artifact_dict() -> None:
    spec = custom_command_adapter_spec(
        adapter_id="safe",
        provider="safe",
        command_template="echo done",
        capabilities=["coding", "review"],
    )
    adapter = AgentAdapter(spec=spec)
    assert adapter.required_commands == ("echo", "echo")
    assert adapter.has_capability("coding")
    assert not adapter.has_capability("deploy")

    assert adapter.adapter_id == "safe"
    assert json.loads(json.dumps(spec.to_dict()))["provider"] == "safe"


def test_collect_output_structure() -> None:
    artifact = AdapterRunArtifact(
        return_code=2,
        stdout="ok",
        stderr="bad",
        command="x",
        session_id="s1",
        run_dir="/tmp/run",
        lane_id="lane-1",
        task_id="task-1",
        task="do one",
        project_id="project-1",
        project_path="/tmp/project",
    )
    d = artifact.to_dict()
    assert d["lane_id"] == "lane-1"
    assert d["task_id"] == "task-1"
    assert d["task"] == "do one"
    assert d["project_id"] == "project-1"
    assert d["project_path"] == "/tmp/project"
    assert d["metadata"] == {}


def test_default_openhands_adapter_has_expected_template() -> None:
    spec = default_openhands_adapter_spec()
    assert spec.provider == "openhands"
    assert spec.default_for_tasks == ["coding", "review"]
    assert "run-lane-agent.sh" in spec.command_template
    assert "--project-path" in spec.command_template
    assert "{task_id}" in spec.command_template
    assert "{task_id or ''}" not in spec.command_template


def test_send_with_stdin_pipe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    agent = fake_bin / "cat-sleeper"
    _write_executable(agent, "#!/usr/bin/env bash\ntrap 'exit 0' TERM\ncat\n")
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    spec = HocaAgentAdapterSpec(
        adapter_id="stdin",
        provider="stdin",
        command_template="cat-sleeper",
        command_allowlist=["cat-sleeper"],
        max_concurrency=1,
    )
    adapter = AgentAdapter(spec=spec)
    run_dir = tmp_path / "run"
    session = adapter.start(
        session_id="session-with-stdin",
        lane_id="lane-2",
        project_path=tmp_path,
        task="hello",
        run_dir=run_dir,
        extra_env={"OPENAI_API_KEY": "ok"},
    )
    assert session.process.stdin is not None
    adapter.send(session, "from-manager")
    adapter.stop(session)
    artifact = adapter.collect(session=session, run_dir=run_dir)
    assert "from-manager" in artifact.stderr + artifact.stdout


def test_custom_adapter_without_allowlist_is_rejected() -> None:
    spec = HocaAgentAdapterSpec(
        adapter_id="unsafe",
        provider="unsafe",
        command_template="cat-sleeper",
        max_concurrency=1,
    )

    with pytest.raises(AdapterUnavailableError, match="requires command_allowlist"):
        AgentAdapter(spec=spec)


def test_start_rejects_worktree_outside_project(tmp_path: Path) -> None:
    spec = HocaAgentAdapterSpec(
        adapter_id="cat-sleeper",
        provider="cat-sleeper",
        command_template="python3",
        command_allowlist=["python3"],
        max_concurrency=1,
    )

    adapter = AgentAdapter(spec=spec)
    project_path = tmp_path / "project"
    project_path.mkdir()
    run_dir = tmp_path / "run"
    external_worktree = tmp_path / "external"
    external_worktree.mkdir()

    with pytest.raises(
        AdapterCommandError,
        match="worktree_path must be inside project_path",
    ):
        adapter.start(
            session_id=fake_session_id(),
            lane_id="lane-1",
            project_path=project_path,
            task="work outside",
            run_dir=run_dir,
            worktree_path=external_worktree,
        )


def test_command_allowlist_blocks_unlisted_binary() -> None:
    spec = HocaAgentAdapterSpec(
        adapter_id="unsafe",
        provider="unsafe",
        command_template="bash -lc 'echo hi'",
        command_allowlist=["python"],
        max_concurrency=1,
    )

    with pytest.raises(AdapterUnavailableError, match="not be allow-listed"):
        AgentAdapter(spec=spec)


def test_adapter_doctor_lines_reports_missing_required_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = HocaAgentAdapterSpec(
        adapter_id="openhands-hermes",
        provider="openhands",
        command_template="not-a-real-command --task {task}",
        max_concurrency=1,
    )
    monkeypatch.setattr("hoca.agent_adapters.default_openhands_adapter_spec", lambda: spec)

    lines = adapter_doctor_lines()

    assert lines[0][0] == "fail"
    assert "not-a-real-command" in lines[0][1]
