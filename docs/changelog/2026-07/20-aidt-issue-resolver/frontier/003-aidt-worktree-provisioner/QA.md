# QA - Frontier 003 AIDT Worktree Provisioner

- Verdict: PASS

## Required Evidence

- [x] Fresh plan attack passed after three binding amendments; all original MUST/SHOULD/added-risk findings closed.
- [x] Red tests reproduced route-child overblocking and unsafe generic fallback before product changes.
- [x] Temporary Git fixtures cover create, exact resume, prepared recovery, ambiguous interruption, ref/path/branch
  collisions, concurrent provision attempts, dirty root, protected occupancy, and authorized cleanup.
- [x] Orchestrator tests prove initial and retry dispatch share the pre-backend barrier and unmanaged tickets retain the
  current workspace lifecycle.
- [x] A manager-stage failure after preparation retains the exact prior runtime/provisioner/manager/generation tuple,
  denies candidate work, and returns before heartbeat, reconciliation, fetch, or dispatch.
- [x] A fresh verifier repeated isolated, affected, full, static, structure, diff, doctor, and literal gates.

Backward-trace: clean

## Trusted Commands

- `evaluator_owned` - exact 41-case Core/workspace barrier matrix.
- `evaluator_owned` - frozen 459-case affected matrix and 326-case orchestrator matrix.
- `evaluator_owned` - 752-case AIDT routing/worktree/recovery/workspace matrix and full repository pytest.
- `evaluator_owned` - Ruff, Pyright with the repository dev interpreter, AST, lazy-import, tracked/no-index whitespace,
  and Symphony doctor checks.

## Commands

| Command | Source | Proves |
|---|---|---|
| `rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q tests/test_aidt_worktree_core_integration.py tests/test_workspace.py::test_delegate_unmanaged_preserves_workspace_create_hooks_marker_and_return tests/test_workspace.py::test_delegate_handled_create_returns_guard_without_generic_side_effects tests/test_workspace.py::test_delegate_owned_create_and_before_run_never_fall_back tests/test_workspace.py::test_keyworded_remove_is_a_non_destructive_unmanaged_probe tests/test_workspace.py::test_keyworded_owned_remove_preserves_before_generic_hook_or_rmtree tests/test_aidt_routing_runtime.py::test_core_releases_only_provisionable_managed_children_in_input_order tests/test_aidt_routing_runtime.py::test_never_enabled_core_does_not_load_provisioner_or_git_state tests/test_orchestrator_health.py::test_worktree_degraded_and_fatal_health_add_one_bounded_reason` | evaluator_owned | Exact integration barrier: 41 passed in 1.37s. |
| `rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q --tb=line tests/test_workspace.py -k 'delegate or keyworded'` | agent_detected | Workspace delegate and keyworded lifecycle integration: 20 passed, 41 deselected. |
| `rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q --tb=short tests/test_aidt_worktree_core_integration.py tests/test_aidt_routing_runtime.py::test_core_releases_only_provisionable_managed_children_in_input_order tests/test_aidt_routing_runtime.py::test_never_enabled_core_does_not_load_provisioner_or_git_state tests/test_orchestrator_health.py::test_worktree_degraded_and_fatal_health_add_one_bounded_reason` | agent_detected | Core/workspace integration and three frozen extensions: 21 passed. |
| `rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q tests/test_aidt_route_dispatch_contract.py tests/test_aidt_routing_contract.py tests/test_aidt_routing_decision.py tests/test_aidt_routing_git_objects.py tests/test_aidt_routing_runtime.py tests/test_aidt_routing_storage.py tests/test_workspace.py tests/test_orchestrator_dispatch.py tests/test_orchestrator_reconcile.py tests/test_orchestrator_health.py -k 'not test_delegate_unmanaged_preserves_workspace_create_hooks_marker_and_return and not test_delegate_handled_create_returns_guard_without_generic_side_effects and not test_delegate_owned_create_and_before_run_never_fall_back and not test_keyworded_remove_is_a_non_destructive_unmanaged_probe and not test_keyworded_owned_remove_preserves_before_generic_hook_or_rmtree and not test_core_releases_only_provisionable_managed_children_in_input_order and not test_never_enabled_core_does_not_load_provisioner_or_git_state and not test_worktree_degraded_and_fatal_health_add_one_bounded_reason'` | evaluator_owned | Frozen affected-control matrix: 459 passed, 1 skipped, 23 deselected. |
| `rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q tests/test_orchestrator_archive.py tests/test_orchestrator_continuous_improvement.py tests/test_orchestrator_contract_integration.py tests/test_orchestrator_contracts.py tests/test_orchestrator_dispatch.py tests/test_orchestrator_health.py tests/test_orchestrator_max_retries.py tests/test_orchestrator_phase_transition.py tests/test_orchestrator_reconcile.py` | evaluator_owned | Broader orchestrator compatibility: 326 passed. |
| `rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q tests/test_aidt_route_dispatch_contract.py tests/test_aidt_routing_contract.py tests/test_aidt_routing_decision.py tests/test_aidt_routing_git_objects.py tests/test_aidt_routing_runtime.py tests/test_aidt_routing_storage.py tests/test_aidt_worktree_contract.py tests/test_aidt_worktree_core_integration.py tests/test_aidt_worktree_git_state.py tests/test_aidt_worktree_manifest.py tests/test_aidt_worktree_provisioner.py tests/test_aidt_worktree_recovery_proofs.py tests/test_aidt_worktree_runtime.py tests/test_workspace.py` | evaluator_owned | Broader AIDT routing, worktree, recovery, and workspace compatibility: 752 passed, 1 skipped. |
| `rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q tests/test_aidt_worktree_runtime.py::test_never_enabled_unmanaged_runtime_is_inert` | agent_detected | Executable runtime AST, lazy-facade, and import-boundary gate: 1 passed. |
| `rtk env uv run --extra dev ruff check .` | agent_detected | Full Ruff gate. |
| `rtk env uv run --extra dev pyright` | agent_detected | Full Pyright gate. |
| `rtk env RUFF_CACHE_DIR=/tmp/f003-ruff-cache ../../.venv/bin/ruff check .` | evaluator_owned | Full Ruff passed without writing a repository cache. |
| `rtk ../../.venv/bin/pyright --pythonpath ../../.venv/bin/python` | evaluator_owned | Full Pyright: 0 errors, 0 warnings, 0 information. |
| `rtk git diff --check` plus evaluator-owned no-index scans | evaluator_owned | Tracked and product/test whitespace pass; complete untracked scan reports six documentation files. |
| `rtk ../../.venv/bin/symphony doctor ./WORKFLOW.md` | frozen_repo | Outside-sandbox workspace probe passed; only accepted missing worktree `kanban/` remained. |
| `rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q` | evaluator_owned | Repository parity: 2192 passed, 6 skipped, with only the accepted pre-change missing `kanban/CI-1.md` failure. |
| `rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q tests/test_aidt_worktree_core_integration.py::test_failed_generation_publication_keeps_manager_and_denies_candidate_work` | agent_detected | Repair RED exposed `publish -> reject -> heartbeat` and manager replacement; final GREEN is 1 passed. |
| `rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q tests/test_aidt_worktree_core_integration.py tests/test_workspace.py::test_delegate_unmanaged_preserves_workspace_create_hooks_marker_and_return tests/test_workspace.py::test_delegate_handled_create_returns_guard_without_generic_side_effects tests/test_workspace.py::test_delegate_owned_create_and_before_run_never_fall_back tests/test_workspace.py::test_keyworded_remove_is_a_non_destructive_unmanaged_probe tests/test_workspace.py::test_keyworded_owned_remove_preserves_before_generic_hook_or_rmtree tests/test_aidt_routing_runtime.py::test_core_releases_only_provisionable_managed_children_in_input_order tests/test_aidt_routing_runtime.py::test_never_enabled_core_does_not_load_provisioner_or_git_state tests/test_orchestrator_health.py::test_worktree_degraded_and_fatal_health_add_one_bounded_reason` | agent_detected | Final-byte repair barrier: 41 passed in 1.37s. |
| `rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q tests/test_agent_lifecycle_e2e.py::test_file_board_e2e_auto_triage_dispatches_and_reaches_done` | agent_detected | Lifecycle workflow-identity fixture repair: RED on `profile_invalid`, then GREEN with 1 passed. |

## Results

- [x] Exact Core/workspace integration: 41 passed in 1.37s.
- [x] Frozen affected matrix: 459 passed, 1 skipped, 23 deselected in 67.28s.
- [x] Orchestrator compatibility: 326 passed in 17.24s.
- [x] Full AIDT routing/runtime/provisioner/recovery/workspace matrix: 752 passed, 1 skipped in 610.17s.
- [x] Full repository: 2192 passed, 6 skipped, with the sole failure exactly matching the run-state ledger:
  `test_run_continuous_improvement_real_git_target_worktree_e2e` could not read `kanban/CI-1.md`.
- [x] Ruff passed; Pyright reported 0 errors, warnings, or information; 608 scanned product functions had maximum 47
  lines and nesting 4; the two default-off/lazy-import sentinels passed.
- [x] Tracked whitespace and all 16 untracked product/test files are clean. Doctor passed every check except the
  accepted absent worktree `kanban/` baseline; the escalated workspace writability probe passed.
- [x] Atomic publication repair is fail-closed: manager preparation occurs before runtime publication and the complete
  manager/key/generation tuple is installed only after publication succeeds.
- [x] Backward trace is clean: every changed product/test/example file maps to 001a or one 003a-003i seam; generated
  `uv.lock` is absent from the repository diff and retained only as a recoverable `/private/tmp` copy.
- [x] Fixed-base and working-tree whitespace checks pass; all 87 untracked files pass the no-index whitespace gate.
- [x] Exact literal commit gates for reclosed Frontier 001 and Frontier 003 both pass.

## Reproduction Fidelity

Fidelity level: synthetic-representative

Residual risk from data gap: no live AIDT fetch or service worktree mutation is allowed in this frontier; later
activation must re-run the same invariants against an approved clean target ticket and accessible remote.

Post-deploy confirmation plan: after later controlled activation and aidt-dev deployment, prove the exact manifest,
branch/base identity, Jenkins result, and dev E2E before terminal cleanup is authorized.

## Residual Risk

- No browser, DB, live AIDT repository, Jira, backend, merge, push, or deployment proof belongs to Frontier 003.
- Temporary repositories prove Git semantics, but approved later activation must still confirm remote access and the
  real service checkout identities before completion authority is introduced.
- The accepted repository/doctor baseline remains the absent worktree-local `kanban/CI-1.md`; no new unrelated
  repository failure appeared.

## Builder Repair Evidence - 2026-07-22 13:07 KST

- Fail-closed publication regression: RED with one unexpected `heartbeat` plus manager replacement; GREEN `1 passed`.
- Final-byte mandatory matrices: exact integration `41 passed`; frozen affected `459 passed, 1 skipped, 23
  deselected`; orchestrator compatibility `326 passed`.
- Static and structure: Ruff passed; Pyright reported no diagnostics; executable lazy/AST sentinel passed; the
  baseline-delta product scan found zero new line/nesting limit crossings.
- Whitespace: tracked diff clean; complete 73-file untracked no-index scan clean.
- Optional AIDT matrix: `751 passed, 1 skipped`; one temporary-Git timeout at
  `tests/test_aidt_worktree_recovery_proofs.py::test_removed_recovery_rejects_incomplete_and_drifted_shapes[branch_absent-collision]`.
  The exact isolated node then passed in `1.43s`. The timeout and test were not changed.
- Fresh `qa-auditor` still owns verification and verdict. No `Verdict: PASS`, GOAL tick, run-state finalization,
  literal gate, commit, live operation, merge, push, or deployment is claimed here.

## Fresh Exact Verify Evidence - 2026-07-22 13:51 KST

- Focused fail-closed publication: `1 passed in 0.60s`; exact prior manager/generation retained and the tick stopped
  before heartbeat or fetch.
- Exact Core/workspace barrier: `41 passed in 1.09s`.
- Frozen affected controls: `459 passed, 1 skipped, 23 deselected in 163.54s`.
- Orchestrator compatibility: `326 passed in 17.81s`.
- Full AIDT/worktree/recovery/workspace matrix: `752 passed, 1 skipped in 633.53s`; the builder's isolated timeout did
  not recur in that command.
- Full repository: `4 failed, 2189 passed, 6 skipped in 1366.88s`. The accepted CI-1 missing-card baseline remained,
  but a stale lifecycle fixture was rejected for mismatched workflow identity and two real `git worktree add`
  processes independently exceeded the frozen 10-second deadline.
- Repeated Git timeout is not waived: the builder saw one occurrence and this fresh full-suite run saw two more.
  Exact nodes, root-cause hypothesis, and the red-first repair boundary are recorded in the latest `R-LOOP.md`
  section.
- Remaining static, doctor, completion-marker, and literal commit gates were intentionally not run after repository
  parity failed. No product/test file, `GOAL.md`, `run-state.json`, Z marker, commit, merge, push, deploy, or live
  system was changed by this verifier.

## Lifecycle Fixture Repair Builder Evidence - 2026-07-22 14:00 KST

- RED: `tests/test_agent_lifecycle_e2e.py::test_file_board_e2e_auto_triage_dispatches_and_reaches_done` failed with
  actual `Todo` versus expected `In Progress` after fail-closed workflow-identity rejection.
- GREEN: the same node passed (`1 passed in 0.36s`) after separating `_orch`'s process workflow path from its fake
  workspace path; all lifecycle tests passed (`5 passed in 0.72s`).
- Regression checks: exact Core/workspace barrier `41 passed in 1.43s`; orchestrator compatibility
  `326 passed in 20.16s`; targeted Ruff and whitespace checks passed.
- Scope: fixture-only; no product behavior, universal fail-closed path, assertions, timeout, Git runner, or verifier-
  owned state changed. Fresh Exact Verify remains required.

## Fixed-Deadline Git Diagnosis Builder Evidence - 2026-07-22 14:22 KST

- Added three focused runner characterizations: early success before the deadline; real process-group kill/reap at
  the deadline; and 20 exact real add/remove cycles with exact argv, per-add safety margin, registration/path,
  descriptor, child-process, and reader-thread cleanup assertions.
- Focused characterizations: `3 passed in 12.38s`; isolated historical-plus-characterization matrix:
  `6 passed in 21.59s`; full Git-state runner file: `121 passed in 33.96s`.
- Exclusive provisioner plus persisted-recovery suites: `218 passed in 516.28s`; zero timeout or resource-leak
  failure. Four prior ordinary repeats of the three historical nodes were also green.
- Comparative timing: the 20-cycle test took `8.41-8.83s` with dedicated basetemps versus `11.96-12.82s` while the
  shared pytest session area was active. Eight shared session roots coexisted and their IDs advanced during a period
  when this builder launched no pytest.
- The repository contains no exclusive/cross-process pytest wrapper. `scripts/git_quality_gate.py` defines one
  serial CI-parity pytest invocation; the literal supergoal commit gate consumes recorded evidence but does not
  serialize evaluators.
- Binary recommendation: no product or fixture mutation. Run exact verification exclusively with a dedicated
  basetemp; reopen diagnosis only if the same timeout recurs there.
- Run-to-prove (`agent_detected`):
  `rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider --basetemp=/private/tmp/f003-git-runner-20260722-1420 -q tests/test_aidt_worktree_git_state.py`.
- Run-to-prove (`agent_detected`):
  `rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider --basetemp=/private/tmp/f003-git-provisioner-recovery-20260722-1422 -q tests/test_aidt_worktree_provisioner.py tests/test_aidt_worktree_recovery_proofs.py`.
- Ruff and targeted whitespace passed. No source, timeout, assertions, lifecycle fixture, verifier-owned state,
  commit, merge, push, deployment, or live system changed.

## Exclusive Final Verification and Ask Matt Review - 2026-07-22 15:09 KST

- Exclusive evidence is retained: lifecycle node `1 passed`; lifecycle file `5 passed`; three runner
  characterizations plus three historic timeout nodes `6 passed`; exact barrier `41 passed`; frozen affected matrix
  `459 passed, 1 skipped, 23 deselected`; orchestrator matrix `326 passed`; complete AIDT/worktree matrix
  `755 passed, 1 skipped`; full repository `2195 passed, 6 skipped`, with only the accepted missing `kanban/CI-1.md`
  failure. No fixed-deadline Git timeout recurred.
- Ruff passed; Pyright reported 0 errors/warnings/information; six executable lazy/AST sentinels passed; the
  evaluator scan found no new 50-line/nesting-4 crossing; tracked and all 73 untracked files were whitespace-clean.
  Doctor passed the real workspace-writability probe and retained only the accepted absent worktree `kanban/` red.
- Mandatory Ask Matt spec review found a new blocker: Core publishes the new runtime generation before manager
  replacement. A manager-stage exception returns fail-closed for the tick but leaves the runtime generation changed,
  contradicting the frozen atomic-publication rule and exact prior-generation preservation.
- Mandatory standards review found no shipped operator example for the new `jira_intake`, `aidt_routing`, and
  `aidt_worktree` configuration shapes; the single Frontier 003 Build slice also exceeds Symphony's one-contract,
  roughly-five-files/500-lines ticket-quality standard.
- Untracked `uv.lock` is generated output outside the Frontier 003 cohesive scope and has no acceptance trace.
- Final verdict remains REVISE. GOAL ticks, Finalize run-state, Z marker, and literal commit gate were intentionally
  not written or run.

## Post-Ask-Matt Final Verification - 2026-07-22 16:37 KST

- Jira response-status correction: 5 focused and 235 affected cases passed with exact membership and zero-write
  failure proof.
- Atomic-publication focused barrier: 3 passed; expanded exact barrier: 42 passed. Lifecycle/example validation:
  7 passed. Frozen affected matrix: 459 passed, 1 skipped, 23 deselected in 67.07s.
- Orchestrator compatibility: 326 passed. Complete AIDT/worktree/Git matrix: 756 passed, 1 skipped in 758.27s; no
  fixed-deadline Git timeout recurred.
- Full repository: 2202 passed, 6 skipped; the sole failure is the accepted pre-change
  `test_run_continuous_improvement_real_git_target_worktree_e2e` missing `kanban/CI-1.md` ledger entry.
- Full Ruff passed. Full Pyright reported 0 errors, warnings, or information. Six executable AST/lazy sentinels
  passed. Independent baseline-delta AST found 732 new functions with maximum 47 lines/nesting 4 and zero new limit
  crossings.
- The shipped example loaded and its temporary-copy doctor exited 0 with 12 PASS checks and the expected legacy
  viewer warning. Root doctor passed workspace writability outside the sandbox and retained only the accepted absent
  worktree `kanban/` failure.
- Fresh Ask Matt standards and spec reviews both returned PASS. Six standards observations are advisory follow-up
  opportunities; none contradicts a frozen criterion or creates a safety bypass in normal immutable-key publication.
- `git diff --check origin/dev`, `git diff --check`, and the complete 87-file untracked no-index scan are clean after
  the docs-only removal of three historical date-line trailing-space pairs. No commit, merge, push, deployment, live
  Jira/AIDT call, backend start, or product-repository mutation was performed.
- Frontier 001's exact literal gate passed on its first final invocation. Frontier 003's first invocation reached the
  run-state gate and exposed a stale string-form `forced_reflection`; Finalize state was corrected to the schema's
  required `null`, and the same exact command then passed every check, including its single Z marker.
