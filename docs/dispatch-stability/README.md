# Dispatch stability — G1-G5

This folder is the architecture-decision record for the five
dispatch-stability surfaces shipped on `main` 2026-05-20. Each file
explains one fix at two levels:

- **Beginner**: what was the symptom on the board, what changed, and
  how to recognize the fix is working from the log alone.
- **Expert**: the code path, the invariant, the test that pins it, and
  the failure mode it replaces.

If you're new to Symphony, read this README + [glossary](#glossary) first,
then pick whichever G-page matches the symptom you're seeing.

## What is "dispatch stability"?

Symphony's orchestrator runs a periodic tick that:

1. Reconciles in-flight workers (`_reconcile_running`)
2. Fetches candidate tickets from the tracker
3. Sorts them and dispatches as many as the slot budget allows
4. Sweeps archives, notifies observers, sleeps until the next tick

Every G-fix here is about making sure tickets that *should* be dispatched
actually get dispatched — and tickets that *shouldn't* keep their slot
free their slot promptly so the queue doesn't deadlock.

| Page | Symptom on the board | Root cause |
|------|----------------------|------------|
| [G1](./G1-stale-claimed-prune.md) | Ticket stuck behind a conflict for 45 min after the conflict cleared | `_claimed` set had no symmetric clear path |
| [G2](./G2-empty-response-loop.md) | Agent silently empty-loops; only `max_total_turns` ever escalates | No per-turn empty-message counter |
| [G3](./G3-wait-age-bump.md) | Long-starved tickets keep losing dispatch to new numbered tickets | `_sort_for_dispatch_fifo` was pure registration order |
| [G4](./G4-tui-file-logging.md) | `log/symphony.log` is empty in TUI sessions but full in headless | TUI never attached the file sink |
| [G5](./G5-strip-on-restore.md) | Board still shows `## Conflict` / `## Budget Exceeded` after restore | Tracker write didn't strip orchestrator-authored sections |

## How to verify the loop in 30 seconds

```bash
.venv/bin/symphony WORKFLOW.md --log-level INFO 2>&1 | tee /tmp/sym.log
# Look for these markers — any of them appearing means that G-fix wiring
# is alive:
grep -E "stale_claimed_pruned|empty_response_loop|stage_advance" /tmp/sym.log
```

`stale_claimed_pruned` should fire whenever a conflict-blocked or
budget-blocked ticket leaves `_running`; if it never appears under load,
G1 is silently broken.

## Glossary

- **`_claimed`** — in-process set of ticket ids the orchestrator has
  decided to skip on the current tick (conflict, budget, hit_max_turns).
- **`_running`** — in-process dict of `RunningEntry` for each worker.
- **`_retry`** — pending retry timers; an id here counts against the
  concurrency budget even before its worker resumes.
- **`EVENT_TURN_COMPLETED`** — backend event fired when one agent turn
  ends; the place every per-turn budget check lives.
- **`active_states`** — the set of tracker states that mean "this
  ticket is in flight" (typically Todo, Explore, Plan, In Progress, …).
  Transitions *into* one of these are what trigger G5's strip.

## Cross-references

- [`docs/improvements/dispatch-stability-2026-05-20.md`](../improvements/dispatch-stability-2026-05-20.md)
  — the original punch list with per-item plan / touch points / risk
- [`docs/architecture.md`](../architecture.md) — `src/symphony/` map
- [`tests/test_orchestrator_dispatch.py`](../../tests/test_orchestrator_dispatch.py)
  `test_g_dispatch_stability_full_cycle_5_ticks` — composite regression
  test that exercises G1+G2+G3+G5 in one orchestrator instance
