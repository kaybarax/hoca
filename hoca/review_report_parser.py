"""Parse reviewer output into a structured HOCA review report."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from hoca.contracts import HocaReviewFinding, HocaReviewReport

LEGACY_LGTM_TOKEN = "LGTM"


class ReviewReportParseError(ValueError):
    """Raised when review output cannot be safely parsed."""


@dataclass(frozen=True)
class ReviewReportParseResult:
    report: HocaReviewReport
    source: str


def legacy_text_to_report(
    review_text: str,
    *,
    run_id: str,
    round_number: int,
) -> HocaReviewReport:
    if not review_text.strip():
        raise ReviewReportParseError("Review output is empty.")

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


def _fenced_code_blocks(text: str, languages: tuple[str, ...]) -> list[str]:
    language_pattern = "|".join(re.escape(language) for language in languages)
    blocks: list[str] = []
    for match in re.finditer(
        rf"```(?:{language_pattern})?\s*\n(.*?)\n```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    ):
        block = match.group(1).strip()
        if block:
            blocks.append(block)
    return blocks


def _json_object_candidates(text: str) -> list[str]:
    decoder = json.JSONDecoder()
    candidates: list[str] = []
    idx = 0
    while idx < len(text):
        start = text.find("{", idx)
        if start == -1:
            break
        try:
            _, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            idx = start + 1
            continue
        candidates.append(text[start : start + end])
        idx = start + end
    return candidates


def _openhands_message_text_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for raw in _json_object_candidates(text):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("kind") != "MessageEvent":
            continue
        message = event.get("llm_message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            value = part.get("text")
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
    return candidates


def _dedupe_candidates(candidates: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def _json_candidates(text: str) -> list[str]:
    return _dedupe_candidates(
        [
            *_fenced_code_blocks(text, ("json",)),
            text.strip(),
            *_openhands_message_text_candidates(text),
            *_json_object_candidates(text),
        ]
    )


def _yaml_candidates(text: str) -> list[str]:
    candidates = [*_fenced_code_blocks(text, ("yaml", "yml"))]
    stripped = text.strip()
    if re.search(r"(?m)^\s*verdict\s*:", stripped):
        candidates.append(stripped)
    return _dedupe_candidates(candidates)


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if value in ("", "null", "~"):
        return None
    if value.startswith("[") and value.endswith("]"):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            if value == "[]":
                return []
        else:
            if isinstance(loaded, list):
                return loaded
    if value in ("true", "false"):
        return value == "true"
    if value.isdecimal():
        return int(value)
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in ("'", '"')
    ):
        return value[1:-1]
    return value


def _yaml_key_value(line: str) -> tuple[str, Any]:
    if ":" not in line:
        raise ReviewReportParseError(f"Malformed YAML line: {line!r}")
    key, value = line.split(":", 1)
    return key.strip(), _parse_scalar(value)


def _parse_string_list(lines: list[str], start: int, indent: int) -> tuple[list[str], int]:
    values: list[str] = []
    idx = start
    prefix = " " * indent + "- "
    while idx < len(lines):
        line = lines[idx]
        if not line.strip():
            idx += 1
            continue
        if not line.startswith(prefix):
            break
        values.append(str(_parse_scalar(line[len(prefix) :])))
        idx += 1
    return values, idx


def _parse_findings(lines: list[str], start: int) -> tuple[list[dict[str, Any]], int]:
    findings: list[dict[str, Any]] = []
    idx = start
    while idx < len(lines):
        line = lines[idx]
        if not line.strip():
            idx += 1
            continue
        if not line.startswith("  - "):
            break
        item: dict[str, Any] = {}
        first = line[4:]
        if first:
            key, value = _yaml_key_value(first)
            item[key] = value
        idx += 1
        while idx < len(lines):
            child = lines[idx]
            if not child.strip():
                idx += 1
                continue
            if child.startswith("  - ") or not child.startswith("    "):
                break
            key, value = _yaml_key_value(child[4:])
            item[key] = value
            idx += 1
        findings.append(item)
    return findings, idx


def _parse_pr_notes(lines: list[str], start: int) -> tuple[dict[str, list[str]], int]:
    notes: dict[str, list[str]] = {}
    idx = start
    while idx < len(lines):
        line = lines[idx]
        if not line.strip():
            idx += 1
            continue
        if not line.startswith("  "):
            break
        key, value = _yaml_key_value(line[2:])
        if isinstance(value, list):
            notes[key] = [str(item) for item in value]
            idx += 1
        elif value is None:
            parsed, idx = _parse_string_list(lines, idx + 1, 4)
            notes[key] = parsed
        else:
            notes[key] = [str(value)]
            idx += 1
    return notes, idx


def _simple_yaml_load(raw: str) -> dict[str, Any]:
    lines = [line.rstrip() for line in raw.splitlines()]
    data: dict[str, Any] = {}
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if not line.strip() or line.lstrip().startswith("#"):
            idx += 1
            continue
        if line.startswith(" "):
            raise ReviewReportParseError(f"Unexpected indented YAML line: {line!r}")
        key, value = _yaml_key_value(line)
        idx += 1
        if key == "findings" and value is None:
            data[key], idx = _parse_findings(lines, idx)
        elif key == "pr_notes" and value is None:
            data[key], idx = _parse_pr_notes(lines, idx)
        else:
            data[key] = value
    return data


def _structured_report_from_json(text: str) -> HocaReviewReport | None:
    for candidate in _json_candidates(text):
        try:
            return HocaReviewReport.from_json(candidate)
        except Exception:
            continue
    return None


def _structured_report_from_yaml(text: str) -> HocaReviewReport | None:
    for candidate in _yaml_candidates(text):
        try:
            return HocaReviewReport.from_dict(_simple_yaml_load(candidate))
        except Exception:
            continue
    return None


def try_extract_structured_report(review_text: str) -> HocaReviewReport | None:
    if not review_text.strip():
        return None
    report = _structured_report_from_json(review_text)
    if report is not None:
        return report
    return _structured_report_from_yaml(review_text)


def parse_review_report_text(
    review_text: str,
    *,
    run_id: str,
    round_number: int,
) -> ReviewReportParseResult:
    if not review_text.strip():
        raise ReviewReportParseError("Review output is empty.")
    report = try_extract_structured_report(review_text)
    if report is not None:
        return ReviewReportParseResult(report=report, source="structured")
    return ReviewReportParseResult(
        report=legacy_text_to_report(
            review_text, run_id=run_id, round_number=round_number
        ),
        source="legacy",
    )
