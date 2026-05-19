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
├── agent.py             # AgentKind enum + per-agent capabilities
├── errors.py            # typed exceptions
├── i18n.py              # message catalog (Korean + English)
├── issue.py             # Issue dataclass (canonical board card)
├── logging.py           # structured logger setup
├── mock_codex.py        # offline codex stub used in tests + demos
├── progress_md.py       # WORKFLOW-PROGRESS.md renderer
├── prompt.py            # symphony-prompts loader / variable expansion
├── server.py            # HTTP board / dashboard JSON endpoints
├── service.py           # top-level `symphony run` service wiring
├── workspace.py         # git-worktree lifecycle + commit_workspace_on_done
│
├── backends/            # agent CLI adapters (claude, codex, gemini, pi)
├── cli/                 # `symphony` argparse surface (board, doctor, main)
├── notifications/       # opt-in Slack dispatcher
├── orchestrator/        # state machine — see "Orchestrator package"
├── trackers/            # board adapters (file, jira, linear)
├── tui/                 # Textual Kanban UI — see "TUI package"
├── utils/               # archive, auto-merge, keep-awake, wiki-sweep
└── workflow/            # WORKFLOW.md loader + typed config — see below
```

Three packages were carved out of single-file modules on
`refactor/surgical-decomposition`. Each split kept the existing public
dotted paths intact (the same `from symphony.X import Y` continues to
work), and `pytest` stayed green through the refactor — the suite
moved from 565 passed / 6 skipped to 566 passed / 5 skipped (one test
came off the skip list; no regressions).

## Workflow package (`symphony.workflow`)

Replaces the former 1,204-line `workflow.py`. Modules ordered the way a
reader scans the public surface — parser → coercion → config →
builder → preflight → state:

| file              | LOC | role                                                                  |
|-------------------|----:|-----------------------------------------------------------------------|
| `constants.py`    |  65 | defaults, env keys, `_VAR_PATTERN`                                    |
| `parser.py`       |  92 | `WorkflowDefinition` + `parse_*` / `load_*` / `resolve_*`             |
| `coercion.py`     | 125 | `$VAR` / `~` expansion, `_as_*` / `normalize_*` helpers               |
| `config.py`       | 359 | every `@dataclass(frozen=True)` config type                           |
| `builder.py`      | 599 | `build_service_config` + strict validators                            |
| `preflight.py`    |  75 | `validate_for_dispatch`                                               |
| `state.py`        |  47 | `WorkflowState` (SPEC §6.2 hot reload)                                |
| `__init__.py`     | 141 | re-exports the full surface from `symphony.workflow.X`                |

The package `__init__.py` re-exports every name that callers and tests
import from `symphony.workflow`. Adding a new public symbol means
adding it to both its leaf module and the re-export list.

## TUI package (`symphony.tui`)

Replaces the former 1,626-line `tui.py`. A Textual app with focusable
lanes, first-class cards, and modal detail screens; CLI continues to do
`await KanbanTUI(orchestrator, workflow_state).run()`.

| file            | LOC | role                                                                   |
|-----------------|----:|------------------------------------------------------------------------|
| `constants.py`  |  53 | `STATE_COLOR` / `AGENT_COLOR`, density flags, lane widths              |
| `helpers.py`    | 197 | `_CardStatus` + pure runtime helpers (`_silent_seconds`, `_parse_iso`) |
| `screens.py`    |  81 | `_RefreshNow` event + `TicketDetailScreen` modal                       |
| `widgets.py`    | 554 | `IssueCard` / `Lane` / `StatsBar` / `DetailPane` / `FilterBar`         |
| `app.py`        | 818 | `KanbanApp` + `KanbanTUI` (single cohesive class kept intact)          |
| `__init__.py`   |  91 | re-exports the public surface                                          |

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

Replaces the former 3,324-line `orchestrator.py`. The state machine
itself stayed a single class (`Orchestrator`); only the surrounding
helpers, parsers, dataclasses, and constants were extracted.

| file             | LOC  | role                                                                               |
|------------------|-----:|------------------------------------------------------------------------------------|
| `constants.py`   |   77 | `AUTO_TRIAGE_*`, retry timing, `STALL_FORCE_EJECT_GRACE_S`                         |
| `parsing.py`     |  136 | `_parse_touched_files`, `_parse_findings_rows` (markdown agent output)             |
| `entries.py`     |  125 | `RunningEntry`, `RetryEntry`, `_CodexTotals`, `_IssueDebug`                        |
| `helpers.py`     |  221 | pure module-level helpers (`_sort_for_dispatch_fifo`, `_is_rewind_transition`, …)  |
| `core.py`        | 2873 | the `Orchestrator` class                                                           |
| `__init__.py`    |   88 | re-exports + monkeypatch-target bindings                                           |

### Monkeypatch indirection — `_pkg`

Tests stub three names on the package itself:

- `symphony.orchestrator.build_backend`
- `symphony.orchestrator.commit_workspace_on_done`
- `symphony.orchestrator.auto_merge_on_done_best_effort`

`core.py` reaches them through the parent package module:

```python
import sys
_pkg = sys.modules[__package__]
...
client = _pkg.build_backend(...)
await _pkg.commit_workspace_on_done(...)
await _pkg.auto_merge_on_done_best_effort(...)
```

This requires a strict import order in `__init__.py`:

1. Bind `build_backend`, `commit_workspace_on_done`, and
   `auto_merge_on_done_best_effort` on the package module **before**
   `from .core import Orchestrator`. `core` reads them through
   `_pkg.<name>` at call time, so the package attribute must already
   exist when `core` is imported.
2. Re-export the constants, parsing helpers, dataclasses, and pure
   helpers next — `core` itself pulls them via `from .helpers import …`
   and `from .entries import …`.
3. Import `core` last; `Orchestrator` is the lone public symbol from it.

Reordering these steps (for example, importing `core` before the three
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

## Adding a new public symbol

1. Place it in the most cohesive leaf module (not in `__init__.py`).
2. Add it to the package `__init__.py` re-export list **and** `__all__`.
3. If a test stubs it via `monkeypatch.setattr` against the package
   dotted path, follow the `_pkg.<name>` / `_tui_pkg.<name>` indirection
   pattern from the existing modules. Do not bind the name at the call
   site's module level — patches will not reach you.
4. Run the full pytest suite (`pytest -q`); the current baseline is
   566 passed, 5 skipped.

## Rollback

The five commits that produced this layout are each self-contained and
revertible individually:

```
89bfbf8  .gitignore for .omc/ and WORKFLOW-PROGRESS.md
ec5881a  refactor(workflow): split into 8 submodules
d5c4477  refactor(tui): split into 6 submodules
3ec7aed  refactor(orchestrator): split into a package
39b4a59  chore(release): lockstep pyproject at 0.6.5
```

`git reset --hard baseline/pre-refactor-2026-05-19` returns the tree to
the dev@54050c4 starting point.
