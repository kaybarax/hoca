from __future__ import annotations

from pathlib import Path

import pytest

from hoca.contracts import HocaReviewReport
from hoca.review_gate import (
    LEGACY_REVIEW_WARNING,
    ReviewGateError,
    ReviewGateResult,
    code_review_pr_fragment,
    evaluate_review_gate,
    main,
    materialize_structured_report_from_text,
    task_report_review_status,
    try_extract_structured_report,
    try_resolve_review_gate,
)
from hoca.review_report_parser import ReviewReportParseError, legacy_text_to_report


def test_legacy_lgtm_converts_to_approved_structured_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    review_text = run_dir / "openhands-review.txt"
    review_text.write_text("Looks correct.\nLGTM\n", encoding="utf-8")

    result = evaluate_review_gate(run_dir, review_text_path=review_text, run_id="run-1")

    assert result.approved is True
    assert result.source == "legacy"
    assert result.report.verdict == "LGTM"
    assert result.report.findings == []
    assert result.report_path == run_dir / "reviews" / "review-report-1.json"
    assert HocaReviewReport.from_json(result.report_path.read_text(encoding="utf-8")).verdict == "LGTM"


def test_legacy_lgtm_compatibility_accepts_token_in_text() -> None:
    report = legacy_text_to_report(
        "LGTM all good",
        run_id="run-1",
        round_number=1,
    )

    assert report.verdict == "LGTM"


def test_legacy_empty_output_is_rejected() -> None:
    with pytest.raises(ReviewReportParseError, match="empty"):
        legacy_text_to_report("", run_id="run-1", round_number=1)


def test_legacy_missing_lgtm_converts_to_fix_required(tmp_path: Path) -> None:
    report = legacy_text_to_report(
        "Please add tests for the changed behavior.",
        run_id="run-1",
        round_number=1,
    )

    assert report.verdict == "fix_required"
    assert report.findings[0].id == "legacy-review-1"
    assert report.findings[0].required_fix == "Please add tests for the changed behavior."


def test_structured_report_is_preferred_over_legacy_text(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    reports = run_dir / "reviews"
    reports.mkdir(parents=True)
    review_text = run_dir / "openhands-review.txt"
    review_text.write_text("LGTM\n", encoding="utf-8")
    structured = HocaReviewReport(
        run_id="run-1",
        round=1,
        role="reviewer",
        verdict="blocked",
        findings=[],
        pr_notes={"summary": ["Reviewer was blocked."], "known_followups": []},
    )
    (reports / "review-report-1.json").write_text(structured.to_json(), encoding="utf-8")

    result = evaluate_review_gate(run_dir, review_text_path=review_text, run_id="run-1")

    assert result.source == "structured"
    assert result.approved is False
    assert result.report.verdict == "blocked"


def test_review_gate_downgrades_lgtm_when_changed_python_has_syntax_error(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / "calc.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "def subtract(a, b):\n"
        "    return a -\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "run-1"
    reports = run_dir / "reviews"
    review_dir = run_dir / "review"
    reports.mkdir(parents=True)
    review_dir.mkdir(parents=True)
    (review_dir / "changed-files.txt").write_text("calc.py\n", encoding="utf-8")
    review_text = run_dir / "openhands-review.txt"
    review_text.write_text("LGTM\n", encoding="utf-8")
    structured = HocaReviewReport(
        run_id="run-1",
        round=1,
        role="reviewer",
        verdict="LGTM",
        findings=[],
        pr_notes={"summary": ["Reviewer approved."], "known_followups": []},
    )
    (reports / "review-report-1.json").write_text(structured.to_json(), encoding="utf-8")

    result = evaluate_review_gate(
        run_dir,
        review_text_path=review_text,
        run_id="run-1",
        project_path=project_path,
    )

    assert result.approved is False
    assert result.report.verdict == "fix_required"
    assert result.report.findings[0].id == "sanity-python-syntax-1"
    assert result.report.findings[0].file == "calc.py"
    assert "syntax error" in result.report.findings[0].summary.lower()
    persisted = HocaReviewReport.from_json(
        (reports / "review-report-1.json").read_text(encoding="utf-8")
    )
    assert persisted.verdict == "fix_required"


def test_malformed_structured_report_fails_gracefully(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    reports = run_dir / "reviews"
    reports.mkdir(parents=True)
    review_text = run_dir / "openhands-review.txt"
    review_text.write_text("LGTM\n", encoding="utf-8")
    (reports / "review-report-1.json").write_text('{"verdict": "LGTM"}', encoding="utf-8")

    with pytest.raises(ReviewGateError, match="Malformed HocaReviewReport"):
        evaluate_review_gate(run_dir, review_text_path=review_text, run_id="run-1")


def test_empty_review_output_fails_gracefully(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    review_text = run_dir / "openhands-review.txt"
    review_text.write_text("\n", encoding="utf-8")

    with pytest.raises(ReviewGateError, match="empty"):
        evaluate_review_gate(run_dir, review_text_path=review_text, run_id="run-1")


def test_cli_returns_distinct_exit_for_blocked_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    reports = run_dir / "reviews"
    reports.mkdir(parents=True)
    review_text = run_dir / "openhands-review.txt"
    review_text.write_text("LGTM\n", encoding="utf-8")
    blocked = HocaReviewReport(
        run_id="run-1",
        round=1,
        role="reviewer",
        verdict="blocked",
        findings=[],
        pr_notes={"summary": ["Reviewer could not complete review."], "known_followups": []},
    )
    (reports / "review-report-1.json").write_text(blocked.to_json(), encoding="utf-8")

    assert main([str(run_dir), "--review-text", str(review_text), "--run-id", "run-1"]) == 4


def test_try_resolve_review_gate_returns_none_without_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()

    assert try_resolve_review_gate(run_dir, run_id="run-1") is None


def test_cli_print_status_for_legacy_lgtm(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    review_text = run_dir / "openhands-review.txt"
    review_text.write_text("LGTM\n", encoding="utf-8")

    assert (
        main(
            [
                str(run_dir),
                "--review-text",
                str(review_text),
                "--run-id",
                "run-1",
                "--print",
                "status",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out.strip() == "LGTM"
    assert LEGACY_REVIEW_WARNING in captured.err


def test_cli_does_not_warn_for_structured_review(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run-1"
    reports = run_dir / "reviews"
    reports.mkdir(parents=True)
    review_text = run_dir / "openhands-review.txt"
    review_text.write_text("LGTM\n", encoding="utf-8")
    structured = HocaReviewReport(
        run_id="run-1",
        round=1,
        role="reviewer",
        verdict="LGTM",
        findings=[],
        pr_notes={"summary": ["Structured report approved."], "known_followups": []},
    )
    (reports / "review-report-1.json").write_text(structured.to_json(), encoding="utf-8")

    assert (
        main(
            [
                str(run_dir),
                "--review-text",
                str(review_text),
                "--run-id",
                "run-1",
                "--print",
                "status",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out.strip() == "LGTM"
    assert LEGACY_REVIEW_WARNING not in captured.err


def test_code_review_pr_fragment_reflects_blocked_verdict() -> None:
    report = HocaReviewReport(
        run_id="run-1",
        round=1,
        role="reviewer",
        verdict="blocked",
        findings=[],
        pr_notes={"summary": ["Reviewer could not complete review."], "known_followups": []},
    )
    result = ReviewGateResult(report=report, report_path=Path("review.json"), source="structured")

    assert "Review blocked" in code_review_pr_fragment(result)
    assert task_report_review_status(result) == "blocked"


def test_try_extract_structured_report_from_fenced_json() -> None:
    review_text = (
        "Review complete.\n"
        "```json\n"
        "{\n"
        '  "schema_version": 1,\n'
        '  "run_id": "run-1",\n'
        '  "round": 1,\n'
        '  "role": "reviewer",\n'
        '  "verdict": "fix_required",\n'
        '  "findings": [\n'
        "    {\n"
        '      "id": "F1",\n'
        '      "severity": "medium",\n'
        '      "category": "test",\n'
        '      "file": "tests/test_module.py",\n'
        '      "summary": "Missing error-path coverage",\n'
        '      "required_fix": "Add a test for invalid input"\n'
        "    }\n"
        "  ],\n"
        '  "pr_notes": {"summary": ["Needs tests."], "known_followups": []}\n'
        "}\n"
        "```\n"
        "Please add the missing test.\n"
    )

    report = try_extract_structured_report(review_text)

    assert report is not None
    assert report.verdict == "fix_required"
    assert report.findings[0].id == "F1"


def test_try_extract_structured_report_prefers_json_before_yaml() -> None:
    review_text = (
        "verdict: blocked\n"
        '{"schema_version":1,"run_id":"run-1","round":1,"role":"reviewer",'
        '"verdict":"LGTM","findings":[],"pr_notes":{"summary":["Looks good."],'
        '"known_followups":[]}}\n'
    )

    report = try_extract_structured_report(review_text)

    assert report is not None
    assert report.verdict == "LGTM"


def test_try_extract_structured_report_from_openhands_message_event() -> None:
    review_text = (
        '{"kind":"MessageEvent","source":"user","llm_message":{"content":[{"text":"'
        'Legacy prompt says LGTM | fix_required | blocked"}]}}\n'
        '{"kind":"MessageEvent","source":"agent","llm_message":{"content":[{"text":'
        '"{\\"schema_version\\":1,\\"run_id\\":\\"run-1\\",\\"round\\":1,'
        '\\"role\\":\\"reviewer\\",\\"verdict\\":\\"fix_required\\",'
        '\\"findings\\":[{\\"id\\":\\"F1\\",\\"severity\\":\\"high\\",'
        '\\"category\\":\\"correctness\\",\\"file\\":\\"calc.py\\",'
        '\\"summary\\":\\"Syntax error\\",\\"required_fix\\":\\"Complete expression\\"}],'
        '\\"pr_notes\\":{\\"summary\\":[\\"Needs repair.\\"],\\"known_followups\\":[]}}"}]}}\n'
    )

    report = try_extract_structured_report(review_text)

    assert report is not None
    assert report.verdict == "fix_required"
    assert report.findings[0].id == "F1"


def test_try_extract_structured_report_from_dependency_free_yaml() -> None:
    review_text = (
        "Review complete.\n"
        "```yaml\n"
        "schema_version: 1\n"
        "run_id: run-1\n"
        "round: 1\n"
        "role: reviewer\n"
        "verdict: fix_required\n"
        "findings:\n"
        "  - id: F1\n"
        "    severity: medium\n"
        "    category: test\n"
        "    file: tests/test_module.py\n"
        "    summary: Missing error-path coverage\n"
        "    required_fix: Add a test for invalid input\n"
        "pr_notes:\n"
        "  summary:\n"
        "    - Needs tests.\n"
        "  known_followups: []\n"
        "```\n"
    )

    report = try_extract_structured_report(review_text)

    assert report is not None
    assert report.verdict == "fix_required"
    assert report.findings[0].id == "F1"
    assert report.pr_notes["summary"] == ["Needs tests."]


def test_try_extract_structured_report_rejects_random_text() -> None:
    assert try_extract_structured_report("Looks good.\nLGTM\n") is None
    assert try_extract_structured_report("") is None


def test_materialize_structured_report_from_text_writes_valid_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    review_text = run_dir / "openhands-review.txt"
    output_path = run_dir / "reviews" / "review-report-1.json"
    review_text.write_text(
        "Summary\n"
        '{"schema_version":1,"run_id":"run-1","round":1,"role":"reviewer",'
        '"verdict":"LGTM","findings":[],"pr_notes":{"summary":["Looks good."],'
        '"known_followups":[]}}\n'
        "LGTM\n",
        encoding="utf-8",
    )

    assert materialize_structured_report_from_text(
        review_text,
        output_path,
        run_id="run-1",
        round_number=1,
    ) is True
    assert HocaReviewReport.from_json(output_path.read_text(encoding="utf-8")).verdict == "LGTM"
