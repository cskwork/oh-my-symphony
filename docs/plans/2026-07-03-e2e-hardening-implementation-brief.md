# Implementation brief: four-agent E2E production hardening

Date: 2026-07-03. Branch: `feat/e2e-production-hardening` (already checked out).
Parent plan: `docs/plans/2026-07-03-four-agent-todo-e2e-production-hardening.md`
(read it for context, but THIS brief overrides it where they differ).

## Operator policy override (IMPORTANT)

The parent plan proposed auto-DENYING unattended Codex approval prompts.
The operator has overridden that decision:

> Allow full permission for all agents for any tool use. Only dangerous
> commands like delete should be blocked; else allow all.

So every approval request must be answered AFFIRMATIVELY, automatically,
without human input — except commands matching a small destructive-command
denylist, which are declined (not aborted) so the agent can continue the turn.

## Ground rules

- Python 3.11+. Run the full suite with `python3 -m pytest -q` (or the repo
  venv if present). Establish a baseline FIRST; do not fix pre-existing
  failures unrelated to this work, but report them.
- Never call raw `proc.wait()` on asyncio subprocesses — use
  `symphony._shell.safe_proc_wait` (asyncio child-watcher hang under
  Textual+3.12 on macOS).
- Do NOT touch the two-stage stall force-eject logic
  (`STALL_FORCE_EJECT_GRACE_S`, `_reconcile_running` grace window).
- Do NOT bump the version (pyproject.toml / src/symphony/__init__.py stay).
- Match existing code style. No emojis anywhere. Comments state constraints,
  not narration.
- `tests/test_prompt.py` and the workflow pipeline prompt tests pin byte-exact
  anchors from prompt templates — when you edit stage templates, update those
  tests deliberately in the same commit and audit old-vs-new for rule drops.
- Commit after each work item passes tests, on the current branch, using
  conventional commit messages (`fix:`, `feat:`, `test:`, `docs:`). Do NOT
  push. Do NOT merge to main/dev.
- Append a decision log (including rejected alternatives) to
  `docs/changelog/changelog-2026-07-03.md`.

---

## Work item 1 (P0) — Codex approval auto-responder

### Problem

`src/symphony/backends/codex.py` `_stdout_reader` (~line 608) only resolves
JSON-RPC messages whose `id` is in `self._pending` (responses to OUR
requests). Server-initiated REQUESTS — messages that carry BOTH `id` and
`method` — fall through to `_handle_notification`, which never writes a
response. Codex app-server (0.142.x) then blocks forever awaiting the reply.
Observed live: ticket E2E-104 sat on
`Allow codebase-memory-mcp MCP server to run tool "index_status"?` until the
stall watchdog cancelled it, looping through retries.

### Protocol facts (verified against openai/codex@main app-server-protocol)

Reference copies of the upstream Rust protocol sources are saved at
`/private/tmp/claude-501/-Users-danny-Documents-PARA-Resource-symphony-multi-agent/59f13f2c-d8ca-48cf-bc96-7f77bdeadf17/scratchpad/codex-v2-*.rs`
(item.rs, mcp.rs, permissions.rs, turn.rs, shared.rs). Consult them for exact
shapes. Summary:

Server → client requests (all camelCase, `{"id": ..., "method": ..., "params": {...}}`;
respond with `{"id": <same>, "result": {...}}`):

| method | respond with |
|---|---|
| `item/commandExecution/requestApproval` | `{"decision": "accept"}` or `{"decision": "decline"}`. Params include optional `command` (string), `cwd`, `reason`. Decision enum: `accept`, `acceptForSession`, `decline`, `cancel` (+ amendment variants — do not use). `decline` lets the agent continue the turn; `cancel` interrupts it. Use `decline` for dangerous commands. |
| `item/fileChange/requestApproval` | `{"decision": "accept"}` always (sandbox already scopes writes). Enum: accept/acceptForSession/decline/cancel. |
| `mcpServer/elicitation/request` | `{"action": "accept", "content": {}}`. Action enum: `accept`, `decline`, `cancel`. `content` is nullable; `{}` is the safest accept payload. |
| `item/tool/requestUserInput` (experimental) | `{"answers": {<question_id>: {"answers": []}}}` for each entry in `params.questions` (each question has an id field — check ToolRequestUserInputQuestion in the reference copy). |
| `item/permissions/requestApproval` | Grant what was requested: `{"permissions": <GrantedPermissionProfile mirroring params.permissions>, "scope": "session"}`. Check `codex-v2-permissions.rs` for RequestPermissionProfile vs GrantedPermissionProfile field mapping before implementing. |
| legacy `execCommandApproval` (v1) | `{"decision": "approved"}` / `{"decision": "denied"}`. `params.command` may be a list of argv strings — join for classification. |
| legacy `applyPatchApproval` (v1) | `{"decision": "approved"}`. |
| anything else with an `id` | JSON-RPC error response `{"id": ..., "error": {"code": -32601, "message": "unsupported server request: <method>"}}` — codex must NEVER be left hanging on an unanswered request. |

### Implementation

1. In `_stdout_reader`: after the existing `_pending` check, detect
   server-initiated requests (`"id" in msg and msg.get("method")`) and route
   to a new `async def _handle_server_request(self, msg)`. Everything else
   keeps flowing to `_handle_notification`.
2. Response writing: reuse the stdin write pattern from `_request`
   (`self._process.stdin.write(...)` + `drain()`), factored so requests and
   responses share one locked writer path if a lock exists; if `_request`
   writes unlocked today, keep the same discipline (single reader task calls
   `_handle_server_request` sequentially, so no new lock is strictly needed —
   document that assumption in a comment).
3. Dangerous-command classifier: new module
   `src/symphony/backends/approval_policy.py`:
   - `def dangerous_command_reason(command: str) -> str | None`
   - Returns a human-readable reason when the command matches the denylist,
     else None. Scan the WHOLE string (commands arrive as shell strings that
     may chain with `&&`, `;`, `|`).
   - Denylist (keep tight — the policy is allow-by-default):
     - `rm` with recursive+force flags in any order/combination
       (`-rf`, `-fr`, `-r -f`, `-Rf`, `--recursive --force`, etc.)
     - any `sudo` invocation
     - `mkfs` family
     - `dd` with `of=/dev/...`
     - `shred`
     - `find ... -delete`
     - `git clean` with `-f` AND `-x` together (`-fdx` style; plain
       `git clean -fd` stays allowed)
   - Unit-test the classifier exhaustively, including negatives
     (`rm -f single-file`, `rm -r dir` alone, `grep sudo`, `echo "rm -rf"` is
     a false positive we ACCEPT — document why: quoting-aware shell parsing
     is not worth the complexity for a safety denylist).
4. Events: add `EVENT_APPROVAL_DENIED = "approval_denied"` to
   `src/symphony/backends/__init__.py`. Emit:
   - `EVENT_APPROVAL_AUTO_APPROVED` with payload `{method, command|tool|message preview}`
     on every affirmative answer;
   - `EVENT_APPROVAL_DENIED` with payload `{method, command, reason}` on
     denials.
5. Orchestrator: in `src/symphony/orchestrator/core.py` `_on_codex_event`,
   handle `approval_denied`: set
   `debug.last_error = f"approval denied: {reason} ({command})"` and
   `log.warning("approval_denied", ...)` so it shows in `/api/v1/state`.
   Approval events already count as progress for codex (default
   `is_progress_event` returns True) — leave that.
6. Rework `_handle_approval` / `_handle_tool_call` legacy notification
   handlers to stay as-is for notifications, but the new request path
   supersedes them for anything carrying an `id`.

### Tests

New `tests/test_codex_approvals.py` (or extend `tests/test_backends_edges.py`
following its existing fake-process pattern):
- feed each request type through the reader; assert exact JSON written to
  stdin (id echo, result shape);
- dangerous command → decline/denied + EVENT_APPROVAL_DENIED emitted;
- unknown method with id → error response, no hang;
- classifier unit tests.

Acceptance: an unattended MCP/tool/exec approval prompt never stalls a turn;
denials surface in `last_error` within one turn.

---

## Work item 2 (P0) — Empty-response pause → operator-ready attention

### Current state

`EMPTY_TURN_LOOP_THRESHOLD` breach (core.py ~3024-3080) already auto-pauses
via `_paused_issue_ids` + `_set_issue_flags(paused=True)`, and
`_rehydrate_issue_flags` restores `paused` on restart. What is missing:
- WHY it is paused is not persisted or surfaced — `issue_attention()`
  (core.py ~795) never reports paused tickets, so a non-running paused ticket
  looks idle on the board.

### Implementation

1. Registry (`src/symphony/orchestrator/run_registry.py`): add nullable TEXT
   column `pause_reason` to `issue_flags` with a lenient migration
   (`ALTER TABLE ... ADD COLUMN`, swallow duplicate-column errors — follow
   whatever migration idiom the file already uses). Thread it through
   `set_issue_flags` / `get_issue_flags` / `list_issue_flags` / `IssueFlags`.
2. Orchestrator: `self._pause_reasons: dict[str, str]`.
   - empty-loop auto-pause sets reason
     `"empty_response_loop: N consecutive empty turns (threshold T); resume via resume_worker after inspecting the ticket"`
     and persists it with the paused flag.
   - `pause_worker` (operator manual) sets reason `"operator pause"` unless a
     reason param is given.
   - `resume_worker` clears the reason (memory + registry).
   - `_rehydrate_issue_flags` restores reasons.
3. `issue_attention`: add a branch — if `issue.id in self._paused_issue_ids`,
   return `_attention_signal("paused", "Paused", reason-or-default,
   "warning")`. Place it AFTER the stalled/lease checks (a stalled worker is
   more urgent) and BEFORE budget/tracker/retry.

### Tests

- pause → attention payload appears for a non-running issue;
- empty-loop trip persists reason; a fresh Orchestrator rehydrating the same
  registry restores paused + reason;
- resume clears both.

---

## Work item 3 (P0) — No-stage-change watchdog

### Problem

A worker (observed: Pi) can keep producing turns/files indefinitely in the
same stage without ever editing the ticket state — burning ~1.49M tokens
while looking "productive". Existing floors (`max_turns`/attempt,
`max_total_turns`, token caps) are per-attempt or unset-by-default; nothing
detects "turns keep completing but the STAGE never changes".

### Implementation

1. Config: add `agent.max_state_turns: int` to
   `src/symphony/workflow/config.py` (+ builder/coercion parsing, mirroring
   how `max_total_turns` is wired). Default `30`; `0` disables. Meaning:
   maximum completed turns while the ticket stays in one state (cumulative
   across attempts/continuations, reset on any state change).
2. Tracking: `_IssueDebug` gains `state_turn_state: str = ""` and
   `state_turn_count: int = 0`. In the worker loop after the post-turn state
   refresh (core.py ~2519-2540), compare `normalize_state(issue.state)`:
   same state → increment; different → reset to the new state with count 0.
   Also reset in the phase-transition branch so rewinds start fresh.
3. Breach handling (worker loop, right where `max_turns` is checked ~2541):
   when `max_state_turns > 0` and `state_turn_count >= max_state_turns`:
   - `log.warning("no_stage_change_watchdog", ...)` with counts;
   - set a new `entry.hit_no_stage_change = True`; break the loop.
   In `_on_worker_exit_impl` `reason == "normal"` handling, mirror the
   `hit_max_turns` branch: no continuation retry; add to `_claimed`;
   `debug.last_error = "no stage change after N turns in <state> — operator action required"`;
   call `_persist_budget_exhausted_state(budget_kind="no_stage_change",
   target_state=...)` — extend that function's `budget_detail` branches and
   note text accordingly. Also auto-pause with a pause reason (reuse work
   item 2 plumbing) so dispatch cannot restart it even when
   `budget_exhausted_state` is unset.
4. Optional explicit policy: `agent.no_stage_change_action: str` with values
   `"block"` (default) or a state name (e.g. `"Verify"`). When a state name
   is configured, instead of blocking, move the ticket to that state and
   append a handoff note titled `Stage Watchdog Handoff` explaining the
   forced transition. Default stays conservative (`block`). Validate the
   value at config-build time.

### Tests

- fake backend completing turns without state change → watchdog trips exactly
  at threshold, no continuation scheduled, ticket paused, note appended;
- state change mid-run resets the counter;
- `no_stage_change_action: Verify` → ticket moved to Verify with handoff
  note;
- `max_state_turns: 0` → disabled.

---

## Work item 4 (P1) — Serialize after_create git critical section

### Problem

Four concurrent `after_create` hooks race inside the HOST repo:
`git worktree remove/prune/add` plus `git config extensions.worktreeConfig true`
(a SHARED `.git/config` write) collide → `could not lock config file .git/config: File exists`.
Hooks run concurrently via `asyncio.to_thread` (`src/symphony/workspace.py`).

### Implementation

Edit `scripts/symphony-setup-worktree.sh`:
1. Acquire an exclusive lock BEFORE `git worktree remove` and hold it through
   the `git config --worktree symphony.*` writes (the `extensions.worktreeConfig true`
   write mutates the shared host config; the `--worktree` writes touch
   `.git/worktrees/<ID>/` admin space that `prune`/`add` from siblings can
   race).
2. Lock strategy — macOS is the primary platform and ships NO `flock(1)`:
   - if `command -v flock` → `flock` on `"$HOST_REPO/.git/symphony-worktree.lock"`
     (fd-based, e.g. `exec 9>lockfile; flock 9`);
   - else mkdir spin lock `"$HOST_REPO/.git/symphony-worktree.lock.d"`:
     0.2s sleep loop, 120s timeout (then fail loudly — the dispatcher retry
     handles it), stale-lock breaking when the dir mtime is older than 300s;
     write `$$` into the lock dir for diagnosability.
   - release via an EXIT trap so `set -e` failures cannot leak the lock.
3. Keep everything else byte-identical — the Windows junction logic and venv
   priming stay outside the lock.

### Tests

Add a shell-driven pytest (follow the pattern of existing workspace/hook
tests if one runs the real script; otherwise a new test that creates a temp
git repo, then launches 4 concurrent invocations of the script with distinct
`ISSUE_ID` workspaces via subprocess) asserting: all 4 worktrees exist, zero
non-zero exits, no `could not lock config file` in stderr. Mark it
POSIX-only (`pytest.mark.skipif(os.name != "posix")`).

---

## Work item 5 (P1/P2) — Browser QA Verify gate + telemetry

### 5a. Verify prompt

`docs/symphony-prompts/file/stages/verify.md` (and the linear variant if it
has the same section): strengthen step 4 (real acceptance checks) with a
browser-app rule:

> If the deliverable is a browser UI (todo app, web page, anything with DOM
> behavior), QA MUST drive a real browser (Playwright or headless Chromium)
> against `file://` or a tiny local static server, covering the core user
> flows (e.g. add, toggle, edit-cancel via Escape, delete, filter, reload
> persistence). Generated DOM shims / zero-dependency harnesses count as
> smoke tests only — they can NEVER be the final Verify authority. If browser
> dependencies are unavailable in the environment, record an explicit
> `## Environment Block` note with what is missing and set state to
> `Blocked` — do not pass QA on shim evidence alone.

Check the template tests that pin anchors (`tests/test_prompt.py`,
`tests/test_workflow_pipeline_prompt.py` if present) and update them in the
same commit. Keep wording compact — these templates are token-budgeted.

### 5b. Telemetry

- Verify `/api/v1/state` running rows carry `agent_kind`, `session_id`,
  `attention`, token totals for opencode/pi/claude/codex alike; fill gaps if
  a backend never populates `latest_usage` (report what you find; only make
  minimal normalization fixes, no refactors).
- Add `tokens_per_completed_stage` to the stats surface ONLY if it falls out
  naturally from existing `codex_state_total_tokens` bookkeeping (a small
  addition to `_running_row` or stats.jsonl record); skip otherwise and note
  the decision in the changelog.

---

## Final verification (run all, report results verbatim)

1. `python3 -m pytest -q` — full suite green (minus documented pre-existing
   failures, if any).
2. `python3 -m pytest tests/test_codex_approvals.py tests/test_run_registry.py tests/test_orchestrator_dispatch.py -q`.
3. `bash -n scripts/symphony-setup-worktree.sh` + the new concurrency test.
4. `symphony doctor` still passes on a sample WORKFLOW.md if a fixture
   exists in tests (optional).
5. Summarize per work item: what changed, files touched, test evidence.
