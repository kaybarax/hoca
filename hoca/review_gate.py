from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from hoca.contracts import HocaReviewFinding, HocaReviewReport
from hoca.review_report_parser import (
    ReviewReportParseError,
    parse_review_report_text,
    try_extract_structured_report,
)

class ReviewGateError(ValueError):
    """Raised when a structured review report is present but invalid."""


@dataclass(frozen=True)
class ReviewGateResult:
    report: HocaReviewReport
    report_path: Path
    source: str

    @property
    def approved(self) -> bool:
        return self.report.verdict == "LGTM"


def default_report_path(run_dir: Path, round_number: int) -> Path:
    return run_dir / "reviews" / f"review-report-{round_number}.json"


def _read_changed_files(run_dir: Path) -> list[str]:
    candidates = (
        run_dir / "review" / "changed-files.txt",
        run_dir / "changed-files.txt",
    )
    for path in candidates:
        if not path.is_file():
            continue
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return []


def _python_syntax_findings(
    *,
    run_dir: Path,
    project_path: Path | None,
) -> list[HocaReviewFinding]:
    if project_path is None:
        return []

    findings: list[HocaReviewFinding] = []
    for rel_path in _read_changed_files(run_dir):
        if not rel_path.endswith(".py"):
            continue
        source_path = (project_path / rel_path).resolve()
        try:
            source = source_path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            ast.parse(source, filename=rel_path)
        except SyntaxError as exc:
            location = f"line {exc.lineno}" if exc.lineno is not None else "unknown line"
            findings.append(
                HocaReviewFinding(
                    id=f"sanity-python-syntax-{len(findings) + 1}",
                    severity="high",
                    category="correctness",
                    file=rel_path,
                    summary=f"Python syntax error in {rel_path} at {location}: {exc.msg}",
                    required_fix="Fix the Python syntax error before approving the review.",
                )
            )
    return findings


def apply_review_sanity_checks(
    report: HocaReviewReport,
    *,
    run_dir: Path,
    project_path: Path | None = None,
) -> HocaReviewReport:
    """Enforce deterministic review blockers that must override live reviewer LGTM."""
    if report.verdict != "LGTM":
        return report

    findings = _python_syntax_findings(run_dir=run_dir, project_path=project_path)
    if not findings:
        return report

    pr_notes = {key: list(value) for key, value in report.pr_notes.items()}
    pr_notes.setdefault("summary", []).append(
        "Deterministic review sanity checks found blocking issues after reviewer LGTM."
    )
    pr_notes.setdefault("known_followups", list(report.pr_notes.get("known_followups", [])))
    return replace(
        report,
        verdict="fix_required",
        findings=[*report.findings, *findings],
        pr_notes=pr_notes,
    )


def _load_structured_report(path: Path) -> HocaReviewReport:
    try:
        return HocaReviewReport.from_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReviewGateError(f"Malformed HocaReviewReport at {path}: {exc}") from exc


def materialize_structured_report_from_text(
    review_text_path: Path,
    output_path: Path,
    *,
    run_id: str,
    round_number: int,
) -> bool:
    if not review_text_path.exists():
        return False
    report = try_extract_structured_report(review_text_path.read_text(encoding="utf-8"))
    if report is None:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.to_json(), encoding="utf-8")
    return True


def has_review_artifacts(run_dir: Path, *, round_number: int = 1) -> bool:
    run_dir = run_dir.resolve()
    return default_report_path(run_dir, round_number).exists()


def code_review_pr_fragment(result: ReviewGateResult) -> str:
    footer = "Full review output is saved in the HOCA run artifacts."
    if result.approved:
        return f"**Status**: Review gate approved (LGTM).\n\n{footer}"
    if result.report.verdict == "blocked":
        return f"**Status**: Review blocked.\n\n{footer}"
    return (
        "**Status**: Review requires fixes "
        "(human review recommended).\n\n"
        f"{footer}"
    )


def code_review_error_fragment() -> str:
    return (
        "**Status**: Review gate could not evaluate review artifacts "
        "(human review recommended).\n\n"
        "Full review output is saved in the HOCA run artifacts."
    )


def task_report_review_status(result: ReviewGateResult) -> str:
    if result.approved:
        return "LGTM"
    if result.report.verdict == "blocked":
        return "blocked"
    return "required fixes or inconclusive"


def try_resolve_review_gate(
    run_dir: Path,
    *,
    review_text_path: Path | None = None,
    run_id: str | None = None,
    round_number: int = 1,
    structured_report_path: Path | None = None,
    project_path: Path | None = None,
) -> ReviewGateResult | None:
    run_dir = run_dir.resolve()
    if not has_review_artifacts(run_dir, round_number=round_number) and not (
        structured_report_path and structured_report_path.exists()
    ):
        return None
    resolved_review_text = review_text_path or (run_dir / "openhands-review.txt")
    return evaluate_review_gate(
        run_dir,
        review_text_path=resolved_review_text,
        run_id=run_id,
        round_number=round_number,
        structured_report_path=structured_report_path,
        project_path=project_path,
    )


def evaluate_review_gate(
    run_dir: Path,
    *,
    review_text_path: Path,
    run_id: str | None = None,
    round_number: int = 1,
    structured_report_path: Path | None = None,
    project_path: Path | None = None,
) -> ReviewGateResult:
    run_dir = run_dir.resolve()
    review_text_path = review_text_path.resolve()
    run_id = run_id or run_dir.name
    output_path = default_report_path(run_dir, round_number)
    report_path = structured_report_path or output_path

    if report_path.exists():
        report = _load_structured_report(report_path)
        report = apply_review_sanity_checks(
            report,
            run_dir=run_dir,
            project_path=project_path,
        )
        if report_path != output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            report_path = output_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report.to_json(), encoding="utf-8")
        return ReviewGateResult(report=report, report_path=report_path, source="structured")

    if not review_text_path.exists():
        raise ReviewGateError(f"Review text file does not exist: {review_text_path}")

    try:
        parsed = parse_review_report_text(
            review_text_path.read_text(encoding="utf-8"),
            run_id=run_id,
            round_number=round_number,
        )
    except ReviewReportParseError as exc:
        raise ReviewGateError(str(exc)) from exc
    report = parsed.report
    report = apply_review_sanity_checks(
        report,
        run_dir=run_dir,
        project_path=project_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.to_json(), encoding="utf-8")
    return ReviewGateResult(report=report, report_path=output_path, source=parsed.source)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a HOCA review gate.")
    parser.add_argument("run_dir")
    parser.add_argument("--review-text")
    parser.add_argument("--run-id")
    parser.add_argument("--round", type=int, default=1, dest="round_number")
    parser.add_argument("--structured-report")
    parser.add_argument("--project-path")
    parser.add_argument(
        "--print",
        choices=("verdict", "status", "pr-fragment"),
        help="Print only the verdict, task-report status label, or PR fragment text.",
    )
    parser.add_argument(
        "--materialize-from-text",
        help="Extract a structured HocaReviewReport from review text when possible.",
    )
    parser.add_argument(
        "--output",
        help="Output path for --materialize-from-text.",
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    round_number = args.round_number
    review_text_path = (
        Path(args.review_text)
        if args.review_text
        else run_dir / "openhands-review.txt"
    )

    if args.materialize_from_text:
        output_path = (
            Path(args.output)
            if args.output
            else default_report_path(run_dir, round_number)
        )
        if materialize_structured_report_from_text(
            Path(args.materialize_from_text),
            output_path,
            run_id=args.run_id or run_dir.name,
            round_number=round_number,
        ):
            print(f"Structured review report materialized at {output_path}")
        return 0

    try:
        result = try_resolve_review_gate(
            run_dir,
            review_text_path=review_text_path,
            run_id=args.run_id,
            round_number=round_number,
            structured_report_path=(
                Path(args.structured_report) if args.structured_report else None
            ),
            project_path=Path(args.project_path).resolve() if args.project_path else None,
        )
    except ReviewGateError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    if result is None:
        print("Review artifacts were not found.", file=sys.stderr)
        return 3

    if args.print == "verdict":
        print(result.report.verdict)
    elif args.print == "status":
        print(task_report_review_status(result))
    elif args.print == "pr-fragment":
        print(code_review_pr_fragment(result), end="")
    else:
        print(
            f"Review gate verdict: {result.report.verdict} "
            f"(source: {result.source}, report: {result.report_path})"
        )
    if result.report.verdict == "LGTM":
        return 0
    if result.report.verdict == "fix_required":
        return 2
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
