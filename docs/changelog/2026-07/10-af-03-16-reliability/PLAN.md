# PLAN - AF-03 through AF-16 reliability

Frozen plan. Builders receive one cohesive task brief and must follow red-green TDD. Amendments append;
they do not silently rewrite approved scope.

## Approval

- Status: auto-approved
- Record: 2026-07-09T20:27:19Z; pre-authorized autonomous run: the user invoked `supergoal` and explicitly requested resolving every AF-03-and-later ticket.

## Intent

- Goal: resolve AF-03 through AF-16 against the current `dev` behavior with the smallest ticket-traceable changes.
- Constraints: preserve each ticket's non-goals; do not absorb the dirty AF-02 worktree; no dependency upgrades, migrations, external publishing, or speculative refactors.
- Tradeoffs: use cohesive seam batches so one concurrency/state invariant is changed once, while retaining one independently named regression test per ticket.
- Rejected: one mega-patch (poor fault isolation); fourteen isolated patches (duplicates shared reconcile/tracker setup); merging the unfinished AF-02 worktree (out of scope and unverified); AF-11 per-window reset (contradicts the checked-in lifetime-cap contract); AF-14 defensive production code (current and historical protocol schemas require `total`).
- Completion promise: every GOAL criterion is proven by fresh focused and full-suite evidence; mandatory full-spec, edge-case, adversarial, and exact-verification roles report no open grounded gap; `max_iterations=8`. Stop only when the commit gate is green or a concrete requirement-level blocker is reported.

## Priority Rules

Domain(s): async orchestrator lifecycle + process ownership + file tracker integrity + backend protocol contracts.

1. A worker slot, lease, process group, and retry describe one ownership lifecycle; release them in a safe, observable order.
2. Cancellation and pause are different states; system cancellation cannot be hidden by an operator pause.
3. Reconcile one issue at a time so one cleanup failure cannot starve the rest of the board.
4. Board mutation must be atomic and serialized per ticket; scanners ignore implementation artifacts and expose malformed/duplicate inputs.
5. A completed turn requires meaningful backend content; exit code alone is not productive progress.
6. Persistent backend reader failure closes and reaps the backend so later calls fail fast.
7. Shutdown is bounded; it records and kills owned survivors without hiding prompt worker failures.
8. Configured pipeline order defines forward/rewind semantics; human language does not.
9. Backward compatibility is mandatory for old lease rows and existing public behavior.
10. Operator-reachable safety refusals return explicit errors and never partially mutate state.

## Steps

### 1. Lifecycle and reconcile state (AF-03, AF-07, AF-08, AF-12 degraded state, AF-15)

- Tests first in `tests/test_orchestrator_dispatch.py` and `tests/test_dispatch_state.py`:
  - resume after a pause older than `stall_timeout` does not cancel until a fresh resume window elapses;
  - a cancelled+paused zombie still ejects;
  - a cleanup exception still schedules retry and does not prevent the next entry's stall check;
  - a cancellation-resistant task cannot keep `stop()` open past the configured bound;
  - a running id omitted from tracker refresh records an explicit tracker/degraded signal;
  - stop clears retained completed/debug state.
- In `src/symphony/orchestrator/entries.py`, add only the timestamp/state required to distinguish resume from progress.
- In `src/symphony/orchestrator/core.py`, restamp resume eligibility, order cancelled escalation before pause, isolate Part A per issue, make force-eject retry scheduling cleanup-safe, bound worker drain, expose missing-running refresh, and clear diagnostic state.
- In `src/symphony/orchestrator/dispatch_state.py`, remove reader-less `completed` state or bound it; migrate only verified consumers.

### 2. Running-state API and file-board integrity (AF-04, AF-06, AF-12)

- Tests first in `tests/test_webapi.py` and the existing file-tracker test module:
  - changed state on a running card is 409 and byte-for-byte board file remains unchanged;
  - non-state running patch and idle state patch stay green;
  - parseable `.tmp-*.md` files never appear in scans/candidates/state reads;
  - stale root temps are swept with a structured warning;
  - duplicate frontmatter ids collapse deterministically with warning and create rejects a non-canonical duplicate;
  - delete versus append/update is serialized and cannot resurrect a ticket.
- In `src/symphony/webapi.py`, guard only an actual state change using the same running lookup as delete, with `state_in_use` and a pause/wait hint.
- In `src/symphony/trackers/file.py`, write atomic temps with a non-board suffix/location, filter legacy temps at every scan, perform a safety-aged startup sweep, warn on parse/duplicate skips, reject create via `find_path`, and resolve+unlink under the per-ticket lock.
- Do not add cross-process semantics beyond the tracker lock already used by mutation paths.

### 3. Per-turn content and persistent Codex corruption (AF-05, AF-09)

- Tests first in `tests/test_backends.py`, `tests/test_backend_contract.py`, and only if unavoidable `tests/test_orchestrator_dispatch.py`:
  - every productive Plain/Gemini/Claude completion payload yields a non-empty orchestrator preview;
  - productive plain events reset G2 while real empty events still trip three-turn protection;
  - exit 0 plus whitespace-only stdout is a turn failure and never emits `EVENT_TURN_COMPLETED`;
  - `MALFORMED_LINE_LIMIT` consecutive lines closes/reaps Codex and a later turn fails without timeout;
  - malformed streak resets after valid JSON.
- In `src/symphony/backends/plain_cli.py` and `gemini.py`, add the canonical top-level `message` preview.
- For Claude, flatten assistant content blocks at its completion payload boundary; do not broaden generic payload parsing if the backend can satisfy the contract.
- In `src/symphony/backends/per_turn.py`, reject empty successful stdout through the existing failure event/error family.
- In `src/symphony/backends/codex.py`, mark corrupt streams closed, log structured corruption, and reuse process teardown/reaping without self-await deadlock.

### 4. Lease orphan recovery (AF-10)

- Tests first in `tests/test_run_registry.py` (and startup integration test if the reclaim caller owns process teardown): a dead owner with recorded live `backend_agent_pid` invokes an injected group killer before the lease becomes redispatchable; null pid preserves legacy behavior.
- Keep schema compatible in `src/symphony/orchestrator/run_registry.py`; use the existing nullable `backend_agent_pid` field and returned `RunRecord`.
- Put OS process killing at the startup recovery boundary in `src/symphony/orchestrator/core.py`, not inside the SQLite transaction; log `reclaim_killed_orphan_agent` with pid/outcome before candidates can dispatch.
- AF-02 will expand which backends persist this field; AF-10 must not rename it or import the unfinished AF-02 patch.

### 5. Scheduler, configured rewinds, and turn-budget prompts (AF-11, AF-13, AF-16)

- Tests first in the existing continuous-improvement, phase-transition, dispatch, and prompt pipeline tests:
  - lifetime cap logs exactly once per latch and reset permits a future latch warning;
  - lease-held next-due equals now + interval;
  - `require_idle_board` accounts for `_terminal_persist_pending` and dispatch refuses overlap while CI is active;
  - custom `qa -> in progress` and Korean later->earlier transitions increment rewind budget and cap to Blocked;
  - default pipeline rewind behavior remains unchanged;
  - first and continuation turn displays share lifetime numerator and `max_total_turns` denominator.
- In `src/symphony/orchestrator/core.py`, add minimal latch observability/idleness coordination and pass configured pipeline order to rewind detection.
- In `src/symphony/orchestrator/helpers.py`, replace the static-pair-only predicate with case-insensitive active-state index ordering while retaining a compatibility default if direct callers require it.
- In `src/symphony/prompt.py` and its core call sites, use lifetime turn number/denominator consistently; audit `tests/test_workflow_pipeline_prompt.py` anchors before editing.
- Update the closest WORKFLOW/template documentation for the exact `turn_number`/`max_turns` semantics; do not redesign prompt content.

### 6. AF-14 research closure and delivery documentation

- Add a compact research note under `docs/improvements/research/` recording:
  - `codex-cli 0.144.0`;
  - `codex app-server generate-json-schema --experimental --out <tmp>`;
  - current `ServerNotification.json` requires `ThreadTokenUsage.last` and `.total`;
  - checked-in Codex 0.130 schema requires the same shape;
  - therefore the `total=1000 -> last=200 -> total=1200` path is not protocol-reachable and production accounting remains unchanged.
- Mark AF-14 resolved by research in its ticket without changing machine anchors elsewhere.
- Append the implementation decisions and rejected alternatives for all tickets to `docs/changelog/changelog-2026-07-10.md`.
- Do not rewrite the source audit tickets' defect text; add a concise resolution/evidence section where useful.

### 7. Required role loop and exact verification

- Fresh full-spec improver: re-read AF-03..AF-16 and fix only grounded omissions.
- Fresh edge-case improver: probe cancellation, timing, duplicates, legacy rows, content whitespace, and custom-state casing.
- Fresh adversarial reviewer: no source edits; try to disprove every GOAL criterion against diff and tests.
- Fresh verifier: run focused tests, full suite, lint, type check, schema proof, and ticket/diff trace; tick GOAL only from evidence.
- If any criterion remains open, append `R-LOOP.md` with expected/actual/evidence/smallest fix and re-enter Build, capped at eight iterations.

## Tools & Skills

- Required process: `supergoal`, `superpowers:test-driven-development`, `superpowers:systematic-debugging`, `superpowers:verification-before-completion`.
- Structural discovery: codebase-memory `search_graph`, `trace_path`, `get_code_snippet`, `search_code`.
- Python: `PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest ...`.
- Static checks: `/Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/ruff check src tests`; `/Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/pyright src`.
- Protocol research: `codex --version`; `codex app-server generate-json-schema --experimental --out /private/tmp/codex-schema-af14-20260710`.
- Commit gate: `bash /Users/danny/.agents/skills/supergoal/templates/commit-gate.sh docs/changelog/2026-07/10-af-03-16-reliability none`.

## Verification strategy

- Before proof: full tests/lint/type checks on `dev@4de380f`, plus focused RED tests for each reachable defect.
- Step -> GOAL criterion: step 1 -> AF-03/07/08/12/15; step 2 -> AF-04/06/12; step 3 -> AF-05/09; step 4 -> AF-10; step 5 -> AF-11/13/16; step 6 -> AF-14; step 7 -> scope/full repository criteria.
- Trusted commands: full pytest, ruff, and pyright commands above (`frozen_repo`); generated Codex schema inspection (`evaluator_owned`).

## Domain Brief

- Knowledge path: ephemeral in this vault because `.domain-agent/` is absent.
- Selected sources: AF-03..AF-16 tickets; `WORKFLOW.md`; `docs/continuous-improvement/rubric.md`; current code graph.
- Stable terms: running entry = in-memory worker ownership; lease = persisted run ownership; force-eject = slot/process cleanup after cancellation grace; rewind = later configured active state to earlier one.
- Invariants: running state changes are guarded; one issue failure does not abort board reconciliation; process ownership is killed before redispatch; file board writes are locked+atomic; token totals are cumulative.
- Current-code verification: `resume_worker`, `_reconcile_running`, `_force_eject_zombie`, `stop`, `_preview_from_payload`, scheduler methods, `FileBoardTracker` scan/mutate methods, per-turn completion methods, Codex stdout reader/token handler, `RunRegistry.reclaim_dead_owner_leases`, rewind helper, prompt builders.
- Entry points: `src/symphony/orchestrator/core.py`, `src/symphony/webapi.py`, `src/symphony/trackers/file.py`, `src/symphony/backends/*.py`, `src/symphony/orchestrator/run_registry.py`, `src/symphony/prompt.py`.
- Test commands: focused modules above, then full pytest/ruff/pyright.
- Gaps: AF-02 is a separate dirty worktree; AF-10 is implemented against today's compatible `backend_agent_pid` seam and must compose later.

## Amendment - 2026-07-09T20:31:00Z

- The isolated worktree does not inherit the original checkout's virtual environment executables. Current dev extras were therefore installed into the worktree's ignored `.venv` with `uv run --extra dev`.
- Authoritative verification commands are `uv run --extra dev pytest -q`, `uv run --extra dev ruff check src tests`, and `uv run --extra dev pyright src`.
- Reason: the shared interpreter proved the clean test baseline, but its global Pyright was older than the project minimum and its environment did not expose the project Ruff/Pyright executables. The current project extras produced the authoritative `0 errors` type-check baseline.

## Grounding ledger

- Which branches? -> current clean `dev` is both source and integration target; run branch is isolated -> no original-checkout edits.
- AF-11 rate window or lifetime? -> checked-in heartbeat plan/rubric explicitly define lifetime-until-reset -> retain semantics, add once-per-latch warning.
- AF-13 rule? -> ticket recommendation plus configured-state domain -> active-state index ordering.
- AF-14 reachable? -> Codex 0.144.0 and checked-in 0.130 generated schemas both require `last` and `total` -> research closure, no production change.
- AF-10 without AF-02? -> nullable `RunRecord.backend_agent_pid` already exists -> kill recorded survivor now; AF-02 later broadens writers without changing AF-10 contract.

## Wrap-up handoff - 2026-07-10

The user requested an immediate pause after committing and pushing the current work to `dev`. This section is the resume point; the frozen implementation plan above remains unchanged.

### Achieved

- Implemented and documented AF-03 through AF-16, including backend completion contracts, file-board/API safety, lifecycle/reconcile fixes, lease recovery, scheduler/rewind behavior, prompt budgets, and AF-14's schema-backed research closure.
- Corrected four adversarially confirmed gaps before integration: selective temp cleanup, shutdown lease finalization, retryable Codex teardown, and a two-phase non-redispatchable dead-owner reclaim fence.
- Preserved and integrated the existing AF-02 backend-neutral process ownership work and the later OpenCode telemetry/worktree hardening from `origin/dev@4eadaa4`.
- Final integrated verification on the rebased branch passed: full pytest `1363 passed, 5 skipped`; combined affected suites `611 passed, 3 skipped`; AF-02 ownership/force-eject selector `38 passed`; OpenCode/backend/workspace suites `192 passed, 3 skipped`; Ruff clean; project Pyright `0 errors`; diff/conflict checks clean.
- Regenerated the Codex 0.144.0 schema and confirmed `ThreadTokenUsage.last` plus `.total` are required and byte-identical to the checked-in 0.130 notification schema.

### Left for the next session

- No known source or test blocker remains in AF-03 through AF-16.
- Run the optional disposable live-board smoke before release: pause/resume, running-state PATCH refusal, selective temp cleanup, bounded stop, and dead-owner recovery.
- If the Supergoal evidence vault should be formally closed, reconcile the already captured verification evidence into `GOAL.md`, `QA.md`, and `run-state.json`, then add the exact-verifier/Z records. They are intentionally left at their previously committed partial/open state because the user requested only this PLAN handoff update.
- Release/version/tag work is not included; do it only when explicitly requested.
