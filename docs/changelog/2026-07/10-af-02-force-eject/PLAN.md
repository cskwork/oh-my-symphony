# AF-02 Force-Eject Backend Process Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use the Supergoal role-loop. The builder reads this frozen plan only; later improvers independently re-read the ticket and current diff.

**Goal:** Ensure force-eject terminates the recorded OS process group for every backend while preserving retry behavior when no pid exists.

**Architecture:** Backends already expose `pid` and emit it as `agent_pid` while their session/turn subprocess is active. The orchestrator will store that value under a backend-neutral running-entry field, reuse it for run-registry heartbeats, and use it during force-eject with backend-aware structured logging.

**Tech Stack:** Python 3.11+, asyncio subprocesses, dataclasses, pytest/pytest-asyncio, structlog-style repository logger.

---

## Approval

- Status: auto-approved
- Record: 2026-07-09T19:18:00Z; pre-authorized by the user's explicit `supergoal implement` command for the checked-in AF-02 design.

## Intent

- Goal / constraints / tradeoffs / rejected approaches: implement the ticket's backend-neutral PGID contract with the smallest migration. Preserve backend event envelopes and run-registry schema. Reject backend-specific kill branches, changing process-spawn/reaping semantics, AF-01 exit identity work, and AF-10 startup reclaim.
- Completion promise: deliver a red-first regression test, backend-neutral entry recording, all-backend contract coverage, missing-pid preservation, backend-kind logging, a clean backward trace, and green focused + full repository gates. Stop when every `GOAL.md` criterion is proven and the commit gate passes, or after `max_iterations=8` with a concrete blocker.

## Steps

1. Capture the existing focused force-eject/event/lease tests as the shared-state neighbor baseline and preserve their output under `qa/baseline/`.
2. In `tests/test_orchestrator_dispatch.py`, first migrate/add a non-Codex force-eject case using the backend-neutral field, spy on `kill_process_group`, and assert the structured warning includes backend kind; add a missing-pid case that still releases/schedules retry without killing. Run the focused selector and record the expected RED against current code.
3. In `src/symphony/orchestrator/entries.py`, replace the Codex-specific running-entry pid field with a backend-neutral `agent_pgid: int | None` field. Do not add process-control behavior to the data object.
4. In `src/symphony/orchestrator/core.py`, record `agent_pid` (with the legacy Codex event key accepted as input compatibility) into `entry.agent_pgid`; use it as the heartbeat fallback; and make `_force_eject_zombie` kill `entry.agent_pgid` when present while logging `agent_kind` and the recorded identifier. Leave release, lease finishing, pause wake-up, retry backoff, and debug-state behavior unchanged.
5. In `tests/test_backend_contract.py`, extend the shared backend lifecycle contract so each concrete backend must emit a non-null integer `agent_pid` while a live subprocess is published. Keep protocol-specific parsing assertions in their existing tests.
6. Migrate the focused tests that construct/read `RunningEntry.codex_app_server_pid` to the backend-neutral field, then run the targeted orchestrator, backend-contract, and registry checks until green.
7. Record the reasoning and rejected alternatives in `docs/changelog/changelog-2026-07-10.md`, matching the repo's existing daily changelog format.
8. Run `python -m ruff check src tests`, `python -m pyright`, and the CI-equivalent full coverage suite. Re-run the baseline command and compare it with the saved pre-change result; only the named AF-02 behavior may drift.

## Implementation shapes

The builder must preserve surrounding style and may adjust local variable names, but the behavior stays this narrow:

```python
# src/symphony/orchestrator/entries.py
agent_pgid: int | None = None
```

```python
# src/symphony/orchestrator/core.py event ingestion / lease fallback
pid = event.get("agent_pid") or event.get("codex_app_server_pid")
if isinstance(pid, int):
    entry.agent_pgid = pid
    self._heartbeat_run_lease(issue_id, entry, backend_agent_pid=pid)

backend_agent_pid=backend_agent_pid or entry.agent_pgid
```

```python
# src/symphony/orchestrator/core.py force-eject branch
if entry.agent_pgid is not None:
    killed = kill_process_group(entry.agent_pgid)
    log.warning(
        "force_eject_killed_process_group",
        issue_id=issue_id,
        identifier=entry.issue.identifier,
        agent_kind=self._entry_agent_kind(entry),
        pid=entry.agent_pgid,
        killed=killed,
    )
```

```python
# tests/test_backend_contract.py shared live-turn assertion
turn_completed = next(
    event for event in events if event["event"] == EVENT_TURN_COMPLETED
)
assert turn_completed["agent_pid"] == _FakeSubprocess.pid
```

The first tracer-bullet test constructs a Claude `RunningEntry(agent_kind="claude", agent_pgid=4242)`, drives the existing force-eject path, and spies on `kill_process_group` plus `log.warning`. It must fail before the source field migration. The second test omits `agent_pgid`, proves no kill call, and preserves slot release + retry scheduling.

## Tools & Skills

- Supergoal LEGACY role-loop: Builder -> Improve full spec -> Improve edge cases -> Mandatory Adversarial Review -> Exact Verify/QA.
- Codebase Memory graph for symbol/call-path mapping; repository reads for non-code docs/config and exact line citations.
- Focused TDD: `python -m pytest -q tests/test_orchestrator_dispatch.py -k force_eject`.
- Contract proof: `python -m pytest -q tests/test_backend_contract.py tests/test_orchestrator_dispatch.py -k 'agent_pid or full_lifecycle'`.
- Neighbor proof: `python -m pytest -q tests/test_run_registry.py tests/test_orchestrator_dispatch.py -k 'backend_agent_pid or force_eject'`.
- CI gates: `python -m ruff check src tests`; `python -m pyright`; `python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80`.

## Verification strategy

- Before proof: existing Codex-named entry field and force-eject tests pass, while a new non-Codex `agent_pgid` force-eject regression test fails before source migration.
- Step -> GOAL.md criterion: step 2 -> criteria 1-3; steps 3-4 -> criteria 1-3 and 5; step 5 -> criterion 4; steps 6-8 -> criteria 4-6.
- Trusted commands: `python -m pytest -q tests/test_orchestrator_dispatch.py -k force_eject` (evaluator_owned); `python -m pytest -q tests/test_backend_contract.py tests/test_orchestrator_dispatch.py -k 'agent_pid or full_lifecycle'` (evaluator_owned); `python -m ruff check src tests` (frozen_repo); `python -m pyright` (frozen_repo); `python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80` (frozen_repo).

## Domain Brief

- Knowledge path: ephemeral in this run vault (`.domain-agent/` absent; no persistent pack created).
- Stable terms: backend `pid` is the leader pid of the POSIX session/process group because backends spawn with `start_new_session=True`; `agent_pid` is the backend event-envelope field; `agent_pgid` is the running-entry field used for kill ownership.
- Invariants: never free a zombie slot without attempting to kill a recorded owned process group; missing pid must not block slot release/retry; process termination must stay scoped to the spawned session; do not change reaping or exit identity in AF-02.
- Current-code verification: `RunningEntry` has only the Codex-named field (`src/symphony/orchestrator/entries.py:84`); event ingestion already accepts both legacy `codex_app_server_pid` and backend-neutral `agent_pid` but stores into the Codex-named field (`src/symphony/orchestrator/core.py:4301-4304`); heartbeat fallback and force-eject consume that field (`core.py:862`, `core.py:4997-5004`).
- Entry points: `RunningEntry`, `Orchestrator._on_codex_event`, `Orchestrator._heartbeat_run_lease`, `Orchestrator._force_eject_zombie`, backend `_emit` methods, shared backend contract.
- Test commands: focused selectors and CI-equivalent commands above.
- Gaps: none. Structural search found no external/public constructor surface; the only named field uses are the three core reads/writes and two focused orchestrator tests, so a direct internal migration is smaller than a compatibility alias.

## Priority Rules

Domain(s): async process lifecycle + orchestrator shared state
1. Kill only process groups owned by spawned backend sessions.
2. Record process identity while the child is live, before cleanup clears backend-local state.
3. Preserve slot release and retry even when spawn fails before a pid exists.
4. Keep structured logs sufficient to identify issue, backend kind, pid/pgid, and kill result.
5. Preserve run-registry heartbeat semantics and existing database schema.
6. Keep AF-01 identity races, AF-10 reclaim, and reaping outside this patch.
7. Treat cancellation, force-eject, and eventual worker wake-up as concurrent lifecycle paths.

## Grounding ledger

- Ticket direction -> backend-neutral recorded process group, per-backend logging, missing-pid preservation -> `docs/improvements/tickets/2026-07-09/AF-02-force-eject-kills-all-backends.md` -> implement surgically.
- Current event contract -> Codex, Claude, shared per-turn (Gemini/AGY/Kiro/OpenCode), and Pi emit `agent_pid` from their live backend pid (`src/symphony/backends/codex.py:922-938`, `claude_code.py:376-392`, `per_turn.py:286-301`, `pi.py:446-462`) -> do not change backend spawn or envelope code.
- Live pid timing -> per-turn, Claude, and Pi publish `_active_proc` before consuming/emitting the turn and clear it only in `finally` (`per_turn.py:166-197`, `claude_code.py:152-258`, `pi.py:163-273`); Codex owns its persistent spawned process from `start()` (`codex.py:299-321`) -> completion events can assert non-null pid.
- Call path -> `_reconcile_running` calls `_force_eject_zombie`; the latter pops `_running`/`_claimed`, conditionally kills, finishes the lease, wakes pauses, and schedules retry (`core.py:4985-5022`) -> change only its pid source/log fields.
- Logging reuse -> `_entry_agent_kind` already resolves `entry.agent_kind`, issue override, then workflow default (`core.py:1428-1435`) -> reuse it; do not duplicate backend selection logic.
- Existing mitigation -> commit `1818d60` added `event.get("agent_pid")`, so current `dev` already kills non-Codex processes when an event recorded the pid; AF-02's remaining verified gaps are the backend-specific field name, missing backend-kind log assertion, and missing live-pid value assertion in the shared contract.
- Compatibility choice -> direct internal rename, no alias. Exact search found `codex_app_server_pid` only at `entries.py:84`, `core.py:862,4301-4304,4997-5004`, and two focused test uses (`tests/test_orchestrator_dispatch.py:650,1352`).

## Explorer code map

- Entry/state path: `RunningEntry` owns the only Codex-named runtime field (`src/symphony/orchestrator/entries.py:84`). `_on_codex_event` accepts the legacy `codex_app_server_pid` event key before the shared `agent_pid`, writes the selected integer to the entry, and immediately persists it through the lease heartbeat (`src/symphony/orchestrator/core.py:4298-4305`). Periodic heartbeats currently fall back to that entry field (`src/symphony/orchestrator/core.py:838-865`). Migrate those entry reads/writes together; retain the legacy event-key read as wire-input compatibility.
- Eject path: reconciliation reaches `_force_eject_zombie`; the method removes the running/claimed ownership, conditionally calls `kill_process_group`, records the lease completion, wakes any pause waiter, and schedules the retry (`src/symphony/orchestrator/core.py:4985-5022`). Change only the pid field and add `agent_kind`; the existing `if pid is not None` boundary already preserves slot release and retry when spawn produced no pid.
- Backend emission boundary: every emitter snapshots its current `pid` into the normalized `agent_pid` envelope — Codex (`src/symphony/backends/codex.py:922-935`), Claude (`src/symphony/backends/claude_code.py:377-389`), the shared per-turn family used by Gemini/AGY/Kiro/OpenCode (`src/symphony/backends/per_turn.py:286-298`), and Pi (`src/symphony/backends/pi.py:449-459`). The load-bearing timing rule is therefore: each spawn family must emit at least one callback after its live process is published and before backend-local cleanup clears it; per-turn backends must do this on every turn spawn, not rely only on the earlier session event or eventual turn outcome.
- Contract shape: `tests/test_backend_contract.py` defines the shared envelope, including `agent_pid`, at lines 50-60 and exercises `start -> initialize -> start_session -> run_turn -> stop` at lines 78-133. Its six non-Codex lifecycle subclasses are at lines 162-234; Codex is deliberately kept in its persistent app-server suite (`tests/test_backend_contract.py:20-22`). Extend the shared lifecycle assertion to require a non-null integer pid from an event observed while the fake child is published; keep Codex proof in its existing backend-family tests instead of forcing it into the per-turn matrix.
- Backend-kind logging reuse: call `_entry_agent_kind(entry)`, which resolves the entry override, ticket override, then configured default (`src/symphony/orchestrator/core.py:1428-1435`), rather than duplicating backend-kind resolution in force-eject.
- Exact legacy-field footprint and alias decision: production references are the dataclass field, heartbeat fallback, event assignment, and force-eject read/log (`src/symphony/orchestrator/entries.py:84`; `src/symphony/orchestrator/core.py:862,4301-4303,4997-5003`). Tests have one constructor and one read (`tests/test_orchestrator_dispatch.py:643-650,1327-1352`). No other Python caller uses the field, so a `RunningEntry.codex_app_server_pid` compatibility alias is not needed; migrate this closed footprint to `agent_pgid`. Preserve only the legacy inbound event-key alias at `src/symphony/orchestrator/core.py:4301` and capture the current envelope/missing-pid behavior as the preserve-baseline before RED.
- Blast radius: source changes should stay within `entries.py`, `core.py`, and the four emitter families only if spawn-time emission is currently absent; tests stay within the focused orchestrator regression, the shared non-Codex lifecycle contract, and the existing Codex backend suite. Do not change the run-registry schema, process spawning/session flags, reaping, exit identity, or startup reclaim.

## R-loop refinement: clear ownership before the next spawn

Immediately before every `run_turn`, copy the backend's current `pid` into
`RunningEntry.agent_pgid`. This deliberately clears the previous per-turn
child while no next child exists, but preserves Codex's persistent process id.
The normalized `turn_started` event then installs the newly spawned per-turn
child id. Rejected: retaining the previous pid until the next event, because a
spawn failure or pre-output hang could make force-eject target a stale or
reused process id.

## R-loop refinement: synchronize complete backend lifetimes

Process ownership now follows the backend's actual live interval, not only its
events. A `run_turn` finally refreshes `agent_pgid` from `client.pid`, clearing
a completed per-turn child before after-run hooks or tracker refresh can stall;
a persistent backend retains its still-live pid. Initial and phase-rebuilt
persistent clients copy and heartbeat their pid immediately after `start()` so
initialization and session setup are covered. Phase rebuild clears the stopped
backend's ownership before starting the replacement. Rejected: waiting for a
later backend event, because initialize/start-session stalls and post-turn
orchestrator work occur outside those event windows.

## R-loop refinement: persist only confirmed process ownership

`heartbeat(None)` remains a lease-only heartbeat and therefore preserves the
last pid by design. AF-02 adds a separate registry clear operation and routes
all ownership transitions through one orchestrator helper: a non-null pid uses
the established heartbeat path; `None` explicitly clears the active run row.
The helper runs before and in `finally` around start/turn boundaries, after a
confirmed old-phase stop, and after confirmed final/new-client cleanup. A stop
exception is now an ownership failure: the old phase transition aborts, no
replacement starts, and the last known pid remains available for force-eject.

Rejected: treating a logged stop exception as teardown success. That can run
two backends for one issue and discard the only kill target. Also rejected:
changing `safe_proc_wait` or adding reaping/reclaim behavior; those remain the
ticket's explicit non-goals.
