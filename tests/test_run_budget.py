from __future__ import annotations

from hoca.contracts import HocaRoleModelSelection, HocaSandboxPolicy, HocaTaskSpec
from hoca.run_budget import derive_run_budget


def spec(**overrides: object) -> HocaTaskSpec:
    base = HocaTaskSpec(
        run_id="run-test",
        repo_root="/repo",
        base_branch="main",
        task_branch="feat/test",
        issue_id=None,
        raw_request="Update docs",
        goal="Update docs",
        non_goals=[],
        expected_areas=["README.md"],
        acceptance_criteria=["Docs updated"],
        test_commands=[],
        risk_level="low",
        requires_human_approval=False,
        max_total_rounds=3,
        models=HocaRoleModelSelection(
            manager="m",
            worker="w",
            reviewer="r",
            fallback="m",
        ),
        sandbox=HocaSandboxPolicy(enabled=True, network_mode="offline"),
    )
    data = base.to_dict()
    data.update(overrides)
    return HocaTaskSpec.from_dict(data)


def test_low_risk_small_task_gets_tighter_budget_than_high_risk() -> None:
    low = derive_run_budget(spec(risk_level="low", expected_areas=["README.md"]))
    high = derive_run_budget(
        spec(risk_level="high", expected_areas=["src/a.py", "src/b.py", "tests/test_a.py"])
    )

    assert low.max_total_rounds < high.max_total_rounds
    assert low.openhands_timeout < high.openhands_timeout
    assert low.hermes_timeout < high.hermes_timeout


def test_repair_round_increases_timeouts_within_ceilings() -> None:
    first = derive_run_budget(spec(risk_level="medium"), repair_round=1)
    repair = derive_run_budget(spec(risk_level="medium"), repair_round=3)

    assert repair.openhands_timeout > first.openhands_timeout
    assert repair.openhands_stall > first.openhands_stall
    assert repair.hermes_timeout > first.hermes_timeout


def test_explicit_env_overrides_win() -> None:
    budget = derive_run_budget(
        spec(risk_level="low"),
        env={
            "HOCA_MAX_TOTAL_ROUNDS": "3",
            "HOCA_OPENHANDS_TIMEOUT": "777",
            "HOCA_OPENHANDS_STALL": "333",
            "HOCA_HERMES_TIMEOUT": "1555",
        },
    )

    assert budget.max_total_rounds == 3
    assert budget.openhands_timeout == 777
    assert budget.openhands_stall == 333
    assert budget.hermes_timeout == 1555
    assert budget.env_overrides["HOCA_OPENHANDS_TIMEOUT"] == "777"
