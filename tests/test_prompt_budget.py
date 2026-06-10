from __future__ import annotations

from pathlib import Path

from hoca.worker_direct import build_worker_direct_prompt
from hoca.worker_hermes import build_worker_hermes_prompt
from tests.test_worker_direct import MAC_HOME, sample_task_spec


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_direct_worker_prompt_is_compact_but_keeps_bindings() -> None:
    spec = sample_task_spec()
    kwargs = {
        "spec": spec,
        "project_path": Path(f"{MAC_HOME}/project"),
        "run_dir": Path(f"{MAC_HOME}/project/.hoca-runtime/runs/run-test"),
        "round_number": 2,
        "task_spec_path": Path(f"{MAC_HOME}/project/.hoca-runtime/runs/run-test/task-spec.json"),
    }

    direct_prompt = build_worker_direct_prompt(**kwargs)
    hermes_prompt = build_worker_hermes_prompt(**kwargs)

    assert len(direct_prompt) < int(len(hermes_prompt) * 0.65)
    assert "run_id: run-test" in direct_prompt
    assert "README documents install steps" in direct_prompt
    assert "Do not cd to repo_root_reference_only" in direct_prompt
    assert "Do not set or override HOCA_REQUESTED_MODEL" in direct_prompt
    assert "Bounded iteration discipline" not in direct_prompt
    assert "Implementation quality principles" not in direct_prompt


def test_direct_reviewer_prompt_keeps_report_rules_without_long_rubric() -> None:
    script = (REPO_ROOT / "scripts" / "review-with-openhands.sh").read_text(encoding="utf-8")

    assert "Produce a structured HocaReviewReport" in script
    assert "verdict: LGTM | fix_required | blocked" in script
    assert "Do not implement fixes or edit repository files" in script
    assert "Severity rubric:" in script
    assert "Distinguish blockers from PR tech debt" in script
    assert "roughly 1000 lines" not in script
    assert "merely rearranging it" not in script
