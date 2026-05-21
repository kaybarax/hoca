#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values
from flask import Flask, abort, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hoca.run_state import acquire_lock, is_duplicate_issue_run
from hoca.security import is_allowed_repo, verify_hmac_signature, verify_timestamp

DOTENV = {key: value for key, value in dotenv_values().items() if value is not None}


def config_value(name: str, default: str = "") -> str:
    return os.environ.get(name, DOTENV.get(name, default))

app = Flask(__name__)

WEBHOOK_SECRET = config_value("HOCA_WEBHOOK_SECRET")
ALLOWED_REPOS = config_value("HOCA_ALLOWED_REPOS")
HOCA_WORKSPACE_ROOT = Path(config_value("HOCA_WORKSPACE_ROOT", str(Path.home()))).expanduser()
MAX_CONTENT_LENGTH = int(config_value("HOCA_MAX_WEBHOOK_BYTES", "65536"))

app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH


def validate_payload(data: dict) -> dict:
    required = ["issue_title", "issue_number", "repo"]
    for key in required:
        if key not in data:
            abort(400, f"Missing required payload field: {key}")

    issue_title = str(data["issue_title"]).strip()
    issue_number = str(data["issue_number"]).strip()
    repo = str(data["repo"]).strip()

    if not issue_title:
        abort(400, "Empty issue_title")
    if not issue_number.isdigit():
        abort(400, "issue_number must be numeric")
    if "/" not in repo:
        abort(400, "repo must look like owner/name")
    if not is_allowed_repo(repo, ALLOWED_REPOS):
        abort(403, "Repository is not allowed")

    return {
        "issue_title": issue_title,
        "issue_number": issue_number,
        "repo": repo,
        "action": str(data.get("action", "")),
        "sender": str(data.get("sender", "")),
    }


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


@app.route("/webhook", methods=["POST"])
def handle_issue():
    raw_body = request.get_data()
    signature = request.headers.get("X-HOCA-Signature")
    timestamp = request.headers.get("X-HOCA-Timestamp")

    if not verify_timestamp(timestamp):
        abort(401, "Invalid or stale timestamp")
    if not verify_hmac_signature(WEBHOOK_SECRET, raw_body, signature):
        abort(401, "Invalid signature")

    data = validate_payload(request.get_json(force=True))
    issue_number = data["issue_number"]
    issue_title = data["issue_title"]
    repo = data["repo"]

    project_path = HOCA_WORKSPACE_ROOT / repo.split("/", 1)[1]

    if is_duplicate_issue_run(project_path, issue_number):
        return {
            "status": "duplicate",
            "issue": issue_number,
            "repo": repo,
        }, 200

    lock_path = project_path / ".hoca-runtime" / "runs" / f"issue-{issue_number}.lock"
    acquired = acquire_lock(
        lock_path,
        {
            "issue_number": issue_number,
            "issue_title": issue_title,
            "repo": repo,
            "action": data["action"],
            "sender": data["sender"],
        },
    )
    if not acquired:
        return {
            "status": "duplicate",
            "issue": issue_number,
            "repo": repo,
        }, 200

    task = f"Fix GitHub issue #{issue_number}: {issue_title} in {repo}"
    command = [
        str(Path(__file__).resolve().parents[1] / "bin" / "hoca"),
        "issue",
        str(project_path),
        issue_number,
        task,
    ]
    subprocess.Popen(
        command,
        cwd=str(HOCA_WORKSPACE_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    return {
        "status": "agent_dispatched",
        "issue": issue_number,
        "repo": repo,
        "project_path": str(project_path),
    }, 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
