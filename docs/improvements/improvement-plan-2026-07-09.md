# Improvement plan — 2026-07-09: agent-flow reliability

Companion to [`improvement-plan-2026-07-07.md`](./improvement-plan-2026-07-07.md)
(the active structural plan). That document tracks the god-class decomposition
and backend-contract work; **this one is the reliability track**: a focused
audit of the agent flow — dispatch → worker turns → board transitions →
reconcile/eject — hunting for latent bugs. Five independent read-only analysis
sweeps (dispatch/slot lifecycle, reconcile/stall/eject, backend subprocess
layer, tracker/board state machine, worker-turn/heartbeat interplay) produced
22 findings, consolidated into 16 tickets under
[`tickets/2026-07-09/`](./tickets/2026-07-09/).

Nothing in this plan changes product code by itself. Every ticket requires a
failing reproduction test before its fix (red → green) and names its proof
commands.

## Destination

The orchestrator survives its own edge cases: a force-ejected zombie can never
corrupt or eject its replacement worker; every stuck subprocess is actually
killed regardless of backend; operator actions (pause/resume, Kanban drag) can
never falsely kill or falsely complete a healthy run; productive turns are
never misclassified as empty; the board scan never yields phantom or silently
dropped tickets. Concretely: all 16 tickets closed or explicitly rejected with
a recorded reason, each closed one proven by a test that failed before its fix.

## Verified baseline (2026-07-09)

- Full suite: **1279 passed, 2 skipped** (`/opt/anaconda3/bin/python -m pytest -q`, 83s).
- `ruff check src tests`: clean. CI (`Tests` workflow): last 3 runs green.
- Local bare `pyright` reports 16 errors — all environment-resolution
  artifacts (pyright 1.1.411 resolving against pyenv 3.9 instead of the
  anaconda env; `textual` 8.2.5 and `ruamel.yaml` are installed and import
  fine). CI's `python -m pyright` is the authoritative gate and is green.
- `main == dev`, working tree clean at v0.12.0.

## Current state evidence

Five parallel read-only sweeps over `src/symphony/` at v0.12.0, each briefed
with the known-fixed list (done-callback identity check, retry-inclusive slot
budget, two-stage eject, `safe_proc_wait`, 499e787 stall predicate, opencode
schema-drift fix, G2/G3/G5) so findings below are new, not rediscoveries.
Load-bearing claims behind the P0/P1 tickets were re-verified against the
working tree by the conductor before this plan was written (exact file:line
in each ticket).

Headline result: **the residual `finished_without_cleanup` race — open since
commit 01803f0 and carried as P2-2 in the 07-07 plan — now has a confirmed
root cause** (ticket AF-01). The docstring assumption in
`_force_eject_zombie` ("`_on_worker_exit` is a no-op on a missing entry, so
this is race-safe", `core.py:4990-4993`) does not hold once a backoff retry
re-installs a *fresh* entry under the same issue id: the old zombie's
`finally` then finds — and pops — the live replacement's entry, because
neither `core.py:3915` nor the pop at `core.py:4654` verifies task identity.
The `entry_owned_by` primitive built for exactly this exists
(`dispatch_state.py:79`) but was only ever applied to the done-callback.

## Decisions so far (do not re-litigate)

- **TaskGroup stays rejected** (2026-07-05): sibling auto-cancel couples
  independent ticket workers. Worker fan-out remains `create_task` +
  done-callback. (ADR still owed per 07-07 P1-4.)
- **Two-stage eject stays**: cancel → 30s terminal grace → force-eject.
  Tickets here harden its edges (AF-02, AF-07); none collapse the stages.
- **Strict-serial slot semantics stay**: `_available_slots` counts
  `_running` + `_retry`.
- **Fix placement for the preview-key bug is backend-side** (add a
  `"message"` key next to the wire shape, mirroring the opencode fix) rather
  than widening `_preview_from_payload` — the payload contract lives with the
  backend (AF-05).
- **Ticket ordering**: AF-01 first. It is the only P0, it closes the oldest
  open mystery, and every reconcile/retry hardening ticket is easier to test
  once exits are identity-safe.

## Not yet specified (decide at ticket time)

- AF-11: whether `continuous_improvement.max_turns` becomes a per-window rate
  (auto-reset) or stays a documented lifetime kill-switch — operator-facing
  semantics, needs Danny's call.
- AF-14: whether a `last`-without-`total` `tokenUsage` shape actually ships
  from the codex app-server (research before hardening).
- AF-13: exact rewind-detection rule for custom pipelines (index-order in
  `active_states` vs per-workflow config).

## Out of scope

- The 07-07 structural sequence (ImprovementScheduler extraction, field-
  cluster extractions, Split-Phase, StreamingCliBackend). AF-11 explicitly
  rides on/after that P1-1 extraction rather than duplicating it.
- File-size breaches, branch cleanup, doc audits (already tracked as 07-07
  P0/P2 items).
- Any TUI/web UI feature work.

## Ticket graph

| ID | Title | Sev | Confidence | Route | Blocked by |
|---|---|---|---|---|---|
| [AF-01](./tickets/2026-07-09/AF-01-identity-safe-worker-exit.md) | Identity-safe worker exit path (root cause of `finished_without_cleanup`) | P0 | CONFIRMED | DEBUG | none |
| [AF-02](./tickets/2026-07-09/AF-02-force-eject-kills-all-backends.md) | Force-eject must kill non-codex process groups too | P1 | CONFIRMED | LEGACY | none |
| [AF-03](./tickets/2026-07-09/AF-03-resume-after-pause-stall-false-positive.md) | Resume after long pause instantly stall-cancels healthy worker | P1 | CONFIRMED | DEBUG | none |
| [AF-04](./tickets/2026-07-09/AF-04-webapi-state-patch-running-guard.md) | Web API issue PATCH mutates running ticket's state unguarded | P1 | CONFIRMED | DEBUG | none |
| [AF-05](./tickets/2026-07-09/AF-05-per-turn-content-contract.md) | Per-turn backends: preview-key mismatch → false empty-loop; exit-0/empty-stdout false success | P1 | CONFIRMED | DEBUG | none |
| [AF-06](./tickets/2026-07-09/AF-06-board-scan-ignores-tmp-files.md) | Board scan matches `.tmp-*.md` → phantom duplicate tickets | P1 | CONFIRMED | DEBUG | none |
| [AF-07](./tickets/2026-07-09/AF-07-reconcile-part-a-isolation.md) | Reconcile Part A: per-issue isolation + cancelled-zombie vs pause ordering | P2 | CONFIRMED/PLAUSIBLE | LEGACY | none |
| [AF-08](./tickets/2026-07-09/AF-08-bounded-stop-drain.md) | `stop()` awaits worker tasks unbounded — zombie can hang shutdown | P2 | PLAUSIBLE | LEGACY | none |
| [AF-09](./tickets/2026-07-09/AF-09-codex-stream-corrupt-wedge.md) | Codex malformed-streak break wedges the persistent app-server | P2 | CONFIRMED | DEBUG | none |
| [AF-10](./tickets/2026-07-09/AF-10-lease-reclaim-agent-liveness.md) | Lease reclaim checks orchestrator PID, not the live agent subprocess | P2 | PLAUSIBLE | LEGACY | AF-02 |
| [AF-11](./tickets/2026-07-09/AF-11-heartbeat-scheduler-semantics.md) | Heartbeat scheduler: `max_turns` silent latch, lease busy-spin, idle guard duration | P2 | CONFIRMED/PLAUSIBLE | LEGACY | 07-07 P1-1 (soft) |
| [AF-12](./tickets/2026-07-09/AF-12-tracker-integrity.md) | Tracker integrity: silent parse drops, duplicate ids, unlocked delete | P2 | CONFIRMED/PLAUSIBLE | LEGACY | none |
| [AF-13](./tickets/2026-07-09/AF-13-rewind-budget-custom-pipelines.md) | Rewind budget only counts hard-coded English state pair | P2 | CONFIRMED | LEGACY | none |
| [AF-14](./tickets/2026-07-09/AF-14-codex-token-last-vs-total.md) | Codex token accounting: `last`-only tokenUsage can double-count | P3 | PLAUSIBLE | LEGACY | research |
| [AF-15](./tickets/2026-07-09/AF-15-dispatch-state-hygiene.md) | Dispatch-state hygiene: `_completed`/`_issue_debug` unbounded growth | P3 | CONFIRMED | LEGACY | none |
| [AF-16](./tickets/2026-07-09/AF-16-consistent-turn-budget-prompts.md) | First-turn vs continuation prompts use different turn-budget denominators | P3 | CONFIRMED | LEGACY | none |

Findings verified clean (no ticket): stderr-pipe deadlocks, readline limits,
naive/aware datetime math in stall predicates, done-callback vs reconcile
double-eject, CAS write path, id allocation, phase-boundary token rebase,
turn-loop termination, blocked-board recovery gating, fcntl locking between
web API and orchestrator. Details in each sweep's "Checked clean" list
(changelog 2026-07-09).

## Frontier

**Closed 2026-07-10.** All 16 tickets are resolved: AF-01 (dev 4de380f),
AF-02 (dev 793813a), AF-03..AF-16 (dev ee64bad; AF-14 closed by research —
the Codex 0.130/0.144 protocol schemas make a `last`-only tokenUsage
unreachable). Each closed ticket carries red-green evidence in its ticket
file and run vault. Exact-verification closure on `dev@ee64bad`:
`python -m pytest -q` 1363 passed / 5 skipped, ruff clean, pyright 0 errors.
Residual risks (AF-04 guard-vs-dispatch window, AF-10 pid-reuse identity) are
recorded in `docs/changelog/2026-07/10-af-03-16-reliability/QA.md`; the
reliability track is complete.

## Global rules (unchanged)

Failing reproduction test before every fix; one ticket = one branch = small
diffs; commit on `dev`, merge to `main`; full `pytest` + ruff + pyright green
before merge; never collapse the two-stage eject; never re-propose TaskGroup.
