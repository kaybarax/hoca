from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "webhook-listener.py"
spec = importlib.util.spec_from_file_location("webhook_listener", SCRIPT_PATH)
webhook_listener = importlib.util.module_from_spec(spec)
sys.modules["webhook_listener"] = webhook_listener
spec.loader.exec_module(webhook_listener)


@pytest.fixture()
def _webhook_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOCA_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("HOCA_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("HOCA_ALLOWED_REPOS", "owner/repo")
    webhook_listener.WEBHOOK_SECRET = "test-secret"
    webhook_listener.ALLOWED_REPOS = "owner/repo"
    webhook_listener.HOCA_WORKSPACE_ROOT = tmp_path


@pytest.fixture()
def client(_webhook_env):
    webhook_listener.app.config["TESTING"] = True
    with webhook_listener.app.test_client() as c:
        yield c


def _sign(body: bytes, secret: str = "test-secret") -> str:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _payload(**overrides) -> dict:
    base = {"issue_title": "Bug title", "issue_number": "42", "repo": "owner/repo"}
    base.update(overrides)
    return base


def _post_webhook(
    client,
    payload: dict | None = None,
    *,
    secret="test-secret",
    timestamp=None,
    omit_signature=False,
    omit_timestamp=False,
):
    body = json.dumps(payload or _payload()).encode()
    headers = {"Content-Type": "application/json"}
    if not omit_timestamp:
        headers["X-HOCA-Timestamp"] = timestamp or str(int(time.time()))
    if not omit_signature:
        headers["X-HOCA-Signature"] = _sign(body, secret)
    return client.post("/webhook", data=body, headers=headers)


def test_missing_signature_returns_401(client):
    resp = _post_webhook(client, omit_signature=True)
    assert resp.status_code == 401


def test_invalid_signature_returns_401(client):
    resp = _post_webhook(client, secret="wrong-secret")
    assert resp.status_code == 401


def test_stale_timestamp_returns_401(client):
    stale = str(int(time.time()) - 9999)
    resp = _post_webhook(client, timestamp=stale)
    assert resp.status_code == 401


def test_missing_required_field_returns_400(client):
    for field in ("issue_title", "issue_number", "repo"):
        payload = _payload()
        del payload[field]
        resp = _post_webhook(client, payload)
        assert resp.status_code == 400, f"Expected 400 for missing {field}"


def test_invalid_issue_number_returns_400(client):
    resp = _post_webhook(client, _payload(issue_number="not-a-number"))
    assert resp.status_code == 400


def test_invalid_repo_format_returns_400(client):
    resp = _post_webhook(client, _payload(repo="no-slash"))
    assert resp.status_code == 400


def test_disallowed_repo_returns_403(client):
    resp = _post_webhook(client, _payload(repo="evil/repo"))
    assert resp.status_code == 403


@mock.patch("webhook_listener.subprocess.Popen")
@mock.patch("webhook_listener.acquire_lock", return_value=True)
@mock.patch("webhook_listener.is_duplicate_issue_run", return_value=False)
def test_allowed_repo_dispatches_task(mock_dup, mock_lock, mock_popen, client):
    resp = _post_webhook(client)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "agent_dispatched"
    assert data["issue"] == "42"
    assert data["repo"] == "owner/repo"
    mock_popen.assert_called_once()


@mock.patch("webhook_listener.subprocess.Popen")
@mock.patch("webhook_listener.acquire_lock", return_value=False)
@mock.patch("webhook_listener.is_duplicate_issue_run", return_value=False)
def test_duplicate_lock_prevents_duplicate_run(mock_dup, mock_lock, mock_popen, client):
    resp = _post_webhook(client)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "duplicate"
    mock_popen.assert_not_called()
