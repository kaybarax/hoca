from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class RiskCategory(str, Enum):
    INFRASTRUCTURE = "infrastructure"
    MIGRATION = "migration"
    GENERATED = "generated"
    DEPENDENCY_LOCKFILE = "dependency_lockfile"
    BROAD_REWRITE = "broad_rewrite"


JUSTIFICATION_REQUIRED_CATEGORIES: frozenset[RiskCategory] = frozenset(
    {
        RiskCategory.INFRASTRUCTURE,
        RiskCategory.MIGRATION,
        RiskCategory.GENERATED,
        RiskCategory.DEPENDENCY_LOCKFILE,
        RiskCategory.BROAD_REWRITE,
    }
)


@dataclass(frozen=True)
class RiskClassification:
    level: RiskLevel
    reason: str
    auto_mergeable: bool
    categories: tuple[str, ...] = ()
    requires_justification: bool = False
    paths_requiring_justification: tuple[str, ...] = ()


_GENERATED_SUFFIXES = (".min.js", ".min.css")
_GENERATED_SUBSTRINGS = (".generated.", ".gen.", "/generated/", "/__generated__/", ".egg-info/")
_DEPENDENCY_LOCKFILES = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pipfile.lock",
        "uv.lock",
        "cargo.lock",
        "gemfile.lock",
        "composer.lock",
    }
)

_BROAD_REWRITE_SOURCE_FILE_THRESHOLD = 8
_BROAD_REWRITE_DIFF_LINE_THRESHOLD = 500
_BROAD_REWRITE_DESCRIPTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brewrite\b", re.IGNORECASE),
    re.compile(r"\brefactor\s+(the\s+)?(entire|whole|full|complete)\b", re.IGNORECASE),
    re.compile(r"\breplace\s+all\b", re.IGNORECASE),
    re.compile(r"\boverhaul\b", re.IGNORECASE),
]

_HIGH_RISK_PATH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(^|/)auth/", re.IGNORECASE),
    re.compile(r"authenticat|authoriz|login|oauth|session|jwt", re.IGNORECASE),
    re.compile(r"(^|/)security/", re.IGNORECASE),
    re.compile(r"encrypt|decrypt|crypto|tls-|ssl-|(^|/)certs/", re.IGNORECASE),
    re.compile(r"(^|/)payments?/|(^|/)billing/|stripe|invoice|subscription", re.IGNORECASE),
    re.compile(r"(^|/)permissions?/|(^|/)acl/|(^|/)rbac/|access.control", re.IGNORECASE),
    re.compile(r"migrat|db/schema|alembic|flyway|liquibase", re.IGNORECASE),
    re.compile(r"destroy|teardown|drop-|nuke|purge|wipe", re.IGNORECASE),
    re.compile(r"(^|/)infra/|(^|/)terraform/|\.tf$|(^|/)k8s/|(^|/)kubernetes/", re.IGNORECASE),
    re.compile(r"deploy|pipeline|(^|/)ci/|(^|/)cd/", re.IGNORECASE),
    re.compile(r"\.github/workflows/", re.IGNORECASE),
    re.compile(r"Dockerfile|docker-compose", re.IGNORECASE),
]

_HIGH_RISK_DEPENDENCY_FILES: frozenset[str] = frozenset(
    p.lower()
    for p in [
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "go.sum",
        "go.mod",
        "requirements.txt",
        "requirements-dev.txt",
        "Pipfile.lock",
        "Gemfile.lock",
        "Cargo.lock",
        "composer.lock",
        "poetry.lock",
    ]
)

_LOW_RISK_EXTENSIONS: frozenset[str] = frozenset({
    ".md",
    ".txt",
    ".rst",
    ".adoc",
})

_LOW_RISK_FILENAMES: frozenset[str] = frozenset(
    f.lower()
    for f in [
        "README.md",
        "README.rst",
        "README.txt",
        "README",
        "CHANGELOG.md",
        "CHANGES.md",
        "LICENSE",
        "LICENSE.md",
        "CONTRIBUTING.md",
        "AUTHORS",
        "AUTHORS.md",
        ".gitignore",
        ".editorconfig",
    ]
)

_HIGH_RISK_KEYWORDS: list[re.Pattern[str]] = [
    re.compile(r"\bauth\b", re.IGNORECASE),
    re.compile(r"\bpayment", re.IGNORECASE),
    re.compile(r"\bbilling\b", re.IGNORECASE),
    re.compile(r"\bmigrat", re.IGNORECASE),
    re.compile(r"\bsecret", re.IGNORECASE),
    re.compile(r"\bencrypt", re.IGNORECASE),
    re.compile(r"\bpermission", re.IGNORECASE),
    re.compile(r"\binfrastructure\b", re.IGNORECASE),
    re.compile(r"\bdeploy", re.IGNORECASE),
    re.compile(r"\bdelet(e|ion)\b", re.IGNORECASE),
]


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def justified_files_from_run_dir(run_dir: Path) -> set[str]:
    justified: set[str] = set()
    justification_path = run_dir / "staging-justification.txt"
    if not justification_path.is_file():
        return justified
    for line in justification_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped:
            file_part = stripped.split(":", 1)[0].strip()
            if file_part:
                justified.add(file_part)
        else:
            justified.add(stripped)
    return justified


def is_generated_file(path: str) -> bool:
    lower = _normalize_path(path).lower()
    if any(lower.endswith(suffix) for suffix in _GENERATED_SUFFIXES):
        return True
    return any(marker in lower for marker in _GENERATED_SUBSTRINGS)


def is_dependency_lockfile(path: str) -> bool:
    return Path(_normalize_path(path)).name.lower() in _DEPENDENCY_LOCKFILES


def is_migration_file(path: str) -> bool:
    lower = _normalize_path(path).lower()
    return (
        lower.startswith("migrations/")
        or "/migrations/" in lower
        or lower.startswith("db/migrate/")
        or "/db/migrate/" in lower
        or lower.startswith("alembic/")
        or "/alembic/" in lower
        or "/flyway/" in lower
        or "/liquibase/" in lower
    )


def is_infrastructure_file(path: str) -> bool:
    lower = _normalize_path(path).lower()
    if lower.startswith(".github/workflows/"):
        return True
    name = Path(lower).name
    if name in {"dockerfile", "vercel.json"}:
        return True
    if name.startswith("docker-compose") and name.endswith((".yml", ".yaml")):
        return True
    return any(
        lower.startswith(prefix) or f"/{prefix}/" in lower
        for prefix in ("terraform/", "k8s/", "kubernetes/", "charts/", "helm/")
    ) or lower.endswith(".tf")


def _is_test_only_path(path: str) -> bool:
    lower = path.lower()
    return (
        lower.startswith("tests/")
        or lower.startswith("test/")
        or "/tests/" in lower
        or "/test/" in lower
        or Path(lower).name.startswith("test_")
        or Path(lower).name.endswith("_test.py")
        or Path(lower).name.endswith(".test.ts")
        or Path(lower).name.endswith(".test.js")
        or Path(lower).name.endswith(".spec.ts")
        or Path(lower).name.endswith(".spec.js")
    )


def _is_doc_only_path(path: str) -> bool:
    p = Path(path)
    lower_name = p.name.lower()
    if lower_name in _LOW_RISK_FILENAMES:
        return True
    return p.suffix.lower() in _LOW_RISK_EXTENSIONS


def _is_example_path(path: str) -> bool:
    lower = path.lower()
    return "example" in lower or "sample" in lower or lower.startswith("examples/")


def _is_source_path(path: str) -> bool:
    return not _is_doc_only_path(path) and not _is_test_only_path(path) and not _is_example_path(path)


def _path_risk_categories(path: str) -> tuple[RiskCategory, ...]:
    categories: list[RiskCategory] = []
    if is_infrastructure_file(path):
        categories.append(RiskCategory.INFRASTRUCTURE)
    if is_migration_file(path):
        categories.append(RiskCategory.MIGRATION)
    if is_generated_file(path):
        categories.append(RiskCategory.GENERATED)
    if is_dependency_lockfile(path):
        categories.append(RiskCategory.DEPENDENCY_LOCKFILE)
    return tuple(categories)


def _is_broad_rewrite(
    *,
    description: str = "",
    changed_paths: list[str] | None = None,
    run_dir: Path | None = None,
) -> bool:
    if description.strip() and any(
        pattern.search(description) for pattern in _BROAD_REWRITE_DESCRIPTION_PATTERNS
    ):
        return True
    source_paths = [path for path in (changed_paths or []) if _is_source_path(path)]
    if len(source_paths) >= _BROAD_REWRITE_SOURCE_FILE_THRESHOLD:
        return True
    if run_dir is not None:
        diff_path = run_dir / "git-diff.patch"
        if diff_path.is_file():
            line_count = len(diff_path.read_text(encoding="utf-8", errors="replace").splitlines())
            if line_count >= _BROAD_REWRITE_DIFF_LINE_THRESHOLD:
                return True
    return False


def _broad_rewrite_justified(run_dir: Path | None, justified_paths: set[str]) -> bool:
    if run_dir is not None:
        justification_path = run_dir / "staging-justification.txt"
        if justification_path.is_file():
            content = justification_path.read_text(encoding="utf-8").lower()
            if "broad rewrite" in content or "broad-rewrite" in content:
                return True
    return "broad rewrite" in {line.lower() for line in justified_paths}


def _path_matches_high_risk(path: str) -> str | None:
    lower_name = Path(path).name.lower()
    if lower_name in _HIGH_RISK_DEPENDENCY_FILES:
        return "dependency file"
    for pattern in _HIGH_RISK_PATH_PATTERNS:
        if pattern.search(path):
            return f"matches high-risk pattern: {pattern.pattern}"
    return None


def classify_path_categories(path: str) -> tuple[str, ...]:
    return tuple(category.value for category in _path_risk_categories(path))


def classify_risk_categories(
    *,
    description: str = "",
    changed_paths: list[str] | None = None,
    run_dir: Path | None = None,
    justified_paths: set[str] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    justified = set(justified_paths or [])
    if run_dir is not None:
        justified |= justified_files_from_run_dir(run_dir)

    categories: set[str] = set()
    paths_requiring_justification: list[str] = []

    for path in changed_paths or []:
        for category in _path_risk_categories(path):
            categories.add(category.value)
            if category in JUSTIFICATION_REQUIRED_CATEGORIES and path not in justified:
                if path not in paths_requiring_justification:
                    paths_requiring_justification.append(path)

    if _is_broad_rewrite(
        description=description,
        changed_paths=changed_paths,
        run_dir=run_dir,
    ):
        categories.add(RiskCategory.BROAD_REWRITE.value)
        if not _broad_rewrite_justified(run_dir, justified):
            for path in changed_paths or []:
                if _is_source_path(path) and path not in justified:
                    if path not in paths_requiring_justification:
                        paths_requiring_justification.append(path)
            if not paths_requiring_justification:
                paths_requiring_justification.append("(task-level broad rewrite)")

    requires_justification = bool(paths_requiring_justification)
    return tuple(sorted(categories)), tuple(paths_requiring_justification), requires_justification


def classify_paths(paths: list[str]) -> RiskLevel:
    if not paths:
        return RiskLevel.UNKNOWN

    has_high = False
    has_source = False

    for path in paths:
        high_reason = _path_matches_high_risk(path)
        if high_reason:
            has_high = True
            break
        if _is_source_path(path):
            has_source = True

    if has_high:
        return RiskLevel.HIGH
    if has_source:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def classify_description(description: str) -> RiskLevel:
    if not description.strip():
        return RiskLevel.UNKNOWN
    for pattern in _HIGH_RISK_KEYWORDS:
        if pattern.search(description):
            return RiskLevel.HIGH
    return RiskLevel.UNKNOWN


def format_risk_notes(classification: RiskClassification) -> str:
    lines = [f"Risk level: {classification.level.value}."]
    if classification.categories:
        lines.append(f"Categories: {', '.join(classification.categories)}.")
    if classification.reason:
        lines.append(classification.reason)
    if classification.requires_justification:
        if classification.paths_requiring_justification:
            shown = ", ".join(classification.paths_requiring_justification[:5])
            if len(classification.paths_requiring_justification) > 5:
                shown = f"{shown}, ..."
            lines.append(f"Requires staging justification for: {shown}.")
        else:
            lines.append("Requires staging justification before merge.")
    if not classification.auto_mergeable:
        lines.append("Auto-merge disabled by risk policy.")
    return "\n".join(lines) + "\n"


def classify_task(
    *,
    description: str = "",
    changed_paths: list[str] | None = None,
    run_dir: Path | None = None,
) -> RiskClassification:
    if not description.strip() and not changed_paths:
        return RiskClassification(
            level=RiskLevel.UNKNOWN,
            reason="No description or file paths provided.",
            auto_mergeable=False,
        )

    desc_risk = classify_description(description) if description.strip() else RiskLevel.UNKNOWN
    path_risk = classify_paths(changed_paths) if changed_paths else RiskLevel.UNKNOWN

    level = _combine_levels(desc_risk, path_risk)
    categories, paths_requiring_justification, requires_justification = classify_risk_categories(
        description=description,
        changed_paths=changed_paths,
        run_dir=run_dir,
    )

    reason = _build_reason(
        level,
        desc_risk,
        path_risk,
        changed_paths or [],
        categories,
    )
    auto_mergeable = _is_auto_mergeable(level, requires_justification)

    return RiskClassification(
        level=level,
        reason=reason,
        auto_mergeable=auto_mergeable,
        categories=categories,
        requires_justification=requires_justification,
        paths_requiring_justification=paths_requiring_justification,
    )


def write_risk_artifacts(
    run_dir: Path,
    *,
    changed_paths: list[str],
    description: str = "",
) -> RiskClassification:
    classification = classify_task(
        description=description,
        changed_paths=changed_paths,
        run_dir=run_dir,
    )
    (run_dir / "risk-level.txt").write_text(f"{classification.level.value}\n", encoding="utf-8")

    generated_notes = format_risk_notes(classification).strip()
    notes_path = run_dir / "risk-notes.txt"
    existing = notes_path.read_text(encoding="utf-8").strip() if notes_path.is_file() else ""
    if existing and generated_notes not in existing:
        notes_path.write_text(f"{generated_notes}\n\n{existing}\n", encoding="utf-8")
    else:
        notes_path.write_text(f"{generated_notes}\n", encoding="utf-8")
    return classification


def _combine_levels(desc_risk: RiskLevel, path_risk: RiskLevel) -> RiskLevel:
    # UNKNOWN means "not enough info" — it should not override a concrete assessment.
    if desc_risk == RiskLevel.UNKNOWN and path_risk != RiskLevel.UNKNOWN:
        return path_risk
    if path_risk == RiskLevel.UNKNOWN and desc_risk != RiskLevel.UNKNOWN:
        return desc_risk
    priority = {RiskLevel.HIGH: 3, RiskLevel.MEDIUM: 2, RiskLevel.UNKNOWN: 1, RiskLevel.LOW: 0}
    if priority[desc_risk] >= priority[path_risk]:
        return desc_risk
    return path_risk


def _is_auto_mergeable(level: RiskLevel, requires_justification: bool = False) -> bool:
    if requires_justification:
        return False
    if level == RiskLevel.HIGH:
        return False
    if level == RiskLevel.UNKNOWN:
        return False
    if level == RiskLevel.MEDIUM:
        return False
    return True


def _build_reason(
    level: RiskLevel,
    desc_risk: RiskLevel,
    path_risk: RiskLevel,
    paths: list[str],
    categories: tuple[str, ...],
) -> str:
    parts: list[str] = []
    if level == RiskLevel.LOW:
        parts.append("All changed files are documentation, tests, or examples.")
    elif level == RiskLevel.HIGH:
        if desc_risk == RiskLevel.HIGH:
            parts.append("Task description references high-risk domain.")
        if path_risk == RiskLevel.HIGH:
            high_paths = [p for p in paths if _path_matches_high_risk(p)]
            if high_paths:
                parts.append(f"High-risk paths: {', '.join(high_paths[:3])}")
    elif level == RiskLevel.MEDIUM:
        parts.append("Source code changes outside high-risk domains.")
    elif level == RiskLevel.UNKNOWN:
        parts.append("Insufficient information to determine risk.")
    if categories:
        parts.append(f"Detected categories: {', '.join(categories)}.")
    return " ".join(parts) if parts else f"Classified as {level.value}."
