# AF-09 — Codex malformed-streak break wedges the persistent app-server

Route: DEBUG | Severity: P2 | Confidence: CONFIRMED | Blocked by: none

## Defect

When the codex app-server emits ≥10 consecutive non-JSON stdout lines (Rust
panic backtrace, stray prints), `_stdout_reader` sets `stream_corrupt` and
`break`s (`backends/codex.py:614-668`, break at `:639`) — failing in-flight
futures (good) but **neither setting `self._closed` nor terminating the
process** (`:369-386` is the only close path). The orphaned app-server keeps
running; every subsequent `run_turn` passes the guards, writes to stdin, and
waits a future no reader will resolve → full `read_timeout_ms` /
`turn_timeout_ms` timeout per turn for the rest of the session. Per-turn
backends are immune (they reap each turn); only codex's persistence turns
the break into a wedge.

## Fix direction

On `stream_corrupt`: set `self._closed = True` and terminate the process
tree (reusing `stop()`'s teardown) after failing pending futures, so later
`run_turn` calls fail fast ("client is closed") and the orphan is reaped.

## Acceptance checks

- [ ] RED first: feed ≥`MALFORMED_LINE_LIMIT` malformed lines while the fake
  process stays alive, then call `run_turn`; assert it fails fast instead of
  hanging until timeout (fails on current `main`).
- [ ] WHEN the stream corrupts THEN the subprocess is terminated/reaped and
  a structured log records the corruption.
- [ ] Sparse malformed lines (streak resets on valid line) still tolerated —
  existing reader tests green. Full suite green.

## Non-goals

Malformed-limit tuning; app-server restart/reconnect logic (worker retry
already covers recovery at the orchestrator level).

## Resolution — 2026-07-10

Resolved by closing the persistent backend at the malformed-line limit,
failing pending work, logging `codex_stream_corrupt`, and reaping the process
tree from the reader without self-awaiting. Focused backend tests cover fast
later-turn failure and valid-line streak reset. Process teardown remains
idempotently retryable by a later `stop()` when the reader's first reap fails.
