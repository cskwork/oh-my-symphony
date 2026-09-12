# Session Handoff — 2026-09-12 (improvement pass on the SDLC cycle)

## What landed on `feat/sdlc-cycle-hardening` (from `dev`)

Nine items, one commit each, all gates green at every step (ruff, pyright
0 errors, i18n, focused suites; full suite serial + `-n auto` at the end —
see the last section).

| # | Change | Where |
|---|--------|-------|
| 1 | Empty `auto_merge_target_branch` resolves to the current branch in the release binder (2026-09-05 item 0) | `release_contracts.resolve_configured_target_branch`, tests in `test_release_contracts.py` |
| 2 | Deep preset has a mechanical contract set (vault file + verdict line + ticket section); **the move into Done is contract-gated on every preset** — it never was, terminal transitions skipped the phase handler | `contracts.py` (`_evaluate_deep_contract`, `contract_producing_states`), `core._enforce_stage_contract`, `attempt._post_turn_refresh`, `presets.board_uses_shipped_contracts` |
| 3 | `agent.max_reopens` (default 3) parks a ticket that keeps returning from Done; `## Reopen Approved` buys one cycle | `core._hold_reopened_ticket_over_budget`, config/builder, WORKFLOW comments |
| 4 | Trip-wire scan on the chat intent card; hits land on the card, `## Trip-wires` on the ticket and `intent.md`, force `full` track; deep Review treats hits as objection candidates | `intent.py` (`scan_tripwires`), `app.js`/`i18n.js`, `deep/review.md` |
| 5 | `agent.fallback_kinds`: quota/usage-limit exits re-pin the file-board ticket to the next backend and retry instead of pausing | `worker_exit._switch_backend_on_quota_error`, `core._pin_fallback_agent_kind`, `FileBoardTracker.record_agent_kind(force=)` |
| 6 | `symphony doctor` runs `<agent> --version` after the PATH lookup (warn on non-zero, fail on exec error) | `cli/doctor._probe_agent_binary` |
| 7 | Learning loop: `gate` stats events, rewinds derived from lane order, per-lane counters + top contract misses on `/api/v1/stats`, web Stats, TUI; Document lane receives `{{ board_health }}` | `stats.py`, `core._record_stats_gate`/`_board_health_for_prompt`, `prompt.py`, the three `document.md` prompts |
| 8 | sdlc-kit re-seeded at v0.8.0: `.gitignore` set, `memory/POLICY.md`, closed feature archived to `.sdlc/archive/`, evidence/approvals untracked (files stay on disk) | `.sdlc/`, `.gitignore` |
| 9 | `_enforce_app_release_transition_inner` extracted to `release_transition.py` (core 9822 → 9680 lines); the two `-n auto` load flakes given realistic waits | `orchestrator/release_transition.py`, `test_workspace.py`, `test_orchestrator_release_contract_integration.py` |

Not done from the 2026-09-05 list, still open (see the rewritten list at the
bottom): `_on_tick` / `_reconcile_one` extractions, Linear/Jira intent gate
(neither adapter has an issue-create API yet), win32 `process_identity()`,
the deferred UX items, #31 codex sandbox repro.

Verify-before-trusting notes for the next session:

- Item 2 changes behaviour on existing default boards: `Document -> Done`
  now requires `## Wiki Updates` plus a completion record, and
  `artifacts.require_for_done` finally fires. A board whose Document prompt
  was customised to skip those sections will start rewinding — that is the
  gate working, but say so to the operator.
- Item 3 counts prior Done runs in `state.db`; a board migrated from an
  older Symphony has no history, so the cap starts counting from now.
- Item 5 only re-pins file boards. Remote trackers keep the pause; the
  quota marker list (`core._QUOTA_WORKER_ERROR_MARKERS`) is heuristic and
  was derived from the 2026-09-05 log strings, not from every backend's
  actual wording — extend it when a real quota exit slips through to
  `worker_error_auto_paused`.

---

# Session Handoff — 2026-09-05

## Where things stand

Symphony (`oh-my-symphony`) gained its **dark-factory cycle** on branch
`feat/dark-factory-cycle` (from `dev`): a software request typed into the
web chat becomes a strict intent card, the operator approves once, and the
server files the request ticket into the pipeline. That approval is the only
human gate; the deep preset does the rest. Design and plan live in
`docs/plans/2026-09-05-dark-factory-cycle-design.md` and
`docs/plans/2026-09-05-dark-factory-cycle-implementation-plan.md`.

Every gate is green on the branch:

- ruff ✓ · pyright 0 errors ✓ · i18n 566 keys (en/ko) ✓
- Full pytest suite **2476 passed, 14 skipped** (baseline before the branch:
  2415 passed, 14 skipped)
- Live run-path check: `symphony service start` on a scratch project, a real
  `claude` chat session in Q&A mode produced the intent card from one
  request, the bare `approve` reply filed `REQ-1` (`request: python-todo-cli`)
  in `Todo` and wrote `.sdlc/work/python-todo-cli/intent.md` with its
  `## Approval` section.

## What the branch did

| Area | Content |
|---|---|
| Intent gate | `src/symphony/intent.py` (marker parser, `IntentAction`, `file_intent_request`), `chat.py` (`confirm_intent`, `intent_for_reply`, supersede/expiry/prune, agent notice), `webapi.py` (`POST /api/v1/chat/sessions/{sid}/intent/{action_id}/approve`, `approve` reply routing), web card (`app.js`, `i18n.js`, `style.css`) |
| Pipeline | `_BOARD_PREAMBLE` rewritten around `_INTENT_PROTOCOL`; deep `intake.md` consumes the intent (`Track: micro` → Plan); project-setup proposals accept `preset: deep` and `projects.create_or_adopt_project(preset=)` applies it before the initial commit |
| Bugs | #29 per-dispatch env in `BackendInit.env`; #28 `RetryEntry.touched_files` snapshot for the conflict pre-check; #30 `utils/atomic_json.py` rename retry; #32 per-workflow state file names |
| Debt | `orchestrator/worker_exit.py` (worker-exit state machine, 1009 lines) and `orchestrator/attempt.py` (`_run_agent_attempt` phases, 1095 lines) extracted from `core.py` (11349 → 9815 lines); claude and pi backends now subclass `per_turn.JsonlStreamBackend` (claude_code.py 503 → 267, pi.py 666 → 386) |
| Docs | README (en/ko) "Chat intake" section, `CONTEXT.md` (Intent proposal, Intent approval, Track), `docs/PIPELINE.md` intent gate, `skills/symphony-skill/reference/operations.md`, `docs/architecture.md` module table, CHANGELOG |

## End-to-end run (2026-09-05, scratch project, real agents)

Scratch registry: `SYMPHONY_PROJECTS_FILE=/tmp/symphony-intent-smoke.ntna/projects.json`;
the deep project lives at `/tmp/symphony-intent-smoke.ntna/todo-app`
(service port 9999, log `log/symphony.log`, stop with
`symphony service stop ./WORKFLOW.md` from that directory). The pipeline was
still finishing when this handoff was written; check the board there.

| Step | What happened | Evidence |
|---|---|---|
| Deep project from chat | On the default smoke board, an edit-mode codex chat proposed a separate project with `preset: deep`; one confirmation created and registered `todo-cli` with the eight lanes | `e2e-step2-codex.log`, `projects.json` |
| Intent gate | On the new board, a Q&A codex chat produced the intent card from one request; a bare `approve` reply filed `REQ-1` in `Intake` with `request: python-cli-todo` and wrote `.sdlc/work/python-cli-todo/intent.md` | `e2e-step3.log`, `kanban/REQ-1.md` |
| Request lanes | `REQ-1` walked Intake → Research → Plan → Review → Done with no operator question; Review passed; the branch merged (`auto_merge_completed`) | `log/symphony.log` |
| Decomposition | Plan spawned `BUILD-1..3`, `QA-1`, `VERIFY-1`, `DOCUMENT-1` and wrote `docs/req/python-cli-todo/{brief,research,plan,contracts,review}.md` plus `release-contract.yaml` | scratch `main` |
| Builds | `BUILD-1`, `BUILD-2`, `BUILD-3` each reached Done and merged in order; `QA-1` reached Done at 23:10 UTC; `VERIFY-1` and `DOCUMENT-1` were still pending | monitor log |
| Delivered app | `git archive main` of the scratch project: `todo.py` (`$TODO_FILE` override, JSON next to the script), `tests/test_{storage,cli,readme}.py`, README; `pytest -q` → 26 passed; `todo.py add "buy milk"` then `list` prints `[ ] 1. buy milk` | export run at 23:09 UTC |

Findings from the run (not code defects in Symphony unless noted):

- The claude CLI hit the account's session limit; the chat turn failed
  cleanly (`turn_failed` in the transcript) and no card was produced.
- codex hit its usage limit mid-Plan; the worker was auto-paused
  (`worker_error_auto_paused`). Recovery that worked: edit
  `agent.kind` in `WORKFLOW.md` **and** the ticket's own `agent.kind`
  (the file tracker stamps the dispatched kind on the ticket and it
  overrides the board default), then `POST /api/v1/REQ-1/resume`.
  `WORKFLOW.md` edits are picked up live; no restart was needed.
- `~/.opencode/bin/opencode` on this host is a symlink to a broken npm
  postinstall stub; the real 1.18.18 binary is `~/.opencode/bin/opencode1`.
  The scratch workflow uses that absolute path in `opencode.command`.
  Product follow-up worth considering: `symphony doctor` could run
  `<agent> --version` for the configured kind and flag a non-zero exit.
- Wall clock: intent approval 22:29 UTC → REQ-1 Done 22:59 → three builds
  merged by 23:07 → QA Done 23:10, with ~20 minutes lost to the two quota
  failures.

## Open questions / next steps (rewritten 2026-09-12)

0. ~~Deep boards from the example workflow cannot dispatch the app-release
   verifier~~ — **fixed** (`resolve_configured_target_branch`, regression
   tests for empty and detached-HEAD targets).
1. **#31 codex sandbox vs symlinked board files.** Unchanged: needs a live
   codex run against a symlinked file board before it can be closed or fixed.
2. **Next `core.py` extractions**: `_on_tick` (~400 lines) and
   `_reconcile_one` (~345). `_enforce_app_release_transition_inner` is done
   (`release_transition.py`); follow the same recipe — module-level function
   taking `orch`, `_core()` late binding for names tests patch on `core`,
   `# noqa: F401` on the seam import, full suite as the gate.
3. **win32 `process_identity()`** still returns `None`.
4. **Deferred UX items** from the 2026-08-23 audit: modal focus trap,
   web-board rendering for Linear/Jira trackers, TUI↔web action parity
   (the scrollable HelpScreen shipped in 0.23.0).
5. **Intent gate on Linear/Jira.** `file_intent_request` still requires a
   file board because neither `LinearClient` nor `JiraClient` exposes an
   issue-create call; adding one (Linear `issueCreate`, Jira `POST /issue`)
   is the prerequisite. The trip-wire scan (item 4 above) already runs on
   every proposal regardless of tracker.
6. `test_startup_reclaim_terminates_live_recorded_orphan_agent_group` stays
   timing-flaky under CPU load; rerun alone before calling it a regression.
7. **Parallel suite.** The two load flakes are fixed; `pytest -n auto` is
   the way to run the suite locally. Adding `pytest-xdist` to the dev extra
   and `-n auto` to CI is a one-line follow-up once a second green parallel
   run on CI-class hardware confirms it.
8. **Quota marker coverage.** Capture the exact usage-limit strings from
   claude / codex / opencode / gemini the next time one trips and pin them
   in `test_backend_contract.py` the way the retryable markers are.

## Environment notes

- Run tests with `.venv/bin/python -m pytest -q` (bare `python` is not the
  project interpreter).
- `symphony` is not on the login-shell PATH on this host; workers receive
  `SYMPHONY_CLI=<venv>/bin/symphony`, and the shipped prompts use
  `${SYMPHONY_CLI:-symphony}`.
- Scratch project used for the live check: created with
  `SYMPHONY_PROJECTS_FILE=<tmp>/projects.json symphony project create ... --port 9997`
  so the global project registry stayed untouched.
