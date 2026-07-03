# Implementation plan: Operator Trust Program (remaining work)

Date: 2026-07-03. Base: `dev` at `7be48e2` (full suite: 942 passed, 2 skipped).
Spec: `docs/spec/operator-trust-program/` (`requirements.md`, `design.md`,
`tasks.md`, `audit.md`).

The 2026-07-03 audit found that commit `1818d60` already landed the Health
Snapshot core, the first Attention Signal, all backend lifecycle work, most
doctor checks, and a working smoke script. This plan covers only the
remaining gaps, in dependency order. Each slice is independently committable
and follows test-first ordering.

Conventions for every slice:

- Run tests with the project venv: `.venv/bin/python -m pytest -q <files>`.
- Write the failing test first, then the minimal implementation.
- Public API changes are additive only (spec compatibility NFR).
- Payloads never include secrets or raw backend stderr beyond existing
  concise error tails.
- Commit per slice on `dev` (or a feature branch merged to `dev`), message
  `feat|fix|docs: <slice>`.

## Slice order and rationale

| # | Slice | Spec tasks | Depends on |
|---|---|---|---|
| A | Health `starting` status + `workflow_path` | 2.3 | — |
| B | Owner-aware port messaging | 2.4, part of 6.1 | — |
| C | Attention taxonomy completion | 3.2 | — |
| D | TUI attention rendering | 3.3 | C |
| E | RunRegistry history query | 4.1 | — |
| F | `/api/v1/runs` + `symphony runs` | 4.2 | E |
| G | Web drawer run history | 4.3 | F |
| H | Doctor prompt visibility row | 6.1 | — |
| I | Smoke health check + next-step hints | 6.2 | A |
| J | README proof path + examples confirm | 7.1, 7.2 | F, I |
| K | Final verification + changelog | 8.1, 8.2 | all |

A, B, C, E, H are mutually independent and can be built in any order or in
parallel worktrees; the listed order front-loads the smallest reviewable
diffs.

## Slice A — Health `starting` status and `workflow_path`

Goal: requirement 1.4 — before the first completed tick, health must state
startup is pending instead of reporting `ok`.

Files: `src/symphony/orchestrator/core.py` (`health()`, ~line 609),
`tests/test_orchestrator_health.py`.

Tests first:

1. `test_health_reports_starting_before_first_tick` — fresh orchestrator
   (no tick completed, not degraded) returns `status == "starting"`.
2. `test_health_includes_workflow_path` — payload carries the workflow path
   once config is applied; `None`/absent-safe before that.
3. Existing degraded tests still pass: degraded reasons outrank `starting`
   (a registry error during startup must read `degraded`, not `starting`).

Implementation:

- In `health()`, after computing `degraded_reasons`:
  `status = "degraded" if degraded_reasons else ("starting" if last is None else "ok")`.
  `last` is `self._last_tick_completed_at`, so one completed tick flips
  `starting` to `ok` permanently.
- Add `"workflow_path": str(cfg.workflow_path)` sourced from the same config
  the orchestrator already holds; keep the key present with `null` when
  config is not yet applied.
- No consumer parses `status` beyond tests (verified in audit), so the new
  enum value is safe.

Verify: `.venv/bin/python -m pytest -q tests/test_orchestrator_health.py tests/test_webapi.py`.

## Slice B — Owner-aware port messaging

Goal: requirements 1.5 and 5.2 — when the configured port cannot bind, say
whether this workflow's own service owns it; keep the current actionable
fallback otherwise.

Files: `src/symphony/cli/main.py` (OSError branch, ~line 226),
`src/symphony/cli/doctor.py` (`_bind_port`, ~line 60),
`src/symphony/service.py` (read-only use of `load_record`),
`tests/test_cli_main_routing.py` or the direct-run tests, `tests/test_doctor.py`.

Tests first:

1. Doctor: bind failure + service record whose `recorded_port` matches →
   fail detail names the running service (pid, started time if recorded) and
   suggests `symphony service status <workflow>`.
2. Doctor: bind failure without a matching record → current message plus
   host, port, and a next diagnostic command (edge case in requirements).
3. Direct run: same two cases through the `cli/main.py` OSError branch.

Implementation:

- Add one helper (single concern, reused by both callers), e.g.
  `port_owner_hint(workflow_path, port) -> str | None` in `service.py` or a
  small shared module: load the service record via `load_record`; if
  `recorded_port == port` (or `requested_port` matches) and the recorded pid
  is alive, return an "owned by this workflow's service" hint string.
- `_bind_port` and the `cli/main.py` OSError handler append the hint when
  present; otherwise keep existing text and add the next-diagnostic-command
  suffix.
- Never raise from the hint path: any record read error returns `None`
  (the record loader already swallows malformed JSON).

Verify: `.venv/bin/python -m pytest -q tests/test_doctor.py tests/test_cli_main_routing.py`.

## Slice C — Attention taxonomy completion

Goal: requirements 2.2-2.5 — extend `issue_attention` from one kind to five,
with deterministic priority and additive `severity`/`due_at` fields.

Files: `src/symphony/orchestrator/core.py` (`issue_attention`, ~line 735),
`tests/test_orchestrator_dispatch.py`, `tests/test_webapi.py`.

Signal sources (all already exist as runtime state):

| kind | severity | source | message / due_at |
|---|---|---|---|
| `stalled` | error | running entry in stall-cancel or force-eject grace (`_reconcile_running` state) | seconds since last progress |
| `lease_blocked` | error | `entry.lease_lost` on a running entry, or dispatch skipped on `has_active_lease` conflict | lease holder info from registry row when cheap |
| `budget_exhausted` | warning | `_turn_budget_exhausted` (existing) | existing message |
| `tracker_error` | warning | per-issue tracker transition/update failure recorded in `_issue_debug` | concise error tail |
| `retry_scheduled` | info | `self._retry[issue_id]` (`RetryEntry`) | `due_at` from `due_at_ms`, message from `RetryEntry.error` or "retry N scheduled" |

Priority (highest first), pinned by a test:
`stalled > lease_blocked > budget_exhausted > tracker_error > retry_scheduled`.
Rationale: causes needing operator intervention outrank self-healing ones.

Tests first (each fails before implementation):

1. One test per kind proving payload `{kind, label, message, severity}` and,
   for retry, ISO `due_at`.
2. `test_issue_attention_priority_order` — seed two-plus causes
   (e.g. budget exhausted and retry scheduled) and assert the winner.
3. Terminal cleanup — after the ticket reaches a terminal state and runtime
   maps are cleaned, `issue_attention` returns `None` (edge case).
4. Missing due time — retry signal without a known due time still renders.
5. `tests/test_webapi.py` — board card and detail payloads carry the new
   fields; unknown kind renders as readable text in the web fallback
   (existing `buildAttentionBadge` behavior; extend
   `tests/test_web_static_contract.py` only if strings change).

Implementation:

- Keep `issue_attention` as the single decision point. Compute candidate
  signals cheaply from in-memory maps only (no tracker or registry I/O —
  same discipline as `health()`), then return the highest-priority one.
- `tracker_error`: record per-issue tracker failures into `_issue_debug`
  where transitions/updates fail (extend the existing debug write sites);
  expose only the concise error tail.
- Add `severity` and `due_at` keys additively; existing `{kind, label,
  message}` consumers keep working. Return type widens to
  `dict[str, str | None]` or a small TypedDict.
- If `issue_attention` outgrows ~50 lines, split per-kind derivation into
  small private helpers next to it.

Verify: `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py tests/test_webapi.py tests/test_web_static_contract.py`.

## Slice D — TUI attention rendering

Goal: requirement 2.6 for the TUI — show the attention cause on card and
detail surfaces without opening logs.

Files: TUI card/detail modules under `src/symphony/tui/` (locate the card
compact-row builder and the detail pane renderer), TUI tests alongside
existing render tests.

Tests first:

1. Card render includes a short attention marker (label) when the snapshot
   issue carries attention.
2. Detail pane shows `label: message` and `due_at` when present.
3. Unknown kind falls back to the raw label/message text (edge case).

Implementation:

- The TUI reads the same state snapshot the web uses; thread the attention
  payload through the snapshot row (it already reaches web cards via
  `_issue_card`) and render text-only — dense-by-default, no color-only
  meaning (accessibility NFR).
- Keep the compact card addition to one short badge-like token; full message
  goes to the detail pane (matches the dense-defaults TUI direction).

Verify: `.venv/bin/python -m pytest -q tests/test_tui*.py` (whichever TUI
test modules exist), then a manual TUI spot-check during Slice K.

## Slice E — RunRegistry history query

Goal: requirement 3.1-3.4 data layer — bounded recent-run lookup.

Files: `src/symphony/orchestrator/run_registry.py`, `tests/test_run_registry.py`.

Tests first:

1. `test_recent_runs_empty` — returns `[]` on a fresh registry.
2. `test_recent_runs_issue_filter` — only the requested issue's rows.
3. `test_recent_runs_limit_clamped` — `limit=0`/negative/huge clamps into
   `[1, 200]`; never unbounded (document the clamp in the docstring).
4. `test_recent_runs_orders_newest_first` and terminal-cause shape — a
   `force_ejected_zombie` row exposes that string via `status`.

Implementation:

- `def recent_runs(self, issue_id: str | None = None, limit: int = 50) -> list[RunRecord]`
  — `SELECT ... FROM runs [WHERE issue_id = ?] ORDER BY rowid DESC LIMIT ?`.
  Reuse the existing row-to-`RunRecord` mapping; no schema change (`status`
  doubles as terminal cause per design.md).
- Read-only; no lease mutation.

Verify: `.venv/bin/python -m pytest -q tests/test_run_registry.py`.

## Slice F — `/api/v1/runs` and `symphony runs`

Goal: requirements 3.1-3.5 operator surfaces.

Files: `src/symphony/webapi.py` (route + handler),
`src/symphony/orchestrator/core.py` (thin accessor guarded by the existing
registry-op wrapper), `src/symphony/cli/main.py` (new `runs` token),
`tests/test_webapi.py`, `tests/test_cli_main_routing.py`.

Tests first:

1. API: `GET /api/v1/runs` → 200 with bounded list; `?issue=X` filters;
   `?limit=abc` or out-of-range → 400 or clamp, matching the registry clamp
   (pick clamp; test pins it).
2. API with registry unavailable → 200 with `{"runs": [], "registry_error": "..."}`
   consistent with health reporting (edge case: missing registry does not
   500).
3. CLI: `symphony runs ./WORKFLOW.md [--issue ID] [--limit N]` prints a
   compact table (identifier, attempt kind, agent, status, started, ended);
   empty history prints an explicit "no runs recorded" line, exit 0.

Implementation:

- Orchestrator accessor `recent_runs(...)` wraps the registry call in the
  existing `_registry_op`-style guard so a broken registry degrades instead
  of raising.
- API response envelope: `{"runs": [...], "count": N}`; timestamps ISO UTC;
  `workspace_path` passed through as string (no existence guarantee).
- CLI opens the registry directly at
  `cfg.workflow_path.parent / ".symphony" / "state.db"` — extract that
  expression into one shared helper (used by `_ensure_run_registry` at
  `core.py:232` and the CLI) so the path never drifts. Read-only open; if
  the DB file is absent, print the same "no runs recorded" line.
- Route the `runs` token in `main.py` next to board/doctor/service/tui.

Verify: `.venv/bin/python -m pytest -q tests/test_webapi.py tests/test_cli_main_routing.py tests/test_run_registry.py`.

## Slice G — Web drawer run history

Goal: requirement 3 web surface — recent attempts in the issue drawer.

Files: `src/symphony/web/static/app.js`, `src/symphony/web/static/style.css`,
`tests/test_web_static_contract.py`.

Tests first: static contract strings for the history section heading and the
`/api/v1/runs?issue=` fetch path.

Implementation:

- Fetch `/api/v1/runs?issue=<id>&limit=10` when the drawer opens (lazy — do
  not block board load or the state poll); render rows as
  `attempt_kind agent status started->completed`.
- Fetch failure renders a single quiet "history unavailable" row, not a
  broken drawer.

Verify: `.venv/bin/python -m pytest -q tests/test_web_static_contract.py`,
then a live drawer check during Slice K (and the browser E2E suite if run
locally: `tests/test_web_browser_e2e.py`).

## Slice H — Doctor prompt visibility row

Goal: requirement 5.1 as re-scoped by the audit — missing prompt files
already fail config load actionably (`coercion.py:117`); doctor adds
visibility.

Files: `src/symphony/cli/doctor.py`, `tests/test_doctor.py`.

Tests first:

1. Workflow with `prompts.base` + stage templates → pass row listing the
   resolved paths (count + base path is enough; avoid dumping seven paths).
2. Workflow without `prompts:` → pass row saying the built-in template is in
   use.
3. Workflow referencing a missing prompt file → doctor exits with the config
   load error text, no traceback (guards the load-time behavior).

Implementation: one `check_prompts(cfg)` reading `cfg.prompts.base_path` and
`cfg.prompts.stage_paths` (already stored on `PromptConfig`); registered in
`run_checks`.

Verify: `.venv/bin/python -m pytest -q tests/test_doctor.py`.

## Slice I — Smoke health check and next-step hints

Goal: requirements 5.4, 5.5 — the smoke script proves health plus the board
APIs and failures name the next diagnostic step.

Files: `scripts/smoke_web_api.py`, `tests/test_web_api_smoke_script.py`.

Tests first:

1. Smoke against the test server includes a `health` check that accepts
   `ok` and `starting` and fails on `degraded` with the reasons in the
   detail.
2. A failing endpoint's `SmokeFailure` message includes method, path,
   expected/actual status, and a next-step hint (e.g. "check `symphony
   service status <workflow>` / server logs").

Implementation:

- Add `expect(base_url, "GET", "/api/v1/health", 200)` as the first check;
  assert `status in {"ok", "starting"}` and surface `degraded_reasons`
  otherwise (depends on Slice A only for the `starting` value; accept `ok`
  alone until A lands if built out of order).
- Extend `SmokeFailure` construction with a `hint` line; keep the script
  stdlib-only (urllib) as today.

Verify: `.venv/bin/python -m pytest -q tests/test_web_api_smoke_script.py`.

## Slice J — README proof path and examples confirm

Goal: requirements 6.3-6.5 — document the trust surfaces as the fresh-clone
proof path; confirm examples still match the four active lanes.

Files: `README.md`, `README.ko.md`, `examples/*`,
`skills/using-symphony/*` references.

Steps:

1. Add a short "Prove it works" block to the existing quickstart: `symphony
   doctor ./WORKFLOW.md` → start service → `curl /api/v1/health` →
   `symphony runs ./WORKFLOW.md --limit 5` → `python scripts/smoke_web_api.py
   --base-url http://127.0.0.1:<port>` (verify the script's actual flag
   names before writing). Korean README mirrors the same block (repo policy:
   the two stay in sync).
2. Grep-confirm no stale lane claims and that Linear/Jira remain labeled as
   credentialed secondary paths.
3. Every documented command must be run once from a fresh-clone-shaped
   checkout before commit (repo-public memory: examples must actually run).

Verify: run each documented command; `grep` for the commands in both READMEs.

## Slice K — Final verification and changelog

Goal: requirements 7.4, 7.5 and spec task 8.

Steps:

1. `.venv/bin/python -m pytest -q` — full suite green (baseline was 942
   passed, 2 skipped; expect the count to grow).
2. `symphony doctor ./WORKFLOW.md`; if port 9999 is busy, use the service
   record to confirm ownership or rerun with an alternate configured port —
   never report a false pass.
3. One real local service smoke against a file board: start the service,
   run `scripts/smoke_web_api.py`, open the board, confirm an attention
   badge and the drawer history section render.
4. Append decisions, rejected alternatives, and verification evidence to
   `docs/changelog/changelog-<date>.md`; update spec `tasks.md` checkboxes.
5. Commit on `dev`, then merge `dev` to `main` per repo policy (commit and
   push as separate commands).

## Risks and mitigations

- **Attention derivation races** — `issue_attention` reads in-memory maps
  that the tick loop mutates; keep it synchronous, read-only, and tolerant
  of missing keys (`.get`, no subscripts — the `KeyError('OLV-002')`
  incident came from direct `_running[id]` subscripts).
- **CLI registry access vs running service** — SQLite WAL allows a
  concurrent read-only open, but open with a short timeout and treat lock
  errors as "history temporarily unavailable", never a traceback.
- **`starting` status regressions** — any consumer that treats non-`ok` as
  unhealthy would misread startup; audit found only tests consume the field
  today, and the smoke check explicitly accepts both.
- **Doc drift** — Slice J is last so it documents commands that already
  exist and pass.
