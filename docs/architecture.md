# Symphony architecture

This document is the resident map of `src/symphony/`. It captures the
public package surface, the responsibility of each submodule, and the
import-order / monkeypatch indirection rules that the test suite relies
on. Update it whenever a public surface or one of those indirection
rules changes — drift here costs other engineers (and your future self)
hours of greps.

## Top-level layout

```
src/symphony/
├── __init__.py          # package docstring + __version__ only
├── __main__.py          # `python -m symphony` entry point
├── _shell.py            # safe subprocess wrappers
├── agent.py             # backwards-compat shim for symphony.backends
├── errors.py            # typed exceptions
├── i18n.py              # message catalog (Korean + English)
├── issue.py             # Issue dataclass (canonical board card)
├── logging.py           # structured logger setup
├── mock_codex.py        # offline codex stub used in tests + demos
├── progress_md.py       # WORKFLOW-PROGRESS.md renderer
├── prompt.py            # symphony-prompts loader / variable expansion
├── server.py            # aiohttp app, state / health / refresh routes
├── service.py           # managed `symphony service` lifecycle
├── skills.py            # SKILL.md discovery + prompt injection
├── stats.py             # append-only run stats store + aggregation
├── webapi.py            # web board REST routes + static SPA serving
├── workspace.py         # git-worktree lifecycle + commit_workspace_on_done
│
├── backends/            # agent CLI adapters (codex, claude, gemini, opencode, pi)
├── cli/                 # `symphony` argparse surface (board, doctor, main)
├── notifications/       # opt-in Slack dispatcher
├── orchestrator/        # state machine + run registry — see below
├── trackers/            # board adapters (file, jira, linear)
├── tui/                 # Textual Kanban UI — see "TUI package"
├── utils/               # archive, auto-merge, keep-awake, wiki-sweep
├── web/static/           # built-in browser board assets
└── workflow/            # WORKFLOW.md loader + typed config — see below
```

The old single-file `workflow.py`, `tui.py`, and `orchestrator.py`
surfaces are now packages. Public imports are preserved through package
`__init__.py` re-exports, so existing `from symphony.X import Y` call
sites keep working. Later reliability work added `orchestrator/run_registry.py`
for SQLite run leases / issue flags, and the web revamp added `webapi.py`
plus packaged static assets.

## Workflow package (`symphony.workflow`)

Replaces the former single-file `workflow.py`. Modules are ordered the way a
reader scans the public surface: parser, coercion, config, builder, preflight,
and state.

| file | role |
| --- | --- |
| `constants.py` | defaults, env keys, `_VAR_PATTERN`, supported agent constants |
| `parser.py` | `WorkflowDefinition` + `parse_*` / `load_*` / `resolve_*` |
| `coercion.py` | `$VAR` / `~` expansion, `_as_*` / `normalize_*` helpers |
| `config.py` | every frozen config dataclass, including backend-specific blocks |
| `builder.py` | `build_service_config` + strict validators |
| `mutate.py` | comment-preserving `WORKFLOW.md` edits for web column / prompt / branch policy UI |
| `preflight.py` | `validate_for_dispatch` |
| `state.py` | `WorkflowState` hot reload |
| `__init__.py` | re-exports the full surface from `symphony.workflow.*` |

The package `__init__.py` re-exports every name that callers and tests
import from `symphony.workflow`. Adding a new public symbol means
adding it to both its leaf module and the re-export list.

## TUI package (`symphony.tui`)

Replaces the former single-file `tui.py`. A Textual app with focusable
lanes, first-class cards, and modal detail screens; CLI continues to do
`await KanbanTUI(orchestrator, workflow_state).run()`.

| file | role |
| --- | --- |
| `constants.py` | `STATE_COLOR` / `AGENT_COLOR`, density flags, lane widths |
| `helpers.py` | `_CardStatus` + pure runtime helpers (`_silent_seconds`, `_parse_iso`) |
| `screens.py` | ticket detail plus create/edit modal screens |
| `widgets.py` | `IssueCard` / `Lane` / `StatsBar` / `DetailPane` / `FilterBar` |
| `app.py` | `KanbanApp` + `KanbanTUI`, keyboard write actions, API/TUI coordination |
| `__init__.py` | re-exports the public surface |

### Monkeypatch indirection — `_tui_pkg`

Tests stub `symphony.tui._fetch_candidates` and
`symphony.tui._fetch_terminals` with `monkeypatch.setattr`. The actual
call site is `app.py`, which would normally bind these names at import
time — that would render the patches invisible. To keep the patches
live, `app.py` does:

```python
import symphony.tui as _tui_pkg  # bound to the package module
...
candidates = await asyncio.to_thread(_tui_pkg._fetch_candidates, cfg)
terminals  = await asyncio.to_thread(_tui_pkg._fetch_terminals, cfg)
```

`_tui_pkg.<name>` resolves through the package namespace at call time,
so a `monkeypatch.setattr("symphony.tui._fetch_candidates", stub)` in a
test reaches the actual runtime call. If a future change inlines those
names back into `app.py`, the relevant tests will silently lose their
stub coverage — do not inline.

## Orchestrator package (`symphony.orchestrator`)

Replaces the former single-file `orchestrator.py`. The state machine
itself stayed a single class (`Orchestrator`), while helpers, parsers,
dataclasses, contract checks, and the SQLite run registry live in leaf
modules.

| file | role |
| --- | --- |
| `constants.py` | `AUTO_TRIAGE_*`, retry timing, stall / tick supervision constants |
| `parsing.py` | `_parse_touched_files`, `_parse_findings_rows` from markdown agent output |
| `entries.py` | `RunningEntry`, `RetryEntry`, `_CodexTotals`, `_IssueDebug` |
| `helpers.py` | pure helpers (`_sort_for_dispatch_fifo`, `_is_rewind_transition`, and related utilities) |
| `contracts.py` | stage-contract evaluation for pipeline headings and evidence gates |
| `run_registry.py` | SQLite WAL run leases, dead-owner reclaim, persisted retry / pause / budget flags |
| `core.py` | the `Orchestrator` class, tick loop, dispatch, health, pause/resume, Learn skip |
| `__init__.py` | re-exports + monkeypatch-target bindings |

### Monkeypatch indirection — `_pkg`

Tests stub two names on the package itself:

- `symphony.orchestrator.commit_workspace_on_done`
- `symphony.orchestrator.auto_merge_on_done_best_effort`

`core.py` reaches them through the parent package module:

```python
import sys
_pkg = sys.modules[__package__]
...
await _pkg.commit_workspace_on_done(...)
await _pkg.auto_merge_on_done_best_effort(...)
```

`build_backend` left this contract (architecture-improvement plan,
initiative D): it is constructor-injectable on `Orchestrator`
(`Orchestrator(state, build_backend=factory)`) and otherwise late-bound
from `core`'s own module global, so tests patch
`symphony.orchestrator.core.build_backend` — the consumer's reference.
The package still re-exports `build_backend` for the public API surface.

This requires a strict import order in `__init__.py`:

1. Bind `commit_workspace_on_done` and
   `auto_merge_on_done_best_effort` on the package module **before**
   `from .core import Orchestrator`. `core` reads them through
   `_pkg.<name>` at call time, so the package attribute must already
   exist when `core` is imported.
2. Re-export the constants, parsing helpers, dataclasses, and pure
   helpers next — `core` itself pulls them via `from .helpers import …`
   and `from .entries import …`.
3. Import `core` last; `Orchestrator` is the lone public symbol from it.

Reordering these steps (for example, importing `core` before the
collaborators are bound on the package) breaks the monkeypatch contract
and the runtime path simultaneously.

## Invariants preserved bit-for-bit through the split

These were already enforced by tests, but they are listed here because
they are easy to regress and hard to spot in a diff:

- `Orchestrator._on_worker_task_done` keeps the **task-identity check**
  before mutating `_running` / `_retry` — a stale task callback must
  not pop a newer task off the queue.
- `Orchestrator._available_slots` subtracts **both `_running` and
  `_retry`** from the cap. Dropping `_retry` lets retries race the
  primary attempt.
- `Orchestrator._reconcile_running` waits the full
  **`STALL_FORCE_EJECT_GRACE_S` (30s)** between cancel and force-eject.
- `last_progress_timestamp` is filtered to **`type == "assistant"`**
  events — tool-use events are not progress.
- Module-level helpers are re-exported through every package
  `__init__.py` so that `monkeypatch.setattr("symphony.<pkg>.helper", …)`
  in tests reaches the live name.
- `RunRegistry.acquire_run` is the durable single-issue claim when a registry
  is configured; dispatch must not start a worker without first owning the
  lease.
- File tracker writes go through the lock-and-compare-and-swap mutation path;
  new helpers should build on that path instead of rewriting ticket files
  directly.
- `Orchestrator.health()` stays cheap and counter-based so `/api/v1/health`
  remains available even when tracker refresh or run-registry cleanup is
  degraded.

## Adding a new public symbol

1. Place it in the most cohesive leaf module (not in `__init__.py`).
2. Add it to the package `__init__.py` re-export list **and** `__all__`.
3. If a test stubs it via `monkeypatch.setattr` against the package
   dotted path, follow the `_pkg.<name>` / `_tui_pkg.<name>` indirection
   pattern from the existing modules. Do not bind the name at the call
   site's module level — patches will not reach you.
4. Run the relevant focused tests plus the full pytest suite before publishing
   runtime changes. For documentation-only edits, run stale-string/static
   checks and any affected contract tests.

## Historical Split Commits

The original package split came from these self-contained commits. Use them as
history when auditing the split; prefer scoped reverts or follow-up fixes over
resetting a working branch.

```
89bfbf8  .gitignore for .omc/ and WORKFLOW-PROGRESS.md
ec5881a  refactor(workflow): split into 8 submodules
d5c4477  refactor(tui): split into 6 submodules
3ec7aed  refactor(orchestrator): split into a package
39b4a59  chore(release): lockstep pyproject at 0.6.5
```
