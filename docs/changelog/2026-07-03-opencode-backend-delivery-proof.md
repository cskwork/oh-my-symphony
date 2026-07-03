# Delivery Proof

## Eval Intent

- Goal: add first-class OpenCode backend support so `agent.kind: opencode` can dispatch Symphony tickets.
- Constraints: keep existing Codex, Claude, Gemini, and Pi behavior unchanged; use OpenCode's documented CLI automation path; no new dependencies.
- Tradeoffs: one subprocess per turn is simpler and matches existing Gemini/Pi patterns, but it may pay OpenCode startup cost per turn.
- Rejected approaches: persistent `opencode serve` lifecycle and ACP protocol integration are larger surfaces than needed for initial support.

## Before State

- Mode: LEGACY
- Proof: `SUPPORTED_AGENT_KINDS` contains only `codex`, `claude`, `gemini`, and `pi`; `build_backend()` rejects anything else.
- Command or artifact: code inspection plus pending red tests for opencode config/factory/backend behavior.
- What this proves: OpenCode is not selectable as a Symphony backend today.
- What this does not prove: behavior of a real authenticated OpenCode provider call.

## After Target

- Expected behavior: `agent.kind: opencode` parses from workflow config, appears in supported agent surfaces, builds an `OpenCodeBackend`, runs `opencode run` turns with the prompt as the documented `message` argument, resumes with `--session <id>` after OpenCode reports a session id when enabled, and reports best-effort token usage.
- Compatibility to preserve: existing backend config defaults, factory behavior, doctor/preflight checks, board override surfaces, and command examples.
- Intentional drift: supported backend lists gain `opencode`; docs/examples include an `opencode:` block.

## Command Manifest

| Name | Command | Source | Proves | Used when |
|---|---|---|---|---|
| focused backend/preflight/doctor tests | `pytest tests/test_backends.py tests/test_workflow_preflight_full.py tests/test_doctor.py -q` | frozen_repo | backend factory/config/protocol tests plus dispatch/doctor validation | after |
| full test suite | `pytest -q` | frozen_repo | repo-level regression coverage remains green | after |
| doctor | `symphony doctor ./WORKFLOW.md` | frozen_repo | configured active workflow remains doctor-clean | after |
| whitespace | `git diff --check` | frozen_repo | patch has no whitespace errors | after |

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | no-op | resolved | Real OpenCode smoke may consume model/provider credits. | Keep unit tests deterministic; report whether real smoke was run. | final verification |

## After Evidence

| Check | Status | Evidence | Verifies | Does not verify |
|---|---|---|---|---|
| focused backend/preflight/doctor tests | passed | `120 passed in 0.41s` | OpenCode config/factory/run behavior, dispatch validation, and doctor CLI selection | real OpenCode provider call |
| full test suite | passed | `942 passed, 2 skipped, 1 warning in 72.00s` | repo-level regressions including TUI fallback | real OpenCode provider call |
| doctor | passed | all checks PASS after unsandboxed workspace-root probe | active workflow config is launchable on this host | runtime dispatch against a live ticket |
| whitespace | passed | `git diff --check` exited 0 | no whitespace errors in tracked diff | semantic behavior |

## Residual Risk

- Not proven: real authenticated OpenCode provider behavior unless a live smoke is run.
- Follow-up: consider an `opencode serve --attach` backend only if per-turn startup cost is material.
