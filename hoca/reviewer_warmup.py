from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from hoca.run_state import write_json_atomic


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _ollama_url(base_url: str) -> str:
    normalized = (base_url or "http://127.0.0.1:11434").rstrip("/")
    if normalized.endswith("/v1"):
        normalized = normalized.removesuffix("/v1")
    return normalized + "/api/generate"


def warm_ollama_model(model: str, base_url: str, timeout_seconds: float) -> dict[str, Any]:
    alias = model.removeprefix("ollama/")
    payload = json.dumps(
        {
            "model": alias,
            "prompt": "",
            "stream": False,
            "keep_alive": os.environ.get("OLLAMA_KEEP_ALIVE", "30m"),
            "options": {"num_predict": 0},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        _ollama_url(base_url),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        response.read()
    return {"attempted": True, "provider": "ollama", "model": alias}


def inspect_sandbox_container(run_dir: Path) -> dict[str, Any]:
    if not _truthy(os.environ.get("HOCA_USE_SANDBOX", "true")):
        return {"attempted": False, "reason": "sandbox disabled"}
    if shutil.which("docker") is None:
        return {"attempted": False, "reason": "docker unavailable"}
    container_name = f"hoca-worker-{run_dir.name}"
    completed = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", container_name],
        check=False,
        capture_output=True,
        text=True,
    )
    running = completed.returncode == 0 and completed.stdout.strip() == "true"
    return {
        "attempted": True,
        "container": container_name,
        "running": running,
        "status": "warm" if running else "not_started",
    }


def warm_reviewer(run_dir: Path, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    started = time.time()
    model = os.environ.get("LLM_MODEL") or os.environ.get("OLLAMA_MODEL", "")
    if model and not model.startswith("ollama/") and "/" not in model:
        model = f"ollama/{model}"
    base_url = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434")
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "completed",
        "model": model,
        "model_warmup": {"attempted": False, "reason": "unsupported model provider"},
        "sandbox": inspect_sandbox_container(run_dir),
    }
    try:
        if model.startswith("ollama/"):
            result["model_warmup"] = warm_ollama_model(model, base_url, timeout_seconds)
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        result["status"] = "failed"
        result["model_warmup"] = {
            "attempted": True,
            "provider": "ollama",
            "error": exc.__class__.__name__,
        }
    result["duration_seconds"] = round(max(0.0, time.time() - started), 6)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Best-effort HOCA reviewer warm-up.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)

    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "reviewer-warmup.json"
    result = warm_reviewer(run_dir, timeout_seconds=args.timeout)
    write_json_atomic(output_path, result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
