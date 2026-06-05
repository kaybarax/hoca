# HOCA Agent Adapters

HOCA stays agent-agnostic by routing lane execution through adapter specs.
An adapter describes how to start, talk to, and stop a concrete coding agent
without hard-coding any one provider into the scheduler.

## Adapter Contract

An adapter spec is represented by `HocaAgentAdapterSpec` and includes:

- `adapter_id`
- `provider`
- `command_template`
- `command_allowlist`
- optional runtime and capability metadata

The adapter implementation resolves a command template, filters the runtime
environment for the selected phase, and records stdout, stderr, and structured
artifacts in the lane run directory.

## Default OpenHands Adapter

HOCA ships with a default OpenHands adapter spec. Its command template points
at the HOCA lane runner script and includes the standard lane identifiers and
project metadata.

The default adapter is intentionally narrow:

- it is geared toward coding and review lanes
- it starts with a single active session per lane
- it records adapter state in the lane run directory
- it relies on the manager-side environment allowlist for safe credential
  forwarding

## Custom Adapter Safety Requirements

Custom adapters are allowed, but they must be explicit.

- Non-OpenHands adapters require a command allowlist.
- The required command set is checked before launch.
- Command templates must be parseable and should avoid secret-like paths.
- Worker and reviewer sessions should receive only the credentials needed for
  that phase.
- Adapter commands should be treated as lane-local tooling, not as arbitrary
  shell access to the host.

## Future Adapter Shapes

Future adapters for Codex, Claude, Gemini, or other providers should keep the
same shape:

- one adapter spec per provider or runtime profile
- one command template per lane type
- one allowlist that is reviewed before launch
- one runtime directory for logs and artifacts

The important property is the contract, not the vendor name. HOCA should keep
working even as adapter implementations change underneath the scheduler.
