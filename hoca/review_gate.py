from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from hoca.contracts import HocaReviewFinding, HocaReviewReport


LEGACY_LGTM_TOKEN = "LGTM"


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


def legacy_text_to_report(
    review_text: str,
    *,
    run_id: str,
    round_number: int,
) -> HocaReviewReport:
    if LEGACY_LGTM_TOKEN in review_text:
        verdict = "LGTM"
        findings: list[HocaReviewFinding] = []
        summary = "Legacy review output contained LGTM."
    else:
        verdict = "fix_required"
        summary = "Legacy review output did not contain LGTM."
        findings = [
            HocaReviewFinding(
                id=f"legacy-review-{round_number}",
                severity="medium",
                category="correctness",
                file=None,
                summary=summary,
                required_fix=review_text.strip() or "Review requested changes.",
            )
        ]

    return HocaReviewReport(
        run_id=run_id,
        round=round_number,
        role="reviewer",
        verdict=verdict,
        findings=findings,
        pr_notes={
            "summary": [summary],
            "known_followups": [],
        },
    )


def _load_structured_report(path: Path) -> HocaReviewReport:
    try:
        return HocaReviewReport.from_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReviewGateError(f"Malformed HocaReviewReport at {path}: {exc}") from exc


def has_review_artifacts(run_dir: Path, *, round_number: int = 1) -> bool:
    run_dir = run_dir.resolve()
    return (run_dir / "openhands-review.txt").exists() or default_report_path(
        run_dir, round_number
    ).exists()


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
) -> ReviewGateResult | None:
    run_dir = run_dir.resolve()
    if not has_review_artifacts(run_dir, round_number=round_number):
        return None
    resolved_review_text = review_text_path or (run_dir / "openhands-review.txt")
    return evaluate_review_gate(
        run_dir,
        review_text_path=resolved_review_text,
        run_id=run_id,
        round_number=round_number,
        structured_report_path=structured_report_path,
    )


def evaluate_review_gate(
    run_dir: Path,
    *,
    review_text_path: Path,
    run_id: str | None = None,
    round_number: int = 1,
    structured_report_path: Path | None = None,
) -> ReviewGateResult:
    run_dir = run_dir.resolve()
    review_text_path = review_text_path.resolve()
    run_id = run_id or run_dir.name
    output_path = default_report_path(run_dir, round_number)
    report_path = structured_report_path or output_path

    if report_path.exists():
        report = _load_structured_report(report_path)
        if report_path != output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(report_path, output_path)
            report_path = output_path
        return ReviewGateResult(report=report, report_path=report_path, source="structured")

    if not review_text_path.exists():
        raise ReviewGateError(f"Review text file does not exist: {review_text_path}")

    report = legacy_text_to_report(
        review_text_path.read_text(encoding="utf-8"),
        run_id=run_id,
        round_number=round_number,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.to_json(), encoding="utf-8")
    return ReviewGateResult(report=report, report_path=output_path, source="legacy")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a HOCA review gate.")
    parser.add_argument("run_dir")
    parser.add_argument("--review-text")
    parser.add_argument("--run-id")
    parser.add_argument("--round", type=int, default=1, dest="round_number")
    parser.add_argument("--structured-report")
    parser.add_argument(
        "--print",
        choices=("verdict", "status", "pr-fragment"),
        help="Print only the verdict, task-report status label, or PR fragment text.",
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    round_number = args.round_number
    review_text_path = (
        Path(args.review_text)
        if args.review_text
        else run_dir / "openhands-review.txt"
    )

    try:
        result = try_resolve_review_gate(
            run_dir,
            review_text_path=review_text_path,
            run_id=args.run_id,
            round_number=round_number,
            structured_report_path=(
                Path(args.structured_report) if args.structured_report else None
            ),
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
