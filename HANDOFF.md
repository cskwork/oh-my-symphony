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

## Open questions / next steps

1. **#31 codex sandbox vs symlinked board files.** Current code already
   injects resolved symlink targets and git admin dirs
   (`backends/codex.py:_scan_workspace_symlinks`). The 2026-05 failure has no
   deterministic repro; it needs a live codex run against a symlinked file
   board before it can be closed or fixed.
2. **Next `core.py` extractions** (in order): `_enforce_app_release_transition_inner`
   (~420 lines) into `release_cycle.py`; `_on_tick` (~400); `_reconcile_one`
   (~345). Follow the `worker_exit.py` convention: module-level functions that
   take `orch` explicitly, `_core()` late binding for names tests patch on
   `core`, full suite as the gate.
3. **win32 `process_identity()`** still returns `None` (kills stay ungated
   warn-once on Windows). A `GetProcessTimes`-based fingerprint in
   `_shell.py` would enable pid-reuse protection.
4. **Deferred UX items** from the 2026-08-23 audit: scrollable HelpScreen,
   modal focus trap, web-board rendering for Linear/Jira trackers, TUI↔web
   action parity.
5. **Intent gate follow-ups**: Linear/Jira boards are not covered (proposals
   parse only on file boards); the kit's `tools/tripwire.sh` hedging scan is
   not run on the intent body; this repository's own `.sdlc/config.md` still
   has no `lazymode:` line (default 1) — set `lazymode: 3` only on the
   owner's explicit instruction.
6. `tests/test_orchestrator_dispatch.py::test_startup_reclaim_terminates_live_recorded_orphan_agent_group`
   is timing-flaky under CPU load (it kills a real process group). It passed
   in every full-suite run on this branch; rerun it alone before treating a
   failure as a regression.

7. **Parallel test suite (pytest-xdist) is not isolation-clean yet.**
   `pytest -n auto` finishes in ~72 s instead of ~234 s, but two tests fail
   under parallel load and pass serially:
   `tests/test_workspace.py::test_setup_worktree_script_supports_linked_workflow_dir[mkdir]`
   and
   `tests/test_orchestrator_release_contract_integration.py::test_reconcile_terminal_release_holds_lease_until_cleanup_finishes`
   (an `asyncio` timeout). Make those two load-tolerant before adding
   `pytest-xdist` to the dev extra and `-n auto` to CI; nothing in the repo
   depends on xdist today.

## Environment notes

- Run tests with `.venv/bin/python -m pytest -q` (bare `python` is not the
  project interpreter).
- `symphony` is not on the login-shell PATH on this host; workers receive
  `SYMPHONY_CLI=<venv>/bin/symphony`, and the shipped prompts use
  `${SYMPHONY_CLI:-symphony}`.
- Scratch project used for the live check: created with
  `SYMPHONY_PROJECTS_FILE=<tmp>/projects.json symphony project create ... --port 9997`
  so the global project registry stayed untouched.
