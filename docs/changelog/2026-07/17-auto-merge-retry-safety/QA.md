# QA - retry-safe terminal auto-merge

All testing results as succinct plain-language checklist sentences. Evidence lives in `qa/`.

- Verdict: PASS

## Before

- [x] Existing auto-merge suite is green before the change - `uv run --extra dev pytest -q tests/test_auto_merge.py` -> `11 passed in 17.23s`.
- [x] Static path inspection shows `_build_script` exits with `nothing_to_apply` before configured-upstream synchronization, and Python maps that status to `ok=True` - evidence: codebase graph snippets recorded in `PLAN.md`.
- [x] Consumer baseline enumerated: the only production caller is `Orchestrator._auto_merge_done_gate_or_block`; existing tests exercise fresh merge, upstream success/failure, dirty overlap, missing branch, excluded roots, capture, and conflict - evidence: codebase graph trace recorded in `PLAN.md`.

## Results

- [x] RED reproduced through the public interface - `uv run --extra dev pytest -q tests/test_auto_merge.py -k retries_rejected_push_until_upstream_matches` -> `1 failed, 11 deselected in 3.27s`; the second call returned `AutoMergeResult(ok=True, status='nothing_to_apply', detail='SKIP: nothing differs')` while the configured upstream remained stale.
- [x] The same regression is green after routing no-op retries through upstream synchronization - `1 passed, 11 deselected in 3.74s`; rejection remains `push_failed`, then an accepted retry reaches exact SHA equality without another merge commit.
- [x] Independent retry proof - `.venv/bin/python -m pytest -q tests/test_auto_merge.py -k retries_rejected_push_until_upstream_matches` -> `1 passed, 11 deselected in 3.70s`.
- [x] Focused auto-merge suite - `.venv/bin/python -m pytest -q tests/test_auto_merge.py` -> `12 passed in 17.50s`.
- [x] Full trusted suite and coverage gate - `.venv/bin/python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80` -> `1382 passed, 5 skipped, 2 warnings in 107.68s`; total coverage `83.82%` (required `80%`).
- [x] Ruff - `All checks passed!`.
- [x] Pyright source analysis is clean when the worktree interpreter is explicit - `.venv/bin/pyright --pythonpath .venv/bin/python` and an activated `.venv/bin/pyright` each report `0 errors, 0 warnings, 0 informations`.
- [x] Diff integrity - `git diff --check` exits `0`; tracked production/test changes are limited to `src/symphony/utils/auto_merge.py` and `tests/test_auto_merge.py`; `uv.lock` is absent.
- [x] Modified-symbol trace is scope-clean: `_build_script` composes new preflight, target, merge-safety, no-op, merge, capture, and upstream-sync helpers; the public function is consumed by `Orchestrator._auto_merge_done_gate_or_block` from worker-exit and reconcile paths, and the full suite covers that consumer's failure gate.
- [x] Iteration 1 RED evidence is retained: the frozen `-k retry_after_push_failure` selector exited `5` with `12 deselected`, so it proved no retry behavior before the test was renamed.
- [x] Iteration 1 RED evidence is retained: the no-capture regression did not reach the capture-configured staged-empty retry branch in `_build_merge_phase`.
- [x] Iteration 1 environment RED evidence is retained: bare `.venv/bin/pyright` exited `1` with 24 unresolved-import errors and 3 warnings when the worktree environment was not active.
- [x] Iteration 1 environment RED evidence is retained: `.venv/bin/symphony doctor ./WORKFLOW.md` exited `1` when the isolated worktree lacked its ignored `kanban/` board link.
- [x] Iteration 2 frozen selector - `.venv/bin/python -m pytest -q tests/test_auto_merge.py -k retry_after_push_failure` -> `2 passed, 11 deselected in 8.37s`.
- [x] Iteration 2 assigned selector - `.venv/bin/python -m pytest -q tests/test_auto_merge.py -k retries_rejected_push_until_upstream_matches` -> `2 passed, 11 deselected in 8.24s`.
- [x] Both collected parameter cases are public-interface Git regressions: `no-capture-preflight-noop` reaches preflight `sync_upstream`; `empty-capture-staged-noop` reaches merge abort, staged-empty `sync_upstream`; repeated rejection remains `push_failed`, accepted recovery verifies remote/local exact SHA equality, and the merge count remains `1` throughout.
- [x] Iteration 2 owning module floor - `.venv/bin/python -m pytest -q tests/test_auto_merge.py` -> `13 passed in 21.34s`.
- [x] Iteration 2 full trusted suite - `.venv/bin/python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80` -> `1383 passed, 5 skipped, 2 warnings in 110.03s`; total coverage `83.82%`.
- [x] Iteration 2 Ruff - `.venv/bin/ruff check src tests` -> `All checks passed!`.
- [x] Iteration 2 Pyright with the approved worktree environment - `env PATH=/private/tmp/symphony-auto-merge-retry-safety-20260717/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin .venv/bin/pyright` -> `0 errors, 0 warnings, 0 informations`.
- [x] Iteration 2 workflow health - `.venv/bin/symphony doctor ./WORKFLOW.md` exits `0`; every check passes, including the ignored host-board symlink resolving to 16 tickets.
- [x] Iteration 2 diff and inventory - `git diff --check` exits `0`; `uv.lock` is absent; only the two approved tracked code/test files and expected run-vault/changelog files appear; the original checkout still contains only its two preserved July 12 untracked user files.
- [x] Changed-symbol coverage is complete: `_build_script`, `_build_preflight_phase`, `_build_target_preflight_block`, `_build_merge_safety_block`, `_build_nothing_to_apply_block`, `_build_merge_phase`, `_build_capture_block`, and `_build_upstream_sync_block` are executed by the 13-test module suite; module coverage is `90%`, and its only missed lines (`111-121`, `188-197`) are unchanged exception and unknown-return handling outside the diff.
- [x] Compatibility is unchanged: `auto_merge_on_done_best_effort` keeps its keyword-only signature; exit `43` still maps to successful `nothing_to_apply`, exit `52` still maps to non-success `push_failed`, and the sole production caller remains `Orchestrator._auto_merge_done_gate_or_block` from worker-exit and reconcile paths.

Backward-trace: clean

Every final diff hunk maps to the approved retry/refactor and black-box regression scope, every changed helper and production consumer has re-run coverage, and no unrelated or original-checkout user file is present in the worktree diff.

## Commands

| Command | Source | Proves |
|---|---|---|
| `uv run --extra dev pytest -q tests/test_auto_merge.py` | frozen_repo | Existing and new auto-merge behavior |
| `uv run --extra dev pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80` | frozen_repo | Full suite and coverage floor |
| `uv run --extra dev ruff check src tests` | frozen_repo | Lint |
| `uv run --extra dev pyright` | frozen_repo | Source type checking |
| `git diff --check` | evaluator_owned | Patch whitespace integrity |
| `uv run --extra dev pytest -q tests/test_auto_merge.py -k retries_rejected_push_until_upstream_matches` | agent_detected | run-to-prove: rejected-push retries synchronize the existing merge without duplication |
| `.venv/bin/python -m pytest -q tests/test_auto_merge.py -k retries_rejected_push_until_upstream_matches` | evaluator_owned | Independent assigned retry proof |
| `.venv/bin/ruff check tests/test_auto_merge.py src/symphony/utils/auto_merge.py` | agent_detected | Correction lint for the changed test and source |
| `.venv/bin/python -m pytest -q tests/test_auto_merge.py` | frozen_repo | Independent module regression floor |
| `.venv/bin/python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80` | frozen_repo | Independent full suite and coverage floor |
| `.venv/bin/ruff check src tests` | frozen_repo | Independent lint |
| `.venv/bin/pyright` | frozen_repo | Exact type-check invocation; failed interpreter discovery in an unactivated shell |
| `.venv/bin/pyright --pythonpath .venv/bin/python` | evaluator_owned | Type analysis with the already-created interpreter selected explicitly |
| `env PATH=/private/tmp/symphony-auto-merge-retry-safety-20260717/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin .venv/bin/pyright` | frozen_repo | Iteration-2 type analysis with the amended worktree environment active |
| `.venv/bin/symphony doctor ./WORKFLOW.md` | evaluator_owned | Iteration-1 missing-board RED and iteration-2 green workflow health |
| `.venv/bin/python -m pytest -q tests/test_auto_merge.py -k retry_after_push_failure` | frozen_repo | Frozen GOAL selector; both retry paths |
| `git status --short --branch` and `git ls-files --others --exclude-standard` | evaluator_owned | Expected worktree inventory and absent package-manager noise |

## QA

Tool: none (library/Git integration path; no browser surface)

## Reproduction Fidelity

- Fidelity level: synthetic-representative
- Residual risk from data gap: a local bare Git remote reproduces push rejection and exact ref reads but not hosted-provider authentication or branch-protection messaging.
- Post-deploy confirmation plan: none required for local-library behavior; any release smoke should verify a configured upstream ref equals the local target after a forced push failure and retry.

## Residual Risk

- Environment: the local bare Git remote proves exact ref and rejection/recovery semantics but does not simulate hosted-provider authentication or branch-protection message text; those provider-specific messages do not affect this result-status invariant.
- Existing warning: the full suite emits two `aiohttp` `NotAppKeyWarning` notices from unchanged `src/symphony/server.py:212`; they are outside this diff and do not affect the auto-merge path.
- Follow-up: separate OpenCode terminal-heartbeat, malformed-scorecard-row, and pre-push parity slices remain outside this surgical run.
