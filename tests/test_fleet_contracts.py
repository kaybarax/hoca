from __future__ import annotations

import pytest

from hoca.fleet_contracts import (
    HocaAgentAdapterSpec,
    HocaAgentSession,
    HocaFleetTask,
    HocaLane,
    HocaLaneLease,
    HocaMergeReadiness,
    HocaNotification,
    HocaProject,
    HocaProjectMemoryEntry,
    HocaResourceBudget,
    HocaReviewSignal,
    HocaSchedulerDecision,
    HocaTaskDependency,
    VALID_FLEET_DECISION_TYPES,
    VALID_FLEET_LANE_STATUSES,
    VALID_FLEET_NOTIFICATION_STATUSES,
    VALID_FLEET_READINESS_STATES,
    VALID_FLEET_REVIEW_VERDICTS,
    VALID_FLEET_TASK_STATUSES,
)


def test_project_roundtrip() -> None:
    payload = {
        "schema_version": 1,
        "project_id": "alpha",
        "repo_path": "/abs/path/alpha",
        "display_name": "Alpha",
        "default_branch": "main",
        "max_parallel_tasks": 2,
        "runtime_archive_root": "/tmp/hoca-runtime",
        "agent_policy": {"default": "hermes"},
        "created_at": "2026-06-01T10:00:00Z",
        "updated_at": "2026-06-01T10:00:00Z",
        "is_active": True,
    }
    item = HocaProject.from_dict(payload)
    assert HocaProject.from_json(item.to_json()) == item


def test_task_roundtrip_and_invalid_status() -> None:
    payload = {
        "schema_version": 1,
        "task_id": "task-a",
        "project_id": "alpha",
        "status": "queued",
        "readiness": "not_ready",
        "priority": 1,
    }
    item = HocaFleetTask.from_dict(payload)
    assert HocaFleetTask.from_json(item.to_json()) == item

    payload["status"] = "invalid"
    with pytest.raises(ValueError, match="Invalid task status"):
        HocaFleetTask.from_dict(payload)


def test_task_dependencies_keep_known_fields() -> None:
    payload = {
        "schema_version": 1,
        "task_id": "task-a",
        "depends_on_task_id": "task-b",
        "required": False,
        "reason": "chain",
        "created_at": "2026-06-01T10:00:00Z",
        "future_only_field": "future",
    }
    item = HocaTaskDependency.from_dict(payload)
    assert item.task_id == "task-a"
    assert item.depends_on_task_id == "task-b"
    assert item.required is False


def test_lane_status_validates_known_statuses() -> None:
    payload = {
        "schema_version": 1,
        "lane_id": "lane-1",
        "task_id": "task-a",
        "project_id": "alpha",
        "status": "running",
        "attempt_number": 0,
        "branch": "hoca/feat",
    }
    lane = HocaLane.from_dict(payload)
    assert lane.status in VALID_FLEET_LANE_STATUSES

    for invalid in ("bad", "unknown"):
        payload["status"] = invalid
        with pytest.raises(ValueError, match="Invalid lane status"):
            HocaLane.from_dict(payload)


def test_lane_rejects_secret_worktree_path() -> None:
    payload = {
        "schema_version": 1,
        "lane_id": "lane-1",
        "task_id": "task-a",
        "project_id": "alpha",
        "status": "running",
        "branch": "hoca/feat",
        "attempt_number": 0,
        "worktree_path": "/tmp/.env",
    }
    with pytest.raises(ValueError, match="secret-like"):
        HocaLane.from_dict(payload)


def test_lane_lease_roundtrip() -> None:
    payload = {
        "schema_version": 1,
        "lease_id": "lease-1",
        "lane_id": "lane-1",
        "project_id": "alpha",
        "task_id": "task-a",
        "branch": "hoca/feat",
        "base_ref": "main",
        "worktree_path": "/tmp/worktrees/alpha",
        "acquired_at": "2026-06-01T10:00:01Z",
    }
    lease = HocaLaneLease.from_dict(payload)
    assert HocaLaneLease.from_json(lease.to_json()) == lease


def test_agent_adapter_raises_on_secret_runtime_path() -> None:
    payload = {
        "schema_version": 1,
        "adapter_id": "hermes",
        "provider": "hermes",
        "command_template": "hermes run {task}",
        "runtime_home": "/tmp/.ssh",
        "max_concurrency": 1,
    }
    with pytest.raises(ValueError, match="secret-like"):
        HocaAgentAdapterSpec.from_dict(payload)


def test_agent_session_roundtrip() -> None:
    payload = {
        "schema_version": 1,
        "session_id": "s1",
        "lane_id": "lane-1",
        "adapter_id": "hermes",
        "status": "running",
        "started_at": "2026-06-01T10:00:00Z",
        "ended_at": None,
        "log_path": "/tmp/sessions/s1.log",
        "process_id": 1200,
    }
    session = HocaAgentSession.from_dict(payload)
    assert HocaAgentSession.from_json(session.to_json()) == session


def test_resource_budget_validates_limits() -> None:
    payload = {
        "schema_version": 1,
        "budget_id": "default",
        "max_parallel_projects": 2,
        "max_parallel_tasks": 2,
        "max_parallel_lanes": 4,
        "max_agents": 4,
        "memory_limit_mb": 2048,
        "cpu_limit_percent": 400,
        "created_at": "2026-06-01T10:00:00Z",
        "updated_at": "2026-06-01T10:00:00Z",
    }
    budget = HocaResourceBudget.from_dict(payload)
    assert budget.max_parallel_projects == 2

    payload["max_parallel_tasks"] = 0
    with pytest.raises(ValueError, match="must be >= 1"):
        HocaResourceBudget.from_dict(payload)


def test_scheduler_decision_roundtrip_and_type_validation() -> None:
    payload = {
        "schema_version": 1,
        "decision_id": "dec-1",
        "project_id": "alpha",
        "decision_type": "launch",
        "reason": "capacity available",
        "confidence": 0.9,
        "created_at": "2026-06-01T10:00:00Z",
    }
    decision = HocaSchedulerDecision.from_dict(payload)
    assert HocaSchedulerDecision.from_json(decision.to_json()) == decision
    assert decision.decision_type in VALID_FLEET_DECISION_TYPES

    payload["decision_type"] = "invalid"
    with pytest.raises(ValueError, match="Invalid decision type"):
        HocaSchedulerDecision.from_dict(payload)


def test_merge_readiness_validates_enum() -> None:
    payload = {
        "schema_version": 1,
        "lane_id": "lane-1",
        "readiness": "ready",
        "checks": ["tests"],
    }
    readiness = HocaMergeReadiness.from_dict(payload)
    assert readiness.readiness in VALID_FLEET_READINESS_STATES

    payload["readiness"] = "invalid"
    with pytest.raises(ValueError, match="Invalid readiness state"):
        HocaMergeReadiness.from_dict(payload)


def test_review_signal_validates_verdict() -> None:
    payload = {
        "schema_version": 1,
        "signal_id": "sig-1",
        "lane_id": "lane-1",
        "source": "ci",
        "verdict": "pass",
        "review_round": 1,
        "created_at": "2026-06-01T10:00:00Z",
    }
    signal = HocaReviewSignal.from_dict(payload)
    assert signal.verdict in VALID_FLEET_REVIEW_VERDICTS

    payload["verdict"] = "unknown"
    with pytest.raises(ValueError, match="Invalid review verdict"):
        HocaReviewSignal.from_dict(payload)


def test_notification_status_validation() -> None:
    payload = {
        "schema_version": 1,
        "notification_id": "n1",
        "channel": "webhook",
        "recipient": "ci",
        "message": "lane ready",
        "status": "queued",
        "created_at": "2026-06-01T10:00:00Z",
    }
    notification = HocaNotification.from_dict(payload)
    assert notification.status in VALID_FLEET_NOTIFICATION_STATUSES

    payload["status"] = "bad"
    with pytest.raises(ValueError, match="Invalid notification status"):
        HocaNotification.from_dict(payload)


def test_memory_entry_ignores_non_dict_value() -> None:
    payload = {
        "schema_version": 1,
        "entry_id": "m1",
        "project_id": "alpha",
        "key": "knowledge",
        "value": "not-a-dict",
        "scope": ["lane-1"],
        "created_at": "2026-06-01T10:00:00Z",
    }
    # Current contract intentionally keeps value as null when data is not a mapping.
    entry = HocaProjectMemoryEntry.from_dict(payload)
    assert entry.value is None
