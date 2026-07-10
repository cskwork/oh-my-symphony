# Changelog — 2026-07-10

## AF-01 — Identity-safe worker exit path (P0, DEBUG)

Ticket: docs/improvements/tickets/2026-07-09/AF-01-identity-safe-worker-exit.md
Branch: debug/af-01-identity-safe-worker-exit → dev

### What

A force-ejected zombie worker's cleanup path could eject the fresh replacement entry the backoff
retry installed under the same issue id, because neither the worker `finally` nor the `_running`
pop in `_on_worker_exit_impl` verified task identity. This was the root cause of the residual
`finished_without_cleanup` / `worker_running_entry_vanished` incidents (OLV-002).

Changes (src):

- `dispatch_state.py` — new `DispatchState.entry_foreign_to(issue_id, task)`: True only when a
  running entry exists AND its `worker_task` is populated AND disagrees with `task`. The single
  identity predicate both exit-path gates reuse.
- `core.py` worker `finally` — computes `owning_task = asyncio.current_task()`; a foreign or
  missing entry skips `exit_started_at` stamping and the `_on_worker_exit` call entirely
  (`worker_finally_stale_entry` warning on foreign); the owning task is passed down.
- `core.py` `_on_worker_exit` / `_on_worker_exit_impl` — keyword-only `owning_task` threaded
  through; when provided, a missing or foreign entry logs `worker_exit_stale_task` and returns
  BEFORE any mutation (including the `_claim_released_at` / `_pause_events` pops that previously
  ran ahead of the entry-None check). This closes the TOCTOU across the `asyncio.shield` yield
  between the finally's check and the pop.
- `core.py` `_on_worker_task_done` — retrieves `task.exception()` (CancelledError-guarded) BEFORE
  the entry-identity early return; a post-pop exception now logs
  `worker_task_errored_after_cleanup` instead of asyncio's unstructured "Task exception was never
  retrieved". Passes `owning_task=task` into the exit it spawns.
- `core.py` `_force_eject_zombie` docstring — the "no-op on a missing entry, so race-safe" claim
  was false once a retry re-installs a fresh entry; now documents the identity-gate contract.

Tests: 7 new in `tests/test_orchestrator_dispatch.py` (RED-first: the two core race tests failed
verbatim on unmodified source). Full suite 1286 passed / 2 skipped (baseline 1279 + 7 new).

### Why (decisions and rejected alternatives)

- **`entry.worker_task is None` counts as OWNED, not foreign.** Strict `entry_owned_by` semantics
  in the exit path (rejected) broke 6+ existing tests that drive `_run_agent_attempt` /
  `_on_worker_exit_impl` directly against hand-installed entries that never went through
  `_dispatch`. Only a populated `worker_task` that disagrees is a genuine identity conflict; in
  production, `_dispatch` always binds `worker_task` before the worker coroutine's first slice.
- **`owning_task=None` callers skip the check.** Legacy/internal call sites and tests keep
  pre-AF-01 behavior; only the two real exit paths (worker `finally`, done-callback) pass it.
- **Identity checks over locks/generation counters (rejected).** asyncio's single-thread
  cooperative scheduling means a re-check at each mutation point suffices; the only yield between
  the finally's gate and the pop is the `asyncio.shield` boundary, which the impl-side re-check
  covers.
- **Missing entry now skips `_on_worker_exit` from the finally (behavior change, deliberate).**
  Force-eject already finishes the lease, pops the pause event, and schedules the retry; letting
  the stale finally run the exit handler against an absent/replaced entry is exactly the defect.
  Matches the ticket's `entry_owned_by` gate direction.
- **No version bump here.** Restoring intended behavior would be a patch-level bump per repo
  convention; left for a separate `chore(release)` commit when the AF batch ships.

Non-goals honored: no OS-process kill (AF-02), no reconcile Part A isolation (AF-07), no change to
two-stage eject timing.

Process note: build by local subagent; full-spec/edge-case improve passes and the adversarial
review ran on Codex CLI (user-directed); exact verification (full suite in the worktree run path,
`PYTHONPATH=<worktree>/src`) by the conductor.

## AF-02 — Force-eject kills all backend process groups (P0, BUILD)

Ticket: docs/improvements/tickets/2026-07-09/AF-02-force-eject-kills-all-backends.md
Branch: codex/supergoal-af-02

### What

Force-eject now owns a backend-neutral `agent_pgid`, kills it for every backend kind, and logs the
backend kind with the kill result. Per-turn backends publish a normalized pid event immediately
after each spawn, while Codex exposes its persistent app-server pid. Legacy
`codex_app_server_pid` input remains accepted, with normalized `agent_pid` taking precedence.

Process ownership now follows the complete backend lifecycle in memory and in the run registry:

- `RunRegistry.clear_backend_agent_pid` explicitly clears ownership; ordinary `heartbeat(None)`
  keeps its existing lease-only, preserve-pid meaning.
- One orchestrator helper synchronizes non-null pids through the established heartbeat path and
  clears persisted ownership explicitly for `None`.
- Start and turn boundaries synchronize in `finally`, so late start failures/cancellation and
  successful or failed per-turn completion cannot leave stale service-force-visible ownership.
- Confirmed phase/final/new-client stops clear ownership. A failed old-phase stop aborts the
  replacement and retains the last pid. Failed old or replacement cleanup remains unconfirmed
  across a later idempotent final stop, so neither path can erase its force-eject target.

### Why (decisions and rejected alternatives)

- **Confirmed teardown is required before replacement.** Swallowing an old `stop()` failure and
  erasing its PGID could run two backends for one issue while discarding the only force-eject
  target. The transition now logs and re-raises; only the successful-stop branch clears.
- **Explicit registry clear over sentinel heartbeat behavior.** Changing `heartbeat(None)` was
  rejected because callers rely on it to refresh a lease without mutating process ownership. A
  named operation makes destructive ownership change intentional and testable without a schema
  migration.
- **Lifecycle synchronization over event-only recording.** Events remain the immediate spawn
  signal, but start/turn `finally` boundaries cover pre-event failures, cancellation, and the
  post-child interval where orchestrator hooks may block.
- **No reaping expansion.** If a normal stop raises, ownership is deliberately retained because
  termination is unconfirmed. `safe_proc_wait`, AF-10 startup reclaim/kill, and unrelated process
  reaping remain outside AF-02.

Iteration-4 RED proved five ownership failures while two event compatibility controls passed.
GREEN evidence: 7 focused tests, 13 RunRegistry tests, 38 phase-transition tests, and 26 combined
AF-02 contract tests passed; full Ruff and changed-source Pyright passed. Full-suite exact
verification remains the fresh verifier's gate.

Iteration-5 RED exposed two idempotent-stop ownership losses: final cleanup erased an old pid after
the first stop marked closed and raised, and a failed replacement cleanup lost its new pid because
the caller still referenced the old backend. GREEN evidence: all three stop-confirmation
regressions passed, the phase-transition module passed 40 tests, and the combined AF-02 selector
passed 28; focused/full Ruff and `git diff --check` passed.

Iteration-6 exact verification exposed a compatibility assumption in six lifecycle reads: older
backend doubles without `pid` exited before their intended orchestration behavior. One normalizer now
treats an absent or non-integer pid as no live child without weakening the backend protocol. All 14
prior failures, 260 relevant neighbor tests, and 28 combined AF-02 tests pass; full Ruff, Pyright,
and `git diff --check` are clean. The fresh verifier still owns the final full-coverage gate.

Iteration-7 adversarial review found a process-group safety edge: Python booleans are integers, and
zero/negative values are not valid child process-group leaders. The shared normalizer now accepts
only positive, non-boolean integers at backend-property reads and event ingestion, and the final
force-eject boundary repeats the check as defense in depth. A present invalid normalized key is
ignored rather than falling back to legacy input. RED failed six unsafe cases; GREEN passed 11
focused controls, 34 combined AF-02 tests, and 266 affected-module tests. Full Ruff, Pyright, and
`git diff --check` pass; fresh exact verification remains the completion gate.

Non-goals honored: AF-01 task-identity changes preserved; no schema, startup reclaim, signal,
`safe_proc_wait`, or unrelated reaping changes.
## Linked-worktree setup lock uses the common Git directory

### What

`scripts/symphony-setup-worktree.sh` now resolves `git rev-parse --git-common-dir` to an
absolute path before acquiring its administrative lock. A real-script integration test runs the
hook with `SYMPHONY_WORKFLOW_DIR` set to a linked worktree, forces the macOS-style `mkdir` lock
fallback, and verifies worktree creation, board linking, and lock cleanup after the hook changes
into the ticket worktree. The same test exercises `flock` when that command is available.

### Why (decisions and rejected alternatives)

- **Common Git directory over `$HOST_REPO/.git`.** A linked worktree's `.git` is a file, so a lock
  cannot live beneath it; all linked worktrees share the directory reported by Git.
- **Resolve once before `cd` over retaining Git's relative path.** A primary worktree may report
  `.git`; converting it with `pwd -P` keeps fallback-lock cleanup correct after the script changes
  directories.
- **No timeout or retry changes.** Shortening the production wait would only hide the invalid lock
  path. The root path is fixed while the existing serialization and stale-lock behavior remain.

## OpenCode publishes JSONL telemetry before process exit

### What

`PerTurnCliBackend` now exposes an overridable stdout reader while retaining ownership of timeout,
cancellation, stderr collection, and process reaping. `OpenCodeBackend` frames JSONL from bounded
chunks, publishes the real OpenCode session ID immediately, and emits cumulative usage updates as
`opencode_usage` progress events. The same bytes are retained for the existing final-response
decoder; completion applies only events that were not already processed from the live stream.

### Why (decisions and rejected alternatives)

- **One stdout hook over an OpenCode-specific collector.** Duplicating `_collect` would let timeout
  and teardown behavior drift across backend implementations.
- **CLI JSONL over SQLite polling.** The documented stream is the backend boundary; OpenCode's
  internal database would add schema coupling and a second source of truth.
- **Apply each frame once over completion-time replay.** Live events expose token-budget progress,
  while a per-turn event multiset preserves pretty/multiline whole-JSON compatibility without
  double-counting JSONL usage.
- **Chunk framing over `readline`.** OpenCode text frames can exceed the stream reader's configured
  line limit; fixed-size reads avoid `LimitOverrunError` while still publishing complete lines.
- **Usage plus heartbeat progress.** Token frames are productive activity; heartbeats still cover
  quiet model and tool intervals that emit no JSONL.

## AF-03 through AF-16 — Orchestrator reliability decisions

Scope in this entry: AF-03 through AF-16. Detailed command evidence remains in
the three batch builder records under
`docs/changelog/2026-07/10-af-03-16-reliability/`.

### What and why

- AF-03: add a resume timestamp as the post-pause stall floor. Rewriting the
  real progress timestamp was rejected because it would misreport operator
  activity as model progress.
- AF-04: reject only an actual state delta while a worker owns the ticket,
  before writing any mixed PATCH fields. Blocking all running-ticket edits was
  rejected because metadata and same-state edits are existing supported behavior.
- AF-05: normalize productive Plain, Gemini, and Claude completion previews at
  each backend boundary and reject successful empty stdout before completion.
  Broadening generic preview parsing was rejected because the emitting adapters
  can satisfy the existing canonical `message` contract directly.
- AF-06: give atomic temps a tracker-owned marker, retain defensive filtering
  for legacy `.tmp-*.md` files, and sweep only safety-aged marker-owned or
  parseable legacy ticket artifacts. A broad `.tmp-*` sweep was rejected
  because it can delete operator-owned files.
- AF-07: process cancellation escalation before pause, isolate reconcile Part
  A per issue, and make force-eject lease/retry cleanup exception-safe. Letting
  operator pause hide system cancellation was rejected as a slot leak.
- AF-08: bound worker drain to the existing force-eject grace window, then log
  and reap recorded survivors and finish each survivor lease before clearing
  ownership or closing SQLite. Sequential unbounded awaits were rejected
  because `stop()` must have a deadline.
- AF-09: close and reap a persistent Codex backend after the malformed-line
  limit so pending and future turns fail promptly, while leaving process
  teardown retryable by later `stop()`. Calling `stop()` from its own reader
  was rejected because it would cancel and await itself.
- AF-10: move a dead-owner lease through `reclaiming`, kill its recorded
  process group outside SQLite, then finalize it as `orphaned`. Failed or
  interrupted cleanup stays lease-blocking for startup retry. A non-null pid
  migration and OS side effects inside the transaction were rejected for
  backward compatibility and failure isolation.
- AF-11: keep the documented lifetime CI cap and add a one-warning latch,
  full-interval lease retry, terminal-persist idleness, and CI/worker exclusion.
  Automatic interval reset was rejected because it silently re-enables spend.
- AF-12: warn on parse drops and duplicate ids, collapse duplicates by sorted
  path, reject non-canonical duplicate creation, and serialize delete with the
  existing per-ticket lock; also treat an omitted running id as a visible
  degraded signal. Cross-process locking and CAS redesign were rejected.
- AF-13: compare case-normalized indexes in configured active-state order,
  retaining static default behavior for callers without configuration. Static
  English-only rewind pairs were rejected.
- AF-14: close by research with no production change. Codex 0.144 and the
  checked-in 0.130 schema both require token-usage `last` and `total`; a
  last-only defensive branch would support an unobserved invalid protocol.
- AF-15: remove reader-less completed-id retention and clear issue diagnostics
  on stop. Bounding `_completed` was rejected because there is no consumer.
- AF-16: render first, continuation, and phase-rebuild prompts with the ticket
  lifetime numerator and `max_total_turns` denominator while retaining
  `max_turns` as the execution cap. Per-attempt prompt counters were rejected
  because they disagree with the lifetime guard.
- AF-05 integration: a canonical productive `message` resets G2, while three
  later empty completions still persist `empty_response_loop` and cancel.

Evidence: `docs/changelog/2026-07/10-af-03-16-reliability/builder-orchestrator.md`.

### Edge-case improver follow-up

- AF-10: reject non-positive recovered backend pids at the startup signalling
  boundary. A registry migration and a global process-helper change were
  rejected because boundary validation is smaller and preserves existing
  storage compatibility.
- AF-05: ignore trailing whitespace-only Claude text blocks while preserving
  the original last meaningful block. Concatenating every text block was
  rejected because it would mix tool narration into the completion preview.

## AF-03..AF-16 — exact verification closure

The `10-af-03-16-reliability` run vault is closed: all 16 GOAL criteria are
ticked after re-verification on `dev@ee64bad` — `python -m pytest -q`
1363 passed / 5 skipped (89s), `ruff check src tests` clean, `pyright src`
0 errors. The four adversarial findings (AF-06/08/09/10) were confirmed fixed
in HEAD and their six regressions re-run green in isolation. Residual risks
recorded in the vault QA: the AF-04 guard-vs-dispatch concurrency window and
AF-10 pid-reuse identity (no birth-identity check on persisted pids). With
AF-01 (4de380f) and AF-02 (793813a) this closes the 2026-07-09 reliability
audit — all 16 tickets resolved.

## Done-squash orphan lineage (live E2E finding)

A disposable opencode file-board E2E surfaced a defect in
`commit_workspace_on_done`: the one-commit-per-ticket squash always
soft-reset to the recorded `symphony.basesha` fork point. When Verify had
already merged the branch (`--no-ff`), the squash rewrote the branch onto an
orphan lineage, so the post-Done fallback merge computed its merge base at
the stale fork point and hit guaranteed add/add conflicts (observed live on
the dated changelog file), demoting a successfully merged Done ticket to
Blocked and opening an RCA. The startup auto-commit path re-fired the same
reset on the preserved workspace, compounding it.

Fix: advance the squash base to `merge-base(HEAD, symphony.mergetargetbranch)`
when that merge base descends from the recorded fork point. Never-merged
branches keep the exact previous behavior (merge base == fork point); a
fully merged, clean workspace now no-ops instead of minting an orphan
snapshot commit.

Rejected alternatives:

- Running the fallback merge before the squash: reorders the Done
  finalization pipeline across reconcile and startup paths — much wider
  blast radius for the same effect.
- Making the fallback merge tolerate conflicts (`-X ours`/`theirs`): silently
  drops one side's work instead of surfacing it.
- Skipping the post-Done fallback when Verify already merged: strands
  genuinely unmerged Learn-stage residue (wiki/changelog write-back) on the
  branch.

Evidence: RED failures `test_commit_workspace_on_done_squashes_onto_merged_
lineage` and `..._noops_when_fully_merged_and_clean` on pre-fix code; full
suite 1366 passed / 5 skipped post-fix.

## Verify-contract evidence false positives (live E2E finding)

Two independent opencode Verify agents across two E2E runs were rewound by
the stage contract for evidence cells that satisfy the shipped prompt's
stated rules (`docs/symphony-prompts/file/stages/verify.md` lines 8 and 23):

- A cell citing an existing artifact with a trailing qualifier
  (`` `qa/manual-acceptance.log` (README grep block)``) failed because the
  validator treated the whole cell as one path, gluing the qualifier onto
  the existence check.
- Security Audit rows with an `n/a` result and a prose reason failed even
  though the prompt requires artifacts only "when a row needs proof".

Fix in `contracts.py` `_cited_path_failures`: prefer inline-code-span
extraction (each backticked path validated independently, every cited
`qa/`/`work/` artifact must exist, at least one required; qualifier prose
tolerated), falling back to the previous whole-cell parse when a cell has
no spans; skip evidence validation for Security Audit rows whose result is
`n/a` (AC Scorecard rows are never exempt).

Rejected alternatives:

- Relaxing the "no prose in cells" prompt rule instead: the strictness is
  useful guidance; only enforcement of the two false-positive shapes was
  wrong.
- Accepting any cell that mentions a path anywhere (no existence check):
  fabricated citations must keep hard-rewinding.
- Exempting `n/a` AC Scorecard rows too: acceptance criteria always need
  proof.

Evidence: RED on the three new regressions pre-fix; full suite
1371 passed / 5 skipped post-fix; prompt-anchor suite untouched and green.

### Follow-up: evidence-cell examples in the Verify prompts

Run #3 of the E2E loop showed the remaining contract rewinds are true
positives (source anchors like `README.md:13,21,36` and bare prose in
pass-result rows) — three different agent sessions violated the stated rule
somewhere despite it being explicit. Added one valid/invalid example pair to
the Verify stage prompts (file and linear variants) since examples raise
first-pass compliance where rules alone do not; the prompt-anchor suite is
unchanged and green.
