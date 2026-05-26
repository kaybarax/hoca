"""Environment variable allowlists for worker, reviewer, and manager PR phases.

Worker and reviewer sandboxes receive only the variables listed here.
Anything not on the allowlist is stripped before the subprocess starts.
"""

from __future__ import annotations

import os
import re
from typing import Literal

PhaseRole = Literal["worker", "reviewer", "manager-pr"]

_SECRET_PATTERN = re.compile(
    r"(?i)(token|secret|password|api_key|private_key|credential)"
)

WORKER_REVIEWER_ALLOWLIST: frozenset[str] = frozenset(
    {
        "LLM_MODEL",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OLLAMA_MODEL",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "TOGETHER_API_KEY",
        "XAI_API_KEY",
        "OPENHANDS_SUPPRESS_BANNER",
        "HOME",
        "CI",
        "PATH",
        "LANG",
        "LC_ALL",
        "TERM",
        "TMPDIR",
        "TMP",
        "TEMP",
        "USER",
        "LOGNAME",
        "SHELL",
        "PYTHONPATH",
        "HOCA_AGENT_ROLE",
        "HOCA_SELECTED_MODEL_SLOT",
        "HOCA_REQUESTED_MODEL",
        "HOCA_HERMES_TIMEOUT",
        "HOCA_HERMES_MAX_TURNS",
        "HOCA_OPENHANDS_TIMEOUT",
        "HOCA_OPENHANDS_STALL",
        "HOCA_REVIEW_ROUND",
        "HOCA_REVIEW_REPORT_PATH",
        "HOCA_SKIP_ROLE_MODEL_RESOLUTION",
        "HERMES_ACCEPT_HOOKS",
        "HERMES_HOME",
        "HOCA_ROOT",
        "HOCA_USE_SANDBOX",
        "HOCA_NETWORK_MODE",
    }
)

_WORKER_REVIEWER_PREFIXES: tuple[str, ...] = ("HERMES_",)

MANAGER_PR_ALLOWLIST: frozenset[str] = frozenset(
    {
        "GITHUB_TOKEN",
        "GITHUB_REPOSITORY",
        "GH_TOKEN",
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TERM",
        "TMPDIR",
        "TMP",
        "TEMP",
        "USER",
        "LOGNAME",
        "SHELL",
        "PYTHONPATH",
        "CI",
        "HOCA_AGENT_ROLE",
        "HOCA_ROOT",
    }
)

_PHASE_ALLOWLISTS: dict[PhaseRole, frozenset[str]] = {
    "worker": WORKER_REVIEWER_ALLOWLIST,
    "reviewer": WORKER_REVIEWER_ALLOWLIST,
    "manager-pr": MANAGER_PR_ALLOWLIST,
}

_PHASE_PREFIXES: dict[PhaseRole, tuple[str, ...]] = {
    "worker": _WORKER_REVIEWER_PREFIXES,
    "reviewer": _WORKER_REVIEWER_PREFIXES,
    "manager-pr": (),
}


def allowlist_for_phase(phase: PhaseRole) -> frozenset[str]:
    return _PHASE_ALLOWLISTS[phase]


def _key_allowed(
    key: str,
    allowed: frozenset[str],
    prefixes: tuple[str, ...],
) -> bool:
    if key in allowed:
        return True
    return any(key.startswith(prefix) for prefix in prefixes)


def filter_env(
    env: dict[str, str],
    phase: PhaseRole,
    *,
    extra_allow: frozenset[str] | None = None,
) -> dict[str, str]:
    """Return a copy of *env* containing only allowlisted keys for *phase*."""
    allowed = _PHASE_ALLOWLISTS[phase]
    if extra_allow:
        allowed = allowed | extra_allow
    prefixes = _PHASE_PREFIXES.get(phase, ())
    return {
        key: value
        for key, value in env.items()
        if _key_allowed(key, allowed, prefixes)
    }


def filter_env_for_role(
    env: dict[str, str] | None = None,
    *,
    phase: PhaseRole,
    extra_allow: frozenset[str] | None = None,
) -> dict[str, str]:
    """Convenience wrapper: default to ``os.environ``, filter, return copy."""
    source = dict(env) if env is not None else dict(os.environ)
    return filter_env(source, phase, extra_allow=extra_allow)


def redact_env_for_logging(env: dict[str, str]) -> dict[str, str]:
    """Return a copy with secret-like values replaced by ``***``."""
    out: dict[str, str] = {}
    for key, value in env.items():
        if _SECRET_PATTERN.search(key):
            out[key] = "***" if value else "(unset)"
        else:
            out[key] = value
    return out


def blocked_keys(env: dict[str, str], phase: PhaseRole) -> list[str]:
    """Return sorted list of keys in *env* that would be stripped for *phase*."""
    allowed = _PHASE_ALLOWLISTS[phase]
    prefixes = _PHASE_PREFIXES.get(phase, ())
    return sorted(key for key in env if not _key_allowed(key, allowed, prefixes))
