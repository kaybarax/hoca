from __future__ import annotations

from pathlib import Path

import os
import pytest

from hoca.tmux_sessions import (
    AdapterCommandError,
    _sanitize_session_name,
    launch_tmux_session,
    send_to_session,
    session_exists,
    stop_session,
    _tmux,
)


def _write_fake_tmux(path: Path, state_file: Path, send_log: Path) -> None:
    path.write_text(
        f'''#!/usr/bin/env bash
set -euo pipefail
STATE_FILE="{state_file}"
SEND_LOG="{send_log}"

case "$1" in
  has-session)
    SESSION="${{3}}"
    if grep -qx "$SESSION" "$STATE_FILE" 2>/dev/null; then
      exit 0
    fi
    exit 1
    ;;
  new-session)
    SESSION=""
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "-s" ]; then
        SESSION="$2"
        break
      fi
      shift
    done
    if [ -z "${{SESSION:-}}" ]; then
      exit 1
    fi
    printf '%s\n' "$SESSION" >> "$STATE_FILE"
    exit 0
    ;;
  list-sessions)
    if [ -f "$STATE_FILE" ]; then
      cat "$STATE_FILE"
    fi
    ;;
  list-windows)
    echo "@1"
    ;;
  list-panes)
    echo "%0"
    ;;
  pipe-pane|send-keys)
    printf '%s\n' "$@" >> "$SEND_LOG"
    if [ "$1" = "send-keys" ]; then
      printf '%s\n' "$3" >> "$SEND_LOG"
    fi
    exit 0
    ;;
  kill-session)
    SESSION="${{3}}"
    [ -f "$STATE_FILE" ] || exit 0
    grep -v "^$SESSION$" "$STATE_FILE" > "$STATE_FILE.tmp" || true
    mv "$STATE_FILE.tmp" "$STATE_FILE" || true
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
''',
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)


def test_sanitize_session_name() -> None:
    assert _sanitize_session_name("lane 1:test") == "lane-1-test"


def test_launch_and_send_with_fake_tmux(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "tmux-state.txt"
    send_log = tmp_path / "send.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_bin = fake_bin / "tmux"
    _write_fake_tmux(tmux_bin, state_file, send_log)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    assert _tmux() is not None
    runtime = launch_tmux_session(
        session_name="Lane 7:alpha",
        command="echo hi",
        working_dir=tmp_path,
    )
    assert runtime.session_name == "Lane-7-alpha"
    assert runtime.pane_id == "%0"
    assert runtime.window_id == "@1"
    assert runtime.session_id == "%1"

    send_to_session("Lane 7:alpha", "hello")
    stop_session("Lane 7:alpha")
    assert "hello" in send_log.read_text()
    assert not session_exists("Lane 7:alpha")


def test_send_to_unknown_session_raises(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "tmux-state.txt"
    send_log = tmp_path / "send.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_bin = fake_bin / "tmux"
    _write_fake_tmux(tmux_bin, state_file, send_log)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    with pytest.raises(AdapterCommandError):
        send_to_session("missing", "hello")
