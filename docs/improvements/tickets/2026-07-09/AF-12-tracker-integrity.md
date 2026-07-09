# AF-12 — Tracker integrity: silent parse drops, duplicate ids, unlocked delete

Route: LEGACY | Severity: P2 | Confidence: CONFIRMED (drops) / PLAUSIBLE (dupes, delete)
Blocked by: none

Three integrity gaps in `trackers/file.py`, grouped because they share the
scan/lock seam and one test fixture family.

## Defect A — unparseable tickets vanish silently; running ticket loses reconcile

`_scan_all` / `find_path` swallow `SymphonyError` with no log
(`file.py:577-586`, `:596-600`). A ticket the agent mangled disappears from
board, candidates, and `fetch_issue_states_by_ids` — so reconcile Part B
never sees the running id and the slot waits out the full stall timer with
zero operator signal.

## Defect B — duplicate frontmatter ids undetected

`create()` guards only on the canonical `<id>.md` path existing
(`file.py:797-800`); `_scan_all` never dedupes by id. A copied file (or
`create` with an id that lives under a non-canonical filename) yields two
issues with one id: double-counted stats, and a permanently stale duplicate
that only `find_path`'s canonical-first pick keeps from being mutated.

## Defect C — `delete()` takes no lock

`delete` is `find_path` + `unlink` (`file.py:893-898`) with no
`_exclusive_lock`; the webapi delete guard is check-then-act
(`webapi.py:625-632`). Racing a locked mutate's `os.replace`
(`file.py:474`) resurrects the deleted ticket with the mutation's content.

## Fix direction

- A: structured `ticket_parse_skipped` warning (path + error) at both
  swallow sites; reconcile treats "running id absent from refresh" as an
  explicit degraded state instead of waiting for the stall timer.
- B: collapse duplicate ids in `_scan_all` (first-write-wins + warning);
  `create` rejects when `find_path(identifier)` already resolves.
- C: wrap `delete` in the per-ticket `_exclusive_lock`, re-resolving the
  path under the lock.

## Acceptance checks

- [ ] RED first (A): corrupt a running ticket's file; assert a warning is
  logged and reconcile flags the missing running id (fails on `main`).
- [ ] RED first (B): two files sharing one frontmatter id → one issue
  returned + warning; `create` with an id under a different filename → error.
- [ ] RED first (C): concurrent delete vs `append_note` never resurrects the
  file (loop the race in-process).
- [ ] Full suite green, including tracker and webapi tests.

## Non-goals

Temp-file scan exclusion (AF-06); CAS retry-count tuning; jira/linear parity
(follow-up if the same seams exist there).

## Resolution — 2026-07-10

Parse failures and duplicate ids now emit structured warnings; scans collapse
duplicates deterministically, create rejects non-canonical duplicates, and
delete re-resolves under the existing per-ticket mutation lock. The running
refresh seam records a visible tracker error for an omitted id and clears it
when the id returns. Evidence: focused tracker, reconcile, and web API tests.
