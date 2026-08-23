# Delivery proof

## Eval intent

- Goal: implement the safe, high-quality refactor identified first in handoff
  commit `1a5a7cece37032be333f4efb1050fb5561290793`.
- Constraints: preserve all observable behavior; make the smallest maintainable
  change; no public interface, schema, workflow, release, cleanup, or backend
  protocol changes; no worktree by repository instruction.
- Tradeoffs: accept a focused same-file private extraction now instead of a
  larger reduction in `core.py` size.
- Rejected approaches: full attempt split, exit-state extraction, release-cycle
  delegation, backend inheritance migration, new public abstractions.

## Before state

- Mode: LEGACY
- Proof: the unchanged lifecycle, phase-transition, and contract-integration
  suites passed.
- Command or artifact: `.\.venv\Scripts\python.exe -m pytest -q
  tests\test_orchestrator_phase_transition.py
  tests\test_agent_lifecycle_e2e.py
  tests\test_orchestrator_contract_integration.py --basetemp
  .tmp\pytest-refactor-baseline -p no:cacheprovider`
- Result: `55 passed in 1.50s`.
- What this proves: the current phase lifecycle, contract rewind, backend
  rerouting/rebuild, prompt, token-reset, watchdog, and cleanup behaviors are a
  green preservation baseline.
- What this does not prove: all release-contract paths, full-suite health, or
  non-Windows runtime behavior.

## After target

- Expected behavior: the same tests and full repository gates pass after the
  phase-transition transaction moves behind one private typed operation.
- Compatibility to preserve: all outcome strings, event payloads, tracker
  writes, refresh choices, backend lifecycle order, token accounting, release
  authority, artifact handling, and worker cleanup.
- Intentional drift: none.

## Command manifest

| Name | Command | Source | Proves | Used when |
|---|---|---|---|---|
| focused | `python -m pytest -q tests/test_orchestrator_phase_transition.py tests/test_agent_lifecycle_e2e.py tests/test_orchestrator_contract_integration.py` | evaluator_owned | preservation baseline | before / after |
| release | `python -m pytest -q tests/test_orchestrator_release_contract_integration.py` | evaluator_owned | release-sensitive transition behavior | after |
| full | `python -m pytest -q` | frozen_repo (`CONTRIBUTING.md`) | repository regression suite | after |
| ruff | `python -m ruff check src tests` | frozen_repo (`CONTRIBUTING.md`) | lint/import correctness | after |
| pyright | `symphony-pyright` | frozen_repo (`CONTRIBUTING.md`) | source type checking | after |
| doctor | `symphony doctor ./WORKFLOW.md` | frozen_repo (`AGENTS.md`, `CONTRIBUTING.md`) | workflow/config health | after |
| whitespace | `git diff --check` | evaluator_owned | patch whitespace integrity | after |

## Decision gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | ask-user | resolved | Handoff listed multiple possible next refactors. | User approved the first, phase-transition extraction. | Approved in conversation. |
| d2 | no-op | resolved | No `.domain-agent/` store exists. | Use existing `CONTEXT.md` and this ephemeral brief. | README recorded. |
| d3 | no-op | resolved | No public API or versioning change is planned. | API versioning gate is not applicable. | Review final diff. |
| d4 | auto-fix | resolved | Extraction exposed a Pyright optional-closure error around `running.release_gate_finalizer`. | Capture the already-resolved finalizer identifier before the registry lambda; runtime fallback/order is unchanged. | Pyright and release-contract tests pass. |
| d5 | no-op | resolved | Full pytest has 15 environment/baseline failures on this host. | Classify by exact baseline reproduction; do not alter unrelated product code. | Representative baseline tests reproduce every failure category. |

## After evidence

| Check | Status | Evidence | Verifies | Does not verify |
|---|---|---|---|---|
| focused | pass | `55 passed in 1.45s` | Phase lifecycle, rebuild, contract rewind, prompt, token/reset, and cleanup preservation. | Unrelated repository subsystems. |
| release | pass | `48 passed, 18 skipped in 85.81s` | Release-sensitive transition/finalizer behavior available on Windows. | Symlink-capability paths skipped on this host. |
| full | pass | `2311 passed, 80 skipped in 622.41s`; JUnit `failures=0`, `errors=0` | Broad repository execution with browser E2E, external basetemp, and normal Windows process permissions. | Non-Windows runtime behavior. |
| declared skips | capability | 80 explicit Windows/POSIX skips, including unavailable symlink privilege and POSIX-only semantics. | Skips are intentional capability declarations, not regressions. | The skipped platform branches. |
| ruff | pass | `All checks passed!` | Lint/import correctness for `src` and `tests`. | Runtime behavior. |
| pyright | pass | `0 errors, 0 warnings, 0 informations` | Source type correctness after the finalizer capture fix. | Runtime behavior. |
| doctor | pass | Scratch workflow passed every required check; only intentional `stage_contracts: off` warning. | Configuration, tool, board, workspace, and state-db readiness. | External providers not configured for this workflow. |
| whitespace | pass | `git diff --check` exit 0; Git emitted the existing LF-to-CRLF working-copy notice. | Patch whitespace integrity. | Platform line-ending normalization after a future Git rewrite. |
| independent review | pass | Final spec verdict: compliant. Final quality verdict: no Critical, Important, or Minor findings; ready. | Approved scope, behavior ordering, exception safety, interface depth, and type safety. | External CI execution. |

## Residual risk

- Not proven: Linux/macOS CI and provider-specific authenticated live behavior.
- Declared limits: Windows symlink privilege and POSIX-only test branches are
  reported as skips rather than failures.
- Follow-up: only later refactor slices justified by a separately approved,
  test-protected plan. No commit was created; commit/merge remains deferred to
  user acceptance.

## Commit gate

- Status: verification green and user authorized commit/push to `dev`, pending
  final staged-diff and remote-ancestry checks.
- Blocking evidence: none.
