from __future__ import annotations

import os
from pathlib import Path

import pytest

from hoca.contracts import HocaReviewReport
from hoca.run_layout import ensure_run_layout, review_report_path
from hoca.reviewer_hermes import (
    build_reviewer_hermes_prompt,
    prepare_reviewer_inputs,
    run_reviewer_hermes,
    verify_profile_prerequisites,
)
from tests.test_worker_hermes import clear_model_env, init_repo, make_fake_ollama, sample_task_spec


def make_fake_review_openhands(fake_bin: Path) -> None:
    openhands = fake_bin / "openhands"
    openhands.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "--help" ]]; then\n'
        '  echo "openhands --headless --task --override-with-envs --json"\n'
        "  exit 0\n"
        "fi\n"
        "echo 'Review complete.'\n"
        "echo 'LGTM'\n",
        encoding="utf-8",
    )
    openhands.chmod(openhands.stat().st_mode | 0o100)


def test_verify_profile_prerequisites_requires_hermes_and_reviewer_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    monkeypatch.setattr("hoca.reviewer_hermes.hermes_installed", lambda: False)
    with pytest.raises(RuntimeError, match="hermes command not found"):
        verify_profile_prerequisites(hermes_home=hermes_home)

    monkeypatch.setattr("hoca.reviewer_hermes.hermes_installed", lambda: True)
    with pytest.raises(RuntimeError, match="hoca-reviewer"):
        verify_profile_prerequisites(hermes_home=hermes_home)


def test_build_reviewer_hermes_prompt_includes_review_artifact_paths(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    project.mkdir()
    ensure_run_layout(run_dir)
    spec = sample_task_spec(goal="Fix auth", non_goals=["Never log API_KEY=secret-value"])
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(spec.to_json(), encoding="utf-8")
    inputs = prepare_reviewer_inputs(project_path=project, run_dir=run_dir, round_number=1)

    prompt = build_reviewer_hermes_prompt(
        spec=spec,
        project_path=project,
        run_dir=run_dir,
        round_number=1,
        task_spec_path=task_spec_path,
        inputs=inputs,
    )

    assert "hoca-reviewer-qa" in prompt
    assert "changed_files_path:" in prompt
    assert "diff_path:" in prompt
    assert "test_summary_path:" in prompt
    assert "worker_report_path:" in prompt
    assert "required_review_report_path:" in prompt
    assert "Manager-owned Git lifecycle only" in prompt
    assert "git add, git commit, git push" in prompt
    assert "secret-value" not in prompt
    assert "[redacted: possible secret]" in prompt


def test_run_reviewer_hermes_legacy_mode_writes_review_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    make_fake_ollama(fake_bin)
    make_fake_review_openhands(fake_bin)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HOCA_USE_SANDBOX", "false")

    project = tmp_path / "project"
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    spec = sample_task_spec(repo_root=str(project))
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(spec.to_json(), encoding="utf-8")

    result = run_reviewer_hermes(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
        use_hermes_profiles=False,
    )

    assert result.mode == "legacy"
    assert result.exit_code == 0
    assert result.review_report_path == review_report_path(run_dir, 1)
    report = HocaReviewReport.from_json(result.review_report_path.read_text(encoding="utf-8"))
    assert report.verdict == "LGTM"
    assert (run_dir / "review" / "changed-files.txt").read_text(encoding="utf-8") == "README.md\n"
    assert (run_dir / "review" / "git-diff.patch").is_file()


def test_run_reviewer_hermes_profile_mode_invokes_hermes_and_uses_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    make_fake_ollama(fake_bin)
    hermes_home = tmp_path / "hermes-home"
    (hermes_home / "profiles" / "hoca-reviewer").mkdir(parents=True)
    hermes = fake_bin / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "-p" && "${2:-}" == "hoca-reviewer" ]]; then shift 2; fi\n'
        '[[ "${1:-}" == "chat" ]] || { echo "missing chat subcommand" >&2; exit 2; }\n'
        "shift\n"
        'while [[ $# -gt 0 ]]; do case "$1" in --query|-q) PROMPT="${2:-}"; shift 2;; --max-turns) [[ "${2:-}" == "20" ]] || { echo "wrong max turns" >&2; exit 2; }; shift 2;; --model) [[ "${2:-}" == ollama/* ]] || { echo "missing model override" >&2; exit 2; }; shift 2;; *) shift;; esac; done\n'
        'RUN_DIR="${HERMES_TEST_RUN_DIR:?}"\n'
        'ROUND="${HERMES_TEST_ROUND:?}"\n'
        'mkdir -p "$RUN_DIR/reviews" "$RUN_DIR/logs"\n'
        'printf "%s\\n" "$PROMPT" > "$RUN_DIR/logs/reviewer-hermes-invoked.txt"\n'
        'cat > "$RUN_DIR/reviews/review-report-${ROUND}.json" <<EOF\n'
        '{"schema_version":1,"run_id":"run-test","round":'"${ROUND}"',"role":"reviewer",'
        '"verdict":"LGTM","findings":[],"pr_notes":{"summary":["Looks good."],"known_followups":[]}}\n'
        "EOF\n"
        "exit 0\n",
        encoding="utf-8",
    )
    hermes.chmod(hermes.stat().st_mode | 0o100)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HERMES_TEST_RUN_DIR", str(tmp_path / "project/.hoca-runtime/runs/run-test"))
    monkeypatch.setenv("HERMES_TEST_ROUND", "2")
    clear_model_env(monkeypatch)

    project = tmp_path / "project"
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(sample_task_spec(repo_root=str(project)).to_json(), encoding="utf-8")

    result = run_reviewer_hermes(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=2,
        use_hermes_profiles=True,
        hermes_home=hermes_home,
    )

    assert result.mode == "profile"
    assert result.exit_code == 0
    assert result.review_report_path == review_report_path(run_dir, 2)
    invoked = (run_dir / "logs" / "reviewer-hermes-invoked.txt").read_text(encoding="utf-8")
    assert "required_review_report_path:" in invoked
    assert (run_dir / "logs" / "reviewer-hermes-stdout.txt").is_file()
    assert HocaReviewReport.from_json(result.review_report_path.read_text(encoding="utf-8")).verdict == "LGTM"


def test_run_reviewer_hermes_malformed_profile_report_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    make_fake_ollama(fake_bin)
    hermes_home = tmp_path / "hermes-home"
    (hermes_home / "profiles" / "hoca-reviewer").mkdir(parents=True)
    hermes = fake_bin / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'RUN_DIR="${HERMES_TEST_RUN_DIR:?}"\n'
        'mkdir -p "$RUN_DIR/reviews"\n'
        'printf "{not json}\\n" > "$RUN_DIR/reviews/review-report-1.json"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    hermes.chmod(hermes.stat().st_mode | 0o100)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    project = tmp_path / "project"
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    monkeypatch.setenv("HERMES_TEST_RUN_DIR", str(run_dir))
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(sample_task_spec(repo_root=str(project)).to_json(), encoding="utf-8")

    result = run_reviewer_hermes(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
        use_hermes_profiles=True,
        hermes_home=hermes_home,
    )

    assert result.exit_code == 4
    report = HocaReviewReport.from_json(result.review_report_path.read_text(encoding="utf-8"))
    assert report.verdict == "blocked"
    assert (run_dir / "logs" / "malformed-review-report-1.json").is_file()


def test_run_reviewer_hermes_missing_profile_report_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    make_fake_ollama(fake_bin)
    hermes_home = tmp_path / "hermes-home"
    (hermes_home / "profiles" / "hoca-reviewer").mkdir(parents=True)
    hermes = fake_bin / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'LGTM'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    hermes.chmod(hermes.stat().st_mode | 0o100)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    project = tmp_path / "project"
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(sample_task_spec(repo_root=str(project)).to_json(), encoding="utf-8")

    result = run_reviewer_hermes(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
        use_hermes_profiles=True,
        hermes_home=hermes_home,
    )

    assert result.exit_code == 4
    report = HocaReviewReport.from_json(result.review_report_path.read_text(encoding="utf-8"))
    assert report.verdict == "blocked"
    assert "did not write" in report.findings[0].required_fix
