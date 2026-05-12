from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import subprocess
from collections.abc import Sequence


Command = Sequence[str]


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandError(RuntimeError):
    def __init__(self, result: CommandResult) -> None:
        command_text = " ".join(result.command)
        super().__init__(f"Command failed with exit code {result.returncode}: {command_text}")
        self.result = result


def _validate_command(command: Command) -> tuple[str, ...]:
    command_tuple = tuple(command)
    if not command_tuple:
        raise ValueError("Command must not be empty.")
    if not all(isinstance(part, str) and part for part in command_tuple):
        raise ValueError("Command must be a sequence of non-empty strings.")
    return command_tuple


def _print_failure(result: CommandResult) -> None:
    print(f"Command failed with exit code {result.returncode}: {' '.join(result.command)}", file=sys.stderr)
    if result.stdout:
        print("stdout:", file=sys.stderr)
        print(result.stdout.rstrip(), file=sys.stderr)
    if result.stderr:
        print("stderr:", file=sys.stderr)
        print(result.stderr.rstrip(), file=sys.stderr)


def run_command(command: Command, *, cwd: str | Path | None = None) -> CommandResult:
    command_tuple = _validate_command(command)
    completed = subprocess.run(
        command_tuple,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    return CommandResult(
        command=command_tuple,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_checked(command: Command, *, cwd: str | Path | None = None) -> CommandResult:
    result = run_command(command, cwd=cwd)
    if not result.ok:
        _print_failure(result)
        raise CommandError(result)
    return result
