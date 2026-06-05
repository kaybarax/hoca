from __future__ import annotations

import json
from pathlib import Path

from hoca.contracts import HocaReviewFinding, HocaReviewReport
from hoca.review_fanout import (
    aggregate_review_signals,
    collect_review_signals,
    normalize_review_output,
)


def test_normalize_structured_review_output_preserves_finding_fields() -> None:
    report = HocaReviewReport(
        run_id="run-1",
        round=2,
        role="reviewer",
        verdict="fix_required",
        findings=[
            HocaReviewFinding(
                id="F-1",
                severity="high",
                category="correctness",
                file="src/auth.py",
                summary="Add auth check",
                required_fix="Handle missing token",
            ),
            HocaReviewFinding(
                id="F-2",
                severity="medium",
                category="security",
                file="src/session.py",
                summary="Avoid unsafe cast",
                required_fix="Tighten validation",
            ),
        ],
        pr_notes={"summary": ["Needs follow-up fixes."]},
    )

    signals = normalize_review_output(
        report.to_json(), lane_id="lane-1", source="reviewer", review_round=2
    )
    assert len(signals) == 2
    by_id = {signal.finding_id: signal for signal in signals}
    assert by_id["F-1"].finding_severity == "high"
    assert by_id["F-1"].finding_category == "correctness"
    assert by_id["F-1"].finding_file == "src/auth.py"
    assert by_id["F-1"].required_fix == "Handle missing token"


def test_normalize_adapter_payload_preserves_structured_fields() -> None:
    raw = json.dumps(
        {
            "source": "fake-reviewer",
            "verdict": "BLOCKED",
            "findings": [
                {
                    "id": "A-1",
                    "severity": "low",
                    "category": "style",
                    "file": "app/main.py",
                    "summary": "Add a regression case",
                    "required_fix": "Add test",
                    "evidence": "Existing behavior missed edge case.",
                }
            ],
        }
    )

    signals = normalize_review_output(raw, lane_id="lane-2", source="fake", review_round=1)
    assert len(signals) == 1
    signal = signals[0]
    assert signal.verdict == "blocked"
    assert signal.finding_id == "A-1"
    assert signal.finding_severity == "low"
    assert signal.finding_category == "style"
    assert signal.finding_file == "app/main.py"
    assert signal.required_fix == "Add test"
    assert signal.details == "Add test"


def test_collect_review_signals_deduplicates_duplicates_and_aggregates(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "review-output.json").write_text(
        json.dumps(
            [
                {
                    "id": "F-dup",
                    "summary": "Same finding repeated",
                    "severity": "low",
                    "category": "test",
                    "file": "app.py",
                    "verdict": "NEEDS_WORK",
                },
                {
                    "id": "F-dup",
                    "summary": "Same finding repeated",
                    "severity": "low",
                    "category": "test",
                    "file": "app.py",
                    "verdict": "NEEDS_WORK",
                },
            ]
        ),
        encoding="utf-8",
    )

    signals = collect_review_signals(run_dir=run_dir, lane_id="lane-dup")
    assert len(signals) == 1

    grouped = aggregate_review_signals(signals)
    assert len(grouped["needs_work"]) == 1
    assert grouped["pass"] == []
    assert grouped["blocked"] == []


def test_normalize_review_output_falls_back_to_raw_text() -> None:
    good = normalize_review_output(
        "Looks good: PASS", lane_id="lane-raw", source="manual", review_round=1
    )
    blocked = normalize_review_output(
        "need follow-up", lane_id="lane-raw", source="manual", review_round=1
    )

    assert good and good[0].verdict == "pass"
    assert blocked and blocked[0].verdict == "needs_work"


def test_collect_review_signals_disables_fanout_adapters(monkeypatch, tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "review-output.json").write_text('{"verdict":"pass"}', encoding="utf-8")

    monkeypatch.setenv("HOCA_REVIEW_FANOUT_ENABLED", "false")
    monkeypatch.setenv("HOCA_REVIEW_ADAPTERS", 'fake=echo \'{"verdict":"blocked"}\'')

    signals = collect_review_signals(run_dir=run_dir, lane_id="lane-disabled")
    assert len(signals) == 1
    assert signals[0].verdict == "pass"


def test_collect_review_signals_runs_multiple_fake_review_adapters(
    monkeypatch, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "review-output.json").write_text('{"verdict":"pass"}', encoding="utf-8")

    fake_file = tmp_path / "fake-adapter-report.json"
    fake_file.write_text(
        '{"verdict":"needs_work","findings":[{"id":"A-1","summary":"Manual check","file":"app.py","verdict":"needs_work"}]}',
        encoding="utf-8",
    )

    adapter_payload = '{"verdict":"pass","summary":"fake command"}'
    monkeypatch.setenv("HOCA_REVIEW_FANOUT_ENABLED", "true")
    monkeypatch.setenv(
        "HOCA_REVIEW_ADAPTERS",
        f"file={fake_file},cmd=echo '{adapter_payload}'",
    )

    signals = collect_review_signals(run_dir=run_dir, lane_id="lane-multi")
    assert len(signals) == 3
    sources = {signal.source for signal in signals}
    assert sources == {"adapter", "file", "cmd"}
    assert any(signal.verdict == "needs_work" for signal in signals)
