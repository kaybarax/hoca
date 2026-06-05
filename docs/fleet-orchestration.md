# HOCA Fleet Orchestration

HOCA v1 adds a control plane above the existing single-repository run flow.
The goal is to let one operator coordinate many repos and many tasks while
keeping the same conservative review and cleanup discipline.

## Core Concepts

- **Project registry**: an explicit allowlist of Git repositories that HOCA may
  operate on.
- **Task board**: queued work items attached to a registered project.
- **Scheduler**: the component that decides when a task may launch a lane.
- **Lane**: one isolated task execution, with its own worktree, logs, and
  runtime artifacts.
- **Fleet state**: the aggregate view used by `hoca fleet status` and
  `hoca scheduler status`.

## Typical Workflow

```bash
bin/hoca project add /path/to/app-one --name app-one
bin/hoca project add /path/to/app-two --name app-two
bin/hoca task create app-one "Fix login redirect"
bin/hoca task create app-two "Update webhook tests"
bin/hoca scheduler tick
bin/hoca fleet status
```

The scheduler only launches queued tasks when the registry, project caps, and
fleet caps all say the lane is allowed to start.

## Conservative Parallelism

HOCA keeps parallelism intentionally low by default:

- Each project has its own `max_parallel_tasks` cap.
- The scheduler should be treated as a resource governor, not an unbounded
  dispatcher.
- Stale lanes, cleaned lanes, and orphaned worktree leases should be removed
  before new work is launched.

## Operational Commands

- `hoca scheduler tick` runs one scheduling pass.
- `hoca scheduler start` runs the scheduler loop for a bounded number of
  iterations.
- `hoca scheduler status` prints a concise fleet summary.
- `hoca fleet doctor` checks registry consistency.
- `hoca fleet report` writes a snapshot report to the control root.
- `hoca fleet cleanup --dry-run` previews the cleaned-lane cleanup set.

## Cleanup Discipline

Cleanup should be explicit. A safe fleet cleanup removes only lanes that are
already marked cleaned, updates task lane references, and leaves the active
checkout untouched.

If something looks wrong, stop the scheduler first, inspect the control root,
and clean up stale locks or leases before resuming fleet work.
