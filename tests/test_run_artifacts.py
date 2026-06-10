from __future__ import annotations

import json
from pathlib import Path

from hoca.run_artifacts import main
from hoca.run_state import write_initial_status


def test_write_status_command_updates_status_and_reason(tmp_path: Path) -> None:
    write_initial_status(
        tmp_path,
        status="started",
        max_total_rounds=3,
        run_id="run-1",
        task="Update README",
        repo_root="/workspace/repo",
        started_at="2026-06-10T00:00:00Z",
    )

    exit_code = main(["write-status", str(tmp_path), "--status", "failed", "--reason", "tests"])

    assert exit_code == 0
    payload = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["reason"] == "tests"
