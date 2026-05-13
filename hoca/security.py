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


def is_allowed_repo(repo: str, allowed_repos: str | None) -> bool:
    if not allowed_repos or not allowed_repos.strip():
        return True
    allowed = [r.strip() for r in allowed_repos.split(",") if r.strip()]
    return repo in allowed
