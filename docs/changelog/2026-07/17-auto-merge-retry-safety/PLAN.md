# PLAN - retry-safe terminal auto-merge

Frozen after approval. A fresh-context implementer reads ONLY this file (plus the latest
`R-LOOP.md` section on re-entry) and builds it. Changes after approval append a dated `## Amendment`.

## Approval

- Status: approved-by-user
- Record: 2026-07-17T06:25:29Z; user: `proceed with the issue fix`

## Intent

- Goal: close the confirmed retry gap in the July 11 upstream-verification update while making the
  affected shell-script assembly easier for agents to navigate.
- Constraints: source and integration ref are `dev@ee04e5d`; work only on
  `codex/auto-merge-retry-safety-20260717`; preserve the public Python signature/result statuses and
  all unrelated merge behavior; preserve the original checkout's two untracked July 12 documents;
  no dependency changes, workflow-state redesign, release, merge, or push.
- Tradeoff: keep one Bash subprocess because the existing merge transaction and process-substitution
  behavior depend on Bash, but deepen the Python module by naming preflight, merge, and upstream-sync
  phases instead of moving Git semantics across several process calls.
- Rejected: roll back the July 11 upstream verification (reopens remote-staleness); reset the local
  merge after a push failure (destructive to a shared target branch); treat `nothing_to_apply` as
  unconditional success (the defect); broaden into the Orchestrator god-class or local quality-gate
  redesign (separate concerns).
- Completion promise: the failure is proven red first, fixed through the public interface, the focused
  and full trusted gates pass, and an independent verifier finds no uncovered changed consumer.
  Stop after `max_iterations=3`, or earlier on a requirement-level blocker.

## Steps

1. In `tests/test_auto_merge.py`, add one vertical black-box regression through
   `auto_merge_on_done_best_effort`: configure a bare upstream that rejects the first push, call the
   public function again while rejection remains, and prove the second call currently returns a false
   success or otherwise fails the upstream invariant. Record the RED output in `QA.md`.
2. Extend the same scenario to remove the rejection and retry: require the existing local merge commit
   to reach the upstream, require local/remote SHA equality, and require no duplicate merge commit.
3. In `src/symphony/utils/auto_merge.py`, route the already-integrated/nothing-staged path through the
   same configured-upstream push-and-exact-verify phase used after a fresh merge. Split `_build_script`
   into small named phase builders so branch/dirty/conflict checks, merge commit creation, and upstream
   synchronization each have one purpose. Do not change the public Python call or existing status map.
4. Add the decision, root cause, alternatives, and proof commands to
   `docs/changelog/changelog-2026-07-17.md`. Do not touch the original checkout's untracked July 12 docs.
5. Run the focused auto-merge suite, then full coverage, Ruff, Pyright, and diff checks. Hand the final
   diff and evidence to a fresh `qa-auditor`; loop only through `R-LOOP.md` if it finds a grounded gap.

## Acceptance checklist

- [ ] A retry after a rejected upstream push remains non-success while the remote ref is stale and re-attempts synchronization.
- [ ] Once the upstream accepts the retry, the existing local merge is pushed and verified without creating a duplicate merge commit.
- [ ] Existing successful merge, missing branch, dirty overlap, excluded path, capture, conflict, and no-upstream behavior remains green.
- [ ] Script generation is split into cohesive named phases while the public Python interface and result statuses remain compatible.
- [ ] The repository's trusted test, lint, type, and coverage gates pass.
- [ ] The decision and rejected alternatives are recorded without modifying unrelated work.

## Tools & Skills

- Process: `supergoal` LEGACY role loop and `tdd` red-green-refactor.
- Discovery: codebase-memory `search_graph`, `trace_path`, `get_code_snippet`, and `search_code`.
- Focused: `uv run --extra dev pytest -q tests/test_auto_merge.py`.
- Full: `uv run --extra dev pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80`.
- Static: `uv run --extra dev ruff check src tests`; `uv run --extra dev pyright`.
- Diff: `git diff --check`; `git status --short`; `git diff --stat dev...HEAD`.

## Verification strategy

- Before proof: existing `tests/test_auto_merge.py` is green, then the new retry test is RED because a
  stale configured upstream can be followed by an `ok=True` no-op result.
- Step -> GOAL criterion: steps 1-2 -> criteria 1-2; step 3 -> criteria 3-4; step 4 -> criterion 6;
  step 5 -> criteria 3-6.
- Trusted commands: `uv run --extra dev pytest -q tests/test_auto_merge.py` (frozen_repo),
  `uv run --extra dev pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80`
  (frozen_repo), `uv run --extra dev ruff check src tests` (frozen_repo), and
  `uv run --extra dev pyright` (frozen_repo).

## Domain Brief

- Knowledge path: pending user choice because `.domain-agent/config.json` is absent; this run can use
  the ephemeral brief below without adding local knowledge files.
- Selected sources: `WORKFLOW.md`, `kanban/SMA-31.md`, `kanban/SMA-32.md`, recent July 11 commit diff,
  `src/symphony/utils/auto_merge.py`, `tests/test_auto_merge.py`, and `.github/workflows/tests.yml`.
- Stable terms: target branch = integration branch; upstream = configured remote tracking ref;
  terminal auto-merge = Done gate that integrates `symphony/<ID>` and reports a structured result.
- Invariants: `ok=True` means the merge gate is safe for dependents; a configured upstream must exactly
  equal the local target; unrelated dirty host files remain untouched; excluded paths block the merge.
- Current-code verification: `auto_merge_on_done_best_effort`, `_build_script`,
  `_build_upstream_sync_block`, and `Orchestrator._auto_merge_done_gate_or_block` traced now.
- Entry points: `src/symphony/orchestrator/core.py` ->
  `src/symphony/utils/auto_merge.py:auto_merge_on_done_best_effort`.
- Test commands: focused auto-merge suite, full coverage gate, Ruff, and Pyright listed above.
- Gaps: live network push is intentionally not required; bare local remotes exercise the exact Git
  semantics deterministically.

## Grounding ledger

- Which refs? -> current `dev`, `origin/dev`, and `main` all resolve to `ee04e5d`; use `dev` for source
  and integration and isolate changes in the run worktree.
- Is this a real retry defect? -> `_build_script` exits `43` before `_build_upstream_sync_block` on the
  no-diff path, while Python maps `nothing_to_apply` to `ok=True`; the public caller trusts `ok` to
  release the Done gate.
- Which consumers? -> graph trace shows one production caller,
  `Orchestrator._auto_merge_done_gate_or_block`, reached from worker-exit and reconcile paths; existing
  public-interface integration tests cover the module's other result branches.
- Why not reset the merge? -> the target may contain unrelated operator commits; retrying idempotent
  synchronization is safer than rewriting target history.
- Why not change orchestration? -> the invariant belongs in the auto-merge module, where both fresh and
  retry paths can share one synchronization phase.

## Audit findings and scope boundary

- Selected, High: three independent read-only passes reproduced the auto-merge retry false success:
  first call `push_failed`; later call `nothing_to_apply` with local and remote target SHAs unequal.
  This is the smallest false-green path that directly lets the Done merge gate trust stale integration.
- Deferred, High: OpenCode heartbeat events can refresh the timestamp used by terminal cleanup and may
  retain a terminal worker indefinitely. This crosses backend event classification and orchestrator
  lifecycle state, so it needs a separate red-green slice.
- Deferred, Medium: an AC Scorecard row shorter than its dynamic `Result` column is skipped and can
  pass malformed evidence. This belongs to the contract parser and needs a separate fail-closed slice.
- Deferred, Medium: the local pre-push gate omits Ruff, Pyright, and coverage even though CI runs them.
  CI still detects those failures after push, so it is lower risk than the runtime false success.

## Amendment - 2026-07-17T06:38:18Z

- The first `uv run` created an untracked root `uv.lock` because this checkout has no committed lockfile.
  The builder removed it. Exact Verify should invoke the already-created worktree environment directly:
  `.venv/bin/python -m pytest`, `.venv/bin/ruff`, and `.venv/bin/pyright` with the same arguments.
- Reason: preserve the approved proof scope without leaving package-manager noise in the final diff.

## Amendment - 2026-07-17T06:53:13Z

- Pyright's direct wrapper does not infer its Python import environment from the wrapper path. Exact
  Verify therefore runs the same `.venv/bin/pyright` executable with `.venv/bin` first on `PATH`, the
  non-interactive equivalent of activating the worktree environment. This reports zero errors without
  creating a lockfile; hardcoding a disposable worktree path in repository configuration was rejected.
- The isolated worktree did not initially contain the ignored `kanban/` board root. Exact Verify links
  it to the host board using the same ignored symlink shape as `scripts/symphony-setup-worktree.sh`, then
  runs `.venv/bin/symphony doctor ./WORKFLOW.md`. The link is environment setup and cannot enter the diff.
