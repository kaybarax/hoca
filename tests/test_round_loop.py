from __future__ import annotations

import json
from pathlib import Path


from hoca.contracts import HocaManagerDecision, HocaReviewFinding, HocaReviewReport
from hoca.hard_blockers import ValidationStatus
from hoca.round_loop import (
    decide_after_arbitration,
    decide_after_validation,
    main,
    resolve_after_arbitration,
    resolve_after_validation,
    write_repair_brief_file,
)
from hoca.run_layout import ensure_run_layout, review_report_path
from hoca.run_state import write_json_atomic


def _review(
    *,
    round_number: int = 1,
    verdict: str = "fix_required",
    findings: list[HocaReviewFinding] | None = None,
) -> HocaReviewReport:
    return HocaReviewReport(
        run_id="run-1",
        round=round_number,
        role="reviewer",
        verdict=verdict,
        findings=findings or [],
        pr_notes={"summary": ["Review complete"], "known_followups": []},
    )


def _finding(
    finding_id: str,
    *,
    severity: str = "high",
    category: str = "correctness",
    required_fix: str | None = None,
) -> HocaReviewFinding:
    payload: dict[str, object] = {
        "id": finding_id,
        "severity": severity,
        "category": category,
        "file": "src/app.py",
        "summary": f"Finding {finding_id}",
        "required_fix": required_fix if required_fix is not None else f"Fix {finding_id}",
    }
    if required_fix is None and severity in ("nit", "low"):
        payload["required_fix"] = None
    return HocaReviewFinding.from_dict(payload)


def _setup_run(
    tmp_path: Path,
    *,
    round_number: int,
    review: HocaReviewReport,
    tests_passed: bool = True,
    max_total_rounds: int = 3,
) -> Path:
    run_dir = tmp_path / "run-1"
    ensure_run_layout(run_dir)
    write_json_atomic(
        run_dir / "status.json",
        {"run_id": "run-1", "max_total_rounds": max_total_rounds},
    )
    (run_dir / "tests-exit-code.txt").write_text(
        "0\n" if tests_passed else "1\n",
        encoding="utf-8",
    )
    review_report_path(run_dir, round_number).write_text(review.to_json(), encoding="utf-8")
    return run_dir


class TestDecideAfterValidation:
    def test_passing_validation_continues_to_review(self) -> None:
        decision = decide_after_validation(
            current_round=1,
            max_total_rounds=3,
            validation=ValidationStatus(tests_passed=True),
        )

        assert decision.action == "review"

    def test_current_task_failure_before_round_cap_repairs(self) -> None:
        decision = decide_after_validation(
            current_round=1,
            max_total_rounds=3,
            validation=ValidationStatus(tests_passed=False),
            test_failure_type="current_task",
        )

        assert decision.action == "repair"
        assert decision.next_round == 2

    def test_current_task_failure_at_round_cap_blocks(self) -> None:
        decision = decide_after_validation(
            current_round=3,
            max_total_rounds=3,
            validation=ValidationStatus(tests_passed=False),
            test_failure_type="current_task",
        )

        assert decision.action == "block"
        assert decision.block_reason == "tests_failed"

    def test_environment_failure_blocks_immediately(self) -> None:
        decision = decide_after_validation(
            current_round=1,
            max_total_rounds=3,
            validation=ValidationStatus(tests_passed=False),
            test_failure_type="environment",
        )

        assert decision.action == "block"
        assert decision.block_reason == "tests_environment"


def _manager_decision(**overrides: object) -> HocaManagerDecision:
    payload = {
        "run_id": "run-1",
        "round": 1,
        "decision": "proceed_to_pr",
        "accepted_findings": [],
        "rejected_findings": [],
        "downgraded_to_pr_notes": [],
        "reasoning": ["Looks good"],
        "next_worker_brief": None,
        "human_attention_required": False,
    }
    payload.update(overrides)
    return HocaManagerDecision.from_dict(payload)


class TestDecideAfterArbitration:
    def test_proceed_to_pr_exits_loop(self) -> None:
        decision = decide_after_arbitration(
            manager_decision=_manager_decision(decision="proceed_to_pr"),
            current_round=1,
            max_total_rounds=3,
        )

        assert decision.action == "proceed"

    def test_repair_required_before_round_cap(self) -> None:
        decision = decide_after_arbitration(
            manager_decision=_manager_decision(
                decision="repair_required",
                accepted_findings=["F1"],
                reasoning=["Fix F1"],
                next_worker_brief="Fix only F1.",
            ),
            current_round=1,
            max_total_rounds=3,
        )

        assert decision.action == "repair"
        assert decision.next_round == 2

    def test_repair_required_at_round_cap_blocks(self) -> None:
        decision = decide_after_arbitration(
            manager_decision=_manager_decision(
                round=3,
                decision="repair_required",
                accepted_findings=["F1"],
                reasoning=["Still broken"],
                next_worker_brief="Fix only F1.",
            ),
            current_round=3,
            max_total_rounds=3,
        )

        assert decision.action == "block"
        assert decision.block_reason == "review_not_lgtm"

    def test_draft_pr_proceeds_with_flag(self) -> None:
        decision = decide_after_arbitration(
            manager_decision=_manager_decision(
                round=3,
                decision="draft_pr_with_blockers",
                accepted_findings=["F1"],
                reasoning=["Residual medium finding"],
                human_attention_required=True,
            ),
            current_round=3,
            max_total_rounds=3,
        )

        assert decision.action == "proceed"
        assert decision.draft_pr is True


class TestRoundBehavior:
    """End-to-end round semantics via resolve_after_arbitration/validation."""

    def test_lgtm_after_round_1_proceeds_to_pr(self, tmp_path: Path) -> None:
        run_dir = _setup_run(
            tmp_path,
            round_number=1,
            review=_review(round_number=1, verdict="LGTM"),
        )

        decision = resolve_after_arbitration(run_dir, current_round=1, max_total_rounds=3)

        assert decision.action == "proceed"
        assert decision.manager_decision == "proceed_to_pr"
        assert decision.draft_pr is False

    def test_fix_required_in_round_1_goes_to_round_2(self, tmp_path: Path) -> None:
        run_dir = _setup_run(
            tmp_path,
            round_number=1,
            review=_review(
                round_number=1,
                findings=[_finding("F1", severity="high", category="correctness")],
            ),
        )

        decision = resolve_after_arbitration(run_dir, current_round=1, max_total_rounds=3)

        assert decision.action == "repair"
        assert decision.next_round == 2
        assert decision.repair_brief_path is not None
        assert "Round: 2 of 3" in Path(decision.repair_brief_path).read_text(encoding="utf-8")

    def test_fix_required_in_round_2_goes_to_round_3(self, tmp_path: Path) -> None:
        run_dir = _setup_run(
            tmp_path,
            round_number=2,
            review=_review(
                round_number=2,
                findings=[_finding("F1", severity="medium", category="test")],
            ),
        )

        decision = resolve_after_arbitration(run_dir, current_round=2, max_total_rounds=3)

        assert decision.action == "repair"
        assert decision.next_round == 3
        assert decision.repair_brief_path is not None

    def test_low_priority_finding_after_round_3_proceeds_to_pr_notes(
        self, tmp_path: Path
    ) -> None:
        run_dir = _setup_run(
            tmp_path,
            round_number=3,
            review=_review(
                round_number=3,
                verdict="LGTM",
                findings=[
                    _finding(
                        "F1",
                        severity="nit",
                        category="style",
                        required_fix=None,
                    )
                ],
            ),
        )

        decision = resolve_after_arbitration(run_dir, current_round=3, max_total_rounds=3)

        assert decision.action == "proceed"
        assert decision.manager_decision == "proceed_to_pr"
        assert decision.draft_pr is False

    def test_medium_residual_after_round_3_produces_draft_pr(self, tmp_path: Path) -> None:
        run_dir = _setup_run(
            tmp_path,
            round_number=3,
            review=_review(
                round_number=3,
                findings=[_finding("F1", severity="medium", category="test")],
            ),
        )

        decision = resolve_after_arbitration(run_dir, current_round=3, max_total_rounds=3)

        assert decision.action == "proceed"
        assert decision.manager_decision == "draft_pr_with_blockers"
        assert decision.draft_pr is True

    def test_critical_finding_after_round_3_blocks(self, tmp_path: Path) -> None:
        run_dir = _setup_run(
            tmp_path,
            round_number=3,
            review=_review(
                round_number=3,
                findings=[_finding("F1", severity="critical", category="security")],
            ),
        )

        decision = resolve_after_arbitration(run_dir, current_round=3, max_total_rounds=3)

        assert decision.action == "block"
        assert decision.manager_decision == "blocked"
        assert decision.block_reason == "review_blocked"

    def test_test_failure_after_round_3_blocks(self, tmp_path: Path) -> None:
        run_dir = _setup_run(
            tmp_path,
            round_number=3,
            review=_review(round_number=3, verdict="LGTM"),
            tests_passed=False,
        )

        decision = resolve_after_validation(
            run_dir,
            current_round=3,
            max_total_rounds=3,
            test_failure_type="current_task",
        )

        assert decision.action == "block"
        assert decision.block_reason == "tests_failed"


class TestResolveAfterArbitration:
    def test_writes_manager_repair_brief_for_next_round(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        ensure_run_layout(run_dir)
        write_json_atomic(
            run_dir / "status.json",
            {"run_id": "run-1", "max_total_rounds": 3},
        )
        (run_dir / "tests-exit-code.txt").write_text("0\n", encoding="utf-8")
        review = _review(
            round_number=1,
            findings=[_finding("F1", severity="high")],
        )
        review_report_path(run_dir, 1).write_text(review.to_json(), encoding="utf-8")

        decision = resolve_after_arbitration(run_dir, current_round=1, max_total_rounds=3)

        assert decision.action == "repair"
        assert decision.repair_brief_path is not None
        repair_text = Path(decision.repair_brief_path).read_text(encoding="utf-8")
        assert "Fix only the accepted reviewer findings" in repair_text
        assert "Round: 2 of 3" in repair_text

    def test_three_round_cap_prevents_infinite_repairs(self) -> None:
        for current_round in (1, 2):
            decision = decide_after_arbitration(
                manager_decision=_manager_decision(
                    round=current_round,
                    decision="repair_required",
                    accepted_findings=["F1"],
                    reasoning=["Needs another round"],
                    next_worker_brief="Fix only F1.",
                ),
                current_round=current_round,
                max_total_rounds=3,
            )
            assert decision.action == "repair"
            assert decision.next_round == current_round + 1

        final = decide_after_arbitration(
            manager_decision=_manager_decision(
                round=3,
                decision="repair_required",
                accepted_findings=["F1"],
                reasoning=["Still broken"],
                next_worker_brief="Fix only F1.",
            ),
            current_round=3,
            max_total_rounds=3,
        )
        assert final.action == "block"


class TestRoundLoopCli:
    def test_cli_after_validation_returns_repair_exit_code(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        ensure_run_layout(run_dir)
        (run_dir / "tests-exit-code.txt").write_text("1\n", encoding="utf-8")

        exit_code = main(
            ["after-validation", str(run_dir), "--round", "1", "--max-rounds", "3"]
        )

        assert exit_code == 2

    def test_cli_after_arbitration_writes_json(self, tmp_path: Path, capsys) -> None:
        run_dir = tmp_path / "run-1"
        ensure_run_layout(run_dir)
        write_json_atomic(
            run_dir / "status.json",
            {"run_id": "run-1", "max_total_rounds": 3},
        )
        (run_dir / "tests-exit-code.txt").write_text("0\n", encoding="utf-8")
        review = _review(round_number=1, verdict="LGTM")
        review_report_path(run_dir, 1).write_text(review.to_json(), encoding="utf-8")

        exit_code = main(["after-arbitration", str(run_dir), "--round", "1"])
        payload = json.loads(capsys.readouterr().out)

        assert exit_code == 0
        assert payload["action"] == "proceed"


def test_write_repair_brief_file_includes_round_metadata(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    path = write_repair_brief_file(
        run_dir,
        repair_attempt=1,
        round_number=2,
        max_total_rounds=3,
        reason="tests_failed",
        brief="Fix the failing tests only.",
    )

    text = path.read_text(encoding="utf-8")
    assert "Round: 2 of 3" in text
    assert "Repair reason: tests_failed" in text
    assert "Fix the failing tests only." in text
