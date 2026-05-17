from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Literal

from hoca.paths import repo_root


DoctorStatus = Literal["ok", "warn", "fail"]

REQUIRED_COMMANDS = (
    "git",
    "gh",
    "python3",
    "node",
    "jq",
    "curl",
    "openssl",
    "docker",
    "openhands",
)

STATUS_PREFIXES: dict[str, DoctorStatus] = {
    "[OK]": "ok",
    "[WARN]": "warn",
    "[FAIL]": "fail",
}


@dataclass(frozen=True)
class DoctorCheck:
    status: DoctorStatus
    message: str


@dataclass(frozen=True)
class DoctorReport:
    returncode: int
    checks: tuple[DoctorCheck, ...]
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.failures

    @property
    def warnings(self) -> tuple[DoctorCheck, ...]:
        return tuple(check for check in self.checks if check.status == "warn")

    @property
    def failures(self) -> tuple[DoctorCheck, ...]:
        return tuple(check for check in self.checks if check.status == "fail")


def parse_doctor_output(output: str) -> tuple[DoctorCheck, ...]:
    checks: list[DoctorCheck] = []
    for line in output.splitlines():
        stripped = line.strip()
        for prefix, status in STATUS_PREFIXES.items():
            if stripped.startswith(prefix):
                message = stripped.removeprefix(prefix).strip()
                checks.append(DoctorCheck(status=status, message=message))
                break
    return tuple(checks)


def doctor_script_path() -> Path:
    return repo_root() / "scripts" / "hoca-doctor.sh"


def run_doctor(*, cwd: str | Path | None = None, echo: bool = True) -> DoctorReport:
    script = doctor_script_path()
    if not script.exists():
        raise FileNotFoundError(f"Missing doctor script: {script}")

    completed = subprocess.run(
        [str(script)],
        cwd=cwd or repo_root(),
        check=False,
        capture_output=True,
        text=True,
    )

    if echo:
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)

    return DoctorReport(
        returncode=completed.returncode,
        checks=parse_doctor_output(completed.stdout),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
