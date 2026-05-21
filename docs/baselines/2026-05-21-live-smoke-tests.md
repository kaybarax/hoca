# HOCA Live Smoke Tests - 2026-05-21

Task: Phase 16.4 Live Smoke Tests

Environment:

- Host: macOS arm64, 48 GB RAM
- Branch: `feat/hermes-multi-agent-upgrade`
- Sandbox image: `hoca-sandbox:latest`, rebuilt during smoke testing
- Local model used for successful live OpenHands runs: `ollama/qwen2.5-coder:7b`

## Results

| Check | Result | Notes |
| --- | --- | --- |
| `./bin/hoca doctor` | Pass with warnings | Required binaries, Docker, Ollama, OpenHands, sandbox image, and worktree sandbox passed. Warnings were non-critical env/default warnings. |
| `HERMES_HOME=/tmp/hoca-16-4-smoke/hermes-home-dry ./bin/hoca setup-profiles --dry-run` | Pass | Dry-run showed manager, worker, and reviewer profiles would be created and configured. |
| `HERMES_HOME=/tmp/hoca-16-4-smoke/hermes-home-live ./bin/hoca setup-profiles` | Pass | Created HOCA Hermes role profiles in a disposable Hermes home. |
| Rebuild sandbox image with `./scripts/sandbox-manager.sh build` | Pass | Existing image was stale and lacked `openhands`; rebuild produced an image with OpenHands CLI 1.15.1. |
| Sandboxed OpenHands tiny fixture run | Pass | With `LLM_MODEL=ollama/qwen2.5-coder:7b` and `HOCA_NETWORK_MODE=package-install`, OpenHands appended `Smoke test passed.` to a fixture README. |
| Reviewer on known-good diff | Pass | Host OpenHands reviewer returned LGTM and review gate materialized an LGTM report. Host mode was used because reviewer sandbox defaults to offline network mode. |
| Reviewer on known-bad diff | Fail | Host OpenHands reviewer incorrectly returned LGTM on a syntactically broken Python `subtract` function. The command path works, but reviewer quality/enforcement is not sufficient for this smoke. |
| Max round behavior with mocked review failures | Pass | `pytest tests/test_round_loop.py` passed. |

## Fixes Made During Smoke

- Rebuilt the stale sandbox image.
- Updated the sandbox wrapper to mount run artifacts at `/hoca-run` and the physical run path instead of relying on a nested `/workspace/.hoca-runtime/...` bind mount.
- Added isolated OpenHands agent configuration inside the sandbox wrapper so local Ollama models do not receive unsupported thinking parameters.
- Made the sandbox wrapper fail on `ConversationErrorEvent`, matching host wrapper behavior.
- Tightened monitor policy scanning so passive OpenHands prompt/message echoes do not trigger dangerous command or manager-only Git lifecycle stops.
- Allowed the monitor to treat the current HOCA run directory as an approved artifact path.
- Improved structured review extraction from OpenHands JSON `MessageEvent` output so embedded structured reports beat legacy text matching.
- Hardened review report contract parsing so string `pr_notes` values become single-item lists instead of character lists.

## Remaining Risk

The known-bad live reviewer smoke shows that the reviewer can still rubber-stamp an obvious broken diff when the model does not actually inspect the patch. Future work should add deterministic review-gate checks or stronger reviewer enforcement before marking the full live toolchain accepted.
