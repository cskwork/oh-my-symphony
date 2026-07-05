# Delivery Proof

## Eval Intent

- Goal: finish `docs/plans/2026-07-05-heartbeat-handoff.md` and the remaining continuous-improvement heartbeat plan tasks.
- Constraints: default-off, no browser-editable arbitrary commands, no product-code edits by the heartbeat, tracker writes through existing tracker APIs, in-memory turn budget, selectable CI ticket agent kind, full repo verification.
- Tradeoffs: implement first for file-board ticket creation and report `unsupported_tracker` for other trackers; optional browser/DB checks report `not_available` unless explicitly configured.
- Rejected approaches: direct Markdown ticket writes, shell string command execution, testing only the current checkout branch, unlimited ticket creation, treating reviewer approval as a substitute for an exact run.

## Before State

- Mode: LEGACY
- Proof: current branch `feat/continuous-improvement-heartbeat` is paused after Task 4 scheduler skeleton.
- Command or artifact: `docs/plans/2026-07-05-heartbeat-handoff.md`
- What this proves: Tasks 1, 2, and 4 are already committed; Tasks 3, 5, 6, 7, and 8 remain.
- What this does not prove: Task 4 spec/quality review, web settings behavior, real check runner, report writer, registrar, or full-suite health.

## After Target

- Expected behavior: web settings persist safe heartbeat controls, due heartbeat runs once in the background, runner records deterministic evidence, failures become bounded de-duplicated Kanban tickets, duplicate failures do not flood the board, max-turns blocks and reset resumes scheduling.
- Compatibility to preserve: heartbeat disabled by default; existing workflow/web/orchestrator behavior unchanged when the section is absent or disabled.
- Intentional drift: `docs/architecture.md` changes from planned to delivered runtime surface.

## Command Manifest

| Name | Command | Source | Proves | Used when |
|---|---|---|---|---|
| workflow tests | `python -m pytest tests/test_workflow.py -q` | frozen_repo | config/mutation compatibility | after |
| web API tests | `python -m pytest tests/test_webapi.py -q` | frozen_repo | web workflow/status/reset routes | after |
| static web contract tests | `python -m pytest tests/test_web_static_contract.py -q` | frozen_repo | settings UI controls/labels | after |
| CI runner tests | `python -m pytest tests/test_continuous_improvement.py -q` | frozen_repo | runner, report, registrar behavior | after |
| full suite | `python -m pytest -q` | frozen_repo | repo regression coverage | after |
| lint | `python -m ruff check src tests` | frozen_repo | Python lint health | after |
| typecheck | `python -m pyright` | frozen_repo | static typing health | after |
| diff check | `git diff --check` | frozen_repo | whitespace/apply sanity | after |

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | no-op | resolved | Non-file trackers lack a safe create contract in this plan. | Return `unsupported_tracker` instead of creating tickets. | Registrar tests |
| d2 | auto-fix | resolved | Task 4 review was skipped at handoff; review found lease-held backoff, missing runner timeout, stale lease steal, and stale config-status gaps. | Fixed lease-held retry, bounded runner with `asyncio.wait_for`, token-guarded stale lease ownership, and live config merge in status. | `python -m pytest tests/test_orchestrator_continuous_improvement.py -q` |
| d3 | auto-fix | resolved | Fresh-context adversarial review found target-branch proof was not exact, cancellation leaked subprocesses, generic failure summaries collapsed unrelated tickets, and the UI hid `not_proven`. | Added temporary target-branch worktree proof/cleanup, cancellation kill/reap, evidence-bearing summaries/fingerprints, and explicit `Not proven` UI state. | focused tests + exact throwaway-repo E2E |
| d4 | auto-fix | resolved | Exact E2E exposed that `safe_proc_wait` could return the timeout sentinel when asyncio had already reaped a short-lived child. | Added watcher-reaped fallback through `proc.wait()`/`proc.returncode`; runner no longer treats completed Git commands as timed out. | `python -m pytest tests/test_shell.py tests/test_continuous_improvement.py -q` |

## After Evidence

| Check | Status | Evidence | Verifies | Does not verify |
|---|---|---|---|---|
| shared subprocess + CI runner tests | passed | `python -m pytest tests/test_shell.py tests/test_continuous_improvement.py -q` -> 26 passed in 6.71s. | subprocess wait fallback, argv runner, timeout/cancel cleanup, redaction, baseline proof, target worktree, report writer, registrar, real throwaway-repo E2E. | Live browser/DB probes beyond the default `not_available` stubs. |
| CI runner tests | passed | `python -m pytest tests/test_continuous_improvement.py -q` -> 14 passed in 1.57s. | runner, report, registrar, cancellation, distinct fingerprints, temporary target worktree, real git/pytest/ruff/pyright E2E. | Live browser/DB probes beyond the default `not_available` stubs. |
| scheduler/API/settings tests | passed | `python -m pytest tests/test_webapi.py tests/test_orchestrator_continuous_improvement.py tests/test_web_static_contract.py -q` -> 46 passed in 0.50s. | due math, in-flight guard, idle/lease skips, max-turns/reset, runner timeout, status/API payloads, static UI controls and explicit `not_proven` label. | Real browser interaction. |
| workflow tests | passed | `python -m pytest tests/test_workflow.py -q` -> 90 passed in 0.21s. | config defaults, strict parsing, mutation compatibility. | None for the config surface. |
| lint | passed | `python -m ruff check src tests` -> All checks passed. | lint health for Python code/tests. | Runtime behavior. |
| typecheck | passed | `python -m pyright` -> 0 errors, 0 warnings. | static typing health. | Runtime behavior. |
| exact runner E2E | passed | Codified in `tests/test_continuous_improvement.py`: current branch `feature`, target `dev`, failing pytest, real `run_continuous_improvement()` -> `status=failed`, `tickets_created=1`, `verified_branch=dev`, failure evidence in ticket/report, host branch still `feature`, temp worktree removed. | The actual target-branch worktree runner, real commands, report write, file-board ticket creation, agent-kind stamp, and cleanup. | Browser/DB probes. |
| full suite | passed | `python -m pytest -q` -> 1233 passed, 2 skipped, 2 warnings in 71.83s. | repo regression coverage. | Live browser/DB probes. |

## Residual Risk

- Not proven: live browser interaction, optional DB probes, optional browser QA probes unless configured.
- Accepted residual risk: non-file trackers report `unsupported_tracker` until their safe ticket-create contracts exist.
