# Architecture improvement plan — 2026-07-05

Strategic follow-up to the tactical punch-lists in
[`dispatch-stability-2026-05-20.md`](./dispatch-stability-2026-05-20.md) and
[`dispatch-double-dispatch-race-2026-06-28.md`](./dispatch-double-dispatch-race-2026-06-28.md),
and a forward-looking companion to the resident map in
[`../architecture.md`](../architecture.md). Those documents fix individual
dispatch bugs one gate at a time; this one asks *why the same class keeps
producing them* and proposes the structural changes that make a whole class of
bug hard to write.

Scope: `src/symphony/` at version `0.10.1`. This is a planning document, not a
patch. Nothing here should be executed big-bang — see **Sequencing**.

---

## TL;DR (for the impatient, including non-engineers)

- **The problem in one sentence.** One file, `orchestrator/core.py`, is **4574
  lines — a single `Orchestrator` class with 105 methods and ~40 mutable
  fields** that hold all of Symphony's live concurrency state. Nearly every
  dispatch race, slot leak, and stuck-ticket incident in our history traces
  back into it, and the class's own constructor reads like a changelog of past
  bugs.
- **Why it keeps happening.** The rules that must stay true while agents run
  (a slot is freed exactly once, a retry counts against the budget, a
  cancelled worker is really gone before its ticket re-dispatches) are
  **written by hand in dozens of separate methods**. `architecture.md` already
  admits these invariants are "easy to regress and hard to spot in a diff."
  There is no single place that enforces them, so each new feature can quietly
  break one.
- **The fix, in five moves.** (A) Give that shared state **one owner object**
  whose methods enforce the invariants, then extract the big responsibilities
  out of the god-class. (B) Supervise worker tasks and the tick loop with
  **structured concurrency** so a task cannot be orphaned. (C) Strengthen the
  already-good backend layer with a **shared base + one contract-test suite**
  so the 9 CLI adapters cannot drift. (D) Replace the fragile
  import-order/monkeypatch test wiring with **dependency injection**. (E) Turn
  on **type-checking and lint in CI** — it would catch real bugs sitting in
  `core.py` *today*.
- **How we do it safely.** Characterization tests first, then Strangler-style
  incremental extraction — the system stays green and shippable after every
  step. No rewrite.
- **The encouraging part.** We do not need to invent the target design. Our own
  `backends/` package already demonstrates it (explicit `Protocol` contract,
  constructor injection via `BackendInit`, a shared base, a factory). This plan
  is largely *"apply the patterns that already work in `backends/` to the one
  module that never got them."*

---

## How this plan was built

Two evidence streams, deliberately kept separate so recommendations are
falsifiable:

1. **Codebase diagnosis.** Structure and hotspots from the codebase knowledge
   graph (`get_architecture`, `documentSymbol`, targeted reads), cross-checked
   against the incident record in the maintainer's working memory (done-callback
   race, slot-budget-includes-retry, two-stage force-eject, stall event-filter,
   residual `finished_without_cleanup`, opencode schema-drift). Every claim below
   carries a `file:line` anchor.
2. **External best-practice research.** Four parallel literature sweeps
   (god-class decomposition; asyncio structured concurrency; ports-and-adapters
   + contract testing; DI vs. monkeypatching), restricted to primary sources
   (Fowler's refactoring catalog, Feathers, CPython docs, Cockburn, Meszaros,
   the *Architecture Patterns with Python* authors). Sources are listed per
   initiative and collected in the appendix.

The striking result of putting them side by side: **the external literature
independently prescribes the exact fixes Symphony already discovered the hard
way** — and then points one level up, to the structural cures that would have
prevented the incidents wholesale.

---

## Current architecture snapshot

`architecture.md` is the authoritative map; only the shape relevant to this plan
is repeated here.

| Layer | Modules | Notes |
|---|---|---|
| Entry | `cli/`, `service.py`, `tui/app.py` | 4 `main` entry points |
| Orchestration | `orchestrator/` (core, run_registry, entries, helpers, contracts, parsing) | the engine |
| Backends | `backends/` (codex, claude_code, gemini, opencode, pi, agy, kiro, plain_cli) | `AgentBackend` Protocol + factory |
| Trackers | `trackers/` (file, jira, linear) | Kanban source of truth |
| Workflow | `workflow/` (parser, config, state, builder, mutate, preflight, coercion) | already split into 8 submodules |
| Surfaces | `webapi.py`, `server.py`, `tui/` | HTTP + Textual |

The `workflow`, `tui`, and `orchestrator` packages were each split out of
single files in earlier refactors (`architecture.md` "Historical Split
Commits"). **The one thing the orchestrator split did not do was break up the
state machine itself** — `Orchestrator` stayed a single class. That is the gap
this plan targets.

Largest source files (lines):

```
4574  src/symphony/orchestrator/core.py     <-- ~19% of all src lines, one class
1119  src/symphony/tui/app.py
1023  src/symphony/backends/codex.py
 986  src/symphony/service.py
 869  src/symphony/trackers/file.py
 775  src/symphony/webapi.py
 753  src/symphony/workflow/builder.py
 690  src/symphony/workspace.py
 628  src/symphony/orchestrator/run_registry.py
```

---

## The core finding: the `Orchestrator` god-class

`src/symphony/orchestrator/core.py` defines one class, `Orchestrator`
(`core.py:170`), with:

- **105 methods** in a single class body.
- **~40 mutable instance fields** initialised in `__init__` (`core.py:181-267`),
  spanning at least seven unrelated responsibilities:
  - **dispatch/slot bookkeeping** — `_running`, `_claimed`, `_retry`,
    `_persisted_retry_attempts`, `_completed`, `_turn_budget_exhausted`,
    `_lease_blocked`, `_terminal_persist_pending`, `_claim_released_at`
  - **pause control** — `_paused_issue_ids`, `_pause_reasons`, `_pause_events`
  - **token accounting** — `_totals`, `_latest_rate_limits`, `_token_ema`,
    `_token_ema_loaded`
  - **loop supervision / health** — `_tick_task`, `_tick_event`, `_stopping`,
    `_last_tick_completed_at`, `_consecutive_tick_failures`, `_tick_error_count`,
    `_tick_loop_restarts`, `_last_tick_error`, `_consecutive_candidate_fetch_failures`,
    `_registry_error_count`, `_last_registry_error`, `_pending_escalations`
  - **run leases / registry** — `_run_registry`
  - **housekeeping** — `_done_count`, `_last_archive_sweep_monotonic`
  - **observers / infra** — `_observers`, `_workspace_manager`, `_stats`,
    `_issue_debug`, `_loop`, `_refresh_pending`
- **One ~600-line method**, `_on_worker_task_done` (`core.py:2174` through the
  continuation logic ending near `_rebuild_backend_for_phase` at `core.py:2805`),
  that owns dispatch continuation, phase-transition detection, backend rebuild,
  and contract refresh in one body.

**The most telling evidence is the constructor's own comments.** Fields in
`__init__` are annotated with the incident IDs that forced them into existence —
`C5`, `C3`, `R1`/`A1`, `R8`, `G1`, `G3`, and a pointer to
`dispatch-double-dispatch-race-2026-06-28.md`. The class's initialiser is
literally a scar map of concurrency bugs. Each scar marks a place where an
invariant *spanning several of these open-coded maps* was violated and then
patched in one method — without any structural guarantee that the next feature
won't violate it again.

This matches the definition of a god object precisely: a class with "one reason
to change" for each of many axes (dispatch, state, concurrency, I/O, health),
which is to say no single responsibility at all
([SRP, R. C. Martin](https://blog.cleancoder.com/uncle-bob/2014/05/08/SingleReponsibilityPrinciple.html)).

### Why decomposition — not more gates — is the right response

Every prior fix (the G1–G5 gates, the double-dispatch race, the done-callback
identity check) added a *guard inside a method*. That is correct triage, but it
grows the class and leaves the invariant enforced in yet another isolated spot.
Fowler's remedy is to make the data and the rules that govern it live together:
**Combine Functions into Class** and **Encapsulate Record**, so every read/write
of the shared maps flows through one object that can validate on every mutation
([Fowler, *Refactoring* 2nd ed.](https://refactoring.com/catalog/)). Then the
invariant is enforced once, in the mutator, instead of re-checked in 105 methods.

---

## Initiative A — Extract a `DispatchState` owner, then split the god-class

**Goal (plain language):** give the live dispatch state a single owner whose
methods are the *only* way to change it, so the "free a slot exactly once,"
"retries count against the budget," and "task-identity before eviction" rules
are enforced in one place instead of scattered across the class.

**Evidence.** `_running`/`_claimed`/`_retry`/`_completed` are read and mutated
directly across dispatch (`_dispatch` `core.py:2089`), completion
(`_on_worker_task_done` `core.py:2174`), exit (`_on_worker_exit` `core.py:3492`),
force-eject (`_force_eject_zombie` `core.py:3819`), retry
(`_schedule_retry` `core.py:3872`, `_on_retry_timer` `core.py:4049`), and
reconcile (`_reconcile_running` `core.py:4131`). The slot math in
`_available_slots` (`core.py:1724`) must subtract both `_running` and `_retry`
— a rule the memory record shows was regressed and re-fixed. These are exactly
the mutations a state owner should encapsulate.

**Target design.**

1. **`DispatchState`** (new, in `orchestrator/dispatch_state.py`) owns
   `_running`, `_claimed`, `_retry`, `_completed`, `_persisted_retry_attempts`,
   `_turn_budget_exhausted`, and exposes intention-revealing mutators:
   `claim(id)`, `begin_run(id, entry)`, `free_slot(id, task)` (asserts
   `entry.worker_task is task` — the done-callback identity invariant, encoded
   once), `schedule_retry(id, entry)`, `available_slots(cap)` (the one place
   that subtracts running **and** retry). Every mutation can assert its
   invariants and log on violation. This is **Encapsulate Record** +
   **Tell, Don't Ask** — callers state intent, the owner keeps the rules
   ([Fowler](https://martinfowler.com/bliki/TellDontAsk.html)).
2. Treat `DispatchState` as the **single writer** for live slot state; the tick
   loop is already the de-facto sole mutator, so making that explicit removes a
   whole category of interleaving reasoning
   ([Thompson, Single Writer Principle](https://mechanical-sympathy.blogspot.com/2011/09/single-writer-principle.html)).
3. Then peel further collaborators off the class with **Extract Class**
   ([Fowler](https://refactoring.com/catalog/extractClass.html)), each owning
   its field cluster: `TokenAccountant` (ema/budget/attention/totals —
   `core.py:1821-2088`, `_apply_token_totals` `core.py:3387`), `HealthReporter`
   (`_health_summary` `core.py:731`, the `_consecutive_*`/`_tick_error_*`
   counters), `PauseController` (`pause_worker`/`resume_worker`
   `core.py:986-1042`, `_pause_*` maps), `StallReconciler`
   (`_reconcile_running`/`_reconcile_one`/`_force_eject_zombie`).
4. Split the 600-line `_on_worker_task_done` with **Split Phase**
   ([Fowler](https://refactoring.com/catalog/splitPhase.html)): a *decide* phase
   returns an immutable `TurnOutcome` describing what should happen
   (advance / rewind / retry / done / block), and an *apply* phase performs it.
   The decision becomes unit-testable without spawning a worker, and the mutation
   is the only part touching `DispatchState`.

`Orchestrator` shrinks to a coordinator that wires these collaborators and runs
the tick loop — the same role `build_backend` + `BackendInit` already play for
backends.

**Risk & guardrails.** This is the highest-value and highest-risk initiative.
Mitigation is non-negotiable: **characterization tests first**
([Feathers, *WEWLC* ch.13](https://www.oreilly.com/library/view/working-effectively-with/0131177052/))
pinning current dispatch behavior, then extract one collaborator at a time with
green tests and a commit between each (**Strangler Fig**, never big-bang —
[Fowler](https://martinfowler.com/bliki/StranglerFigApplication.html)). The
existing 72+ dispatch tests in `tests/test_orchestrator_dispatch.py` are the
starting safety net; add characterization coverage for any behavior they don't
pin before moving its fields.

---

## Initiative B — Structured concurrency for task & subprocess supervision

**Goal (plain language):** make it structurally impossible to leak a worker task
or a concurrency slot, by binding every spawned task to a scope that must wait
for it — instead of tracking tasks by hand in dicts and hoping the done-callback
frees the right slot.

**Evidence.** Workers are spawned as bare tasks and tracked manually
(`_running[id].worker_task`, `_tick_task` `core.py:221`), with completion routed
through `add_done_callback` → `_on_worker_task_done`. The memory record shows
this exact pattern produced the done-callback identity race (a key-only lookup
evicting a live re-created entry) and the two-stage force-eject grace. The
subprocess layer, by contrast, is already correct — `_shell.safe_proc_wait`
(`_shell.py:96`) and `terminate_process_tree` (`_shell.py:189`) time-bound every
reap and kill the whole process group, and its docstring documents the very
macOS child-watcher hang the CPython 3.12 notes warn about. **So this initiative
is about the task layer, not the subprocess layer.**

**Target design.**

- Adopt **`asyncio.TaskGroup`** (3.11+; we require 3.12) for worker fan-out. A
  `TaskGroup` holds strong references and will not exit until every child has
  finished, so a worker cannot be orphaned and its slot cannot silently leak —
  the structural cure for the class of bug the gates patch one at a time
  ([CPython docs, Task Groups](https://docs.python.org/3/library/asyncio-task.html#task-groups)).
  Bare `create_task` is the opposite: "the event loop only keeps weak references
  to tasks," so hand-maintained bookkeeping is load-bearing
  ([CPython docs, create_task](https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task)).
  This is the stdlib port of the nursery model
  ([Smith, "go statement considered harmful"](https://vorpus.org/blog/notes-on-structured-concurrency-or-go-statement-considered-harmful/)).
- Where a full TaskGroup is too invasive mid-migration, keep the current dict but
  **encode the identity check in `DispatchState.free_slot` (Initiative A)** and,
  in every done-callback, read the outcome via `task.exception()` with an
  explicit `CancelledError` guard — `result()`/`exception()` *raise* on a
  cancelled or not-done task
  ([CPython docs, Future.result](https://docs.python.org/3/library/asyncio-future.html#asyncio.Future.result)).
- Treat `cancel()` as a **request, not a guarantee**: always `await` the
  cancelled worker (tolerating `CancelledError`) before reclaiming its slot, so
  `try/finally` cleanup actually runs. This is precisely the two-stage grace the
  reconciler already implements (`_reconcile_running` `core.py:4131`,
  `RECONCILE_RECENT_EVENT_GRACE_S` `core.py:4236`) — the doc note explains *why*
  it must stay two-stage
  ([CPython docs, Task.cancel](https://docs.python.org/3/library/asyncio-task.html#asyncio.Task.cancel)).
- Bound every external await with **`asyncio.timeout()`** (the scoped-deadline
  tool, 3.11+) rather than ad-hoc elapsed-time math
  ([CPython docs, timeout](https://docs.python.org/3/library/asyncio-task.html#asyncio.timeout)).
- Keep the standing rule (already followed in `_shell`): **never
  hand-configure child watchers** — 3.12 auto-selects `PidfdChildWatcher` /
  `ThreadedChildWatcher`, and the SIGCHLD-based watchers are the historical
  source of the "child never reaped" hang under a busy TUI loop
  ([CPython 3.12 what's new](https://docs.python.org/3/whatsnew/3.12.html)).

**Risk & guardrails.** TaskGroup changes cancellation propagation semantics
(sibling auto-cancel on first non-`CancelledError` failure). Introduce it behind
the reconciler's existing grace behavior and verify against
`tests/test_orchestrator_reconcile.py` and `test_agent_lifecycle_e2e.py` before
widening. Never swallow `CancelledError` in worker bodies (it subclasses
`BaseException` and corrupts TaskGroup/timeout internals).

---

## Initiative C — Harden the backend contract (build on what already works)

**Goal (plain language):** the backend layer is the best-designed part of
Symphony. Two modest additions stop the 9 CLI adapters from drifting apart or
silently breaking when an upstream tool changes its output.

**Evidence.** `backends/__init__.py` already defines the right shape: a
`BackendInit` construction dataclass (`:80`), an `AgentBackend` `Protocol` with a
documented lifecycle and MUST-emit events (`:94`), a `BaseAgentBackend` shared
base (`:143`), and a `build_backend` factory (`:168`). This *is* ports-and-adapters
— the Protocol is the port, each CLI driver an adapter, the core stays ignorant
of tool specifics ([Cockburn, Hexagonal](https://alistair.cockburn.us/hexagonal-architecture/)).
Two gaps remain:

1. **The shared base carries almost nothing.** `BaseAgentBackend` shares only
   `is_progress_event` (`__init__.py:154`); each per-turn-CLI adapter
   (claude_code 444, gemini 315, opencode 451, pi 506, plain_cli 256 lines)
   re-implements its own spawn/stream/reap loop. They already converge on
   `safe_proc_wait`, but by copy, not by contract.
2. **No shared contract test.** The opencode schema-drift incident (upstream
   `run --format json` moved the response into `type:text` frames; the tell was
   `input_tokens=0`) is exactly the failure a contract test exists to catch.

**Target design.**

- Move the common per-turn-CLI machinery (argv build → `create_subprocess_exec`
  → stream consume → `safe_proc_wait` reap → normalized-event emit) into
  `BaseAgentBackend` as a **Template Method**: the skeleton lives in the base,
  the tool-specific steps (`_build_argv`, `_parse_frame`) are abstract methods an
  adapter *must* override ([GoF Template Method / Adapter](https://refactoring.guru/design-patterns/adapter)).
  Codex stays the deliberate outlier — it is a *persistent* `app-server`
  (JSON-RPC over stdin/stdout, `codex.py:304`/`:380`), a second lifecycle family,
  not a per-turn spawn. Codify the two families explicitly rather than forcing
  one base.
- Add **one abstract contract-test suite** (`tests/test_backend_contract.py`)
  that every backend subclasses and runs identically — Meszaros's *Testcase
  Superclass* ([xunitpatterns](http://xunitpatterns.com/Testcase%20Superclass.html)).
  It asserts the documented lifecycle order and the MUST-emit events
  (`session_started`, `turn_completed`/`turn_failed`/`turn_cancelled`) so no
  adapter can diverge silently.
- Add per-tool **ContractTests** that check *format, not values* against the real
  CLI, run out-of-pipeline ("once a day is plenty"); a red one triggers a
  fixture update ([Fowler, ContractTest](https://martinfowler.com/bliki/ContractTest.html)).
- **Parse at the boundary, fail loud.** Each adapter should parse raw tool
  output once into a precise internal type; a shape it doesn't recognise raises
  *in the adapter that drifted*, not deep in the core
  ([King, "Parse, Don't Validate"](https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/)).
  Be a **Tolerant Reader** for fields we don't consume (survive additive
  upstream changes) but strict for the ones we do
  ([Fowler](https://martinfowler.com/bliki/TolerantReader.html)). Version the
  boundary parser when a tool breaks compatibility, keeping both branches.

**Risk & guardrails.** Low. This is additive; existing `test_backends*.py`
suites remain. Migrate one adapter into the Template-Method base at a time.

---

## Initiative D — Replace monkeypatch indirection with dependency injection

**Goal (plain language):** today, production import order is load-bearing
*because of how tests patch it*. Handing collaborators in through constructors
removes that coupling and the whole class of "reorder an import, break the tests"
hazard.

**Evidence.** `core.py` reaches collaborators through `_pkg.<name>` /
`_tui_pkg.<name>` indirection at **15 call sites** (9 in `core.py`, 4 in
`tui/app.py`, 2 in `orchestrator/__init__.py`). `architecture.md` documents this
as a deliberate contract: names must be bound via the package attribute so
tests' `monkeypatch` reaches the call site, and **"Import `core` last"** is a
stated rule because reordering "breaks monkeypatch contract." The memory record
also notes module-level function stubs leaking across tests. Both are the
textbook symptoms of patching module globals
([*Architecture Patterns with Python*, ch.3](https://www.cosmicpython.com/book/chapter_03_abstractions.html)).

**Target design.**

- Inject collaborators through the constructor (the `backends` layer already
  does this via `BackendInit`). An injected dependency is visible in the
  signature and swapped by handing in a fake — no in-place patch, no import-order
  choreography ([cosmicpython ch.13](https://www.cosmicpython.com/book/chapter_13_dependency_injection.html)).
- Wire real collaborators in a single **composition root** (the `service.py` /
  `cli` startup path) and override with fakes there in tests.
- Depend on **roles/Protocols, not concrete module internals**
  ([Freeman & Pryce, "Mock Roles, Not Objects"](http://www.jmock.org/oopsla2004.pdf);
  ["Don't mock what you don't own"](https://hynek.me/articles/what-to-mock-in-5-mins/)).
- Interim rule while the indirection still exists: every in-test replacement
  goes through `monkeypatch.setattr` (function-scoped, auto-reverted) — **never**
  bare `module.func = stub`, which is not restored and leaks
  ([pytest monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)).
  Patch the **consumer's** reference, with `autospec=True`
  ([CPython, "Where to patch"](https://docs.python.org/3/library/unittest.mock.html#where-to-patch)).

**Risk & guardrails.** The `_pkg` indirection is an intentional, documented test
contract — do **not** rip it out big-bang. Convert one collaborator to
constructor injection, delete its indirection site, update its tests, ship; then
the next. Each conversion *reduces* the load-bearing import surface rather than
trading it for a new one. Update `architecture.md`'s indirection section as sites
disappear.

---

## Initiative E — Add static-analysis gates to CI (highest ROI / lowest risk)

**Goal (plain language):** turn on the type-checker and linter in CI. This is a
day of work that pays for itself immediately — there are real type bugs in
`core.py` right now that a gate would have blocked.

**Evidence.** CI (`.github/workflows/tests.yml`) runs **only `pytest -q`** on
Python 3.12. `pyproject.toml` configures **only** `[tool.pytest.ini_options]` —
no `[tool.ruff]`, `[tool.mypy]`, or `[tool.pyright]`, and no config files on
disk. Meanwhile Pyright already flags **live errors** in the god-class:

- `core.py:113` — `str | None` passed where `str` is required (`__getitem__`).
- `core.py:382` — a `bool` passed to the `now: datetime | None` parameter of
  `clear_issue_flags`.

These ship today with no gate to stop them.

**Target design.**

- Add **ruff** (lint + format) and a **type checker** (pyright or mypy) as
  required CI steps, with config in `pyproject.toml`. Start non-blocking to
  establish a baseline, then ratchet: block on new errors, burn down existing
  ones. (Aligns with the repo's own Python rules: black/ruff/type-checking.)
- Add a **coverage gate** (`pytest --cov=src`) — the suite is large (59 test
  files) but coverage is currently unmeasured in CI.
- Consider a second Python version in the matrix only if we ever relax
  `requires-python` below 3.12 (today, single-version is fine).

**Risk & guardrails.** Minimal; introduce each gate in report-only mode first so
the baseline is visible before it can block a PR. Fix `core.py:113` and `:382`
as the first two ratchet items.

---

## Secondary decomposition targets

Lower priority than the orchestrator, same playbook (characterization tests →
Extract Class/Module → green between steps):

- **`tui/app.py` (1119).** `KanbanTUI.run` is the single highest-fan-in symbol
  in the codebase (148). Split view/render (`widgets.py`, `screens.py` already
  exist) from runtime coordination (`_refresh_runtime`, `_kick_tracker_refresh`,
  `_submit`). Also carries 4 `_tui_pkg` indirection sites (Initiative D).
- **`service.py` (986).** The HTTP/service composition root; a natural home for
  the DI composition root once Initiative D starts.
- **`trackers/file.py` (869).** The file-board is the canonical source of truth;
  isolate the markdown mutation surface (`update_state`, section-strip helpers —
  cf. deferred gate G5) behind a narrow interface shared with jira/linear.
- **`webapi.py` (775) / `workflow/builder.py` (753).** Watch-list; split when
  next touched, not proactively.

---

## Sequencing (do them in this order)

The initiatives are ordered by *risk-adjusted value* and by dependency:

1. **E — CI gates.** Independent, ~1 day, immediate ROI, makes every later step
   safer. Do first.
2. **C — backend contract tests + Template-Method base.** Additive, low risk,
   locks in the layer we'll use as the reference design for A/D.
3. **A step 1 — `DispatchState` owner.** The keystone. Characterization tests
   first, then move the slot maps behind one invariant-enforcing object. Encodes
   the done-callback identity and slot-budget rules structurally.
4. **B — structured concurrency**, built on top of `DispatchState` (the identity
   check lives in `free_slot`; TaskGroup removes the orphan surface).
5. **A steps 2-4 — extract `TokenAccountant` / `HealthReporter` /
   `PauseController` / `StallReconciler`, then Split-Phase the 600-line method.**
6. **D — DI migration**, one collaborator at a time, retiring `_pkg` sites as it
   goes.
7. **Secondary targets**, opportunistically.

Global rules for the whole plan (from the refactoring literature and this repo's
own history):

- **Characterization tests before every extraction** — pin current behavior,
  including bug-for-bug, then change ([Feathers](https://www.oreilly.com/library/view/working-effectively-with/0131177052/)).
- **One small step, green tests, commit, repeat.** Never a mid-refactor red
  `main`/`dev`. Follow the repo invariant: commit on `dev`, merge to `main`.
- **Strangler, not rewrite** — the system stays shippable throughout
  ([Fowler](https://martinfowler.com/bliki/StranglerFigApplication.html)).
- Run the focused suite **and** full `pytest` before publishing runtime changes
  (the pre-push hook already enforces this, ~70s).

---

## Appendix A — Evidence index (`file:line`)

| Claim | Anchor |
|---|---|
| `Orchestrator` single class, 105 methods, 4574 lines | `orchestrator/core.py:170` |
| ~40 mutable fields, incident-scarred comments | `orchestrator/core.py:181-267` |
| 600-line worker-completion method | `orchestrator/core.py:2174`–`2805` |
| slot math must subtract running **and** retry | `orchestrator/core.py:1724` (`_available_slots`) |
| two-stage cancel/force-eject grace | `orchestrator/core.py:4131`, `:4236`; `:3819` |
| correct subprocess reaping (the model to emulate) | `_shell.py:96` (`safe_proc_wait`), `:189` |
| backend port + base + factory (reference design) | `backends/__init__.py:80`, `:94`, `:143`, `:168` |
| codex is a persistent app-server (2nd lifecycle) | `backends/codex.py:304`, `:380` |
| `_pkg`/`_tui_pkg` indirection, 15 sites | `core.py`×9, `tui/app.py`×4, `orchestrator/__init__.py`×2 |
| live type errors, no gate | `core.py:113`, `core.py:382`; `.github/workflows/tests.yml` |
| no lint/type config | `pyproject.toml` (only `[tool.pytest.ini_options]`) |

## Appendix B — Sources

Decomposition — Fowler *Refactoring* 2nd ed.
([catalog](https://refactoring.com/catalog/),
[Extract Class](https://refactoring.com/catalog/extractClass.html),
[Split Phase](https://refactoring.com/catalog/splitPhase.html),
[Encapsulate Record](https://refactoring.com/catalog/encapsulateRecord.html),
[Tell Don't Ask](https://martinfowler.com/bliki/TellDontAsk.html),
[Strangler Fig](https://martinfowler.com/bliki/StranglerFigApplication.html));
[Feathers *WEWLC*](https://www.oreilly.com/library/view/working-effectively-with/0131177052/);
[R. C. Martin, SRP](https://blog.cleancoder.com/uncle-bob/2014/05/08/SingleReponsibilityPrinciple.html);
[Thompson, Single Writer](https://mechanical-sympathy.blogspot.com/2011/09/single-writer-principle.html).

Concurrency — CPython docs
([Task Groups](https://docs.python.org/3/library/asyncio-task.html#task-groups),
[create_task weak-ref](https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task),
[Task.cancel](https://docs.python.org/3/library/asyncio-task.html#asyncio.Task.cancel),
[timeout](https://docs.python.org/3/library/asyncio-task.html#asyncio.timeout),
[Future.result](https://docs.python.org/3/library/asyncio-future.html#asyncio.Future.result),
[subprocess](https://docs.python.org/3/library/asyncio-subprocess.html),
[3.12 what's new](https://docs.python.org/3/whatsnew/3.12.html));
[Smith, structured concurrency](https://vorpus.org/blog/notes-on-structured-concurrency-or-go-statement-considered-harmful/).

Adapters & contracts —
[Cockburn, Hexagonal](https://alistair.cockburn.us/hexagonal-architecture/);
[GoF Adapter/Template Method](https://refactoring.guru/design-patterns/adapter);
[Meszaros, Testcase Superclass](http://xunitpatterns.com/Testcase%20Superclass.html);
Fowler [ContractTest](https://martinfowler.com/bliki/ContractTest.html) /
[Consumer-Driven Contracts](https://martinfowler.com/articles/consumerDrivenContracts.html) /
[Tolerant Reader](https://martinfowler.com/bliki/TolerantReader.html);
[King, Parse Don't Validate](https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/).

DI & testing —
[*Architecture Patterns with Python* ch.3](https://www.cosmicpython.com/book/chapter_03_abstractions.html) /
[ch.13](https://www.cosmicpython.com/book/chapter_13_dependency_injection.html);
[CPython "Where to patch"](https://docs.python.org/3/library/unittest.mock.html#where-to-patch);
[pytest monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html);
[Freeman & Pryce, Mock Roles Not Objects](http://www.jmock.org/oopsla2004.pdf);
[Schlawack, What to mock](https://hynek.me/articles/what-to-mock-in-5-mins/).
