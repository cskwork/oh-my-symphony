# Symphony surgical-decomposition refactor — continuation plan

Working branch: `refactor/surgical-decomposition`
Rollback tag: `baseline/pre-refactor-2026-05-19`
Base: `dev@54050c4` (jira tracker adapter already shipped)
ultragoal ledger: `.omc/ultragoal/goals.json` (gitignored, in-repo only)

## Done (5/6 ultragoal stories) — all committed on the branch

| commit | what |
|--------|------|
| `89bfbf8` | `.gitignore` updated for `.omc/` + auto-generated `WORKFLOW-PROGRESS.md` |
| `ec5881a` | `workflow.py` (1204 LOC) → `workflow/` package (8 submodules) |
| `d5c4477` | `tui.py` (1626 LOC) → `tui/` package (6 submodules) |
| `3ec7aed` | `orchestrator.py` (3324 LOC) → `orchestrator/` package (6 submodules) |
| `39b4a59` | version lockstep: pyproject `0.6.4 → 0.6.5` to match `__init__` |

Every commit kept the test suite green: **pytest 565 passed, 6 skipped**, identical to baseline.

### Module map after the split

```
src/symphony/
├── workflow/         # 8 files, was 1204-line workflow.py
│   ├── constants.py   65 — defaults, env keys, _VAR_PATTERN
│   ├── parser.py      92 — WorkflowDefinition + parse/load/resolve
│   ├── coercion.py   125 — $VAR / ~ expand, _as_*/normalize helpers
│   ├── config.py     359 — all @dataclass(frozen=True) config types
│   ├── builder.py    599 — build_service_config + strict validators
│   ├── preflight.py   75 — validate_for_dispatch
│   ├── state.py       47 — WorkflowState (§6.2 hot reload)
│   └── __init__.py   141 — re-exports
│
├── tui/              # 6 files, was 1626-line tui.py
│   ├── constants.py   53 — STATE/AGENT colors, density flags, lane widths
│   ├── helpers.py    197 — _CardStatus + pure runtime helpers
│   ├── screens.py     81 — _RefreshNow + TicketDetailScreen modal
│   ├── widgets.py    554 — IssueCard / Lane / StatsBar / DetailPane / FilterBar
│   ├── app.py        818 — KanbanApp + KanbanTUI (single cohesive class)
│   └── __init__.py    91 — re-exports
│
└── orchestrator/     # 6 files, was 3324-line orchestrator.py
    ├── constants.py   77 — AUTO_TRIAGE, retry timing, STALL_FORCE_EJECT_GRACE_S
    ├── parsing.py    136 — Touched Files / Findings markdown parsers
    ├── entries.py    125 — RunningEntry, RetryEntry, _CodexTotals, _IssueDebug
    ├── helpers.py    221 — pure module-level helpers
    ├── core.py      2873 — the Orchestrator class itself
    └── __init__.py    88 — re-exports + monkeypatch-target bindings
```

### Monkeypatch indirection patterns preserved

Tests use `monkeypatch.setattr("symphony.X.Y", stub)` against several
function names. The split keeps those patches live:

- `symphony.tui._fetch_candidates` / `_fetch_terminals` → `tui/app.py`
  reaches them via `_tui_pkg.<name>` at call time.
- `symphony.orchestrator.build_backend` / `commit_workspace_on_done` /
  `auto_merge_on_done_best_effort` → `orchestrator/core.py` reaches
  them via `_pkg.<name>` where `_pkg = sys.modules[__package__]`.

### Memory-tracked invariants preserved bit-for-bit

- `_on_worker_task_done` task-identity check (orchestrator/core.py)
- `_available_slots` subtracts `_running` AND `_retry`
- `_reconcile_running` 30s grace between cancel and force-eject
- `last_progress_timestamp` filtered to `type=="assistant"`
- Module-level helpers re-exported through `__init__.py` so test stubs
  still target the live names

## Remaining (G006 — final story)

### Required
- [ ] Write `docs/architecture.md` reflecting the new module map and the
      monkeypatch indirection patterns documented above.
- [ ] Launcher smoke per the `Verify in the run path` memory: run
      `.tui-launcher.command` against a real WORKFLOW (olive-clone board
      at `/Users/danny/Documents/PARA/Resource/olive-clone` is the
      canonical test target, file backend, port 9991).
- [ ] Full pytest one more time after the docs land.

### Deferred quality gates (run before any merge to dev/main)
- [ ] `ai-slop-cleaner` pass clean
- [ ] `/verify` clean against launched TUI (or http://127.0.0.1:9991 if
      using the olive-clone board)
- [ ] `/review` returns APPROVE
- [ ] Open PR `refactor/surgical-decomposition → dev`. Memory rule:
      commit lands on dev first, then merge dev → main.

## How to resume

1. `git checkout refactor/surgical-decomposition` (it's pushed locally
   only — no remote yet).
2. `omc ultragoal status` to see the ledger; `omc ultragoal complete-goals`
   to fetch the next story handoff (G006).
3. Tackle the checklist above; after each step,
   `omc ultragoal checkpoint --goal-id G006-architecture-doc-final-gate ...`
   with quality-gate JSON once the ai-slop-cleaner / /verify / /review
   trio is clean.
4. Squash-merge into `dev` per repo convention (`feedback_always_commit_dev_then_merge_main`).

## Rollback escape hatch

`git reset --hard baseline/pre-refactor-2026-05-19` returns the working
tree to the exact dev@54050c4 state. The five commits on this branch are
each self-contained and reversible individually with `git revert`.
