"""Deterministic definition-of-ready checks for HOCA manager intake."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from hoca.risk import RiskLevel, classify_task
from hoca.run_state import write_json_atomic

DorDisposition = Literal["block", "escalate", "warn"]


class DorOutcome(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"
    NEEDS_CLARIFICATION = "needs_clarification"


@dataclass(frozen=True)
class DorCheck:
    id: str
    disposition: DorDisposition
    summary: str


@dataclass(frozen=True)
class DorResult:
    outcome: DorOutcome
    checks: tuple[DorCheck, ...]
    risk_level: str

    @property
    def ready(self) -> bool:
        return self.outcome == DorOutcome.READY

    @property
    def blocked(self) -> bool:
        return self.outcome == DorOutcome.BLOCKED

    @property
    def needs_clarification(self) -> bool:
        return self.outcome == DorOutcome.NEEDS_CLARIFICATION


_BROAD_TASK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bfix\s+(everything|all(\s+the)?\s+(bugs|issues|problems|tests)?)\b", re.I),
    re.compile(r"\brefactor\s+(everything|the\s+entire|all(\s+of)?)\b", re.I),
    re.compile(r"\brewrite\s+(everything|the\s+entire|all(\s+of)?)\b", re.I),
    re.compile(r"\b(entire|whole)\s+(codebase|project|repo(sitory)?|application)\b", re.I),
    re.compile(r"\b(clean\s*up|update|improve|modernize)\s+everything\b", re.I),
    re.compile(r"\ball\s+files\b", re.I),
]

_DANGEROUS_REQUEST_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+(-\w*[rR]\w*\s+.*-\w*f|.*-\w*f\w*\s+.*-\w*[rR]|-rf|-Rf)\b"),
    re.compile(r"\bsudo\s+rm\b"),
    re.compile(r"\bchmod\s+(-R\s+)?777\b"),
    re.compile(r"\bchown\s+-R\b"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    re.compile(r"\bgit\s+clean\s+(-\w*f)"),
    re.compile(r"\bgit\s+push\s+(--force|-f)\b"),
    re.compile(r"\bgit\s+push\s+(?:origin\s+)?(?:main|master)\b"),
    re.compile(r"\bpush\s+direct(?:ly)?\s+to\s+(?:main|master)\b", re.I),
    re.compile(r"\bforce\s+push\b", re.I),
    re.compile(r"\bgit\s+merge\s+(?:main|master|origin/(?:main|master))\b"),
    re.compile(r"\bgh\s+pr\s+merge\b"),
    re.compile(r"\bdocker\s+system\s+prune\b"),
    re.compile(r"\bbrew\s+uninstall\b"),
    re.compile(r"\b(delete|drop|wipe|purge)\s+(all|every|entire)\b", re.I),
    re.compile(
        r"\b(expose|commit|print|log)\s+(the\s+)?(secrets?|credentials?|api\s*keys?)\b", re.I
    ),
    re.compile(
        r"\b(?:edit|modify|change|write|create|add|update|commit)\b.*(?:^|[\s`'\"/])\.env(?!\.example)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:add|write|commit|store|save)\b.*\b(?:GITHUB_TOKEN|GH_TOKEN|API_KEY|SECRET|TOKEN)\b",
        re.I,
    ),
    re.compile(r"\bdisable\s+(all\s+)?(auth|authentication|authorization|security)\b", re.I),
]

_UNDERSPECIFIED_PRODUCTION_INFRA_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:deploy|provision|configure|set\s+up|create|change|update)\b.*\b(?:prod|production)\b.*\b(?:infra|infrastructure|terraform|k8s|kubernetes|cluster|pipeline|deployment)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:prod|production)\b.*\b(?:infra|infrastructure|terraform|k8s|kubernetes|cluster|pipeline|deployment)\b",
        re.I,
    ),
]

_VAGUE_TASK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^fix( it| this| bug| bugs| issue| issues)?\.?$", re.I),
    re.compile(r"^update( it| this)?\.?$", re.I),
    re.compile(r"^make it work\.?$", re.I),
    re.compile(r"^do the thing\.?$", re.I),
    re.compile(r"^help\.?$", re.I),
    re.compile(r"^please fix\.?$", re.I),
    re.compile(r"^something is broken\.?$", re.I),
]

_PATH_OR_TARGET_PATTERN = re.compile(
    r"(?:#?\d+|/\S+|`\S+`|'\S+'|\.\S+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|md|yaml|yml|json|toml)|"
    r"\b(?:README|API|endpoint|component|module|function|class|test|schema|migration)\b)",
    re.I,
)


def _normalize_task(task: str) -> str:
    return " ".join(task.strip().split())


def _matches_any(text: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _has_concrete_target(task: str) -> bool:
    return bool(_PATH_OR_TARGET_PATTERN.search(task))


def _issue_referenced(task: str, issue_id: str | None) -> bool:
    if not issue_id:
        return True
    normalized = task.lower()
    issue = issue_id.strip()
    if not issue:
        return False
    if issue in normalized:
        return True
    if f"#{issue}" in normalized:
        return True
    if re.search(rf"\bissue\s+#?{re.escape(issue)}\b", normalized):
        return True
    return False


def _repo_path_error(repo_path: str | Path | None) -> str | None:
    if repo_path is None:
        return "Repository path is required."
    raw = str(repo_path).strip()
    if not raw:
        return "Repository path is required."
    path = Path(raw)
    if not path.exists():
        return f"Repository path does not exist: {raw}"
    if not path.is_dir():
        return f"Repository path is not a directory: {raw}"
    return None


def evaluate_definition_of_ready(
    *,
    repo_path: str | Path | None,
    task: str,
    issue_id: str | None = None,
) -> DorResult:
    checks: list[DorCheck] = []
    normalized_task = _normalize_task(task)

    repo_error = _repo_path_error(repo_path)
    if repo_error:
        checks.append(
            DorCheck(
                id="missing_repo_path",
                disposition="block",
                summary=repo_error,
            )
        )

    if not normalized_task:
        checks.append(
            DorCheck(
                id="empty_task",
                disposition="block",
                summary="Task text must not be empty.",
            )
        )

    if normalized_task and _matches_any(normalized_task, _DANGEROUS_REQUEST_PATTERNS):
        checks.append(
            DorCheck(
                id="dangerous_request",
                disposition="block",
                summary=(
                    "Task requests dangerous or policy-violating actions "
                    "(for example force push, destructive cleanup, or secret exposure)."
                ),
            )
        )

    if normalized_task and _matches_any(normalized_task, _BROAD_TASK_PATTERNS):
        checks.append(
            DorCheck(
                id="broad_task_wording",
                disposition="escalate",
                summary=(
                    "Task wording is too broad. Provide a concrete goal, expected areas, "
                    "and acceptance criteria."
                ),
            )
        )

    if (
        normalized_task
        and _matches_any(normalized_task, _UNDERSPECIFIED_PRODUCTION_INFRA_PATTERNS)
        and not _has_concrete_target(normalized_task)
    ):
        checks.append(
            DorCheck(
                id="underspecified_production_infrastructure",
                disposition="escalate",
                summary=(
                    "Production infrastructure work needs an explicit target, context, "
                    "rollback expectations, and validation plan before delegation."
                ),
            )
        )

    if issue_id and normalized_task and not _issue_referenced(normalized_task, issue_id):
        checks.append(
            DorCheck(
                id="missing_issue_context",
                disposition="escalate",
                summary=(
                    f"Issue id {issue_id} was supplied but the task does not describe "
                    "what to fix for that issue."
                ),
            )
        )

    if normalized_task:
        is_vague = (
            len(normalized_task) < 16 and not _has_concrete_target(normalized_task)
        ) or _matches_any(normalized_task, _VAGUE_TASK_PATTERNS)
        if is_vague:
            checks.append(
                DorCheck(
                    id="material_ambiguity",
                    disposition="escalate",
                    summary=(
                        "Task is materially ambiguous. Clarify the goal, affected areas, "
                        "and expected outcome before delegating to a worker."
                    ),
                )
            )

    risk_level = "unknown"
    if normalized_task:
        classification = classify_task(description=normalized_task)
        risk_level = classification.level.value
        if classification.level == RiskLevel.HIGH:
            checks.append(
                DorCheck(
                    id="high_risk_area",
                    disposition="warn",
                    summary=classification.reason or "Task references a high-risk domain.",
                )
            )

    if any(check.disposition == "block" for check in checks):
        outcome = DorOutcome.BLOCKED
    elif any(check.disposition == "escalate" for check in checks):
        outcome = DorOutcome.NEEDS_CLARIFICATION
    else:
        outcome = DorOutcome.READY

    return DorResult(outcome=outcome, checks=tuple(checks), risk_level=risk_level)


def result_to_dict(result: DorResult) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "outcome": result.outcome.value,
        "ready": result.ready,
        "blocked": result.blocked,
        "needs_clarification": result.needs_clarification,
        "risk_level": result.risk_level,
        "checks": [
            {
                "id": check.id,
                "disposition": check.disposition,
                "summary": check.summary,
            }
            for check in result.checks
        ],
    }


def write_dor_artifact(run_dir: Path, result: DorResult) -> Path:
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "definition-of-ready.json"
    write_json_atomic(path, result_to_dict(result))
    return path


def format_user_message(result: DorResult) -> str:
    if result.ready:
        return "Task is definition-ready."

    lines: list[str] = []
    if result.blocked:
        lines.append("Task is blocked by definition-of-ready checks:")
    else:
        lines.append("Task needs clarification before HOCA can proceed:")

    for check in result.checks:
        if check.disposition in {"block", "escalate"}:
            lines.append(f"- [{check.id}] {check.summary}")

    if result.needs_clarification:
        lines.append(
            "Provide a clearer task description with a concrete goal, expected areas, "
            "and acceptance criteria, then re-run the task."
        )

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate whether a HOCA task is definition-ready."
    )
    parser.add_argument("repo_path", nargs="?", default="", help="Target repository path")
    parser.add_argument("task", nargs="?", default="", help="Raw human task text")
    parser.add_argument("--issue-id", default="")
    parser.add_argument(
        "--run-dir",
        default="",
        help="Optional run directory for writing definition-of-ready.json",
    )

    args = parser.parse_args(argv)
    repo_path = args.repo_path or None
    issue_id = args.issue_id or None

    result = evaluate_definition_of_ready(
        repo_path=repo_path,
        task=args.task,
        issue_id=issue_id,
    )

    if args.run_dir:
        write_dor_artifact(Path(args.run_dir), result)

    print(format_user_message(result))
    if result.ready:
        return 0
    if result.blocked:
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
