from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "notify.sh"


def make_fake_bin(tmp_path: Path) -> tuple[Path, Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    osascript_log = tmp_path / "osascript.log"
    curl_log = tmp_path / "curl.log"

    osascript = fake_bin / "osascript"
    osascript.write_text(
        f'#!/usr/bin/env bash\nset -euo pipefail\nprintf "%s\\n" "$@" >> "{osascript_log}"\n',
        encoding="utf-8",
    )
    osascript.chmod(osascript.stat().st_mode | stat.S_IXUSR)

    curl = fake_bin / "curl"
    curl.write_text(
        f'#!/usr/bin/env bash\nset -euo pipefail\nprintf "%s\\n" "$@" >> "{curl_log}"\n',
        encoding="utf-8",
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)

    return fake_bin, osascript_log, curl_log


def run_notify(
    args: list[str], fake_bin: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_notify_sends_macos_complete_message_without_telegram(tmp_path: Path) -> None:
    fake_bin, osascript_log, curl_log = make_fake_bin(tmp_path)

    result = run_notify(["complete", "Update README"], fake_bin)

    assert result.returncode == 0
    assert "HOCA task complete. Task: Update README" in result.stdout
    assert "HOCA task complete. Task: Update README" in osascript_log.read_text(encoding="utf-8")
    assert not curl_log.exists()


def test_notify_sends_telegram_only_when_enabled(tmp_path: Path) -> None:
    fake_bin, _, curl_log = make_fake_bin(tmp_path)

    result = run_notify(
        ["failed", "Fix tests", "https://github.example/pr/1", "--telegram"],
        fake_bin,
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "123",
        },
    )

    assert result.returncode == 0
    curl_output = curl_log.read_text(encoding="utf-8")
    assert "https://api.telegram.org/bottest-token/sendMessage" in curl_output
    assert "HOCA task failed. Task: Fix tests PR: https://github.example/pr/1" in curl_output


def test_notify_supports_run_directory_status_shape(tmp_path: Path) -> None:
    fake_bin, _, _ = make_fake_bin(tmp_path)
    project = tmp_path / "repo"
    run_dir = project / ".hoca-runtime" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(
        '{"status":"needs_human_staging","task":"Review staged files","notify_telegram":"false"}\n',
        encoding="utf-8",
    )

    result = run_notify([str(project), str(run_dir)], fake_bin)

    assert result.returncode == 0
    assert "HOCA task needs review. Task: Review staged files" in result.stdout
    notification_result = (run_dir / "notification-result.txt").read_text(encoding="utf-8")
    assert "type=needs-review" in notification_result
    assert "macos=sent" in notification_result
    assert "telegram=not_enabled" in notification_result


def test_notify_rejects_unknown_type(tmp_path: Path) -> None:
    fake_bin, _, _ = make_fake_bin(tmp_path)

    result = run_notify(["maybe", "Task"], fake_bin)

    assert result.returncode != 0
    assert "Unknown notification type" in result.stderr
