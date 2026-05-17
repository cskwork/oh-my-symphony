# Orchestrator phase-transition backend rebuild

## Getting the Feel (For Beginners)

### Why phase-transition exists

Every ticket walks through stages like `Todo → Explore → Plan → In Progress → Review → QA → Learn → Done`. The orchestrator runs one agent backend (`claude`, `codex`, `gemini`, `pi`) per ticket, but it **does not reuse the same backend across stages** — each new stage gets a fresh context so the agent isn't dragged down by stale history. The teardown-and-rebuild step in the middle is what we call a phase transition.

The simplest way for a beginner to picture it:

`Stage changes → stop old backend → build new backend → start it → if anything goes wrong, stop the new one too`

There are five terms you need to internalise at this stage.

| Term | Plain-English meaning |
|---|---|
| Backend | The actual agent process (claude/codex CLI) the orchestrator talks to |
| Phase transition | The moment a ticket's stage changes and the agent needs a fresh context |
| start_session | The "open a chat" step inside a backend — sends the first prompt |
| Cleanup leak | A half-built backend that nobody told to stop, eating slots and CPU |
| BaseException | The Python parent of every exception, including cancellation |

To make it concrete:

A ticket is being worked on in `In Progress`. The agent finishes its turn and the orchestrator sees the markdown now says `Review`. It tells the old backend "you're done", builds a brand-new one, and tries to start a session for the Review prompt. If the new backend's `start_session()` raises an error (network blip, auth glitch), the orchestrator must immediately stop that brand-new backend — otherwise it sits there holding a subprocess and a slot, and the ticket gets re-queued behind a zombie.

The decision rule that matters at this stage:

**Just remember this: every step from "old backend stops" to "new session open" lives inside one try/except, and the except clause stops the new backend before re-raising — even when the failure is cancellation.**

When you're ready to go deeper, read [agent-observability](agent-observability.md) for the log lines the cleanup emits and [production-pipeline](production-pipeline.md) for how stage transitions are detected upstream.

## Technical Reference

**Summary:** `Orchestrator._rebuild_backend_for_phase` (`src/symphony/orchestrator.py:2113-2197`) is the single chokepoint that disposes the previous backend and constructs the next one when an issue's state changes mid-run. The PR #21 fix (`dfdbdc8`) wrapped the entire `start → initialize → build_first_turn_prompt → start_session` sequence in a `try/except BaseException` so a failure in any step calls `new_client.stop()` before re-raising. Using `BaseException` (not `Exception`) is intentional: `asyncio.CancelledError` and `KeyboardInterrupt` would otherwise bypass cleanup and leak the freshly-built subprocess.

**Invariants & Constraints:**
- The pre-try region (`old_client.stop()`, `build_backend`, `_apply_dispatch_env`) runs without cleanup protection. Only `build_backend` could allocate the new subprocess, and any raise from `_apply_dispatch_env` happens after construction — that residual risk is acknowledged out-of-scope for SMA-24 because `_apply_dispatch_env` only mutates `os.environ` keys.
- `build_first_turn_prompt` receives every per-state knob the agent loop tracks: `prompt_template_for_state`, `token_ema`, `token_budget`, `max_turns`, `max_attempts`, `auto_merge_on_done`, `rewind_scope` (parsed from `issue.description` only on rewinds), `is_rewind`, `language`. Adding a new per-state argument means extending this call site, not a sibling.
- The nested `try` inside the `except` swallows stop failures with `log.warning("phase_transition_new_stop_failed", ...)` — losing the new client is acceptable; losing the original error context is not.
- The companion `phase_transition_old_stop_failed` warning fires when the *old* client refuses to stop (pre-try region). It is best-effort by design: the new client replaces the reference, so anything stuck in the old backend belongs to the listener-side reaper or the OS.
- Tests must use the `_install_fake_backend` factory pattern at `tests/test_orchestrator_phase_transition.py:218-234`. Every per-instance backend method patched via `monkeypatch.setattr(_FakeBackend, ...)` must append a `(name, payload)` tuple to `self_inst.calls`. The first entry is always `("factory", {"agent_kind": ...})` — appended in the factory function, not by the method patches. Cherry-picks that drop this entry break call-sequence assertions silently; commit `3a8bb7e` was the local follow-up that restored it.

**Files of interest:**
- `src/symphony/orchestrator.py:2113-2197` — `_rebuild_backend_for_phase` (the try/except envelope).
- `src/symphony/orchestrator.py:1087-1092` — `_on_tick` reload branch; calls `update_hooks(..., workflow_dir=...)`, `update_reuse_policy`, `update_hook_env(_branch_hook_env(cfg))` as three independent setters on the same `WorkspaceManager` instance (PR #19 path).
- `src/symphony/workspace.py:91-97` — `WorkspaceManager.update_hooks(hooks, *, workflow_dir=None)`; keyword-only param mutates `self._workflow_dir` only when non-None so legacy callers (`reload_hooks_only`) keep their semantics.
- `src/symphony/workspace.py:241-243` — `_run_hook` reads `self._workflow_dir` on every invocation and exports it as `SYMPHONY_WORKFLOW_DIR`; mutations via `update_hooks` flow into the *next* hook run, no restart needed.
- `tests/test_orchestrator_phase_transition.py:218-234` — `_install_fake_backend` factory helper. Required pattern for any new phase-transition test.
- `tests/test_orchestrator_phase_transition.py:399-464` — paired regression tests (`initialize` failure + `start_session` failure) pinning the try/except boundary.
- `tests/test_orchestrator_dispatch.py:2949-3075` — paired regression tests for the `_on_tick` reload path (single-field `workflow_dir` + three-field simultaneous update).

**Observability hooks:**
- log: `phase_transition_old_stop_failed` at `src/symphony/orchestrator.py:2141` — pre-try cleanup of the previous backend failed; safe to ignore unless it repeats for the same ticket.
- log: `phase_transition_new_stop_failed` at `src/symphony/orchestrator.py:2191` — new backend was built but a downstream step raised, and the try-block's cleanup also failed. Both the original error and this warning are emitted; the original re-raises so the worker loop sees it.

**Decision log:**
- 2026-05-17 | SMA-24 | Pinned PR #19 + PR #21 invariants with two new regression tests. Documented the `_install_fake_backend` factory-call requirement (the cherry-pick conflict footprint that 3a8bb7e fixed) and the `BaseException` catch rationale (cancellation cleanup).

**Last updated:** 2026-05-17 by SMA-24.
