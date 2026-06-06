from __future__ import annotations

import re

ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![\w.-])/(?:Users|home|Volumes|private|tmp)/[^\s;,)]+")
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
TOKEN_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret)\s*=\s*([^\s;]+)"
)


def redact_public_evidence_text(text: str) -> str:
    redacted = ABSOLUTE_PATH_PATTERN.sub("<LOCAL_PATH>", text)
    redacted = EMAIL_PATTERN.sub("<EMAIL>", redacted)
    redacted = TOKEN_ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}=<REDACTED>", redacted)
    return redacted


def redact_public_evidence_lines(lines: list[str]) -> list[str]:
    return [redact_public_evidence_text(line) for line in lines]
