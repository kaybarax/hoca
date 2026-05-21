from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

from hoca.run_layout import ensure_run_layout
from hoca.run_state import optional_report_path, write_json_atomic

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "create-pr.sh"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def init_pr_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    (path / "src").mkdir()
    (path / "src" / "widget.ts").write_text("export const widget = true;\n", encoding="utf-8")
    (path / "templates").mkdir()
    (path / "templates" / "PR_TEMPLATE.md").write_text(
        "## Summary\n\n## Changes\n\n## Validation\n\n## Code Review\n\n"
        "## Hoca Review Notes\n\n## Task Spec\n\n## Run Context\n\n## Risk\n\n## Linked Issue\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "--", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=path, check=True)
    remote = path.parent / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=path, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", "-b", "feat/widget"], cwd=path, check=True, stdout=subprocess.PIPE)
    (path / "src" / "widget.ts").write_text("export const widget = 'updated';\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "src/widget.ts"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "update widget"], cwd=path, check=True)


def write_fixture_run_artifacts(run_dir: Path, repo: Path) -> None:
    ensure_run_layout(run_dir)
    write_json_atomic(
        run_dir / "task-spec.json",
        {
            "schema_version": 1,
            "run_id": "run-fixture",
            "repo_root": str(repo),
            "base_branch": "main",
            "task_branch": "feat/widget",
            "issue_id": "7",
            "raw_request": "Add widget",
            "goal": "Add a reusable widget",
            "non_goals": ["Do not redesign the app"],
            "expected_areas": ["src/widget.ts"],
            "acceptance_criteria": ["Widget module updated", "Tests pass"],
            "test_commands": ["pnpm test"],
            "risk_level": "low",
            "requires_human_approval": False,
            "max_total_rounds": 3,
            "models": {"manager": "m", "worker": "w", "reviewer": "r", "fallback": "f"},
            "sandbox": {"enabled": True, "network_mode": "offline"},
        },
    )
    write_json_atomic(run_dir / "sandbox-policy.json", {"schema_version": 1, "enabled": True, "network_mode": "offline"})
    (run_dir / "tests-summary.md").write_text("# Validation\n\n- pnpm test: passed\n", encoding="utf-8")
    (run_dir / "changed-files.txt").write_text("src/widget.ts\n.env\n", encoding="utf-8")
    (run_dir / "risk-notes.txt").write_text("Low risk fixture change.\n", encoding="utf-8")
    write_json_atomic(
        optional_report_path(run_dir, "review_report", round_number=1),
        {
            "schema_version": 1,
            "run_id": "run-fixture",
            "round": 1,
            "role": "reviewer",
            "verdict": "LGTM",
            "findings": [],
            "pr_notes": {"summary": ["Fixture review passed"], "known_followups": []},
        },
    )
    write_json_atomic(
        optional_report_path(run_dir, "manager_decision", round_number=1),
        {
            "schema_version": 1,
            "run_id": "run-fixture",
            "round": 1,
            "decision": "proceed_to_pr",
            "accepted_findings": [],
            "rejected_findings": [],
            "downgraded_to_pr_notes": [],
            "reasoning": ["Fixture is ready."],
            "next_worker_brief": None,
            "human_attention_required": False,
        },
    )


def test_create_pr_generates_body_from_fixture_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_pr_repo(repo)
    run_dir = repo / ".hoca-runtime" / "runs" / "run-fixture"
    write_fixture_run_artifacts(run_dir, repo)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    captured_body = tmp_path / "captured-pr-body.md"
    write_executable(
        fake_bin / "gh",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "auth" && "${2:-}" == "status" ]]; then exit 0; fi\n'
        'if [[ "${1:-}" == "pr" && "${2:-}" == "create" ]]; then\n'
        '  body_file=""\n'
        '  while [[ $# -gt 0 ]]; do\n'
        '    if [[ "$1" == "--body-file" ]]; then body_file="$2"; shift 2; else shift; fi\n'
        "  done\n"
        '  cat "$body_file" > "${GH_CAPTURE_BODY:?}"\n'
        "  echo 'https://github.com/example/repo/pull/7'\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "${1:-}" == "pr" && "${2:-}" == "view" ]]; then\n'
        "  echo 'https://github.com/example/repo/pull/7'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["GH_CAPTURE_BODY"] = str(captured_body)
    result = subprocess.run(
        [str(SCRIPT), str(repo), "Add widget", str(run_dir), "--issue-id", "7"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    body = captured_body.read_text(encoding="utf-8")
    assert "Add a reusable widget" in body
    assert "src/widget.ts" in body
    assert ".env" not in body
    assert "pnpm test: passed" in body
    assert "Fixture review passed" in body
    assert "Sandbox mode**: docker" in body
    assert "Refs: #7" in body
    assert (run_dir / "pr-url.txt").read_text(encoding="utf-8").strip() == (
        "https://github.com/example/repo/pull/7"
    )
