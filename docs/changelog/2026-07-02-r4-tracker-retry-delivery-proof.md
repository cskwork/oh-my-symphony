# Delivery Proof

## Eval Intent

- Goal: implement R4 classified tracker retries from the reliability handoff.
- Constraints: preserve Jira/Linear public error types, keep clients synchronous,
  avoid real sleeps in tests, and keep timeout configuration additive.
- Tradeoffs: tracker retry classification ships now; backend worker-outcome
  classification stays deferred to later retry/attention work.

## Before State

- Mode: LEGACY
- Proof: Jira `_request` and Linear `_post` were single-shot; pagination loops
  were unbounded; HTTP timeout was hardcoded in each tracker module.
- Command or artifact: `docs/plans/2026-07-02-reliability-handoff.md`.
- What this proves: transient tracker blips could fail a state transition
  immediately and infinite pagination could tie up an executor thread.

## After Target

- Expected behavior: retry transport failures and `429/500/502/503/504` with
  bounded backoff, honor numeric `Retry-After` capped at 30 seconds, fail 4xx
  validation/auth statuses without retry, stop pagination after `MAX_PAGES`,
  and allow `tracker.network_timeout_seconds` to override the default timeout.
- Compatibility to preserve: existing Jira/Linear exception types and injected
  `http_client` test paths.

## Command Manifest

| Name | Command | Source | Proves | Used when |
|---|---|---|---|---|
| tracker-workflow | `.venv/bin/python -m pytest -q tests/test_tracker_jira.py tests/test_tracker_jira_edges.py tests/test_tracker_linear_full.py tests/test_tracker_linear_archive.py tests/test_workflow.py` | frozen_repo | Jira/Linear retry, pagination, timeout config, and workflow parsing behavior | after |
| full-tests | `.venv/bin/python -m pytest -q` | frozen_repo | Broad Python regression check | after |
| workflow-doctor | `.venv/bin/symphony doctor ./WORKFLOW.md` | frozen_repo | Current workflow validates in the operator environment | after |
| browser-e2e | `SYMPHONY_BROWSER_E2E=1 .venv/bin/python -m pytest tests/test_web_browser_e2e.py -q -rs` | frozen_repo | Browser UI flow can execute when Playwright Chromium is installed | after |

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | auto-fix | resolved | Transport failures and retryable statuses were single-shot. | Added `trackers/_retry.py` and routed Jira/Linear through it. | tracker-workflow |
| d2 | auto-fix | resolved | Pagination loops had no aggregate cap. | Added `MAX_PAGES=20` with tracker-specific warnings. | tracker-workflow |
| d3 | auto-fix | resolved | Timeout was a hardcoded module constant. | Added additive `tracker.network_timeout_seconds`. | tracker-workflow |

## After Evidence

| Check | Status | Evidence | Verifies | Does not verify |
|---|---|---|---|---|
| tracker-workflow | pass | `126 passed in 2.05s`. | Retry classification, `Retry-After`, 4xx fail-fast, exhaustion mapping, pagination caps, timeout config. | Live Jira/Linear credentials or online API behavior |
| full-tests | pass | `922 passed, 2 skipped, 1 warning in 58.01s`. | Broad Python regression status including R2/R7, OneShot bootstrap, and R4. | Browser E2E with a real Chromium binary |
| workflow-doctor | blocked | Port `9999` is occupied and `/Users/danny/symphony_workspaces` is not writable from the sandbox. | Current environment blocks the final doctor gate. | Real unsandboxed doctor status |
| browser-e2e | blocked | `1 skipped`; bundled Playwright Chromium missing. Installed Chrome channel also aborts under sandbox permissions. | The opt-in E2E test is wired and skips with the documented missing dependency. | Actual browser interaction |

## Residual Risk

- Not proven: live tracker APIs, non-numeric date-form `Retry-After`, real
  unsandboxed doctor status, browser E2E without a usable Chromium binary, and
  later worker-outcome deterministic/transient classification.
