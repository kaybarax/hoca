from __future__ import annotations

import re
from pathlib import Path

from hoca.worker_direct import build_worker_direct_prompt
from hoca.worker_hermes import build_worker_hermes_prompt
from tests.test_worker_direct import MAC_HOME, sample_task_spec


REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_HOCA_TASK = REPO_ROOT / "scripts" / "run-hoca-task.sh"
RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"
RUN_OPENHANDS_SANDBOXED = REPO_ROOT / "scripts" / "run-openhands-sandboxed.sh"
PYPROJECT = REPO_ROOT / "pyproject.toml"

DIRECT_WORKER_PROMPT_CHAR_BUDGET = 2800


def test_performance_budget_definition_of_ready_runs_once_per_hoca_run() -> None:
    script = RUN_HOCA_TASK.read_text(encoding="utf-8")

    assert script.count('DOR_OUTPUT="$(run_definition_of_ready_check') == 1
    assert script.count('record_timing_phase "definition_of_ready"') == 1


def test_performance_budget_warm_sandbox_reuses_one_run_scoped_container() -> None:
    script = RUN_OPENHANDS_SANDBOXED.read_text(encoding="utf-8")

    assert 'CONTAINER_NAME_FILE="$RUN_DIR/sandbox-container-name.txt"' in script
    assert 'if ! docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then' in script
    assert "record_timing_event --type container_start --name docker-run" in script
    assert len(re.findall(r"record_timing_event --type container_start --name docker-run", script)) == 2
    assert script.count('printf \'%s\\n\' "$CONTAINER_NAME" > "$CONTAINER_NAME_FILE"') == 3
    assert "else\n  printf '%s\\n' \"$CONTAINER_NAME\" > \"$CONTAINER_NAME_FILE\"\nfi" in script


def test_performance_budget_install_cache_skips_unchanged_lockfile() -> None:
    run_tests = RUN_TESTS.read_text(encoding="utf-8")
    sandbox = RUN_OPENHANDS_SANDBOXED.read_text(encoding="utf-8")

    assert '[ "${HOCA_FORCE_INSTALL:-false}" != "true" ]' in run_tests
    assert "Skipping: pnpm install (install cache current)" in run_tests
    assert '[ "${HOCA_FORCE_INSTALL:-false}" = "true" ]' in sandbox
    assert "Skipping pnpm install; install cache current." in sandbox


def test_performance_budget_direct_worker_prompt_size() -> None:
    spec = sample_task_spec()
    kwargs = {
        "spec": spec,
        "project_path": Path(f"{MAC_HOME}/project"),
        "run_dir": Path(f"{MAC_HOME}/project/.hoca-runtime/runs/run-test"),
        "round_number": 1,
        "task_spec_path": Path(f"{MAC_HOME}/project/.hoca-runtime/runs/run-test/task-spec.json"),
    }

    direct_prompt = build_worker_direct_prompt(**kwargs)
    hermes_prompt = build_worker_hermes_prompt(**kwargs)

    assert len(direct_prompt) <= DIRECT_WORKER_PROMPT_CHAR_BUDGET
    assert len(direct_prompt) < int(len(hermes_prompt) * 0.65)


def test_performance_budget_direct_defaults_spawn_zero_hermes_profiles() -> None:
    script = RUN_HOCA_TASK.read_text(encoding="utf-8")

    assert "WORKER_MODE=" in script
    assert "REVIEWER_MODE=" in script
    assert 'record_timing_event --type "agent_loop" --name "worker-$WORKER_ENGINE"' in script
    assert 'record_timing_event --type "agent_loop" --name "reviewer-hermes"' not in script
    assert 'record_timing_event --type "agent_loop" --name "reviewer-$REVIEWER_MODE"' in script
    assert 'export HOCA_WORKER_MODE=direct' in script
    assert 'export HOCA_REVIEWER_MODE=direct' in script


def test_performance_budget_tests_are_part_of_standard_pytest_suite() -> None:
    pyproject = PYPROJECT.read_text(encoding="utf-8")

    assert 'testpaths = ["tests"]' in pyproject
