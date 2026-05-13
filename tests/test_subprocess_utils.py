from __future__ import annotations

import sys

import pytest

from hoca.subprocess_utils import CommandError, run_checked, run_command


def test_run_command_captures_exit_code_stdout_and_stderr() -> None:
    result = run_command(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)",
        ]
    )

    assert result.command[0] == sys.executable
    assert result.returncode == 3
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"
    assert result.ok is False


def test_run_checked_raises_with_failure_context(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(CommandError) as error:
        run_checked(
            [sys.executable, "-c", "import sys; print('bad', file=sys.stderr); sys.exit(2)"]
        )

    captured = capsys.readouterr()
    assert error.value.result.returncode == 2
    assert "Command failed with exit code 2" in captured.err
    assert "stderr:" in captured.err
    assert "bad" in captured.err


def test_empty_command_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        run_command([])
