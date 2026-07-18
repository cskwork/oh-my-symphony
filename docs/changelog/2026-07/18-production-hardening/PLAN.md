# PLAN - Production hardening

Proposed freeze. A fresh-context implementer reads ONLY this file (plus the latest `R-LOOP.md`
section on re-entry) and builds it, so the plan is self-sufficient. After approval and the
domain-context decision, stamp it frozen; later changes append a dated `## Amendment`.

## Approval

- Status: approved-by-user
- Critic status: plan, scope, and verification re-reviews report no remaining approval blocker.
- Record: User approved completion of all planned fixes, improvements, final tests, and a Git commit
  on 2026-07-18. The recommended ignored `.domain-agent/` path is used.

## Intent

- Problem theory: Symphony models a delivery transaction: a board card is leased, dispatched,
  retried or continued, merged, and surfaced to an operator. Production safety therefore requires
  protected board roots to stay out of merges, artifact capture to leave unrelated host edits
  untouched, capacity waits not to count as failed agent attempts, UI refreshes to represent one
  coherent board read, and server configuration to follow the supported aiohttp key contract.
- Goal / constraints / tradeoffs: Fix only the reproduced defects and measured hot path below.
  Preserve public CLI/API/tracker behavior, current retry limits, Jira/Linear query semantics, and
  unrelated user work. Prefer a small domain helper where it expresses a real state transition;
  do not add pass-through wrappers.
- Expected outcome: excluded workspace descendants cannot leak into automatic merges; capture roots
  stage only untracked files; retry-held work waits without losing attempt kind or budget; the file
  TUI parses each ticket once per poll; the server starts without `NotAppKeyWarning`; package
  metadata builds without its current license deprecation warning.
- Completion promise: Deliver this evidence-backed patch set with red-first tests, a measured
  before/after result, full CI-equivalent gates, an isolated CLI/API lifecycle smoke, and a fresh
  adversarial audit. `max_iterations` is 3.

## Proposed patch set

Approved and frozen on 2026-07-18. Production source changes are limited to this bounded set.

1. **Auto-merge exclusion boundary** — `src/symphony/utils/auto_merge.py`.
   Stop parsing `git diff --name-only` to make the protection decision. For each configured root,
   use `git --literal-pathspecs diff --quiet TARGET..BRANCH -- "$root"`: exit 1 means protected
   content changed, exit greater than 1 is a Git failure, and names are diagnostic-only. This makes
   the root, every descendant, regex metacharacters, tabs, and newlines literal while allowing a
   similarly prefixed non-descendant. Preserve exit code 44 and the current block diagnostic.
   Check: targeted `tests/test_auto_merge.py` plus an independent literal-pathspec command repro.
2. **Untracked capture isolation** — `src/symphony/utils/auto_merge.py`.
   Replace whole-directory `git add` with two explicit NUL-safe, literal phases. First create one
   retained temporary manifest before any add, then for every root run
   `git --literal-pathspecs ls-files -z --others --exclude-standard -- "$cap" >> "$manifest"`.
   Only after every enumeration succeeds, stage once with `git --literal-pathspecs add
   --pathspec-from-file="$manifest" --pathspec-file-nul`. Do not retain `|| true`. All post-merge
   failures use one capture rollback:
   first unstage exactly the manifest with `git --literal-pathspecs reset -q HEAD
   --pathspec-from-file="$manifest" --pathspec-file-nul`, then run `git merge --abort` only after that reset
   succeeds. If the reset itself fails, exit nonzero with a recovery diagnostic and preserve the
   merge state rather than risk deleting operator files. This rollback covers enumeration failure,
   a partially successful `git add`, and commit-hook failure; clean the temp manifest on every safe
   exit. Snapshot and compare pre/post `MERGE_HEAD`, NUL status, cached diff, worktree diff, and file
   bytes. Red public-API tests inject (a) an add that stages the first path then fails and (b) a
   failing `commit-msg` hook after capture staging; both must finish non-success with a clean merge
   state/index, the tracked dirty edit unchanged, and every captured file restored untracked with
   identical bytes. On success, ignored content stays uncommitted and filenames containing spaces,
   tabs, or newlines are captured.
3. **Retry re-park semantics and cohesive refactor** — `src/symphony/orchestrator/entries.py`,
   `src/symphony/orchestrator/dispatch_state.py`, and `src/symphony/orchestrator/core.py`.
   Refactor eligibility into a classified decision while retaining `_eligible(...) -> bool` as the
   compatibility seam. Add `RetryEntry.holds_slot: bool = True`; `available_slots()` counts running
   entries plus only slot-holding retries, while `in_flight_ids()` continues to include every retry
   claim so a non-slot wait cannot duplicate-dispatch. Standard agent-failure/continuation backoff
   and paused retries remain slot-holding. A tracker-poll failure preserves the popped entry's
   existing `holds_slot` value, preventing an intermittent tracker from converting every standard
   retry into a non-slot timer. Only classified contention/dependency waits re-park with
   `holds_slot=False` and no attempt consumption: global/per-state capacity, CI activity, active
   lease, and unresolved/in-flight blocker. Preserve `attempt`, `kind`, claim, continuation
   exemption, and a bounded visible wait reason. Durable rejection or invariant
   failure—unsupported agent, inactive state, exhausted turn budget, or duplicate
   running/finalizing ownership—keeps the existing release, failure-retry, cap, or escalation path;
   it must not become a permanent non-slot retry. Extract real decision/re-park concepts and keep
   new/changed functions at no more than 50 lines. Red tests cover transient failure retries and
   continuations at `max_retries=1`, a one-slot unresolved-blocker case where the blocker can
   dispatch, successful dispatch with the original attempt/kind, standard continuation ownership,
   and durable unsupported-agent/exhausted-budget release or escalation.
4. **Typed aiohttp application key** — `src/symphony/server.py` and
   `src/symphony/webapi.py`. `webapi.py`, which owns the Host guard and is already imported by
   `server.py`, defines the one uniquely named `web.AppKey[str]`; `server.py` imports that singleton
   and uses it for the bind-address write. This avoids both a circular import and two unequal keys.
   Preserve the missing-key loopback default and the loopback Host guard. aiohttp 3.9.0
   documentation confirms `AppKey` is available at the repository's declared `aiohttp>=3.9` floor.
   Red test promotes `NotAppKeyWarning` to an error while starting the test server; existing
   Host-guard tests remain characterization coverage.
5. **Single-snapshot TUI refresh** — `src/symphony/tui/helpers.py`,
   `src/symphony/tui/app.py`, and the internal TUI export surface. Replace the two client helpers
   with one close-safe `_fetch_tracker_snapshot` seam used by the app. Retain the existing
   underscore helpers and re-exports for compatibility, while moving app tests to the new internal
   monkeypatch seam. For the file tracker, fetch active and terminal states in one scan, preserve
   source ordering, and partition by the exact existing rule: candidate is active and not terminal;
   an overlapping state is terminal-only. For Jira/Linear, preserve the two existing sequential
   queries on one client and do not claim atomicity. Red tests assert one parse per file, exact
   membership/order including overlap, close-on-success/error, and next-poll mutation visibility.
   Check: targeted TUI/file-tracker tests and the frozen benchmark below.
6. **Warning-free package metadata** — `pyproject.toml`. Replace deprecated
   `license = { text = "Apache-2.0" }` with the PEP 639 SPDX string, list `LICENSE` and `NOTICE`, and
   raise the isolated build backend floor to `setuptools>=77`, the first Setuptools line documented
   to support these fields. Check: wheel and sdist build without the license deprecation warning;
   wheel metadata says `License-Expression: Apache-2.0` and contains both legal files.
7. **Decision record** — create `docs/changelog/changelog-2026-07-18.md` with the problem theory,
   alternatives rejected, compatibility reasoning, benchmark, verification, and residual risks.

## Steps

1. Freeze the baseline evidence and receive explicit approval. Check: `QA.md` has exact before-state
   results; approval and domain-context decisions are recorded here and in `run-state.json`.
2. Builder writes the targeted regressions and captures RED output before production edits. Check:
   each selected defect fails for the intended assertion and the baseline AppKey warning is pinned.
3. Builder implements the minimum fixes and refactor, one seam at a time. Check after each seam:
   its targeted tests pass and `ruff check` covers touched source/tests.
4. Evaluator runs the frozen file-board benchmark against baseline SHA and the changed tree. Check:
   at 1,000 and 5,000 cards the optimized median is at most 65% of the exact two-scan baseline, with
   identical issue membership/order and parse counts reduced from `2N` to `N`.
5. Evaluator runs exact repository and runtime gates. Check: all commands under Verification
   strategy pass, including the disposable CLI/API smoke and cleanup assertions.
6. Fresh QA auditor performs changed-symbol backward tracing, security/concurrency/compatibility
   review, and DEBUG alternative reproductions. Check: literal gate output is complete and `QA.md`
   is PASS only if every `GOAL.md` criterion is independently proven.

## Acceptance checklist

- [ ] All six selected seams have before-state evidence and green results; production-code defects
  have red-first regression tests.
- [ ] Auto-merge exclusion covers roots, descendants, regex characters, unusual filenames, and
  prefix near-misses using literal Git pathspec decisions.
- [ ] Capture commits NUL-safe untracked artifacts, ignores ignored content, and leaves unrelated
  tracked edits unstaged and dirty; an injected enumeration/staging failure aborts the merge and
  restores the exact pre-merge index/worktree/untracked state.
- [ ] A commit-hook failure after capture staging cannot delete captured operator files: the exact
  NUL manifest is unstaged before abort, merge/index state is clean, and bytes return as untracked.
- [ ] Scheduler waits preserve retry attempt/kind, release their capacity slot, stay in-flight for
  duplicate prevention, and do not trigger the retry cap.
- [ ] At `max_concurrent_agents=1`, an unresolved blocker can dispatch while its dependent retry is
  non-slot waiting; normal agent/continuation backoff still owns its slot.
- [ ] Durable retry rejection cannot become a permanent slot-holding or non-slot retry.
- [ ] Server startup emits no `NotAppKeyWarning`; loopback Host protection is unchanged.
- [ ] File-board TUI refresh performs one scan and meets the frozen benchmark threshold.
- [ ] Built wheel uses current SPDX metadata, includes legal files, and emits no license metadata
  deprecation warning.
- [ ] Ruff, Pyright, full coverage suite, package build/install, doctor, and runtime smoke pass.
- [ ] Fresh audit reports no unresolved high-severity issue in the changed surfaces.
- [ ] Original checkout remains untouched apart from its two pre-existing untracked documents.

## Tools & Skills

- `supergoal` LEGACY role loop with DEBUG subcases; `codebase-memory` graph discovery and changed-
  symbol impact checks; repository-owned pytest, Ruff, Pyright, build/install, doctor, and CLI/API
  runtime gates. A fresh builder owns implementation; fresh QA roles own verification.

## Verification strategy

- Before proof: `PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q`; selected RED tests;
  disposable git reproductions; evaluator-owned file-board benchmark at
  `docs/changelog/2026-07/18-production-hardening/qa/benchmark_file_board_snapshot.py`.
- Targeted green: `tests/test_auto_merge.py`, retry cases in
  `tests/test_orchestrator_dispatch.py` and max-retry ownership tests, server/startup/Web API tests,
  and `tests/test_tui.py` plus `tests/test_tracker_file.py`.
- Static gates: `/opt/anaconda3/bin/python -m ruff check src tests` and
  `/opt/anaconda3/bin/python -m pyright --pythonpath
  /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python`.
- Full gate: `PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q --cov=src/symphony
  --cov-report=term --cov-fail-under=80`.
- Benchmark baseline command:
  `PYTHONPATH=src /opt/anaconda3/bin/python
  docs/changelog/2026-07/18-production-hardening/qa/benchmark_file_board_snapshot.py --cards
  1000 5000 --samples 5 --warmups 1 --body-bytes 1024 --expect-implementation legacy_two_scan`.
  Changed-tree command: the same script and sizing plus `--expect-implementation single_snapshot
  --baseline-json docs/changelog/2026-07/18-production-hardening/qa/benchmark-before.json
  --max-ratio 0.65`. The script times only the exact TUI seam, asserts ordered identifier equality
  by SHA-256, and requires exactly `2N` baseline parses or `N` changed parses. Frozen baseline
  medians are 453.860 ms (`N=1000`) and 2,247.174 ms (`N=5000`); changed thresholds are 295.009 ms
  and 1,460.663 ms respectively.
- Build/install gate: `/opt/anaconda3/bin/python
  docs/changelog/2026-07/18-production-hardening/qa/verify_package.py --source .`. The evaluator
  script copies the changed source to an owned `/private/tmp` directory while excluding `.git`,
  runtime state, caches, and linked `kanban`; gives `uv build` explicit temp cache/output paths;
  rejects `SetuptoolsDeprecationWarning`; asserts the wheel's SPDX expression and wheel/sdist legal
  files; installs the wheel into a fresh venv; runs `symphony --help`, `symphony --version`, and a
  static-resource probe; and requires the real worktree's `.egg-info` inventory to stay unchanged.
- Operator gate: run the built artifact's `symphony doctor` against a disposable workflow.
- Runtime gate: an evaluator harness creates an empty disposable file board/workspace and workflow
  with `server.port: 0`, `progress.enabled: false`, and `system.keep_awake: false`; it starts the
  installed `symphony`, parses the emitted ephemeral port, and polls until health is exactly `ok`.
  Run `scripts/smoke_web_api.py` with a unique prefix, externally assert every smoke card was
  deleted, send SIGTERM, require exit 0 plus `shutdown_complete`, require the port closed, and query
  `.symphony/state.db` for zero active runs. Before and after, compare an exact sorted manifest of
  every original linked-board ticket's relative path plus SHA-256 bytes; additions, removals, and
  content changes all fail. The harness enforces hard startup/shutdown timeouts.
- DEBUG literal gate before any commit:
  emit one triplet per defect—`GATE.owner.<id>=<targeted-test>`;
  `GATE.alt_repro.<id>=<structurally-different-command-or-script>: pass`;
  `GATE.conformance.<id>=<exact-expected-vs-actual>`. Required pairs are: exclusion owner public API
  test / raw literal-pathspec Git repro; capture owner public API tests for partial-add and
  commit-hook failure / standalone generated-script NUL-manifest rollback plus byte-for-byte
  index/worktree/MERGE_HEAD inspection; retry owner classified-decision test / real timer-driven one-slot
  blocker repro; AppKey owner warning-as-error startup test / installed-wheel startup with
  `PYTHONWARNINGS=error`; TUI owner parse-count/order test / changed-tree benchmark; packaging owner
  metadata test / direct wheel-and-sdist archive inspection.
- Final hygiene: `git diff --check`, graph `detect_changes`, changed-symbol-to-test reconciliation,
  explicit `git status --short --untracked-files=all` inventory/validation, and original-checkout
  status comparison.

## Non-goals and rejected alternatives

- No broad split of `_run_agent_attempt` or `_on_worker_exit_impl`: high blast radius without a
  reproduced defect in this run.
- No cross-poll tracker cache: invalidation would risk stale dispatch/board state. One immutable
  per-poll snapshot removes duplicate work without persistence semantics.
- No new subprocess-output limit in this patch: a cap changes diagnostic/event payload behavior
  across seven backends and needs a separately approved product limit and compatibility contract.
- No runtime dependency upgrade, schema migration, public API change, viewer redesign, or unrelated
  style cleanup. AppKey stays within `aiohttp>=3.9`; the build-only Setuptools floor moves to the
  documented PEP 639-capable release.

## Grounding ledger

- Base/target branch -> `WORKFLOW.md` leaves both overrides empty and current host branch is `dev` -> source and target are verified `dev` at `9fea55cce6a295d4bde881fd32357a9904b3e95a`.
- Original checkout safety -> two unrelated untracked docs are present -> all run changes stay in `/private/tmp/symphony-production-hardening-20260718`.

## Domain Brief

- Knowledge path: `.domain-agent/` (local and ignored).
- Selected knowledge files: `index.md`, `invariants.md`, `code-map.md`, `test-map.md`, `flows/retry-and-auto-merge.md`.
- Stable terms: retry entry = delayed in-flight claim; capture path = explicitly included untracked operator artifact.
- Terminology conflicts: none.
- Invariants: non-slot waits still prevent duplicate dispatch; tracker-poll failure preserves prior slot ownership; capture rollback unstages the exact NUL manifest before abort; excluded roots use literal Git pathspecs.
- Current-code verification: graph traces for `_on_retry_timer`, `RetryEntry`, `DispatchState.available_slots`, `_build_script`, `_build_capture_block`, TUI fetch helpers, server Host guard.
- Entry points: `orchestrator/core.py`, `orchestrator/dispatch_state.py`, `utils/auto_merge.py`, `tui/helpers.py`, `server.py`, `webapi.py`.
- Test commands: targeted tests in `.domain-agent/test-map.md`; full coverage, packaging, benchmark, doctor, and runtime gates are frozen above.
- Gaps: external Jira/Linear service behavior is not live-tested; existing adapter query semantics remain characterized and unchanged.

## Amendment - 2026-07-18 approval record

- Administrative only: recorded the user's approval, resolved the `.domain-agent/` path, and removed
  the stale pre-approval sentence. The approved technical scope and acceptance criteria are unchanged.

## Appendix: Explore map

This is a read-only grounding map, not an approved patch set. It distinguishes observed
boundaries from hypotheses that still require a red test, a characterization capture, or a
benchmark. The checked-in workflow is a 30-second file-board poller with four active and five
terminal lanes; it provisions isolated git worktrees through hooks
(`WORKFLOW.md:1-24`, `WORKFLOW.md:35-60`). The real-world transaction is therefore: read a card,
lease it, prepare a worktree, run one agent backend through stage prompts, persist card/run state,
and release or clean every process and workspace exactly once.

### Production entry points and call paths

1. **Foreground CLI/runtime.** The installed `symphony` script resolves to
   `symphony.cli:main` (`pyproject.toml:35-36`). `main()` routes board, doctor, service,
   wiki-sweep, runs, and TUI invocations, then enters `asyncio.run(_run(...))`
   (`src/symphony/cli/main.py:403-428`). `_run()` loads and preflights `WORKFLOW.md`, starts the
   orchestrator, optionally binds the aiohttp server and Textual TUI, installs signal handlers,
   and tears all three down in `finally` (`src/symphony/cli/main.py:133-203`,
   `src/symphony/cli/main.py:227-313`). Preserve startup exit codes, stderr guidance, signal
   behavior, and cleanup ordering before changing this path.
2. **Polling/dispatch core.** `Orchestrator.start()` validates the live config, builds the
   workspace manager, opens stats/registry state, performs startup cleanup, and spawns the tick
   loop (`src/symphony/orchestrator/core.py:1102-1136`). Each tick hot-reloads config, heartbeats
   and reconciles leases/workers, validates dispatch, fetches candidates, applies conflict/slot
   policy, dispatches, performs recovery/archive work, and notifies observers
   (`src/symphony/orchestrator/core.py:1973-2106`). `_dispatch()` acquires the persistent lease
   before installing the in-memory entry and worker task (`src/symphony/orchestrator/core.py:3338-3389`).
3. **Worker/stage lifecycle.** `_run_agent_attempt()` is the stage engine: workspace hooks,
   backend construction, prompt/session/turn handling, tracker refresh, phase rebuild, event/PID
   accounting, and final backend/workspace cleanup share one 640-line coroutine
   (`src/symphony/orchestrator/core.py:3506-4145`). `_on_worker_exit_impl()` then owns slot/lease
   release, retry/budget decisions, commit/merge/after-done hooks, and workspace retention/removal
   (`src/symphony/orchestrator/core.py:4896-5258`). These are the highest-blast-radius cohesion
   seams; any split must first pin ordering and identity behavior, then extract cohesive operations
   without pass-through wrappers.
4. **Reconciliation.** A tick heartbeats each running entry, checks stalls, fetches tracker state
   off-loop, isolates per-ticket failures, applies a 60-second terminal grace, and cancels/commits/
   removes terminal or out-of-workflow workers (`src/symphony/orchestrator/core.py:5654-5747`,
   `src/symphony/orchestrator/core.py:5749-5926`). This overlaps worker-exit cleanup by design;
   regression work must cover both interleavings rather than testing either path alone.
5. **Managed service/viewer.** `service start` runs doctor, spawns detached orchestrator and optional
   board-viewer processes, checks early exit, and writes the service record only after spawn
   (`src/symphony/service.py:647-803`). Stop uses TERM, optional KILL, registry PIDs, and
   workspace-command discovery, retaining the service record if anything survives
   (`src/symphony/service.py:454-481`, `src/symphony/service.py:551-621`,
   `src/symphony/service.py:806-847`). The viewer is a separate stdlib HTTP surface, not the
   aiohttp SPA: it proxies Symphony, edits selected workflow/settings/card operations, reads the
   Markdown board, and serves static files (`tools/board-viewer/server.py:1151-1329`,
   `tools/board-viewer/server.py:1360-1476`). Audit and baseline both HTTP surfaces independently.

### Mutable state and durability boundaries

- `DispatchState` is the single in-process owner of `running`, `claimed`, `retry`, persisted retry
  attempts, and budget exhaustion; it counts running plus retry-held slots and enforces task
  identity/one-retry invariants (`src/symphony/orchestrator/dispatch_state.py:1-18`,
  `src/symphony/orchestrator/dispatch_state.py:35-60`,
  `src/symphony/orchestrator/dispatch_state.py:66-144`). The orchestrator adds pause gates,
  terminal-persist fences, debug/health counters, background-task supervision, token EMA, stats,
  and registry handles (`src/symphony/orchestrator/core.py:500-598`). Do not introduce a second
  owner or update these collections outside their existing transition methods.
- `RunRegistry` persists the cross-process dispatch lease, backend PID, run history, pause/retry/
  budget flags, and crash-reclaim fence in `.symphony/state.db`
  (`src/symphony/orchestrator/run_registry.py:57-98`,
  `src/symphony/orchestrator/run_registry.py:109-160`,
  `src/symphony/orchestrator/run_registry.py:291-366`,
  `src/symphony/orchestrator/run_registry.py:377-473`). WAL SQLite calls are intentionally inline
  on the event loop and can wait up to five seconds under lock contention
  (`src/symphony/orchestrator/run_registry.py:16-21`,
  `src/symphony/orchestrator/run_registry.py:475-484`); this is a measured-performance candidate,
  not yet a defect.
- The file tracker reads every `*.md` ticket and hydrates blockers for candidate/state/full-id
  queries (`src/symphony/trackers/file.py:555-625`,
  `src/symphony/trackers/file.py:631-652`). Mutations serialize per ticket, compare both
  `updated_at` and mtime, reapply up to three times, and atomically replace a same-directory temp
  file (`src/symphony/trackers/file.py:474-489`,
  `src/symphony/trackers/file.py:673-722`). ID allocation has a separate allocator lock
  (`src/symphony/trackers/file.py:805-878`). Reuse these primitives; do not add a parallel writer
  or cache without invalidation/concurrency proof.
- Tracker clients are blocking adapters. Orchestrator fetch/reconcile calls create, close, and run
  them via `asyncio.to_thread` (`src/symphony/orchestrator/core.py:5683-5688`,
  `src/symphony/orchestrator/core.py:5932-5961`). Any optimization must preserve close-on-error and
  Jira/Linear behavior, not only the local file board.

### Subprocess and agent lifecycle

- Backend construction is already injectable (`src/symphony/orchestrator/core.py:484-498`,
  `src/symphony/orchestrator/core.py:626-631`), and `AgentBackend` is the shared contract
  (`src/symphony/backends/__init__.py:110-130`). Keep backend-specific parsing behind that seam.
- Per-turn CLI backends share `PerTurnCliBackend`: it owns the closed/spawn race, subprocess
  publication, watchers, bounded stdout/stderr collection, timeout/cancellation reap, and event
  normalization (`src/symphony/backends/per_turn.py:61-95`,
  `src/symphony/backends/per_turn.py:131-215`,
  `src/symphony/backends/per_turn.py:257-302`). Children start in their own POSIX process group
  (`src/symphony/backends/per_turn.py:221-240`). Codex remains a persistent app-server lifecycle,
  so it must not be mechanically folded into the per-turn base.
- `_shell.terminate_process_tree()` is the reusable TERM -> bounded wait -> KILL -> bounded wait
  ladder for a whole process group (`src/symphony/_shell.py:206-240`); `safe_proc_wait()` contains
  the macOS/Python 3.12 zombie workaround (`src/symphony/_shell.py:96-170`). Production proof must
  assert descendant cleanup, not merely that the wrapper PID exited.
- `Orchestrator.stop()` wakes paused workers, cancels tick/workers/retries, drains worker and
  supervised background tasks, then closes registry state (`src/symphony/orchestrator/core.py:1173-1209`).
  Startup recovery fences a dead owner's run before external process cleanup and only then marks
  it orphaned (`src/symphony/orchestrator/run_registry.py:291-366`). Characterize cancellation at
  each await boundary and restart after a forced process death before changing this flow.

### Web API and TUI ownership

- `server.build_app()` owns health/state/refresh/debug/pause/resume/recovery/skip routes and then
  registers the board SPA routes (`src/symphony/server.py:35-198`); `run_server()` owns bind and
  cleanup state (`src/symphony/server.py:207-224`). Preserve status codes, JSON keys/error
  envelopes, route ordering, and host/content-type guards through characterization tests.
- `webapi` uses the same file-tracker and workflow mutation modules as CLI operations
  (`src/symphony/webapi.py:1-16`). Its request middleware enforces loopback-host and JSON-body
  constraints (`src/symphony/webapi.py:52-137`); issue/run/board CRUD is grouped at
  `src/symphony/webapi.py:436-670`, workflow mutations at `src/symphony/webapi.py:678-863`, and
  stats/static serving at `src/symphony/webapi.py:871-915`. Security review must cover bind host,
  Host/Origin validation, traversal, body-size limits, and exception disclosure for both aiohttp
  and the standalone viewer before any behavior is called production-safe.
- The TUI polls blocking trackers in worker threads, but rebuilds the runtime index, every lane's
  sorted cards, widths, and detail pane on a 0.5-second heartbeat
  (`src/symphony/tui/app.py:196-247`, `src/symphony/tui/app.py:249-303`). The standalone viewer has
  its own guarded browser poll loop (`tools/board-viewer/src/js/board.js:517-602`). Both are
  benchmark/profile candidates; no render-cache or polling change is justified until board sizes
  and interaction latency are captured.

### Evidence-ranked characterization and measurement targets

| Rank | Candidate seam | Required proof before a change | Likely blast radius |
|---|---|---|---|
| 1 | Worker exit/reconcile/cancellation interleavings | Red-first deterministic task race plus structurally different isolated process repro; assert exactly-once slot, lease, hook, commit, workspace, and descendant-process outcomes | `core.py`, dispatch state, registry, workspace/hooks, every backend, service stop |
| 2 | File-board concurrent durability and repeated full scans | Preserve atomic/CAS tests; benchmark candidate fetch, board API, and mutation contention at representative 10/100/1,000-card boards before/after | file tracker, TUI, aiohttp board, viewer, orchestrator polling |
| 3 | Inline SQLite lock contention | Evaluator-owned concurrent-lease benchmark and tick-latency capture, including busy/rollback/reopen/reclaim cases | registry, health, slot ownership, service PID cleanup |
| 4 | `_run_agent_attempt` / `_on_worker_exit_impl` cohesion | Characterize observable call order and failure outcomes first; accept only a domain-cohesive extraction with lower graph complexity and unchanged API/events | highest runtime blast radius; all lifecycle tests and E2E |
| 5 | TUI/viewer refresh work | Profile CPU/render/poll latency with small and large boards; preserve focus, filtering, keyboard actions, lane counts, and response payloads | Textual UI, static JS viewer, tracker reads |

No item above is an approved fix. A candidate drops from the build if baseline/reproduction does
not demonstrate a concrete failure or measurable regression.

### Existing ownership tests and frozen candidate gates

- Dispatch and cleanup: `tests/test_dispatch_state.py:61-255`,
  `tests/test_orchestrator_dispatch.py`, `tests/test_orchestrator_reconcile.py`,
  `tests/test_run_registry.py:24-464`, and the Markdown-board lifecycle at
  `tests/test_agent_lifecycle_e2e.py:443-709`. The latter uses injected/fake backends; it is not
  proof of a real CLI subprocess.
- Backend/process ownership: `tests/test_backend_contract.py:71-86`,
  `tests/test_backends_lifecycle.py:60-219`, `tests/test_shell.py:113-203`, and service cleanup/
  stale-record cases at `tests/test_service.py:232-495`.
- Tracker durability: parsing/filtering/atomic temp coverage at
  `tests/test_tracker_file.py:44-679`, concurrent allocation at
  `tests/test_tracker_file.py:724-756`, and concurrent mutation/delete/CAS reapply at
  `tests/test_tracker_file.py:1054-1223`.
- HTTP/UI contracts: `tests/test_server_routes.py:141-330`,
  `tests/test_webapi.py:244-726`, `tests/test_web_static_contract.py:9`, and Textual behavior at
  `tests/test_tui.py:411-1258`. Browser E2E is opt-in and begins at
  `tests/test_web_browser_e2e.py:255`; treat an unselected browser test as `not proven`.
- CI freezes Python 3.12, editable dev install, Ruff, Pyright, and pytest with at least 80% source
  coverage (`.github/workflows/tests.yml:15-46`; `pyproject.toml:23-33`,
  `pyproject.toml:44-69`). The local pre-push helper currently runs full pytest but not Ruff,
  Pyright, or coverage (`scripts/git_quality_gate.py:94-99`), so final verification must execute
  the CI commands explicitly rather than treating that helper as full parity.
- Operator/runtime proof is separately documented: `symphony doctor`, foreground runtime,
  `/api/v1/health`, `symphony runs`, and `scripts/smoke_web_api.py`
  (`README.md:275-311`; `scripts/smoke_web_api.py:98-180`). The evaluator-owned production E2E
  should use a disposable workflow/board/workspace and mock or explicitly authorized real CLI,
  exercise service start/status/API CRUD/dispatch/termination/stop, and prove no surviving child,
  lease, temp ticket, or workspace. It must not mutate the repository's live linked `kanban` board.

### Preserve baselines before implementation

Capture these before source edits: CLI exit code/stdout/stderr for success and startup failure;
server and viewer route status/body/error matrices; normalized backend events and PID fields;
stage prompt/session transitions; ticket bytes/state under concurrent mutation; registry rows across
claim/heartbeat/crash/reclaim/complete; TUI focus/filter/action behavior; and descendant process/
workspace state after normal stop, timeout, cancellation, SIGTERM, and forced SIGKILL. Every later
diff must trace changed symbols to at least one owning test plus the relevant isolated runtime or
benchmark proof.
