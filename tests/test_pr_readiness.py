from __future__ import annotations

import json
import os
from pathlib import Path

from hoca.pr_readiness import PrReadinessInputs, evaluate_pr_merge_readiness


def _fake_gh_binary(
    folder: Path,
    *,
    checks_payload: object,
    view_payload: object,
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "gh"
    script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
if [ "$1" = "pr" ] && [ "$2" = "checks" ]; then
  cat <<'JSON'
{json.dumps(checks_payload)}
JSON
  exit 0
fi

if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  cat <<'JSON'
{json.dumps(view_payload)}
JSON
  exit 0
fi

echo "unexpected gh args: $*" >&2
exit 1
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _make_readiness_status(run_dir: Path, pr_ref: str | None = None) -> None:
    if pr_ref is None:
        return
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "status.json").write_text(f'{{"pr_url": "{pr_ref}"}}\\n', encoding="utf-8")


def test_pr_readiness_blocks_without_pr_reference(tmp_path: Path) -> None:
    result = evaluate_pr_merge_readiness(
        PrReadinessInputs(
            lane_id="lane-no-pr",
            run_dir=tmp_path / "run",
            require_ui_screenshot=False,
        )
    )
    assert result.readiness == "not_ready"
    assert "No PR URL/number" in (result.reason or "")
    assert result.ci_status == "missing"
    assert result.pr_url is None


def test_pr_readiness_passes_when_checks_and_view_are_ready(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _make_readiness_status(run_dir, pr_ref="https://github.com/org/repo/pull/1")
    _fake_gh_binary(
        tmp_path / "bin",
        checks_payload=[{"name": "unit", "status": "completed", "conclusion": "success"}],
        view_payload={
            "state": "OPEN",
            "isDraft": False,
            "mergeStateStatus": "CLEAN",
            "mergeable": True,
            "reviewDecision": "APPROVED",
        },
    )

    old_path = os.environ["PATH"]
    os.environ["PATH"] = f"{tmp_path / 'bin'}:{old_path}"
    try:
        result = evaluate_pr_merge_readiness(
            PrReadinessInputs(
                lane_id="lane-ready",
                run_dir=run_dir,
            )
        )
    finally:
        os.environ["PATH"] = old_path

    assert result.readiness == "ready"
    assert result.ci_status == "passed"
    assert result.pr_url == "https://github.com/org/repo/pull/1"
    assert result.human_review_required is False


def test_pr_readiness_stays_not_ready_when_checks_pending(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _make_readiness_status(run_dir, pr_ref="https://github.com/org/repo/pull/1")
    _fake_gh_binary(
        tmp_path / "bin",
        checks_payload=[{"name": "unit", "status": "in_progress", "conclusion": "neutral"}],
        view_payload={
            "state": "OPEN",
            "isDraft": False,
            "mergeStateStatus": "CLEAN",
            "mergeable": True,
        },
    )
    old_path = os.environ["PATH"]
    os.environ["PATH"] = f"{tmp_path / 'bin'}:{old_path}"
    try:
        result = evaluate_pr_merge_readiness(
            PrReadinessInputs(
                lane_id="lane-pending",
                run_dir=run_dir,
            )
        )
    finally:
        os.environ["PATH"] = old_path

    assert result.readiness == "not_ready"
    assert "pending" in (result.reason or "").lower()
    assert result.ci_status == "pending"


def test_pr_readiness_blocks_when_checks_fail(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _make_readiness_status(run_dir, pr_ref="https://github.com/org/repo/pull/1")
    _fake_gh_binary(
        tmp_path / "bin",
        checks_payload=[{"name": "unit", "status": "completed", "conclusion": "failure"}],
        view_payload={
            "state": "OPEN",
            "isDraft": False,
            "mergeStateStatus": "CLEAN",
            "mergeable": True,
        },
    )
    old_path = os.environ["PATH"]
    os.environ["PATH"] = f"{tmp_path / 'bin'}:{old_path}"
    try:
        result = evaluate_pr_merge_readiness(
            PrReadinessInputs(
                lane_id="lane-failed",
                run_dir=run_dir,
            )
        )
    finally:
        os.environ["PATH"] = old_path

    assert result.readiness == "blocked"
    assert "failed" in (result.reason or "").lower()
    assert result.ci_status == "failed"


def test_pr_readiness_draft_ready_when_review_changes_requested(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "changed-files.txt").write_text("app.py\n", encoding="utf-8")
    _make_readiness_status(run_dir, pr_ref="https://github.com/org/repo/pull/1")
    _fake_gh_binary(
        tmp_path / "bin",
        checks_payload=[{"name": "unit", "status": "completed", "conclusion": "success"}],
        view_payload={
            "state": "OPEN",
            "isDraft": False,
            "mergeStateStatus": "CLEAN",
            "mergeable": True,
            "reviewDecision": "CHANGES_REQUESTED",
        },
    )
    old_path = os.environ["PATH"]
    os.environ["PATH"] = f"{tmp_path / 'bin'}:{old_path}"
    try:
        result = evaluate_pr_merge_readiness(
            PrReadinessInputs(
                lane_id="lane-draft",
                run_dir=run_dir,
            )
        )
    finally:
        os.environ["PATH"] = old_path

    assert result.readiness == "draft_ready"
    assert "draft_ready" in result.checks
    assert "review" in (result.reason or "").lower()
    assert result.ci_status == "passed"


def test_pr_readiness_blocks_ui_changes_without_screenshot_when_required(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "changed-files.txt").write_text("web/app.tsx\n", encoding="utf-8")
    _make_readiness_status(run_dir, pr_ref="https://github.com/org/repo/pull/1")
    _fake_gh_binary(
        tmp_path / "bin",
        checks_payload=[{"name": "unit", "status": "completed", "conclusion": "success"}],
        view_payload={
            "state": "OPEN",
            "isDraft": False,
            "mergeStateStatus": "CLEAN",
            "mergeable": True,
            "reviewDecision": "APPROVED",
        },
    )
    old_path = os.environ["PATH"]
    os.environ["PATH"] = f"{tmp_path / 'bin'}:{old_path}"
    try:
        result = evaluate_pr_merge_readiness(
            PrReadinessInputs(
                lane_id="lane-ui",
                run_dir=run_dir,
                require_ui_screenshot=True,
            )
        )
    finally:
        os.environ["PATH"] = old_path

    assert result.readiness == "blocked"
    assert "screenshot" in (result.reason or "").lower()
    assert result.ci_status == "passed"


def test_pr_readiness_with_screenshot_policy_passes_when_screenshot_exists(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "changed-files.txt").write_text("web/app.tsx\n", encoding="utf-8")
    (run_dir / "ui-screenshot.png").write_text("binary", encoding="utf-8")
    _make_readiness_status(run_dir, pr_ref="https://github.com/org/repo/pull/1")
    _fake_gh_binary(
        tmp_path / "bin",
        checks_payload=[{"name": "unit", "status": "completed", "conclusion": "success"}],
        view_payload={
            "state": "OPEN",
            "isDraft": False,
            "mergeStateStatus": "CLEAN",
            "mergeable": True,
            "reviewDecision": "APPROVED",
        },
    )
    old_path = os.environ["PATH"]
    os.environ["PATH"] = f"{tmp_path / 'bin'}:{old_path}"
    try:
        result = evaluate_pr_merge_readiness(
            PrReadinessInputs(
                lane_id="lane-ui-ok",
                run_dir=run_dir,
                require_ui_screenshot=True,
            )
        )
    finally:
        os.environ["PATH"] = old_path

    assert result.readiness == "ready"
