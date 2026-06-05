from __future__ import annotations

import os
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoca.contracts import HocaReviewFinding, HocaReviewReport
from hoca.fleet_contracts import HocaReviewSignal
from hoca.review_report_parser import try_extract_structured_report
from hoca.run_state import now_iso

REVIEW_FANOUT_ENABLED_ENV = "HOCA_REVIEW_FANOUT_ENABLED"
REVIEW_ADAPTERS_ENV = "HOCA_REVIEW_ADAPTERS"


@dataclass(frozen=True)
class ReviewSignalSource:
    path: Path | None
    source: str
    review_round: int = 1
    command: str | None = None


def _now() -> str:
    return now_iso()


def _read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _fanout_enabled() -> bool:
    value = os.environ.get(REVIEW_FANOUT_ENABLED_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _default_sources(run_dir: Path, review_round: int) -> tuple[ReviewSignalSource, ...]:
    return (
        ReviewSignalSource(
            run_dir / "reviews" / f"review-report-{review_round}.json",
            "reviewer",
            review_round,
        ),
        ReviewSignalSource(run_dir / "openhands-review.txt", "openhands", review_round),
        ReviewSignalSource(run_dir / "review-output.json", "adapter", review_round),
    )


def _parse_adapter_specs() -> tuple[tuple[str, str], ...]:
    raw = os.environ.get(REVIEW_ADAPTERS_ENV, "")
    if not raw.strip():
        return ()

    parsed: list[tuple[str, str]] = []
    for item in raw.split(","):
        spec = item.strip()
        if not spec:
            continue
        if "=" in spec:
            name, value = spec.split("=", 1)
            name = name.strip()
            value = value.strip()
            if name and value:
                parsed.append((name, value))
            continue
        parsed.append((f"adapter-{len(parsed) + 1}", spec))
    return tuple(parsed)


def _configured_fanout_sources(run_dir: Path, review_round: int) -> tuple[ReviewSignalSource, ...]:
    if not _fanout_enabled():
        return ()
    sources: list[ReviewSignalSource] = []
    for name, spec in _parse_adapter_specs():
        candidate = Path(spec).expanduser()
        if candidate.exists() and candidate.is_file():
            sources.append(
                ReviewSignalSource(path=candidate, source=name, review_round=review_round, command=None)
            )
            continue
        sources.append(
            ReviewSignalSource(
                path=None,
                source=name,
                review_round=review_round,
                command=spec,
            )
        )
    return tuple(sources)


def _run_adapter_command(command: str) -> str | None:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except OSError:
        return None
    output = (result.stdout or "").strip()
    if output:
        return output
    if result.returncode != 0:
        return None
    return None


def _as_verdict(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"lgtm", "pass", "approved", "ready"}:
        return "pass"
    if raw in {"fix_required", "requires_fix", "needs_work"}:
        return "needs_work"
    if raw in {"blocked", "reject", "denied", "reject_changes", "changes_requested"}:
        return "blocked"
    return "needs_work"


def _coalesce_text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _signal_id_from_dict(payload: dict[str, Any], *, default: str | None) -> str | None:
    return _coalesce_text(payload, "id", "finding_id", "signal_id") or default


def _signal_id(lane_id: str, source: str, review_round: int, idx: int, finding_id: str | None = None) -> str:
    if finding_id:
        return f"{lane_id}:{source}:{review_round}:{finding_id}"
    return f"{lane_id}:{source}:{review_round}:{idx:03d}"


def _finding_to_signal(
    *,
    lane_id: str,
    source: str,
    report: HocaReviewReport,
    finding: HocaReviewFinding | None = None,
    review_round: int,
    idx: int,
) -> HocaReviewSignal:
    verdict = _as_verdict(report.verdict)
    if finding is None:
        summary = f"review verdict is {report.verdict}"
        details = "\n".join(report.pr_notes.get("summary", [])) if report.pr_notes else ""
        finding_id = report.role or None
    else:
        summary = finding.summary
        details = finding.required_fix or ""
        finding_id = finding.id

    signal = HocaReviewSignal(
        signal_id=_signal_id(lane_id, source, review_round, idx, finding_id=finding_id),
        lane_id=lane_id,
        source=source,
        verdict=verdict,
        summary=summary,
        details=details,
        review_round=review_round,
        finding_id=finding_id,
        finding_severity=str(finding.severity) if finding else None,
        finding_category=str(finding.category) if finding else None,
        finding_file=finding.file if finding else None,
        required_fix=finding.required_fix if finding else None,
        created_at=_now(),
    )
    return signal


def _from_structured_report(
    report: HocaReviewReport,
    *,
    lane_id: str,
    source: str,
    review_round: int,
) -> list[HocaReviewSignal]:
    if not report.findings:
        return [
            _finding_to_signal(
                lane_id=lane_id,
                source=source,
                report=report,
                finding=None,
                review_round=review_round,
                idx=1,
            )
        ]

    return [
        _finding_to_signal(
            lane_id=lane_id,
            source=source,
            report=report,
            finding=finding,
            review_round=review_round,
            idx=idx,
        )
        for idx, finding in enumerate(report.findings, start=1)
    ]


def _from_dict_payload(
    payload: dict[str, Any],
    *,
    lane_id: str,
    default_source: str,
    review_round: int,
) -> list[HocaReviewSignal]:
    source = str(payload.get("source") or default_source)
    verdict = _as_verdict(
        payload.get("verdict")
        or payload.get("status")
        or payload.get("result")
        or "needs_work"
    )

    if "findings" in payload and isinstance(payload["findings"], list):
        signals: list[HocaReviewSignal] = []
        for idx, item in enumerate(payload["findings"], start=1):
            if not isinstance(item, dict):
                continue
            finding_id = _signal_id_from_dict(item, default=None)
            summary = (
                _coalesce_text(item, "summary", "message", "title")
                or _coalesce_text(item, "description")
                or "review finding"
            )
            details = (
                _coalesce_text(item, "required_fix", "fix", "evidence", "details")
            )
            signals.append(
                HocaReviewSignal(
                    signal_id=_signal_id(lane_id, source, review_round, idx, finding_id),
                    lane_id=lane_id,
                    source=source,
                    verdict=_as_verdict(item.get("verdict") or verdict),
                    summary=summary,
                    details=details,
                    review_round=review_round,
                    finding_id=finding_id,
                    finding_severity=_coalesce_text(item, "severity", "finding_severity"),
                    finding_category=_coalesce_text(item, "category", "finding_category"),
                    finding_file=_coalesce_text(item, "file", "path", "finding_file"),
                    required_fix=_coalesce_text(item, "required_fix", "fix"),
                    created_at=_now(),
                )
            )
        if signals:
            return signals

    summary = ""
    details = ""
    if isinstance(payload.get("pr_notes"), str):
        details = str(payload["pr_notes"])
    elif isinstance(payload.get("pr_notes"), dict):
        details = json.dumps(payload["pr_notes"])

    return [
        HocaReviewSignal(
            signal_id=_signal_id(lane_id, source, review_round, 1, None),
            lane_id=lane_id,
            source=source,
            verdict=verdict,
            summary=_coalesce_text(payload, "summary", "message") or source,
            details=details or None,
            review_round=review_round,
            finding_id=_coalesce_text(payload, "finding_id", "id"),
            finding_severity=_coalesce_text(payload, "finding_severity", "severity"),
            finding_category=_coalesce_text(payload, "finding_category", "category"),
            finding_file=_coalesce_text(payload, "finding_file", "file", "path"),
            required_fix=_coalesce_text(payload, "required_fix", "fix"),
            created_at=_now(),
        )
    ]


def normalize_review_output(
    raw: str,
    *,
    lane_id: str,
    source: str = "reviewer",
    review_round: int = 1,
) -> list[HocaReviewSignal]:
    if not raw.strip():
        return []

    structured = try_extract_structured_report(raw)
    if structured is not None:
        return _from_structured_report(
            structured,
            lane_id=lane_id,
            source=source,
            review_round=review_round,
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        return _from_dict_payload(
            payload,
            lane_id=lane_id,
            default_source=source,
            review_round=review_round,
        )
    if isinstance(payload, list):
        signals: list[HocaReviewSignal] = []
        for idx, item in enumerate(payload, start=1):
            if isinstance(item, dict):
                signals.extend(
                    _from_dict_payload(
                        item,
                        lane_id=lane_id,
                        default_source=source,
                        review_round=review_round,
                    )
                )
            else:
                signals.append(
                    HocaReviewSignal(
                        signal_id=_signal_id(lane_id, source, review_round, idx, None),
                        lane_id=lane_id,
                        source=source,
                        verdict="needs_work",
                        summary=str(item),
                        details=None,
                        review_round=review_round,
                        finding_id=None,
                        finding_severity=None,
                        finding_category=None,
                        finding_file=None,
                        required_fix=None,
                        created_at=_now(),
                    )
                )
        return signals

    if "lgtm" in raw.lower() or "pass" in raw.lower():
        verdict = "pass"
    else:
        verdict = "needs_work"

    return [
        HocaReviewSignal(
            signal_id=_signal_id(lane_id, source, review_round, 1, None),
            lane_id=lane_id,
            source=source,
            verdict=verdict,
            summary=raw.strip()[:160],
            details=raw.strip(),
            review_round=review_round,
            finding_id=None,
            finding_severity=None,
            finding_category=None,
            finding_file=None,
            required_fix=None,
            created_at=_now(),
        )
    ]


def collect_review_signals(
    run_dir: Path,
    lane_id: str,
    review_round: int = 1,
    *,
    review_sources: tuple[ReviewSignalSource, ...] | None = None,
) -> list[HocaReviewSignal]:
    if review_sources is None:
        review_sources = _default_sources(run_dir, review_round) + _configured_fanout_sources(
            run_dir,
            review_round,
        )

    signals: list[HocaReviewSignal] = []
    seen: set[tuple[str, str, str]] = set()

    for item in review_sources:
        if item.command is None:
            if item.path is None:
                continue
            raw = _read_text(item.path)
        else:
            raw = _run_adapter_command(item.command)
        if raw is None:
            continue
        batch = normalize_review_output(raw, lane_id=lane_id, source=item.source, review_round=item.review_round)
        for signal in batch:
            key = (signal.signal_id, signal.summary, signal.verdict)
            if key not in seen:
                seen.add(key)
                signals.append(signal)

    return signals


def aggregate_review_signals(signals: list[HocaReviewSignal]) -> dict[str, list[HocaReviewSignal]]:
    result: dict[str, list[HocaReviewSignal]] = {"pass": [], "needs_work": [], "blocked": []}
    for signal in signals:
        if signal.verdict not in result:
            continue
        result[signal.verdict].append(signal)
    return result
