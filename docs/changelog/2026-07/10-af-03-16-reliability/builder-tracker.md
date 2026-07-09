# Builder evidence - AF-04, AF-06, AF-12 tracker integrity

Scope: frozen PLAN step 2 only. The change protects a running worker from an
operator state move, keeps atomic-write artifacts out of the board model, makes
malformed/duplicate board inputs visible, and serializes delete with existing
per-ticket mutations. AF-12's orchestrator degraded-state behavior belongs to
PLAN step 1 and is intentionally excluded.

## Root-cause map

- AF-04: `handle_issue_patch` performed `update_fields` before consulting the
  running-worker lookup already used by delete.
- AF-06: atomic temps used a `.md` suffix and every board scan accepted hidden
  `.tmp-*.md` files; no startup cleanup existed.
- AF-12 A/B: `_scan_all` and `find_path` swallowed parse errors, `_scan_all`
  retained repeated ids, and `create` checked only the canonical path.
- AF-12 C: `delete` did not participate in the per-ticket lock used by append
  and update, so an in-flight atomic replace could recreate the unlinked file.

## RED and GREEN evidence

### AF-04 running-state PATCH

- RED command:
  `env UV_CACHE_DIR=/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_webapi.py -k 'patch_rejects_running_state_change_without_mutating_file or patch_allows_running_non_state_and_same_state_edits'`
- RED result: exit 1; `1 failed, 1 passed, 27 deselected`; the state-change
  request returned 200 instead of the required 409.
- GREEN command:
  `env UV_CACHE_DIR=/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_webapi.py -k 'patch_rejects_running_state_change_without_mutating_file or patch_allows_running_non_state_and_same_state_edits or patch_moves_state_and_updates_fields'`
- GREEN result: exit 0; `3 passed, 26 deselected`.

### AF-06 temp filtering, sweep, and write suffix

- RED command:
  `env UV_CACHE_DIR=/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_tracker_file.py -k 'legacy_temp_files or stale_legacy_temp or atomic_write_temp'`
- RED result: exit 1; `3 failed, 45 deselected`; fresh legacy temps appeared
  in reads, the stale temp remained, and the atomic temp suffix was `.md`.
- GREEN command:
  `env UV_CACHE_DIR=/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_tracker_file.py -k 'legacy_temp_files or stale_legacy_temp or atomic_write_temp'`
- GREEN result: exit 0; `3 passed, 45 deselected`.

### AF-12 parse and duplicate integrity

- RED command:
  `env UV_CACHE_DIR=/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_tracker_file.py -k 'parse_failures_warn or duplicate_frontmatter_ids or create_rejects_identifier'`
- RED result: exit 1; `3 failed, 48 deselected`; no parse warnings were
  emitted, both duplicate ids were returned, and non-canonical duplication was
  created.
- GREEN command:
  `env UV_CACHE_DIR=/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_tracker_file.py -k 'parse_failures_warn or duplicate_frontmatter_ids or create_rejects_identifier'`
- GREEN result: exit 0; `3 passed, 48 deselected`.

### AF-12 delete versus append/update

- RED command:
  `env UV_CACHE_DIR=/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_tracker_file.py -k 'delete_serializes_with_ticket_mutations'`
- RED result: exit 1; `2 failed, 51 deselected`; delete completed while each
  mutation held the ticket lock and the later replace could resurrect the file.
- GREEN command:
  `env UV_CACHE_DIR=/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_tracker_file.py -k 'delete_serializes_with_ticket_mutations'`
- GREEN result: exit 0; `2 passed, 51 deselected`.

## Final verification

- Preserve baseline before edits:
  `env UV_CACHE_DIR=/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_webapi.py tests/test_tracker_file.py`
  -> exit 0; `72 passed in 4.75s`.
- Final focused tests, same command -> exit 0; `82 passed in 1.87s`.
- `env UV_CACHE_DIR=/tmp/symphony-af-03-16-uv-cache uv run --extra dev ruff check src/symphony/webapi.py src/symphony/trackers/file.py tests/test_webapi.py tests/test_tracker_file.py`
  -> exit 0; `All checks passed!`.
- `env UV_CACHE_DIR=/tmp/symphony-af-03-16-uv-cache uv run --extra dev pyright src`
  -> exit 0; `0 errors, 0 warnings, 0 informations`.
- `git diff --check` -> exit 0; no output.

## Decisions and rejected alternatives

- Guard only a case-insensitive state delta; running title/metadata edits and
  same-state PATCHes retain existing behavior.
- Keep atomic rename on the same filesystem but use a non-board `.tmp` suffix;
  also filter legacy `.tmp-*.md` names at the shared ticket-path seam.
- Sweep root `.tmp-*` files only after a 60-second safety age and emit a
  structured warning for success or failure.
- Collapse duplicate ids by first sorted board path and log kept/skipped paths;
  use `find_path` under the existing ticket lock for create rejection.
- Re-resolve and unlink inside the existing per-ticket lock. No cross-process
  protocol, CAS redesign, TUI guard, or orchestrator reconcile change was added.

## Gap

No open gap within PLAN step 2 tracker ownership. Full-suite and AF-12
orchestrator degraded-state proof remain with the conductor and PLAN step 1.
