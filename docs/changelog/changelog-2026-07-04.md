# 2026-07-04 - Fix issue-detail JSON serialization for YAML timestamps

## Root Cause

The 9999 web app failed to load `CODEX-E2E-001` with
`Object of type datetime is not JSON serializable` because
`GET /api/v1/issues/<ID>` returned raw parsed frontmatter. PyYAML converts
unquoted YAML timestamps such as `updated_at: 2026-07-04T14:27:00Z` into Python
`datetime` objects. The board list path already serialized `Issue.created_at`
and `Issue.updated_at`, but the detail drawer also included the raw
`frontmatter` map, so `aiohttp.web.json_response` failed before sending the
ticket payload.

## Decision

Normalize the issue-detail `frontmatter` through a narrow JSON-safety helper
that converts `datetime` and `date` values to ISO strings recursively. This
keeps the UI contract JSON-only without changing tracker parsing or rewriting
ticket files.

- Rejected: quoting timestamps in the current ticket only. That would hide the
  bug for one card while leaving any agent-authored unquoted timestamp broken.
- Rejected: replacing every `web.json_response` with a broad `default=str`.
  That would mask future non-JSON API leaks outside the known YAML timestamp
  boundary.

## Verification

- Added
  `tests/test_webapi.py::test_issue_detail_serializes_unquoted_frontmatter_timestamps`.
- Red: the new test reproduced HTTP 500 with
  `Object of type datetime is not JSON serializable`.
- Green: `pytest tests/test_webapi.py::test_issue_detail_serializes_unquoted_frontmatter_timestamps -q`.
- Green: `pytest tests/test_webapi.py -q` -> 22 passed.
- Green: `pytest tests/test_webapi.py tests/test_web_api_smoke_script.py -q` -> 24 passed.

---

# 2026-07-04 - README 9999 admin UI showcase

## Decision

Feature the built-in 9999 browser admin UI before the terminal TUI in both
README surfaces. The screenshot asset lives at `docs/admin-ui-screenshot.png`
and is rendered from the real static web app with sanitized demo board data, so
the public README shows the prettier operator surface without exposing a live
workflow's ticket content.

- Rejected: using the legacy `tools/board-viewer` screenshots. They show the
  secondary viewer, not the 9999 orchestrator admin app the operator asked to
  showcase.
- Rejected: screenshotting the currently running `jira-symphony` server
  directly. The app was live on `127.0.0.1:9999`, but public README assets
  should not include real local ticket data.
- Rejected: replacing the terminal screenshot. The TUI is still a major
  operator surface; the README should show both, with the 9999 UI first.

## Verification

- Generated `docs/admin-ui-screenshot.png` from the real web app static assets
  at the `http://127.0.0.1:9999/#/board` route with mocked API payloads and
  Playwright Chromium `--single-process`.
- Visually inspected the generated image: nonblank, board columns rendered,
  live/retry badges visible, terminal-state group visible.
- `git diff --check` -> pass.

---

# 2026-07-04 - Fix G2 empty-response-loop false-positive on OpenCode

## Goal

Stop the G2 empty-response-loop guard from falsely blocking healthy OpenCode
tickets. A ticket (observed in `Learn`) was moved to `Blocked` with
`empty_response_loop budget exceeded (consecutive_empty_turns=3, threshold=3)`
despite the agent producing real output every turn.

## Root cause

G2 counts a turn as empty when `RunningEntry.current_turn_message` is blank at
`EVENT_TURN_COMPLETED` (`orchestrator/core.py`). `current_turn_message` is only
populated when `_preview_from_payload(payload)` returns text, and that helper
reads the preview keys `message` / `lastMessage` / `text` / `summary` / `item`.

OpenCode's `EVENT_TURN_COMPLETED` payload delivered its response text only under
`result` / `response` (`backends/opencode.py`) — keys the preview helper never
inspects. So every OpenCode turn looked empty, the counter climbed monotonically
regardless of real work, and G2 escalated after exactly 3 turns.

Why it surfaced now: the recent OpenCode liveness-heartbeat fix (stall-loop)
stopped long turns from being stall-cancelled. Before it, each stall-cancel
minted a fresh `RunningEntry` (counter reset to 0), so turns never accumulated to
3 on one entry. Removing the false stall-kill exposed the latent G2 miscount —
same bug class (OpenCode's batch-per-turn shape not fitting an event model built
for streaming backends), next guard.

## Decisions

### 1. Emit the response under a preview key from the backend

`run_turn`'s `EVENT_TURN_COMPLETED` payload now carries `message` (= the response)
alongside the existing `result` / `response`. `_preview_from_payload` reads
`message` first, so a productive turn populates `current_turn_message` and resets
the G2 counter. A genuinely empty turn (`response == ""`) still yields no preview
text, so real empty-loops are still caught. Bonus: the OpenCode TUI preview
(`last_codex_message`, same code path) was always blank before and now shows the
turn's response.

- Rejected: **raising `EMPTY_TURN_LOOP_THRESHOLD`** (the operator's first
  hypothesis). It is a band-aid — OpenCode would still false-block after N turns
  instead of 3, and a higher threshold weakens genuine empty-loop detection for
  every backend. It does not address the miscount.
- Rejected: **teaching `_preview_from_payload` to read `result` / `response`.**
  That is shared code on the hot event path for all backends; `result` /
  `response` can carry non-message content for other drivers, risking masked
  empty-turns elsewhere. The backend-local fix keeps the blast radius to OpenCode
  and matches the sibling heartbeat fix's "backend quirks live in the backend"
  precedent.

## Verification

- `backends/opencode.py` — 1 key added to the turn-completed payload.
- New `tests/test_backends.py::test_opencode_turn_completed_payload_carries_message_for_preview`
  asserts the emitted payload carries `message`.
- New `tests/test_orchestrator_dispatch.py::test_g2_opencode_shaped_payload_resets_only_with_message_key`
  drives the real `_on_codex_event` path: `result`/`response`-only payloads still
  count as empty (climb 1 -> 2); adding `message` resets the counter to 0.
- Full suite: 1058 passed, 2 skipped. No shared code touched;
  `EMPTY_TURN_LOOP_THRESHOLD` unchanged at 3.

Patch bump 0.9.1 -> 0.9.2 (fix restores intended behavior).

## Follow-up (0.9.3) — the dominant cause: opencode 1.17 schema drift

Live verification against the paused `jira-symphony` board (TASK-001, blocked
in `Learn` by exactly this loop) showed 0.9.2 alone was necessary but not
sufficient. Every `agent_turn_completed` reported `input_tokens=0
output_tokens=0` with an empty `last_message` — yet opencode had done real work
(glm-5.2, 8 agentic steps, 27-file commits). Input tokens cannot be zero for a
12 KB prompt: Symphony's opencode backend was parsing *nothing* out of
opencode 1.17.13's output. So `response` itself was empty, and 0.9.2's
`message: response` carried an empty string — the preview stayed blank and G2
still counted every turn empty.

Root cause: `opencode run --format json` (src/cli/cmd/run.ts `emit`) streams
JSONL frames `{"type": ..., "sessionID": ..., "part": {...}}`; assistant prose
is in `type=="text"` frames under `part.text`. Symphony's `_extract_text` only
scanned flat keys (`response`/`result`/`message`/`text`/`content`/`output`) plus
`data`, so it never saw `part.text` and returned "". (Confirmed by unit probe:
`_extract_text({"type":"text","part":{"text":"..."}}) == ""`.)

Fix: `OpenCodeBackend._text_from_event` reads `part.text` from `type=="text"`
frames, keeping the flat-key scan as a fallback for the raw-stdout and
non-opencode shapes. tool_use/step_start frames carry no prose, so a genuinely
empty turn (no text frame) still counts as empty — G2's real detection is
preserved. New `tests/test_backends.py::test_opencode_extracts_text_from_jsonl_part_frames`
pins the JSONL shape.

- Rejected: **a deep/generic text search** across every nested dict — risks
  grabbing tool-call arguments and file contents as "response". The
  `type=="text"` predicate is precise to opencode's serialization.
- Known follow-up (not G2-blocking, fixed in the v0.9.3 release update below): token usage still
  reads 0 for opencode 1.17. This only affects `max_total_tokens` budgeting and
  telemetry for opencode, not dispatch correctness. Tracked in
  `skills/symphony-skill/reference/troubleshooting.md`.

Operator playbook added: "Backend CLI update broke response parsing" in
`skills/symphony-skill/reference/troubleshooting.md` — the `input_tokens=0`
smoking gun, the capture-the-real-schema recipe, and the fix pattern, so the
next CLI-upgrade drift is a 2-minute triage instead of a rediscovery.

Patch bump 0.9.2 -> 0.9.3 (restores opencode response parsing for opencode >= 1.x).

## Follow-up (v0.9.3 release update) — OpenCode 1.17 token accounting

Live probe:

```bash
opencode run --format json --auto "reply with DONE only"
```

OpenCode 1.17.13 emitted usage on a `step_finish` frame:
`part.type == "step-finish"` with `part.tokens` containing `input`, `output`,
`reasoning`, `cache.read`, `cache.write`, and `total`. Symphony's `_usage_dicts`
never descended through `part`, so the backend emitted useful `message` text but
left every token bucket at 0. That matches the TASK-002 monitor evidence:
nonblank `last_message`, `input_tokens=0`, then a separate
`worker_exit reason=issue_state_refresh_failed` after the turn.

Fix: `OpenCodeBackend` now descends into `part` and `info` usage containers,
reads `totalTokens` as an alias, folds nested `cache.read` / `cache.write` into
input-side tokens, and treats `reasoning` as output-side tokens when totals need
to be derived. New
`tests/test_backends.py::test_opencode_extracts_usage_from_jsonl_step_finish_part_tokens`
pins the real OpenCode 1.17 `step_finish.part.tokens` schema.

- Rejected: **deep traversal of every nested dict**. OpenCode frames also carry
  tool and text payloads; scanning all children would risk treating user-facing
  content or tool arguments as telemetry. The parser only follows known telemetry
  containers.
- Not part of this fix: `issue_state_refresh_failed`. The failure fires after a
  successful turn when tracker state refresh returns `None`; it is a separate
  board/workspace reliability issue, not a token parser symptom.

Included in the v0.9.3 release tag update (restores OpenCode token telemetry for opencode >= 1.x).

## Follow-up (v0.9.3 release update) — issue-state refresh RCA and operator UI

Live `jira-symphony` showed TASK-002 stuck in paused retry with
`worker_exit reason=issue_state_refresh_failed` after a successful nonblank
OpenCode turn. This was separate from the token parser issue. The ticket file
had malformed YAML:

```yaml
created_at: '2026-07-04T00:00:02Z'
  updated_at: '2026-07-04T02:35:57Z'
```

That made the file tracker skip TASK-002, so the worker could not refresh the
post-turn state and paused for operator inspection. Current-board repair was to
unindent `updated_at`, after which `symphony board ls --workflow ...` showed
TASK-002 back in `Learn`, and `POST /api/v1/TASK-002/resume` moved it from
`retrying` to a running `Learn` retry.

Hardening: `parse_ticket_file` now auto-heals the common case where an agent
accidentally indents a canonical top-level key by one or two spaces. It does not
rewrite the file during read, but the next normal tracker write serializes
canonical YAML. New
`tests/test_tracker_file.py::test_parse_ticket_file_auto_heals_misindented_updated_at`
pins the observed TASK-002 shape.

Operator UI: Settings now shows the workflow default agent on both web surfaces.
The 9999 Symphony app renders `Default agent` from `/api/v1/workflow.agent.kind`
under `Settings > Board info`; the board-viewer Settings modal renders
`Agent default` from `/api/symphony/state.workflow.default_agent_kind`. The value
on the live board is `opencode`.

- Rejected: **only patch the current ticket**. That restores one board but lets
  the next agent-authored YAML indentation mistake remove a ticket from refresh.
- Rejected: **deep YAML text surgery on every read**. The heal only runs after a
  YAML parse failure and only unindents known ticket frontmatter keys with shallow
  accidental indentation, avoiding legitimate nested data such as `agent.kind`
  and `blocked_by[].state`.

## Follow-up (operator) — TASK-003 workspace collision RCA

After TASK-002 resumed and completed, live `jira-symphony` moved to TASK-003
and paused before the first agent turn:

```text
hook before_run exited 42;
workspace kanban points to /Users/danny/Documents/PARA/Resource/learn-codex-kr/kanban,
expected /Users/danny/Documents/PARA/Resource/jira-symphony/kanban
```

The path `/Users/danny/symphony_workspaces/TASK-003` was not a `jira-symphony`
worktree. It was a clean, older `learn-codex-kr` worktree registered under
`learn-codex-kr/.git/worktrees/TASK-003`, with `kanban` linked to that project's
board. Symphony derives workspace paths from `workspace.root / ticket-id`, so
two boards using the same global `~/symphony_workspaces` root and the same
identifier (`TASK-003`) collide.

Operator fix: change the `jira-symphony` workflow root to
`~/symphony_workspaces/jira-symphony`, then resume the ticket. The orchestrator
reloads WORKFLOW on each tick and rebuilds the `WorkspaceManager` when
`workspace.root` changes, so no stale foreign worktree has to be deleted.

- Rejected: **remove the foreign worktree as the primary fix**. Its status was
  clean, but it belongs to another project; deleting it would only free this one
  identifier and would not prevent the next cross-board `TASK-*` collision.
- Rejected: **only patch the `kanban` symlink inside the existing directory**.
  That would mutate a worktree registered to `learn-codex-kr` and mix project
  ownership in one checkout.

---

# 2026-07-04 - Dependency-aware dispatch guardrails

## Decision

Treat `## Dependencies` ticket sections as real blockers for file-board
workflows. The file tracker now parses ticket IDs only inside the Dependencies
section, merges them with `blocked_by` front matter, and hydrates their current
board state through the existing scan path. Unknown IDs remain blockers with no
state so typo or cross-board dependencies stop dispatch instead of becoming
silent production-line stalls.

Scheduler eligibility now applies blockers in every active state, not only
`Todo`. That keeps `In Progress`, `Verify`, and retry continuations from running
after an upstream ticket regresses. `/api/v1/state` attention also reports
`blocked_dependency` before retry attention, so held work is visible to the
operator while it waits.

- Rejected: scanning the whole ticket body for ticket-looking strings. Historical
  logs and evidence often mention other tickets; only the explicit Dependencies
  section is an operator dependency contract.
- Rejected: resolving body dependencies in the orchestrator. The file tracker
  already scans the board and hydrates blocker states, so adding filesystem reads
  in dispatch would split the source of truth.
- Rejected: treating unknown dependency IDs as absent. That would let typos or
  cross-board dependencies run too early; warning attention is cheaper than
  wasted backend turns.

## Verification

- Red tests first:
  `pytest tests/test_tracker_file.py::test_body_dependencies_become_blockers_with_state tests/test_tracker_file.py::test_unknown_body_dependency_remains_blocker_without_state tests/test_orchestrator_dispatch.py::test_active_state_issue_with_unresolved_blocker_is_ineligible tests/test_orchestrator_dispatch.py::test_auto_triage_refuses_todo_with_body_dependency tests/test_orchestrator_dispatch.py::test_issue_attention_reports_unresolved_dependency -q`
  -> 5 failed for missing dependency parsing, active-state gating,
  auto-triage refusal, and attention.
- Focused green checks:
  `pytest tests/test_tracker_file.py::test_body_dependencies_become_blockers_with_state tests/test_tracker_file.py::test_unknown_body_dependency_remains_blocker_without_state tests/test_orchestrator_dispatch.py::test_active_state_issue_with_unresolved_blocker_is_ineligible tests/test_orchestrator_dispatch.py::test_auto_triage_refuses_todo_with_body_dependency tests/test_orchestrator_dispatch.py::test_issue_attention_reports_unresolved_dependency -q`
  -> 5 passed.
- Retry recovery:
  `pytest tests/test_orchestrator_dispatch.py::test_retry_timer_waits_for_unresolved_blocker_then_recovers -q`
  -> 1 passed.
- Task slice:
  `pytest tests/test_tracker_file.py -k "dependency or blocker" &&
  pytest tests/test_orchestrator_dispatch.py -k "blocker or dependency or auto_triage" &&
  pytest tests/test_webapi.py -k "attention"` -> 3 passed, 9 passed, 1 passed.

## Quality follow-up

Move the dependency-section parser into `symphony.ticket_markdown` so the file
tracker and orchestrator helper share a neutral Markdown parser. The parser now
skips fenced code blocks before heading detection, so examples containing
`## Dependencies` and ticket IDs do not create false blockers.

- Rejected: keeping the parser private in `trackers.file`. The orchestrator
  helper needs only ticket-body Markdown semantics, not tracker internals.
- Rejected: stripping lines before all parsing decisions. Fence recognition must
  happen first so Markdown examples cannot become dependency contracts.

Verification:

- Red boundary check:
  `pytest tests/test_tracker_file.py -k "outside_dependencies_section or fenced_code" -q`
  -> 1 failed, 1 passed before the parser change.
- Green checks:
  `pytest tests/test_tracker_file.py -k "outside_dependencies_section or fenced_code" -q`
  -> 2 passed.
- Required slice:
  `pytest tests/test_tracker_file.py -k "dependency or blocker" -q` -> 5 passed.
- Required dispatch slice:
  `pytest tests/test_orchestrator_dispatch.py -k "auto_triage or dependency or blocker" -q`
  -> 9 passed.
- Compile:
  `python -m py_compile src/symphony/ticket_markdown.py src/symphony/trackers/file.py src/symphony/orchestrator/helpers.py`
  -> pass.
- Whitespace:
  `git diff --check` -> pass.

---

# 2026-07-04 - Backend token telemetry sanity guards

## Decision

Keep high total-token usage as telemetry, not a failure condition. Reasoning-heavy
OpenCode turns can legitimately spend large hidden/thinking budgets, so Symphony
now warns only for two explicit attention cases: a productive non-empty turn that
reports zero total tokens, and a turn above an operator-configured
`agent.token_attention_threshold_by_state` value. The new threshold map defaults
to `{}` and never cancels, blocks, or transitions a ticket.

OpenCode 1.17 token parsing is pinned around `step_finish.part.tokens`. A new
fixture keeps `part.tokens.total` authoritative while proving nested cache
`read` and `write` tokens both count as input-side usage. The orchestrator still
derives deltas from absolute backend totals, so cumulative reports trend once
instead of inflating per-turn stats.

- Rejected: warning on every large total-token turn. That would turn legitimate
  deep reasoning into noise and push operators toward unsafe low budgets.
- Rejected: connecting attention thresholds to `_persist_budget_exhausted_state`.
  Hard brakes already exist as opt-in `agent.max_total_tokens`; attention is only
  an operator-visible signal.
- Rejected: folding reasoning tokens into a failure category. The parser
  preserves the total and the operator can decide whether the configured
  workflow/backend pair is unusual.

## Quality follow-up

Prefer explicit cache totals over nested cache detail when both are present in
an OpenCode usage payload. Counting both turns schema detail into duplicate
input tokens; nested `cache.read` and `cache.write` remain summed only when no
explicit cache total exists.

- Rejected: always summing every cache-looking field. That protects one schema
  shape while inflating another, which makes attention thresholds noisy.

## Verification

- Red checks:
  `pytest tests/test_backends.py -k "opencode and token" -q` -> failed because
  nested `cache.write` was ignored (`1780` input tokens vs expected `2036`).
- Red checks:
  `pytest tests/test_workflow.py -k "token_attention" -q` -> failed with missing
  `AgentConfig.token_attention_threshold_by_state`.
- Red checks:
  `pytest tests/test_orchestrator_dispatch.py -k "token" -q` -> failed for
  missing zero-token attention and missing threshold config field.
- Red quality check:
  `pytest tests/test_backends.py::test_opencode_does_not_double_count_explicit_and_nested_cache_tokens -q`
  -> failed because explicit plus nested cache counted `120` input tokens
  instead of `110`.
- Green checks:
  `pytest tests/test_backends.py -k "opencode and token" -q` -> 3 passed.
- Green checks:
  `pytest tests/test_workflow.py -k "token_attention or max_total_tokens" -q`
  -> 1 passed.
- Green checks:
  `pytest tests/test_orchestrator_dispatch.py -k "token or attention" -q`
  -> 24 passed.

---

# 2026-07-04 - Stage-aware prompt context compaction

## Decision

Keep full-ticket prompt compaction opt-in behind
`agent.compact_issue_context: false` by default. The selector now builds a
state-specific description body from raw Markdown sections: original user scope
and acceptance criteria stay, while Verify/Learn receive only the latest
quality-critical implementation/evidence sections and rewind turns receive the
newest failure section instead of the accumulated failure history.

The built-in file and Linear prompt bases now render `Full ticket: ...` before
`## Description` whenever the caller provides a path. File workflows thread that
path from the tracker's `find_path(identifier)` helper during initial dispatch
and phase rebuilds, so a compact prompt still points the worker at the raw
ticket for audit or deeper context.

- Rejected: using a Markdown renderer. The prompt boundary only needs stable
  heading selection, and a renderer would introduce formatting changes in a
  path that should preserve retained section text.
- Rejected: enabling compaction by default in this patch. The rollback flag
  stays off until live workflow measurement proves the new context shape is
  safe for a given board.
- Rejected: character caps in the first implementation. Section selection drops
  repeated history without risking code-fence truncation; caps can be added
  later with fence-aware tests if the remaining prompt input is still too high.

## Quality follow-up

Preserve headed user scope before the first agent-owned section. Some tickets put
the original request under `## Description`, `## Goal`, or board-specific
background headings instead of a preamble; compact Verify and Learn prompts now
keep those leading non-agent sections before selecting the latest implementation
and evidence history.

Fresh `In Progress` dispatch also keeps the newest failure section. A restarted
worker may not have the transient `is_rewind` flag even though the ticket body
already contains `## QA Failure`, `## Review Findings`, or `## Learn Defect`;
the compact prompt must still show the item that caused the rework.

- Rejected: only adding `description` to a static allowlist. Retaining leading
  non-agent sections also covers board-specific headings such as `## Background`
  without preserving later repeated delivery history.

## Verification

- Red checks:
  `pytest tests/test_prompt_context.py -q` -> failed because
  `symphony.prompt_context` did not exist.
- Red checks:
  `pytest tests/test_prompt.py::test_compact_issue_context_changes_rendered_prompt_description -q`
  -> failed because `build_first_turn_prompt` did not accept
  `compact_issue_context`.
- Red checks:
  `pytest tests/test_workflow_pipeline_prompt.py::test_file_base_prompt_renders_full_ticket_path_outside_description -q`
  -> failed because `build_prompt_env` did not accept `full_ticket_path`.
- Red checks:
  `pytest tests/test_workflow.py -k "compact_issue_context" -q` -> failed
  because `AgentConfig` had no `compact_issue_context` field.
- Red quality check:
  `pytest tests/test_prompt_context.py::test_headed_description_scope_survives_compaction -q`
  -> failed because compact Verify context dropped `## Description`.
- Red quality check:
  `pytest tests/test_prompt_context.py::test_in_progress_fresh_dispatch_keeps_latest_failure_after_restart -q`
  -> failed because compact fresh `In Progress` context omitted the latest
  `## QA Failure`.
- Green checks:
  `pytest tests/test_prompt_context.py -q` -> 7 passed.
- Green checks:
  `pytest tests/test_prompt.py -k "compact or first_turn" -q` -> 6 passed.
- Green checks:
  `pytest tests/test_workflow_pipeline_prompt.py -k "prompt or description or full_ticket" -q`
  -> 34 passed.
- Green checks:
  `pytest tests/test_workflow.py -k "compact_issue_context" -q` -> 2 passed.

---

# 2026-07-04 - State-local watchdog caps

## Decision

Add `agent.max_state_turns_by_state` as a per-state same-state turn watchdog
before the global `agent.max_state_turns` fallback. This keeps broad legacy
workflows unchanged while allowing reasoning-heavy boards to give `In Progress`
more room than tighter quality gates such as `Verify` or `Learn`.

When a state-local cap trips and `budget_exhausted_state` is configured, the
Budget Exceeded note now records the observed same-state turn count and the
effective state limit. This avoids misleading operators with the global limit
when a smaller per-state cap made the decision.

Token policy remains separate:

- hard token caps are still opt-in via `max_total_tokens` or
  `max_total_tokens_by_state`.
- `token_attention_threshold_by_state` remains attention-only and does not call
  `_persist_budget_exhausted_state`.
- large token turns without an explicit hard cap continue to record telemetry
  without blocking the ticket.

- Rejected: lowering default hard token caps. The live failure mode was repeated
  no-progress work, not proof that high reasoning-token use is invalid.
- Rejected: reusing token attention thresholds as a persistence trigger.
  Attention is cost visibility; budget persistence is a hard workflow decision.
- Rejected: replacing the global watchdog. Existing workflows that only set
  `max_state_turns` must keep the same behavior.

## Quality follow-up

Normalize state-keyed integer maps with the existing strip-and-lower state key
helper. This prevents a config key like `" Verify "` from missing the live
`Verify` state at runtime.

Workflow UI state renames now carry `agent.max_state_turns_by_state` alongside
the existing per-state concurrency and token maps. Without that, renaming
`Verify` to `QA` would silently leave the cap under the old key.

- Rejected: fixing only the new turn-cap map parser. The shared normalization
  helper feeds multiple state-keyed integer maps, so trimming there removes the
  same mismatch class for existing maps too.

## Verification

- Red checks:
  `pytest tests/test_workflow.py::test_build_service_config_reads_state_turn_caps tests/test_workflow.py::test_max_total_tokens_defaults_disabled_for_reasoning_heavy_work -q`
  -> failed because `AgentConfig` had no `max_state_turns_by_state` field; the
  token-default guard passed.
- Red checks:
  `pytest tests/test_orchestrator_dispatch.py::test_verify_state_turn_cap_blocks_with_budget_artifact tests/test_orchestrator_dispatch.py::test_high_token_turn_without_hard_cap_does_not_block tests/test_orchestrator_dispatch.py::test_token_attention_threshold_never_persists_budget_state -q`
  -> failed because `AgentConfig` could not accept
  `max_state_turns_by_state`; the two soft-token guards passed.
- Red quality checks:
  `pytest tests/test_workflow.py::test_build_service_config_reads_state_turn_caps -q`
  -> failed because `" Verify "` normalized to `" verify "`.
- Red quality checks:
  `pytest tests/test_workflow_mutate.py::test_rename_column_updates_per_state_maps -q`
  -> failed because `max_state_turns_by_state` was not renamed with the
  workflow state.
- Green focused checks:
  `pytest tests/test_workflow.py::test_build_service_config_reads_state_turn_caps tests/test_workflow.py::test_max_total_tokens_defaults_disabled_for_reasoning_heavy_work -q`
  -> 2 passed.
- Green focused checks:
  `pytest tests/test_orchestrator_dispatch.py::test_verify_state_turn_cap_blocks_with_budget_artifact tests/test_orchestrator_dispatch.py::test_high_token_turn_without_hard_cap_does_not_block tests/test_orchestrator_dispatch.py::test_token_attention_threshold_never_persists_budget_state -q`
  -> 3 passed.
- Green required checks:
  `pytest tests/test_workflow.py -k "state_turn_caps or max_total_tokens_defaults" -q`
  -> 2 passed, 50 deselected.
- Green required checks:
  `pytest tests/test_orchestrator_dispatch.py -k "budget or no_stage or token" -q`
  -> 29 passed, 96 deselected.
- Green required checks:
  `pytest tests/test_workflow_mutate.py -q` -> 15 passed.
- Green required checks:
  `python -m py_compile src/symphony/workflow/config.py src/symphony/workflow/builder.py src/symphony/orchestrator/core.py src/symphony/workflow/coercion.py src/symphony/workflow/mutate.py`
  -> passed.
- Green required checks:
  `git diff --check` -> passed.

---

# 2026-07-04 - Hook and workspace preflight

## Decision

Keep masked `after_create` setup commands as doctor warnings, not failures.
Commands such as `pnpm install ... | tail -2 || true` can hide the real setup
error, but some workflows intentionally make non-critical setup best-effort.
`symphony doctor` therefore exits 0 when this is the only issue, while still
showing the exact `|| true` or `tail` command for an operator to inspect.
Placeholder clone URLs remain failures because they make every dispatch fail
deterministically.
When a workflow opts into `hooks.fail_on_warning_patterns: true`, those same
warning patterns become fatal doctor results.

Workspace reuse now records ownership in
`<workspace.root>/.symphony-workspace-owners/<workspace-key>.json`, outside the
ticket worktree. Existing workspace paths check that sidecar before any
refresh-policy `after_create` hook runs. If the recorded workflow/repo identity
differs from the current manager identity, dispatch stops with
`workspace owner mismatch` and the hook does not mutate the foreign workspace.
The identity now also records the file tracker's `board_root`, so two boards in
one repository cannot silently share a `TASK-*` workspace when they point at
different Kanban roots.

Hook execution now writes complete stdout/stderr plus a small JSON manifest under
`<workspace.root>/.symphony-workspace-hook-output/<workspace-key>/`. The console
and log messages stay truncated for readability, but failure messages include
the manifest path so operators can inspect the full setup output after a failed
`after_create` cleanup removes the ticket worktree. The manifest also records
known setup failure strings such as `PrismaConfigEnvError`, `Traceback`, and
`ModuleNotFoundError` when they appear in hook output.
Timed-out hooks write the same manifest path when subprocess timeout exposes no
complete output.

- Rejected: writing the marker inside a newly created ticket workspace before
  `after_create`. The default worktree hook expects an empty pre-created target,
  so internal marker files would break setup.
- Rejected: failing doctor on warning patterns by default. The safer rollout is
  visibility first; workflows that need fatal setup hygiene can opt into
  `hooks.fail_on_warning_patterns: true`.
- Rejected: blocking unmarked existing directories. Older workspaces do not have
  sidecars, so only an explicit foreign marker is treated as a collision.
- Rejected: storing hook evidence inside the ticket workspace. The highest-value
  case is a failed `after_create`, and that workspace is cleaned up by design.

## Verification

- Red checks:
  `pytest tests/test_doctor.py::test_after_create_warns_on_masked_install_output tests/test_doctor.py::test_after_create_warning_does_not_fail_by_default -q`
  -> 2 failed because `hooks.after_create` still reported `pass`.
- Red checks:
  `pytest tests/test_workspace.py::test_workspace_collision_blocks_before_after_create -q`
  -> failed because no owner check existed and the refresh hook ran.
- Green required checks:
  `pytest tests/test_doctor.py -k "hook or after_create" -q` -> 4 passed,
  29 deselected.
- Green required checks:
  `pytest tests/test_workspace.py -k "collision or after_create" -q` ->
  8 passed, 24 deselected.
- Green follow-up checks:
  `pytest tests/test_doctor.py -k "hook or after_create" -q` -> 5 passed,
  29 deselected.
- Green follow-up checks:
  `pytest tests/test_workspace.py -k "collision or after_create" -q` ->
  9 passed, 24 deselected.
- Green follow-up checks:
  `pytest tests/test_workflow.py -k "hooks_warning_policy" -q` -> 1 passed,
  52 deselected.
- Green follow-up checks:
  `python -m py_compile src/symphony/cli/doctor.py src/symphony/workspace.py src/symphony/workflow/config.py src/symphony/workflow/builder.py src/symphony/orchestrator/core.py`
  -> passed.
- Spec-review follow-up checks:
  `pytest tests/test_doctor.py -k "hook or after_create" -q` -> 6 passed,
  29 deselected.
- Spec-review follow-up checks:
  `pytest tests/test_workspace.py -k "collision or after_create or hook_output or hook_failure" -q`
  -> 11 passed, 24 deselected.
- Spec-review follow-up checks:
  `python -m py_compile src/symphony/cli/doctor.py src/symphony/workspace.py src/symphony/workflow/config.py src/symphony/workflow/builder.py src/symphony/orchestrator/core.py`
  -> passed.
- Spec-review follow-up checks:
  `git diff --check` -> passed.
- Timeout-artifact follow-up:
  `pytest tests/test_workspace.py -k "collision or after_create or hook_output or hook_failure or hook_timeout" -q`
  -> 12 passed, 24 deselected.

---

# 2026-07-04 - Contract-failure rewind scope

## Decision

Keep stage contracts strict and make failures specific. `ContractResult` now
carries structured `ContractFailure` rows alongside the backward-compatible
`missing` list. Verify evidence checks preserve Markdown table row numbers for
`## AC Scorecard` and `## Security Audit`, reject prose evidence such as
`validated in source`, reject placeholder cells such as `n/a` or `-`, and
render the expected durable artifact shape directly in the `## Contract
Failure` note.

Contract rewind prompts reuse the Task 3 compact prompt path. The newest
`## Contract Failure` section supplies the failing rows and expected artifact
pattern while stale historical logs and older failures are omitted. Contract
validation still reads the raw refreshed ticket body before prompt compaction,
so compact prompt text cannot hide missing required sections.
The same newest contract-failure rows now flow through `SYMPHONY_REWIND_SCOPE`
and the prompt `rewind_scope` field when `## Contract Failure` is newer than
any Review/QA failure section.
Contract failure notes render invalid evidence with a fence length that cannot
collide with evidence cells that already contain backticks, so the production
note round-trips into the parser that feeds rewind scope.

`_ticket_prompt_path` now tolerates tests and early orchestrator paths without a
tracker instance by returning no path instead of raising. File trackers with
`find_path` still populate `issue.full_ticket_path` normally.

- Rejected: accepting prose evidence as a convenience. Source notes belong
  inside a durable `docs/<ID>/qa/...` or `docs/<ID>/work/...` artifact; accepting
  prose would weaken the quality gate.
- Rejected: adding a standalone `symphony ticket-check` CLI in this task. The
  reliability win is the in-loop rewind note and prompt scope; CLI exposure can
  ship independently.
- Rejected: evaluating contracts against compacted prompt text. The validator is
  a quality gate over the full ticket, while compaction is only a backend prompt
  optimization.

## Verification

- Red check:
  `pytest tests/test_orchestrator_contracts.py::test_contract_failure_reports_expected_evidence_path_shape -q`
  -> failed because `validated in source` was silently accepted as no cited path.
- Red orchestration check:
  `pytest tests/test_orchestrator_phase_transition.py::test_contract_validation_uses_raw_ticket_body_when_prompt_compacted -q`
  -> failed first because `_ticket_prompt_path` assumed `_tracker` existed in
  the phase-transition harness.
- Green required checks:
  `pytest tests/test_orchestrator_contracts.py -k "evidence or contract" -q`
  -> 22 passed.
- Green required checks:
  `pytest tests/test_workflow_pipeline_prompt.py -k "contract" -q`
  -> 1 passed, 34 deselected.
- Green raw-body invariant:
  `pytest tests/test_orchestrator_phase_transition.py::test_contract_validation_uses_raw_ticket_body_when_prompt_compacted -q`
  -> 1 passed.
- Green rewind-scope follow-up:
  `pytest tests/test_orchestrator_dispatch.py -k "rewind_scope or contract_failure_scope" -q`
  -> 3 passed, 123 deselected.
- Green compile/whitespace:
  `python -m py_compile src/symphony/orchestrator/contracts.py src/symphony/orchestrator/core.py src/symphony/orchestrator/parsing.py src/symphony/orchestrator/constants.py src/symphony/prompt_context.py`
  -> passed.
- Green compile/whitespace:
  `git diff --check` -> passed.
- Strict-placeholder follow-up:
  `pytest tests/test_orchestrator_contracts.py -k "evidence or contract" -q`
  -> 23 passed.
- Strict-placeholder follow-up:
  `pytest tests/test_supergoal_hardening_loop.py -q` -> 3 passed.
- Strict-placeholder follow-up:
  `pytest tests/test_orchestrator_contract_integration.py -q` -> 3 passed.
- Quality-review follow-up:
  `pytest tests/test_orchestrator_contracts.py -k "round_trips or evidence or contract" -q`
  -> 24 passed.
- Quality-review follow-up:
  `pytest tests/test_orchestrator_dispatch.py -k "rewind_scope or contract_failure_scope" -q`
  -> 3 passed, 123 deselected.
- Quality-review follow-up:
  `pytest tests/test_workflow_pipeline_prompt.py -k "contract" -q` -> 1 passed,
  34 deselected.
- Quality-review follow-up:
  `pytest tests/test_orchestrator_phase_transition.py::test_contract_validation_uses_raw_ticket_body_when_prompt_compacted -q`
  -> 1 passed.
- Quality-review follow-up:
  `pytest tests/test_supergoal_hardening_loop.py tests/test_orchestrator_contract_integration.py -q`
  -> 6 passed.
- Quality-review follow-up:
  `python -m py_compile src/symphony/orchestrator/contracts.py src/symphony/orchestrator/core.py src/symphony/orchestrator/parsing.py src/symphony/orchestrator/constants.py src/symphony/prompt_context.py`
  -> passed.
- Quality-review follow-up:
  `git diff --check` -> passed.

---

# 2026-07-04 - Jira reliability verification pass

## Decision

Verify the token-reliability work with a copied Jira workflow instead of a live
OpenCode rerun. The copied workflow keeps the real board, prompt files, and
hook configuration, but uses an inert `Dry Run` active lane so web/API and
attention behavior can be tested without dispatching paid backend turns.

Rendered prompt-token measurement uses Symphony's prompt builder plus
`tiktoken` `o200k_base`. On the copied board's comparable `Verify` ticket,
`TASK-005`, compact issue context reduced the rendered first-turn prompt from
5,453 tokens to 3,944 tokens, saving 1,509 tokens or 27.7%. The copied board did
not contain an active `Learn` ticket, so no comparable `Learn` prompt was
measured in this pass. `TASK-001` in `Done` also showed the compaction selector
working, from 5,853 to 2,592 tokens, but it is not an active-stage comparison.

- Rejected: using `active_states: []` to neutralize dispatch in the copied
  workflow. The parser treats an empty list as default states, so the corrected
  smoke uses `active_states: [Dry Run]`.
- Rejected: spending a real OpenCode rerun only to prove API and prompt-render
  behavior. Unit and contract tests cover `Verify`, `Learn`, and strict evidence
  gates; the copied service smoke proves operator surfaces without model cost.

## Verification

- Broad targeted suite:
  `rtk pytest tests/test_tracker_file.py tests/test_orchestrator_dispatch.py tests/test_workflow.py tests/test_workflow_pipeline_prompt.py tests/test_prompt.py tests/test_prompt_context.py tests/test_backends.py tests/test_orchestrator_contracts.py tests/test_doctor.py tests/test_workspace.py tests/test_webapi.py tests/test_orchestrator_contract_integration.py tests/test_supergoal_hardening_loop.py -q`
  -> 512 passed.
- Copied workflow doctor:
  `PYTHONPATH=src python -m symphony.cli doctor /private/tmp/jira-symphony-rerun-u25y1U/WORKFLOW.md`
  -> exit 0, `hooks.after_create` warning includes `WORKFLOW.md:57`,
  `WORKFLOW.md:58`, and the masked setup commands.
- Copied workflow smoke:
  `PYTHONPATH=src python scripts/smoke_web_api.py --base-url http://127.0.0.1:10093 --prefix JSMOKE`
  -> 9 checks passed: health, state, board, static assets, issue create, issue
  detail, issue patch, refresh, workflow stats skills.
- No-dispatch proof:
  `GET /api/v1/state` on the copied service -> `running: 0`, `retrying: 0`,
  and all token totals 0 before and after smoke. `GET /api/v1/runs?limit=5`
  -> `{"runs": [], "count": 0}`.
- API safety-signal proof:
  a temporary `Dry Run` ticket with `## Dependencies` containing `TASK-999`
  returned issue attention `kind=blocked_dependency`, message
  `waiting on unresolved dependency: TASK-999`, while `/api/v1/state` still
  reported `running: 0`, `retrying: 0`, and token totals 0.
- Prompt measurement:
  copied `TASK-005` Verify prompt with `compact_issue_context=False` -> 5,453
  `o200k_base` tokens; with `compact_issue_context=True` -> 3,944 tokens.
- Full-suite regression follow-up:
  `rtk pytest -q` initially reported 1,106 passed, 2 failed, 2 skipped. The
  failing lifecycle e2e fixture still used `n/a` security evidence even though
  the stricter Verify contract now rejects placeholders. Updating the fixture to
  cite `qa/security.md` and create that fake artifact restored the stated test
  precondition without weakening contracts.
- Final lifecycle check:
  `rtk pytest tests/test_agent_lifecycle_e2e.py -q` -> 4 passed.
- Final full suite:
  `rtk pytest -q` -> 1,108 passed, 2 skipped.
