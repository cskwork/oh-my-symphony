# AF-06 — Board scan matches `.tmp-*.md` → phantom duplicate tickets

Route: DEBUG | Severity: P1 | Confidence: CONFIRMED | Blocked by: none

## Defect

`write_ticket_atomic` creates its temp file as
`mkstemp(prefix=".tmp-", suffix=".md", dir=path.parent)`
(`trackers/file.py:470`) — in the board root, with a name that
`Path.glob("*.md")` **matches** (verified experimentally). All three scan
sites (`file.py:579`, `:596`, `:746`) glob `*.md` unfiltered.

- Transient: an unlocked concurrent scan (e.g. web `handle_board` via
  `asyncio.to_thread`) between temp-write and `os.replace` returns two
  issues with the same id.
- Persistent: a hard kill between temp-write and replace leaves an orphan
  `.tmp-*.md` that is a fully parseable ticket — every future scan yields a
  permanent duplicate frozen at its old state, which becomes
  dispatch-eligible again once the real ticket leaves `_running` (ghost
  re-dispatch loop). No sweep ever removes board-root temps.

## Fix direction

Make temps unmatchable and clean up orphans: write temps to a dot-directory
(e.g. the existing `.locks/` sibling pattern or a `.tmp/` subdir) or a
non-`.md` suffix, AND filter `name.startswith(".tmp-")` in the three glob
loops (defense in depth for boards written by older versions). Add a startup
sweep that deletes stale board-root `.tmp-*.md` older than a safety margin.

## Acceptance checks

- [ ] RED first: place a parseable `.tmp-XXXX.md` beside tickets; assert
  `_scan_all`/`fetch_*` ignore it — fails on current `main`.
- [ ] WHEN a write is in flight THEN a concurrent scan never returns two
  issues with one id (simulate by leaving the temp present).
- [ ] WHEN the orchestrator starts over a board containing an orphaned temp
  THEN the temp is swept and logged, and the board has no duplicates.
- [ ] `next_identifier` allocation unaffected (temp stems already don't match
  `PREFIX-\d+` — keep it that way). Full suite green.

## Non-goals

Duplicate frontmatter ids across real files (AF-12); CAS write-path changes.

## Resolution — 2026-07-10

Resolved by giving new atomic writes the owned
`.tmp-symphony-ticket-<random>.tmp` marker and filtering legacy `.tmp-*.md`
files from every board read. Startup only sweeps safety-aged marker-owned
temps or legacy `.tmp-*.md` files that parse as required Symphony ticket
artifacts; unrelated operator `.tmp-*` files are preserved. Focused tracker
tests cover scans, writes, selective cleanup, and identifier allocation.
