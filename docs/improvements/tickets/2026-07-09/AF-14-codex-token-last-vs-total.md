# AF-14 — Codex token accounting: `last`-only tokenUsage can double-count

Route: LEGACY | Severity: P3 | Confidence: PLAUSIBLE | Blocked by: research

Research: confirm against the pinned codex app-server whether a
`thread/tokenUsage/updated` notification ever ships `last` without `total`.
If it never does, close this ticket as "not reachable" with the evidence
linked here.

## Suspected defect

`_update_tokens_from_v2_block` documents its input as absolute cumulative
totals and overwrites `_latest_usage` (`backends/codex.py:857-892`), but its
caller feeds `tu.get("total") or tu.get("last")` (`codex.py:702`) — `last`
is per-turn, not cumulative. A `last`-only frame rebases the orchestrator's
high-water mark downward (`_apply_token_totals` unconditionally rebases
`last_reported_*`, `core.py:4569-4576`), so the next genuine cumulative
frame re-adds already-counted tokens. Inflated totals can trip a false
`token_budget_exceeded` cancellation (`core.py:4342-4366`).

## Fix direction

Treat only `total` as an absolute overwrite; a `last`-only frame is either
ignored for accounting or accumulated as a delta. Never rebase the
high-water mark downward off a non-cumulative frame.

## Acceptance checks

- [ ] Research note committed: observed wire shapes for `tokenUsage`.
- [ ] If reachable — RED first: sequence total=1000 → last=200 →
  total=1200 must count 1200 total, not 2200 (fails on current `main`).
- [ ] Full suite green.

## Non-goals

Other backends' accounting (claude's `+=` vs codex's `=` naming nit is a
comment-only cleanup); budget thresholds.
