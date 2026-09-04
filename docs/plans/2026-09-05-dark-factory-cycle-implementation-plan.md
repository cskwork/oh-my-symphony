# Dark-factory cycle implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An operator describes software in Symphony chat, approves one intent card, and the deep pipeline delivers a verified result with no further human gate.

**Architecture:** Add a strict, server-owned intent proposal to chat (mirroring the project-setup proposal), a server-side filer that writes the request ticket and `.sdlc/work/<slug>/intent.md`, and prompt changes so Intake consumes the intent. Fix four open issues first, then pay down `core.py` and backend debt behind the full suite.

**Tech Stack:** Python 3.12, aiohttp, dataclasses, pytest (asyncio auto mode), vanilla JS SPA, ruff, pyright.

**Spec:** `docs/plans/2026-09-05-dark-factory-cycle-design.md`

## Global Constraints

- Python floor: `>=3.12` (pyproject). No new runtime dependencies.
- Gates before every commit: `.venv/bin/python -m ruff check src tests`,
  `.venv/bin/python scripts/check_i18n.py`, `.venv/bin/symphony-pyright`,
  and the relevant test files. Full suite before merge.
- Commit on the feature branch; `dev` receives a merge, `main` receives a
  merge from `dev`. Never commit directly on `main`.
- Preserve the `WORKFLOW.md` configuration shape. New keys are optional.
- Backends keep protocol details inside `src/symphony/backends/`.
- Every behavior change carries a regression test.
- Version bump to `0.22.0` in `pyproject.toml` and `src/symphony/__init__.py`
  together, as its own `chore(release)` commit.

---

## Track 0: lasting bugs

### Task 1: Atomic JSON writes with Windows retry, namespaced per workflow (#30, #32)

**Files:**
- Create: `src/symphony/utils/atomic_json.py`
- Modify: `src/symphony/orchestrator/core.py:3594-3630` (`_done_count_path`, `_persist_done_count`) and `:6157-6206` (`_token_ema_path`, `_persist_token_ema`)
- Test: `tests/test_persist_state.py`

**Interfaces:**
- Produces: `write_json_atomic(path: Path, payload: Any, *, attempts: int = 3, backoff_s: tuple[float, ...] = (0.01, 0.05)) -> None` and `state_file_name(workflow_path: Path, base: str) -> str` (returns `base + ".json"` for `WORKFLOW.md`, else `base + "." + sanitized_stem + ".json"`).

- [ ] Write failing tests: rename raises `PermissionError` twice then succeeds; `state_file_name(Path("/x/WORKFLOW.md"), "token_ema") == "token_ema.json"`; `state_file_name(Path("/x/WORKFLOW.demo.claude.md"), "token_ema") == "token_ema.WORKFLOW.demo.claude.json"`; two orchestrators with sibling workflow files in one directory persist to distinct files.
- [ ] Implement `atomic_json.py`; route both persist sites through it; use `state_file_name` in both path helpers.
- [ ] Run `tests/test_persist_state.py` and `tests/test_orchestrator_dispatch.py -k "ema or done_count"`; commit `fix(orchestrator): retry state renames and namespace state per workflow (#30, #32)`.

### Task 2: Per-dispatch env travels in `BackendInit` (#29)

**Files:**
- Modify: `src/symphony/backends/__init__.py:113-121` (`BackendInit.env: dict[str, str] = field(default_factory=dict)`)
- Modify: `src/symphony/backends/per_turn.py:304`, `pi.py:212`, `claude_code.py:201-214`, `codex.py:337` (merge `self._init_env` last)
- Modify: `src/symphony/orchestrator/core.py:6341-6383` (`_dispatch_env_for(...) -> dict[str, str]` returns instead of mutating), `:5941`, `:6874`, `:7906` (pass `env=`)
- Test: `tests/test_orchestrator_dispatch.py:9692-9740` (rewrite to assert the BackendInit env), `tests/test_backend_contract.py` (env reaches the subprocess)

- [ ] Failing test: two concurrent dispatches under `max_concurrent_agents: 2` produce backends whose `init.env["SYMPHONY_TOKEN_BUDGET"]` differ per state and `os.environ` has no `SYMPHONY_TOKEN_EMA`.
- [ ] Implement; keep the env var names; forward dispatch omits `SYMPHONY_REWIND_SCOPE`.
- [ ] Run the two test files; commit `fix(dispatch): pass per-dispatch env through BackendInit (#29)`.

### Task 3: Retry entries carry touched files for the conflict pre-check (#28)

**Files:**
- Modify: `src/symphony/orchestrator/entries.py:177-186` (`touched_files: frozenset[str] = frozenset()`)
- Modify: `src/symphony/orchestrator/core.py:10002` (caller passes `touched_files=frozenset(self._touched_files_for(issue))`), `:10050-10090` (`_install_retry(..., touched_files)`), `:6075-6090` (`_conflict_blocker` uses `retry_entry.touched_files`)
- Test: `tests/test_orchestrator_dispatch.py` (new test near the existing C1 conflict tests)

- [ ] Failing test: ticket A exits with a retry pending and declares `## Touched Files` `src/a.py`; candidate B declaring `src/a.py` is blocked with a `## Conflict` note naming A.
- [ ] Implement; commit `fix(dispatch): include retry-pending tickets in conflict pre-check (#28)`.

## Track A: chat intent gate

### Task 4: Intent proposal protocol in chat

**Files:**
- Modify: `src/symphony/chat.py` (new `IntentAction`, `_intent_spec`, `ChatSession.intent_actions`, `_record_agent_message`, `snapshot`, supersede/expire/prune helpers)
- Test: `tests/test_chat.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class IntentAction:
      action_id: str          # "intent-" + uuid hex
      slug: str               # ^[a-z0-9][a-z0-9-]{1,63}$
      title: str              # 1..200 chars
      track: str              # "full" | "micro"
      intent: str             # markdown, <= 32 KiB, must contain "## Problem", "## Success criteria", "## Out of scope" and one "- [ ]" line
      status: str = "pending" # pending | running | approved | failed | expired | superseded
      ticket: dict[str, Any] | None = None   # {"identifier", "state", "request", "path"}
      error: str | None = None
      expires_at: str = <now + 30 min>
      task: asyncio.Task | None
      def as_dict(self) -> dict[str, Any]
  def _intent_spec(text: str) -> tuple[str, IntentAction | None]
  ```
- Marker: `<symphony-intent>{"slug": ..., "title": ..., "track": ..., "intent": ...}</symphony-intent>`; exactly one open and one close tag; strict JSON object with exactly those four keys.
- Parsing runs in both `qa` and `edit` modes but only when `cfg.tracker.kind == "file"`.
- Events: `intent_action` (meta `{"intent": as_dict}`), `intent_status`, `intent_removed` (meta `{"intent_action_id"}`).
- Snapshot adds `"intent_actions": [...]`.
- An ordinary operator message marks pending proposals `superseded` and broadcasts `intent_status`.

- [ ] Failing tests: valid marker becomes an action and is stripped from the visible text; each invalid shape stays prose; QA mode accepts; non-file tracker ignores; ordinary reply supersedes; expiry flips to `expired`; at most 20 live actions.
- [ ] Implement; commit `feat(chat): parse server-owned intent proposals`.

### Task 5: Intent approval and the server-owned filer

**Files:**
- Create: `src/symphony/intent.py` (`IntentFiler` protocol, `file_intent_request`, `render_intent_markdown`, `intent_artifact_path`)
- Modify: `src/symphony/chat.py` (`ChatManager.__init__(..., intent_filer=None)`, `intent_for_reply`, `confirm_intent`, `_run_intent_approval`)
- Test: `tests/test_chat_intent_filer.py`, `tests/test_chat.py`

**Interfaces:**
- Produces:
  ```python
  class IntentFiler(Protocol):
      def __call__(self, cfg: ServiceConfig, action: IntentAction, *, approved_at: str, session_id: str) -> dict[str, Any]: ...
  def file_intent_request(cfg, action, *, approved_at, session_id) -> dict[str, Any]
      # state = "Intake" if an active state is Intake (case-insensitive) else cfg.tracker.active_states[0]
      # identifier = tracker.create_validated(identifier=None, prefix="REQ", title=action.title, state=state, description=<ticket body>, request=action.slug)
      # ticket body = action.intent + "\n\n## Track\n\n" + track + "\n\n## Approval\n\n- Approved at: ...\n- Chat session: ...\n- Intent artifact: .sdlc/work/<slug>/intent.md\n"
      # writes <workflow_dir>/.sdlc/work/<slug>/intent.md = "# Intent: <slug>\n\n- Date: ...\n- Track: ...\n- Requested by: chat session <id>\n\n" + action.intent + "\n\n## Approval\n\n- Approved at: ...\n- Ticket: <identifier>\n"
      # raises ChatIntentActionError for non-file trackers
  ChatManager.intent_for_reply(text: str, session_id: str | None) -> IntentAction | None
      # "approve" (case-insensitive, surrounding whitespace) when exactly one pending unexpired action exists; "approve <slug>" selects by slug
  async ChatManager.confirm_intent(action_id, session_id=None, confirmation_token=None) -> dict[str, Any]
      # same token/expiry/idempotency contract as confirm_project_setup; runs the filer in a thread; on success status "approved", ticket set, request_refresh() called
  ```
- Errors: `ChatIntentActionError(ChatError)` code `chat_intent_action_invalid`; `ChatIntentAuthorizationError` code `chat_intent_confirmation_forbidden` (add to `src/symphony/errors.py`).

- [ ] Failing tests as listed in the design's Testing section.
- [ ] Implement; commit `feat(chat): approve an intent into a request ticket and intent.md`.

### Task 6: Web API route and `approve` reply

**Files:**
- Modify: `src/symphony/webapi.py` (`_confirm_intent`, `handle_chat_intent_approve`, `_send` reply routing, route registration next to project-setup)
- Test: `tests/test_webapi_chat_intent.py` (model on the existing chat webapi tests)

- [ ] Failing tests: 403 without header, 404 unknown action, 409 expired, 200 with `{"action": {...}}`; `approve` reply hits the same path; non-matching text still posts a message.
- [ ] Implement; commit `feat(webapi): intent approval endpoint`.

### Task 7: Chat preamble teaches the intent protocol

**Files:**
- Modify: `src/symphony/chat.py:123-210` (`QA_PREAMBLE`, `EDIT_PREAMBLE`, `_BOARD_PREAMBLE`, `_DEFAULT_ROUTING`, `_DEEP_ROUTING`, new `_INTENT_PROTOCOL`)
- Test: `tests/test_chat.py:265, 607, 1776, 1783`

Preamble rules (verbatim intent):
- Questions: answer. Software requests: at most two clarifying turns, only if genuinely ambiguous, then emit exactly one intent marker after the human summary.
- The intent body uses the headings `## Problem`, `## Evidence`, `## Success criteria` (checkbox lines, observable), `## Out of scope`, `## Constraints`, `## Open questions`. `track` is `micro` only when files and symbols are known and success is checkable by an existing command.
- Never file the request ticket yourself; the operator approves the card and the server files it. `symphony board new/update` remains available in edit mode only for operator-directed edits to existing tickets.
- Deep boards: the pipeline decomposes. Default boards: Todo triage routes.

- [ ] Update tests, then the strings; commit `feat(chat): intent-first board preamble`.

### Task 8: Web UI intent card (delegated)

**Files:**
- Modify: `src/symphony/web/static/app.js` (state `intentActions`, `rememberChatIntent`, `renderChatIntentAction`, `buildChatIntentNode`, `reconcileChatIntentActions`, socket handlers for the three events, `api.approveChatIntent`), `src/symphony/web/static/i18n.js` (en + ko keys `chat.intent*`), `src/symphony/web/static/style.css` (`.chat-intent*`)
- Test: `.venv/bin/python scripts/check_i18n.py`; `tests/test_landing_page_contract.py` stays green

Card contract: heading `t('chat.intentTitle')` with title, `code` slug, chip for track, `details` element with the intent markdown rendered via `renderMarkdown`, status chip (pending / approving / approved with ticket id / failed / expired / superseded), Approve button enabled only when pending or failed, unexpired, and a confirmation token exists.

- [ ] Implement; commit `feat(web): intent approval card in chat`.

### Task 9: Deep prompts consume the intent

**Files:**
- Modify: `docs/symphony-prompts/file/deep/intake.md`, `docs/symphony-prompts/file/deep/base.md`
- Test: `tests/test_deep_preset_e2e.py` (ticket produced by `file_intent_request` walks Intake to Done), `tests/test_prompt.py` (anchor grep for `## Track` handling)

Intake changes: read `## Problem` .. `## Open questions` from the description when present and copy them into `brief.md` without re-asking; `Track: micro` sets state to `Plan` directly; `Track: full` sets `Research`.

- [ ] Update tests, prompts; commit `feat(prompts): intake consumes the approved intent`.

### Task 10: Project setup proposal accepts a lane preset

**Files:**
- Modify: `src/symphony/chat.py:403-470` (optional `preset` key, values `default`/`deep`; `ProjectSetupAction.preset`), `src/symphony/webapi.py` (`_create_or_adopt_registered_project(..., preset)`) and `src/symphony/projects.py` (apply `apply_lane_preset(workflow_path, preset)` after bootstrap when `preset != "default"`)
- Test: `tests/test_chat.py`, `tests/test_projects.py`

- [ ] Failing tests: marker with `"preset": "deep"` parses; unknown preset stays prose; creator receives `preset`; created project's WORKFLOW.md has the deep lanes.
- [ ] Implement; commit `feat(projects): chat can create deep-preset projects`.

### Task 11: Documentation and release metadata

**Files:**
- Modify: `CONTEXT.md` (three terms), `README.md` + `README.ko.md` (section "Chat to delivery"), `docs/PIPELINE.md` (intent gate paragraph), `skills/symphony-skill/reference/operations.md` (approve flow), `CHANGELOG.md` (Unreleased → 0.22.0), `pyproject.toml`, `src/symphony/__init__.py`, `HANDOFF.md` (refresh)

- [ ] Write; run `tests/test_package_metadata.py`; commit docs, then `chore(release): prepare 0.22.0`.

## Track C: debt

### Task 12: Extract the worker-exit state machine (delegated)

**Files:**
- Create: `src/symphony/orchestrator/worker_exit.py`
- Modify: `src/symphony/orchestrator/core.py:9284-9910` (`_on_worker_exit_impl` becomes a thin call), plus the helpers it alone uses

Rules: behavior-preserving move; public names on `Orchestrator` unchanged; tests monkeypatch module-level names in `core` (see `tests/test_orchestrator_dispatch.py`), so re-export any moved name from `core` under its old name; full suite green; `core.py` shrinks by at least 500 lines.

- [ ] Implement; commit `refactor(orchestrator): move worker-exit state machine to worker_exit.py`.

### Task 13: claude and pi backends on `PerTurnCliBackend` (delegated)

**Files:**
- Modify: `src/symphony/backends/claude_code.py`, `src/symphony/backends/pi.py` (and `prime_agent.py`, which subclasses `PiBackend`)
- Test: `tests/test_backends.py`, `tests/test_backend_contract.py`, `tests/test_backends_edges.py`, `tests/test_backends_lifecycle.py`, `tests/test_claude_cache_tokens.py`, `tests/test_prime_agent_terminal_completion.py`

Rules: keep every emitted event and token field identical; use the opencode `_read_stdout` pattern; delete duplicated helpers only when unused afterwards.

- [ ] Implement; commit `refactor(backends): claude and pi share PerTurnCliBackend`.

### Task 14: Split `_run_agent_attempt` by phase (delegated, bounded)

**Files:**
- Modify: `src/symphony/orchestrator/core.py:6774-7608`

Rules: extract startup (workspace + backend + lease), turn loop, and cleanup into private coroutines with explicit inputs and outputs; no ordering change; full suite green. Stop and report if the suite cannot be kept green within the step.

- [ ] Implement; commit `refactor(orchestrator): split _run_agent_attempt into phases`.

## Track O: optimization

### Task 15: Parallel test suite

**Files:**
- Modify: `pyproject.toml` (dev extra `pytest-xdist>=3.5`), `.github/workflows/tests.yml`, `CONTRIBUTING.md`

- [ ] Run `.venv/bin/python -m pytest -q -n auto`; adopt only if the suite is isolation-clean; otherwise record the offending tests in HANDOFF.md and skip.

## Finish

- [ ] Full gates; merge `feat/dark-factory-cycle` into `dev`; merge `dev` into `main`; push both; close #58 with the fixing commit; comment on #31 with the current-code analysis.
