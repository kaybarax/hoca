"""Deterministic round loop semantics for HOCA manager/worker/reviewer cycles."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from hoca.config import load_config
from hoca.contracts import HocaManagerDecision, ManagerDecision
from hoca.hard_blockers import ValidationStatus, has_absolute_validation_blocker
from hoca.run_artifacts import build_validation_status_from_run_dir, record_manager_decision
from hoca.run_layout import manager_decision_path
from hoca.run_state import read_optional_json

RoundLoopAction = Literal["review", "repair", "proceed", "block"]

ENVIRONMENT_FAILURE_TYPES = frozenset({"environment", "pre-existing"})
ABSOLUTE_TEST_FAILURE_TYPES = frozenset({"environment", "pre-existing"})


@dataclass(frozen=True)
class RoundLoopDecision:
    action: RoundLoopAction
    current_round: int
    next_round: int | None = None
    repair_brief_path: str | None = None
    block_reason: str | None = None
    block_message: str | None = None
    status_detail: str | None = None
    manager_decision: ManagerDecision | None = None
    draft_pr: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload["manager_decision"] is not None:
            payload["manager_decision"] = str(payload["manager_decision"])
        return payload


def max_total_rounds_for_run(run_dir: Path, *, override: int | None = None) -> int:
    if override is not None and override >= 1:
        return override
    status = read_optional_json(run_dir / "status.json")
    if status is not None:
        configured = status.get("max_total_rounds")
        if isinstance(configured, int) and configured >= 1:
            return configured
    return load_config().max_total_rounds


def _final_round(current_round: int, max_total_rounds: int) -> bool:
    return current_round >= max_total_rounds


def _validation_repair_brief(validation: ValidationStatus) -> str:
    blockers = sorted(set(validation.hard_blockers))
    if not validation.tests_passed and "test_failure" not in blockers:
        blockers.append("test_failure")
    blocker_text = ", ".join(blockers) if blockers else "test_failure"
    return (
        "Resolve validation failures before review can continue.\n"
        f"Blockers: {blocker_text}.\n"
        "Keep changes minimal and do not restart unrelated work.\n"
    )


def write_repair_brief_file(
    run_dir: Path,
    *,
    repair_attempt: int,
    round_number: int,
    max_total_rounds: int,
    reason: str,
    brief: str,
) -> Path:
    repair_file = run_dir / f"repair-attempt-{repair_attempt}.md"
    repair_file.parent.mkdir(parents=True, exist_ok=True)
    repair_file.write_text(
        "\n".join(
            [
                "Continue this HOCA task by fixing the current repository changes; do not start over.",
                "",
                f"Repair reason: {reason}",
                f"Round: {round_number} of {max_total_rounds}",
                "",
                brief.rstrip(),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return repair_file


def decide_after_validation(
    *,
    current_round: int,
    max_total_rounds: int,
    validation: ValidationStatus,
    test_failure_type: str | None = None,
) -> RoundLoopDecision:
    """Decide the next loop action after deterministic validation."""
    if max_total_rounds < 1:
        raise ValueError("max_total_rounds must be greater than or equal to 1")
    if current_round < 1:
        raise ValueError("current_round must be greater than or equal to 1")

    if validation.tests_passed and not validation.hard_blockers:
        return RoundLoopDecision(action="review", current_round=current_round)

    normalized_failure = (test_failure_type or "").strip().lower()
    if normalized_failure in ABSOLUTE_TEST_FAILURE_TYPES:
        return RoundLoopDecision(
            action="block",
            current_round=current_round,
            block_reason=f"tests_{normalized_failure.replace('-', '_')}",
            block_message=(
                f"Tests failed due to {normalized_failure} conditions. "
                "Human intervention is needed."
            ),
        )

    if has_absolute_validation_blocker(validation):
        blockers = ", ".join(sorted(set(validation.hard_blockers)))
        return RoundLoopDecision(
            action="block",
            current_round=current_round,
            block_reason="validation_blocked",
            block_message=(
                "Validation reported absolute hard blockers. "
                f"Human intervention is needed ({blockers})."
            ),
        )

    if _final_round(current_round, max_total_rounds):
        return RoundLoopDecision(
            action="block",
            current_round=current_round,
            block_reason="tests_failed",
            block_message=(
                f"Tests still failed after round {current_round} of {max_total_rounds}. "
                "Human review is needed."
            ),
        )

    next_round = current_round + 1
    return RoundLoopDecision(
        action="repair",
        current_round=current_round,
        next_round=next_round,
        status_detail=f"tests_failed_round_{next_round}",
        manager_decision=None,
    )


def decide_after_arbitration(
    *,
    manager_decision: HocaManagerDecision,
    current_round: int,
    max_total_rounds: int,
) -> RoundLoopDecision:
    """Decide the next loop action from a recorded manager arbitration decision."""
    if max_total_rounds < 1:
        raise ValueError("max_total_rounds must be greater than or equal to 1")

    decision = manager_decision.decision
    if decision == "proceed_to_pr":
        return RoundLoopDecision(
            action="proceed",
            current_round=current_round,
            manager_decision=decision,
            draft_pr=False,
        )

    if decision == "draft_pr_with_blockers":
        return RoundLoopDecision(
            action="proceed",
            current_round=current_round,
            manager_decision=decision,
            draft_pr=True,
            status_detail="draft_pr_with_blockers",
        )

    if decision == "blocked":
        return RoundLoopDecision(
            action="block",
            current_round=current_round,
            manager_decision=decision,
            block_reason="review_blocked",
            block_message=(
                f"Manager blocked the run after round {current_round} of {max_total_rounds}. "
                "Human intervention is needed."
            ),
        )

    if decision == "repair_required":
        if current_round >= max_total_rounds:
            return RoundLoopDecision(
                action="block",
                current_round=current_round,
                manager_decision=decision,
                block_reason="review_not_lgtm",
                block_message=(
                    f"Review still did not approve after round {current_round} of "
                    f"{max_total_rounds}. Human review is needed."
                ),
            )
        next_round = current_round + 1
        return RoundLoopDecision(
            action="repair",
            current_round=current_round,
            next_round=next_round,
            manager_decision=decision,
            status_detail=f"review_not_lgtm_round_{next_round}",
        )

    raise ValueError(f"Unsupported manager decision: {decision!r}")


def load_manager_decision(run_dir: Path, *, round_number: int) -> HocaManagerDecision | None:
    path = manager_decision_path(run_dir, round_number)
    if not path.is_file():
        return None
    return HocaManagerDecision.from_json(path.read_text(encoding="utf-8"))


def resolve_after_validation(
    run_dir: Path,
    *,
    current_round: int,
    max_total_rounds: int | None = None,
    test_failure_type: str | None = None,
) -> RoundLoopDecision:
    validation = build_validation_status_from_run_dir(run_dir)
    rounds = max_total_rounds_for_run(run_dir, override=max_total_rounds)
    decision = decide_after_validation(
        current_round=current_round,
        max_total_rounds=rounds,
        validation=validation,
        test_failure_type=test_failure_type,
    )
    if decision.action != "repair":
        return decision

    repair_attempt = current_round
    brief = _validation_repair_brief(validation)
    repair_path = write_repair_brief_file(
        run_dir,
        repair_attempt=repair_attempt,
        round_number=decision.next_round or current_round + 1,
        max_total_rounds=rounds,
        reason="tests_failed",
        brief=brief,
    )
    return RoundLoopDecision(
        action="repair",
        current_round=current_round,
        next_round=decision.next_round,
        repair_brief_path=str(repair_path),
        status_detail=decision.status_detail,
    )


def resolve_after_arbitration(
    run_dir: Path,
    *,
    current_round: int,
    max_total_rounds: int | None = None,
) -> RoundLoopDecision:
    rounds = max_total_rounds_for_run(run_dir, override=max_total_rounds)
    record_manager_decision(run_dir, round_number=current_round)
    manager_decision = load_manager_decision(run_dir, round_number=current_round)
    if manager_decision is None:
        return RoundLoopDecision(
            action="block",
            current_round=current_round,
            block_reason="review_failed",
            block_message=(
                "Manager arbitration could not be recorded because the structured "
                "review report was missing."
            ),
        )

    decision = decide_after_arbitration(
        manager_decision=manager_decision,
        current_round=current_round,
        max_total_rounds=rounds,
    )
    if decision.action != "repair":
        return decision

    repair_attempt = current_round
    brief = manager_decision.next_worker_brief
    if not brief:
        raise ValueError(
            "repair_required manager decision is missing next_worker_brief"
        )
    repair_path = write_repair_brief_file(
        run_dir,
        repair_attempt=repair_attempt,
        round_number=decision.next_round or current_round + 1,
        max_total_rounds=rounds,
        reason="review_not_lgtm",
        brief=brief,
    )
    return RoundLoopDecision(
        action=decision.action,
        current_round=current_round,
        next_round=decision.next_round,
        repair_brief_path=str(repair_path),
        status_detail=decision.status_detail,
        manager_decision=decision.manager_decision,
    )


def mark_draft_pr_decision(run_dir: Path, *, manager_decision: HocaManagerDecision) -> None:
    flag_path = run_dir / "draft-pr-with-blockers.flag"
    flag_path.write_text(
        json.dumps(
            {
                "round": manager_decision.round,
                "accepted_findings": manager_decision.accepted_findings,
                "downgraded_to_pr_notes": manager_decision.downgraded_to_pr_notes,
                "reasoning": manager_decision.reasoning,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    notes_path = run_dir / "risk-notes.txt"
    existing = notes_path.read_text(encoding="utf-8").strip() if notes_path.is_file() else ""
    note = (
        "Draft PR recommended after round cap: residual medium findings remain "
        "without hard blockers."
    )
    notes_path.write_text(
        (existing + "\n\n" + note).strip() + "\n" if existing else note + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve HOCA round loop decisions.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validation_parser = subparsers.add_parser(
        "after-validation", help="Resolve the next action after validation."
    )
    validation_parser.add_argument("run_dir")
    validation_parser.add_argument("--round", type=int, required=True, dest="current_round")
    validation_parser.add_argument("--max-rounds", type=int, dest="max_total_rounds")
    validation_parser.add_argument("--failure-type", dest="test_failure_type", default="")

    arbitration_parser = subparsers.add_parser(
        "after-arbitration", help="Resolve the next action after manager arbitration."
    )
    arbitration_parser.add_argument("run_dir")
    arbitration_parser.add_argument("--round", type=int, required=True, dest="current_round")
    arbitration_parser.add_argument("--max-rounds", type=int, dest="max_total_rounds")
    arbitration_parser.add_argument(
        "--mark-draft",
        action="store_true",
        help="Write draft-pr-with-blockers artifacts when proceeding as draft PR.",
    )

    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)

    try:
        if args.command == "after-validation":
            decision = resolve_after_validation(
                run_dir,
                current_round=args.current_round,
                max_total_rounds=args.max_total_rounds,
                test_failure_type=args.test_failure_type or None,
            )
        else:
            decision = resolve_after_arbitration(
                run_dir,
                current_round=args.current_round,
                max_total_rounds=args.max_total_rounds,
            )
            if args.mark_draft and decision.draft_pr:
                manager_decision = load_manager_decision(run_dir, round_number=args.current_round)
                if manager_decision is not None:
                    mark_draft_pr_decision(run_dir, manager_decision=manager_decision)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps(decision.to_dict(), sort_keys=True))
    if decision.action == "block":
        return 4
    if decision.action == "repair":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
