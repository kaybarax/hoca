from __future__ import annotations

import hashlib
import hmac as _hmac
import time
from pathlib import Path

WEBHOOK_TIMESTAMP_MAX_AGE = 300


FORBIDDEN_SECRET_FILENAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials",
    "application_default_credentials.json",
    "cookies",
    "cookies.sqlite",
    "id_rsa",
    "id_ed25519",
}

FORBIDDEN_SECRET_SUFFIXES = {
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".kubeconfig",
}

FORBIDDEN_SECRET_PATHS = {
    ".aws/credentials",
    ".config/gcloud/application_default_credentials.json",
    ".docker/config.json",
    ".github/secrets",
    ".gnupg",
    ".ssh",
    ".azure",
}

CREDENTIAL_STORE_DIRECTORIES = {
    ".aws",
    ".azure",
    ".docker",
    ".gnupg",
    ".ssh",
    "keychains",
}

BROWSER_COOKIE_DIRECTORIES = {
    "cookies",
    "firefox/profiles",
    "google/chrome/default",
    "brave-browser/default",
    "microsoft edge/default",
}


def _as_posix_path(path: str | Path) -> str:
    return Path(path).as_posix().strip("/")


def is_secret_like_path(path: str | Path) -> bool:
    candidate_path = _as_posix_path(path)
    candidate = Path(candidate_path)
    name = candidate.name
    lower_path = candidate_path.lower()
    lower_name = name.lower()
    lower_parts = [part.lower() for part in candidate.parts]

    if lower_name in FORBIDDEN_SECRET_FILENAMES:
        return True
    if lower_name.startswith(".env.") and lower_name != ".env.example":
        return True
    if any(lower_name.endswith(suffix) for suffix in FORBIDDEN_SECRET_SUFFIXES):
        return True
    if lower_path in FORBIDDEN_SECRET_PATHS:
        return True
    if any(lower_path == secret_path or lower_path.startswith(f"{secret_path}/") for secret_path in FORBIDDEN_SECRET_PATHS):
        return True
    if any(part in CREDENTIAL_STORE_DIRECTORIES for part in lower_parts):
        return True
    return any(cookie_path in lower_path for cookie_path in BROWSER_COOKIE_DIRECTORIES)


def verify_hmac_signature(
    secret: str, raw_body: bytes, signature_header: str | None
) -> bool:
    if not secret:
        return False
    if not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    received = signature_header[len("sha256="):]
    expected = _hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return _hmac.compare_digest(expected, received)


def verify_timestamp(timestamp: str | None) -> bool:
    if not timestamp:
        return False
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False
    return abs(time.time() - ts) <= WEBHOOK_TIMESTAMP_MAX_AGE


RUNTIME_DIRECTORY_PREFIX = ".hoca-runtime"

LOCK_SUFFIXES = {".lock", ".lck"}


def reject_path_traversal(path: str | Path) -> str | None:
    p = str(path)
    if Path(p).is_absolute():
        return "absolute paths are not allowed"
    if ".." in Path(p).parts:
        return "path traversal is not allowed"
    return None


def is_path_inside_repo(repo_root: str | Path, candidate_path: str | Path) -> bool:
    root = Path(repo_root).resolve()
    candidate = (root / candidate_path).resolve()
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _is_runtime_path(path: str | Path) -> bool:
    return any(
        part == RUNTIME_DIRECTORY_PREFIX for part in Path(path).parts
    )


def _is_lock_file(path: str | Path) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in LOCK_SUFFIXES


def validate_staging_file_list(
    repo_root: str | Path, files: list[str],
) -> list[str]:
    errors: list[str] = []
    for f in files:
        traversal_error = reject_path_traversal(f)
        if traversal_error:
            errors.append(f"{f}: {traversal_error}")
            continue
        if not is_path_inside_repo(repo_root, f):
            errors.append(f"{f}: file is outside the repository")
            continue
        if is_secret_like_path(f):
            errors.append(f"{f}: secret-like file")
            continue
        if _is_runtime_path(f):
            errors.append(f"{f}: runtime file")
            continue
        if _is_lock_file(f):
            errors.append(f"{f}: lock file")
            continue
    return errors


def is_allowed_repo(repo: str, allowed_repos: str | None) -> bool:
    if not allowed_repos or not allowed_repos.strip():
        return True
    allowed = [r.strip() for r in allowed_repos.split(",") if r.strip()]
    return repo in allowed
