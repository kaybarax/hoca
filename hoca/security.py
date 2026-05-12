from __future__ import annotations

from pathlib import Path


SECRET_FILENAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
}

SECRET_SUFFIXES = {
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".kubeconfig",
}


def is_secret_like_path(path: str | Path) -> bool:
    candidate = Path(path)
    name = candidate.name
    if name in SECRET_FILENAMES:
        return True
    if name.startswith(".env.") and name != ".env.example":
        return True
    return any(name.endswith(suffix) for suffix in SECRET_SUFFIXES)
