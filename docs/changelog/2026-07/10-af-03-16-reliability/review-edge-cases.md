# AF-03 through AF-16 edge-case review

Role: fresh-context Edge-Case Improver
Date: 2026-07-10
Worktree: `/private/tmp/symphony-supergoal-af-03-16`

## Decision

Two ticket-grounded gaps remained and were fixed with RED -> GREEN tests:

1. AF-10 recovery accepted persisted process ids `0` and `-1` and could pass
   them to `kill_process_group`; on POSIX, `killpg(0, ...)` targets the
   caller's process group.
2. AF-05 Claude extraction accepted a trailing whitespace-only text block as
   the productive completion preview, hiding the preceding meaningful block.

No third gap was found in the requested pause/cleanup, stop deadline, tracker
race/casing, Codex corruption, ownership, CI scheduling, custom-state, or
prompt-boundary probes.

## Finding 1: AF-10 invalid persisted process ids

The run registry intentionally accepts nullable integer backend pids for
backward compatibility. Startup reclaim therefore has to validate the value at
the OS-signalling boundary. The new guard logs and skips every non-positive pid
before `kill_process_group`.

RED:

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_orchestrator_dispatch.py::test_startup_reclaim_skips_invalid_recorded_orphan_agent_pid
```

Result before the fix: `2 failed in 0.58s`; the kill spy received `[0]` and
`[-1]`.

Fix:

- `src/symphony/orchestrator/core.py`: in `_ensure_run_registry`, skip and warn
  on `pid <= 0` before the existing best-effort process-group kill.
- `tests/test_orchestrator_dispatch.py`: parameterized recovery regression for
  `0` and `-1`, including proof that the database run is still reclaimed as
  `orphaned`.

GREEN, including null/fake/live ownership variants:

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_orchestrator_dispatch.py::test_startup_reclaim_skips_invalid_recorded_orphan_agent_pid tests/test_orchestrator_dispatch.py::test_startup_reclaim_kills_recorded_orphan_agent_before_return tests/test_orchestrator_dispatch.py::test_startup_reclaim_terminates_live_recorded_orphan_agent_group
```

Result: `4 passed in 0.28s`.

Rejected alternatives: a database migration or global change to
`kill_process_group`. Both widen AF-10 unnecessarily; recovery owns the
persisted-data trust boundary.

## Finding 2: AF-05 trailing whitespace content

Claude messages may contain tool blocks and more than one text block. The
existing reverse scan correctly chooses the last meaningful text block, but
Python truthiness treated `" \n\t"` as meaningful. The extractor now requires
`text.strip()` while returning the original text unchanged.

RED:

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_backends.py::test_claude_extract_text_ignores_trailing_whitespace_text_block
```

Result before the fix: `1 failed in 0.21s`; actual preview was the trailing
whitespace instead of `final answer`.

Fix:

- `src/symphony/backends/claude_code.py`: ignore whitespace-only text blocks in
  `_extract_text`.
- `tests/test_backends.py`: regression with meaningful text, an intervening
  tool-use block, and trailing whitespace-only text.

GREEN, including the existing last-block and canonical-message contracts:

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_backends.py::test_claude_extract_text_picks_last_text_block tests/test_backends.py::test_claude_extract_text_ignores_trailing_whitespace_text_block tests/test_backend_contract.py::TestClaudeBackendContract::test_productive_completion_exposes_canonical_message
```

Result: `3 passed in 0.11s`.

Rejected alternative: concatenate all text blocks. That changes the established
last-meaningful-block contract and can mix tool narration into the final
preview.

## Passing ticket-implied probes

### Pause, cancellation, cleanup isolation, and bounded stop

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_dispatch_state.py::test_stop_bounds_cancellation_resistant_worker_drain tests/test_dispatch_state.py::test_stop_clears_issue_debug_state tests/test_orchestrator_dispatch.py::test_reconcile_force_ejects_cancelled_worker_even_when_paused tests/test_orchestrator_dispatch.py::test_reconcile_isolates_force_eject_cleanup_and_still_schedules_retry
```

Result: `4 passed in 0.16s`. A cancelled worker crosses the eject grace even
while paused; a lease-finalization exception still schedules its retry and does
not prevent reconciliation of the next issue; stop remains bounded and clears
diagnostic retention.

### Codex malformed-stream recovery and backend content variants

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_backends.py::test_codex_corrupt_stream_closes_reaps_and_later_turn_fails_fast tests/test_backends.py::test_codex_valid_json_resets_malformed_line_streak tests/test_backend_contract.py -k 'zero_exit_whitespace_stdout_is_a_failed_turn or productive_completion_exposes_canonical_message'
```

Result: `10 passed, 2 skipped, 27 deselected in 0.48s`. Corruption closes the
persistent backend, reaps the process, fails pending/future turns, and a valid
record resets the malformed-line streak. The two skips are the pre-existing
OpenCode/Pi preview-contract exclusions outside AF-05.

### CI latch/reset/idleness and first/continuation/rebuild turn boundaries

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_orchestrator_continuous_improvement.py::test_max_turns_latch_warns_once_until_manual_reset tests/test_orchestrator_continuous_improvement.py::test_reset_during_in_flight_still_counts_that_run tests/test_orchestrator_continuous_improvement.py::test_require_idle_board_counts_terminal_persist_pending tests/test_orchestrator_continuous_improvement.py::test_require_idle_board_blocks_dispatch_while_ci_active tests/test_orchestrator_continuous_improvement.py::test_lease_held_retries_after_full_interval tests/test_orchestrator_phase_transition.py::test_is_rewind_transition_uses_configured_active_state_order tests/test_orchestrator_phase_transition.py::test_custom_pipeline_rewinds_increment_budget_and_block_at_cap tests/test_orchestrator_phase_transition.py::test_prompt_turn_budget_continues_across_attempts tests/test_orchestrator_phase_transition.py::test_phase_rebuild_prompt_keeps_lifetime_turn_budget tests/test_prompt.py::test_first_turn_prompt_accepts_lifetime_turn_budget
```

Result: `11 passed in 0.12s`. Manual reset clears the warning latch without
erasing an already in-flight turn, all pending/live work participates in the
idle gate, configured state order drives rewind accounting, and every prompt
boundary uses the lifetime numerator and denominator.

### Custom state casing and case-only duplicates

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev python - <<'PY'
from symphony.orchestrator.core import _is_rewind_transition

states = ("Todo", "In Progress", "QA", "qa")
assert _is_rewind_transition("qA", "IN PROGRESS", states)
assert not _is_rewind_transition("qa", "QA", states)
print("state edge probe: casing normalizes; case-only duplicate labels are one logical state")
PY
```

Result:
`state edge probe: casing normalizes; case-only duplicate labels are one logical state`.
The helper uses normalized configured order and does not count a casing-only
rename as a rewind.

### Tracker exact-create race and path casing

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev python - <<'PY'
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

from symphony.errors import SymphonyError
from symphony.trackers.file import FileBoardTracker
from symphony.workflow import TrackerConfig

with TemporaryDirectory() as raw_root:
    root = Path(raw_root)
    cfg = TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Cancelled"),
        board_root=root,
    )
    tracker = FileBoardTracker(cfg)
    barrier = Barrier(12)

    def create_once(index: int) -> str:
        barrier.wait()
        try:
            tracker.create(identifier="RACE-1", title=f"writer-{index}")
            return "created"
        except SymphonyError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(create_once, range(12)))
    assert outcomes.count("created") == 1
    assert outcomes.count("duplicate") == 11

    (root / "A-copy.md").write_text(
        "---\nid: CASE-1\ntitle: first\nstate: Todo\n---\n", encoding="utf-8"
    )
    (root / "z-COPY.md").write_text(
        "---\nid: CASE-1\ntitle: second\nstate: Todo\n---\n", encoding="utf-8"
    )
    case_issues = [i for i in tracker.fetch_candidate_issues() if i.id == "CASE-1"]
    assert len(case_issues) == 1
    assert case_issues[0].title == "first"

print("tracker edge probe: 12-way exact create serialized; mixed-case paths deduped")
PY
```

Result: one create and eleven duplicate errors; the sorted first path won the
duplicate-id scan and emitted `duplicate_ticket_id_skipped`.

## Combined regression and static verification

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_backend_contract.py tests/test_backends.py tests/test_dispatch_state.py tests/test_orchestrator_continuous_improvement.py tests/test_orchestrator_dispatch.py tests/test_orchestrator_phase_transition.py tests/test_prompt.py tests/test_tracker_file.py tests/test_webapi.py
```

Result: `489 passed, 2 skipped in 18.53s`.

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev ruff check src/symphony/backends/claude_code.py src/symphony/orchestrator/core.py tests/test_backends.py tests/test_orchestrator_dispatch.py
```

Result: `All checks passed!`

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pyright src
```

Result: `0 errors, 0 warnings, 0 informations`.

```console
git diff --check
```

Result: pass, no output.

## Residuals and non-goals

- This role ran the nine affected test modules, not the entire repository test
  suite; the conductor/verifier retains the final full-suite gate.
- The tracker race probe covers exact-id competitors under the ticket's
  existing lock semantics. Cross-process CAS/locking redesign remains an
  explicit AF-12 non-goal.
- A raw pipeline containing case-only duplicate labels is semantically
  ambiguous configuration. AF-13's predicate safely treats those labels as one
  normalized state; adding workflow-schema validation is outside this ticket.
- Stop and process-liveness checks use controlled local subprocesses/tasks, not
  a live external Kanban board.
- `GOAL.md`, `QA.md`, and `run-state.json` were not edited. No commit was made.
