# HOCA Baseline Capture

Captured at: 2026-05-19T15:43:47Z

## Git Status Short

```text
clean before baseline artifacts were created
```

After baseline capture began, this task intentionally added tracked baseline
artifacts under `docs/baselines/`.

## Current Branch

```text
main
```

## Latest Commit Short Hash

```text
60a87d1
```

## Existing Test Suite Baseline

Command:

```text
pytest
```

Result:

```text
16 failed, 412 passed in 67.68s
```

All failures were in `tests/test_run_hoca_task_script.py`. The observed failure
pattern was that temporary fixture repositories initialized on `master`, while
`scripts/run-hoca-task.sh` tried to switch to configured development branch
`main` before reaching the later behavior under test.

Full output:

```text
docs/baselines/2026-05-19-pytest-baseline.txt
```

## CLI And Runtime Baseline Artifacts

- OpenHands CLI help: `docs/baselines/2026-05-19-openhands-help.txt`
- Hermes CLI help: `docs/baselines/2026-05-19-hermes-help.txt`
- Hermes profile help: `docs/baselines/2026-05-19-hermes-profile-help.txt`
- Docker version output: `docs/baselines/2026-05-19-docker-version.txt`
