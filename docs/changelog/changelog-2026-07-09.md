# Changelog — 2026-07-09

## Agent-flow reliability audit → improvement plan + 16 tickets (docs only)

Deliverable: [`docs/improvements/improvement-plan-2026-07-09.md`](../improvements/improvement-plan-2026-07-09.md)
plus `docs/improvements/tickets/2026-07-09/AF-01…AF-16`. No product code
changed today; every ticket mandates a failing reproduction test before its
fix.

### What was done and why

Objective: carefully analyze the agent flow (board + ticket resolution) for
potential bugs and turn the result into a spec + tickets. Method: five
independent read-only analysis sweeps over `src/symphony/` at v0.12.0, each
covering one seam — (1) dispatch/slot lifecycle, (2) reconcile/stall/eject,
(3) backend subprocess layer, (4) tracker/board state machine, (5)
worker-turn loop + continuous-improvement interplay. Each sweep was briefed
with the known-fixed history (done-callback identity check, retry-inclusive
slot budget, two-stage eject, `safe_proc_wait`, 499e787 stall predicate,
opencode schema-drift fix, G2/G3/G5) so it hunted new defects instead of
rediscovering shipped fixes. 22 findings consolidated into 16 tickets.

The conductor re-verified the load-bearing evidence for the P0/P1 tickets
directly against the working tree before writing the plan
(`core.py:3915-3920`, `:4654`, `:4988-4998`; `webapi.py:549-596` vs `:684`;
`core.py:4262-4283` vs `plain_cli.py:44-59` / `gemini.py:64-76`). P2/P3
findings carry the sweep's own citations; their tickets require a failing
repro test first, which re-validates each claim at implementation time.

### Headline: `finished_without_cleanup` root cause (AF-01, P0)

The race carried as "unidentified" since 01803f0 (07-07 plan P2-2) is now
explained: `_force_eject_zombie`'s race-safety docstring assumes the ejected
worker's `finally` will no-op on a missing `_running` entry — but after the
backoff retry re-installs a *fresh* entry under the same issue id, the
zombie's `finally` (`core.py:3915`, key-based `get`) and
`_on_worker_exit_impl`'s pop (`core.py:4654`, key-based `pop`) operate on the
replacement's live entry. The `entry_owned_by` identity primitive existed
(`dispatch_state.py:79`) but was applied only to the done-callback. A
supporting defect makes the zombie possible in the first place: force-eject
only kills codex process groups (`codex_app_server_pid`); other backends'
wedged subprocesses are never killed (AF-02).

### Decisions and rejected alternatives

- **Plan-first, no fixes in this change.** The objective asked for a plan +
  spec + tickets; fixes land one ticket per session, red-green, per the
  frontier rule. Rejected: fixing AF-01 inline today — the exit-path change
  touches the most race-sensitive seam in core.py and deserves its own
  characterization tests, not a rider on a docs commit.
- **Preview-key fix placed backend-side** (add `"message"` to plain/gemini
  `TURN_COMPLETED` payloads, mirroring the opencode 0.9.2 fix). Rejected:
  widening `_preview_from_payload` to read `result`/`response` as the
  primary fix — the payload contract belongs to the backend, and the
  orchestrator-side reader is shared surface where a broadened key list
  could shadow future backend intent. (A narrow claude dict-flatten branch
  is still allowed in AF-05 because claude's frame shape is upstream-fixed.)
- **16 tickets, not one mega-ticket and not 22.** Findings sharing one seam
  and one test-fixture family were grouped (AF-07 two reconcile edges; AF-11
  three scheduler semantics; AF-12 three tracker integrity gaps); everything
  else stays a vertical slice. Rejected: per-finding tickets (6 of them
  would be sub-hour follow-your-nose items drowning the board) and
  per-sweep tickets (would mix unrelated modules).
- **Standing decisions honored, not re-litigated:** TaskGroup stays
  rejected; the two-stage cancel→grace→force-eject stays; strict-serial slot
  accounting stays. Tickets harden edges of those designs.
- **AF-11 sequenced after/with 07-07 P1-1** (ImprovementScheduler
  extraction) so the fixes don't add more heartbeat code to the god-class
  the other plan is shrinking.

### Verified baseline (evidence for the plan's claims)

- `/opt/anaconda3/bin/python -m pytest -q` → **1279 passed, 2 skipped** (83s).
- `ruff check src tests` → clean. GitHub `Tests` workflow: last 3 runs green.
- Local pyright note for future sessions: bare `pyright` (and
  `python -m pyright` under pyright 1.1.411 locally) reports 14
  `textual` `reportMissingImports` + 2 `ruamel.yaml` stub errors. These are
  environment-resolution artifacts — `textual` 8.2.5 and `ruamel.yaml`
  import fine in the anaconda env; CI's gate is green. Do not "fix" imports
  because of a local pyright run.

### Sweep "checked clean" record (examined, no ticket filed)

- Dispatch: `_dispatch` spawn atomicity (no await between `begin_run` →
  `create_task` → callback registration); retry-timer dedup +
  `_has_active_run_lease` re-entry block; reconcile Part A snapshot
  iteration; workspace double-cleanup guards
  (`workspace_cleanup_started`, `exit_started_at`); `_terminal_persist_pending`
  add/discard balance; `stop()` drain ordering.
- Reconcile: no naive/aware datetime mixing in stall math; done-callback vs
  reconcile double-eject guards; 499e787 progress predicate holds; terminal
  cleanup grace measured from `terminal_seen_at`; tick loop survives
  reconcile exceptions with backoff.
- Backends: stderr drained concurrently (no pipe deadlock); 10 MB readline
  limits; cancellation reaps process trees via `start_new_session` +
  `terminate_process_tree`; `safe_proc_wait` double-reap race handled;
  non-UTF8/malformed lines tolerated sparsely; phase-boundary token rebase
  self-corrects.
- Tracker: CAS write path (retry ×3 vs external writers) sound; fcntl locks
  real between web API and orchestrator (same process, separate fds);
  `next_identifier` allocator lock spans allocate+create; Korean state names
  safe in normalize/active/terminal matching (only the rewind map is
  English-bound — AF-13).
- Worker/heartbeat: turn loop always terminates (`max_turns` break + rewind
  ceiling); improvement turn counted exactly once per run; same-tick
  dispatch↔CI kickoff race prevented by synchronous `begin_run`; scheduler
  cannot wedge on runner exceptions (identity-checked done hook);
  blocked-board recovery properly gated.

### Repo bookkeeping

- `improvement-plan-2026-07-07.md` P2-2 annotated: root-caused → AF-01.
- Baseline for the plan measured on `main == dev`, tree clean, v0.12.0.
