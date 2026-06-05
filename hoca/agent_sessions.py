from __future__ import annotations

from pathlib import Path

from hoca.fleet_contracts import HocaAgentSession
from hoca.run_state import read_optional_json, write_json_atomic


def session_registry_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return root / "agent-sessions"


def session_file(root: Path, session_id: str) -> Path:
    return session_registry_dir(root) / f"{session_id}.json"


def read_session(root: Path, session_id: str) -> HocaAgentSession | None:
    payload = read_optional_json(session_file(root, session_id))
    if not isinstance(payload, dict):
        return None
    try:
        return HocaAgentSession.from_dict(payload)
    except ValueError:
        return None


def write_session(root: Path, session: HocaAgentSession) -> Path:
    path = session_file(root, session.session_id)
    payload = session.to_dict()
    write_json_atomic(path, payload)
    return path


def mark_session_status(
    root: Path,
    session_id: str,
    status: str,
    *,
    ended_at: str | None = None,
    process_id: int | None = None,
) -> None:
    existing = read_session(root, session_id)
    if existing is None:
        raise ValueError("Session not found")

    replacement = dict(existing.to_dict())
    replacement["status"] = status
    replacement["ended_at"] = ended_at
    if process_id is not None:
        replacement["process_id"] = process_id

    write_session(root, HocaAgentSession.from_dict(replacement))


def build_session(
    *, session_id: str, lane_id: str, adapter_id: str, started_at: str
) -> HocaAgentSession:
    return HocaAgentSession(
        session_id=session_id,
        lane_id=lane_id,
        adapter_id=adapter_id,
        status="running",
        started_at=started_at,
    )
