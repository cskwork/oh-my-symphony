# GOAL - AF-03 through AF-16 reliability

Single source of done. Only the verifier ticks a box; unmet requirements stay visible.

## Original Request

> supergoal resolove all of the AF-03 and beyond ticket issues docs/improvements/tickets/2026-07-…/

## Spec

Resolve every ticket from `docs/improvements/tickets/2026-07-09/AF-03-*.md` through
`AF-16-*.md` on a branch based on `dev`, without widening into AF-01/AF-02 or the tickets'
non-goals. Each reachable defect gets a regression test observed failing before its minimal fix.
AF-14 is research-gated: close it without production code if the pinned Codex protocol proves a
`last`-only token-usage notification unreachable. Preserve default workflows, old run-registry rows,
idle state patches, genuine stall detection, actual empty-loop detection, and all existing tests.

Decisions grounded in existing repository contracts:

- AF-11 keeps `continuous_improvement.max_turns` as the documented lifetime cap until manual reset;
  add a one-time warning when it latches. Do not invent interval-window reset semantics.
- AF-13 derives rewinds from configured `tracker.active_states` order, preserving the default
  Verify/Learn-to-In-Progress behavior and supporting custom/non-English state names.
- AF-14 uses Codex 0.144.0 generated protocol schema plus the checked-in 0.130 schema as research
  evidence; both require `tokenUsage.last` and `tokenUsage.total`.

## Success Criteria

Each item is falsifiable and names its verification method.

- [ ] AF-03 resumed workers receive a fresh stall window and later genuine stalls still cancel - verify: focused resume/reconcile tests in `tests/test_orchestrator_dispatch.py`.
- [ ] AF-04 running-ticket state PATCH returns 409 without mutation while non-state and idle state PATCHes retain current behavior - verify: focused `tests/test_webapi.py` cases.
- [ ] AF-05 productive plain/Gemini/Claude completions expose non-empty previews, silent exit-0 turns fail, and true three-turn empty loops still trip G2 - verify: backend contract and dispatch tests.
- [ ] AF-06 scans ignore legacy board-root `.tmp-*.md`, atomic writes cannot surface matching temps, and startup removes stale orphans without affecting identifier allocation - verify: focused file-tracker tests.
- [ ] AF-07 cancelled paused zombies still eject; Part A isolates per-entry errors and schedules retry despite process/lease cleanup failure - verify: focused reconcile tests.
- [ ] AF-08 `stop()` bounds cancellation-resistant worker drain, force-ejects remaining processes, and preserves prompt-cancel ordering - verify: focused orchestrator stop tests with a test timeout.
- [ ] AF-09 corrupt Codex stdout closes the backend, fails later turns quickly, reaps the subprocess, and continues tolerating sparse malformed lines - verify: focused Codex backend tests.
- [ ] AF-10 dead-owner lease reclaim kills any recorded live backend process group before the row becomes redispatchable, while old rows without a pid remain compatible - verify: run-registry recovery tests.
- [ ] AF-11 lifetime cap warns once, lease contention postpones one interval, and `require_idle_board` prevents CI/worker overlap including terminal-persist work - verify: continuous-improvement scheduler tests.
- [ ] AF-12 parse failures warn, running ids missing from refresh enter a visible degraded state, duplicate ids collapse/reject, and delete is serialized with mutation - verify: tracker, reconcile, and web API tests.
- [ ] AF-13 custom and Korean backward active-state transitions consume rewind budget and default pipeline behavior is unchanged - verify: phase-transition and dispatch tests.
- [ ] AF-14 research note records current and historical Codex token-usage shapes and closes the unreachable last-only branch without speculative production code - verify: generated-schema command and diff review.
- [ ] AF-15 completed/debug dispatch state no longer grows without bound and `stop()` clears retained diagnostic state - verify: `tests/test_dispatch_state.py` plus stop assertions.
- [ ] AF-16 first and continuation prompts use one lifetime turn numerator/denominator basis and prompt anchor semantics remain deliberate - verify: prompt pipeline tests and anchor diff review.
- [ ] All ticket non-goals remain untouched and every changed hunk maps to AF-03..AF-16 - verify: adversarial diff review and `Backward-trace: clean`.
- [ ] Repository verification is green - verify: `python -m pytest -q`, `ruff check src tests`, and `pyright src`.

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | no-op | resolved | `.domain-agent/` is absent | Use an ephemeral Domain Brief in `PLAN.md`; do not add unrelated local scaffolding | diff review |
| d2 | no-op | resolved | AF-11 semantics were marked undecided in the audit ticket | Keep the repository's documented lifetime cap and add latch observability | scheduler tests |
| d3 | auto-fix | resolved | AF-13 custom rewind rule was marked undecided | Use configured active-state index order, the ticket's recommended default | custom-state tests |
| d4 | no-op | resolved | AF-14 may be unreachable | Current and checked-in generated schemas require both `last` and `total`; close with research evidence | schema command |
