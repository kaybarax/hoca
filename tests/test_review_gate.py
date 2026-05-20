from __future__ import annotations

from pathlib import Path

import pytest

from hoca.contracts import HocaReviewReport
from hoca.review_gate import (
    ReviewGateError,
    ReviewGateResult,
    code_review_pr_fragment,
    evaluate_review_gate,
    legacy_text_to_report,
    main,
    materialize_structured_report_from_text,
    task_report_review_status,
    try_extract_structured_report,
    try_resolve_review_gate,
)


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


def test_malformed_structured_report_fails_gracefully(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    reports = run_dir / "reviews"
    reports.mkdir(parents=True)
    review_text = run_dir / "openhands-review.txt"
    review_text.write_text("LGTM\n", encoding="utf-8")
    (reports / "review-report-1.json").write_text('{"verdict": "LGTM"}', encoding="utf-8")

    with pytest.raises(ReviewGateError, match="Malformed HocaReviewReport"):
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
    assert capsys.readouterr().out.strip() == "LGTM"


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
