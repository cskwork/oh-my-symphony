# Dark-factory cycle design

Date: 2026-09-05. Branch: `feat/dark-factory-cycle` (from `dev`).

## Context

Symphony already has the pieces of an autonomous delivery loop, but they are
not wired into one human-gated cycle:

- Operator chat (`src/symphony/chat.py`) teaches the agent to file board
  tickets directly with `symphony board new`. There is no intent artifact and
  no approval step; the agent decides when a request is "confirmed enough".
- The deep lane preset (`src/symphony/workflow/presets.py`, prompts under
  `docs/symphony-prompts/file/deep/`) runs Intake, Research, Plan, Review,
  Build, QA, Verify and Document with adversarial Review and re-proving
  Verify. It maps almost one to one onto the six sdlc-kit stages.
- sdlc-kit (`~/.pi/agent/skills/sdlc-kit`) defines the target shape: one
  artifact per stage, a gate between stages, and `lazymode: 3` meaning "intent
  is the only human decision".

The gap is the connective tissue: capturing intent in chat, one explicit
approval, and a server-owned hand-off into the pipeline. Six GitHub issues
(#28, #29, #30, #31, #32, #58) and two structural debts from `HANDOFF.md`
(`orchestrator/core.py` god class, claude/pi backends not sharing the
per-turn base) are folded into the same branch.

## Decision

1. **Intent proposal.** When the chat agent recognises a software request it
   emits one strictly shaped `<symphony-intent>{...}</symphony-intent>` marker
   after its human explanation. The server turns only that marker into an
   action card; everything else stays prose. The payload carries `slug`,
   `title`, `track` (`full` or `micro`) and the intent markdown with the
   sdlc-kit sections (Problem, Evidence, Success criteria, Out of scope,
   Constraints, Open questions).
2. **Intent approval is the only human gate.** The operator approves from the
   card or by replying `approve` (optionally `approve <slug>`). The server,
   never the agent, then files the request ticket through the tracker API and
   writes `.sdlc/work/<slug>/intent.md` beside the workflow with an
   `## Approval` section. Any ordinary reply supersedes pending proposals so
   the agent can revise.
3. **The pipeline does the rest.** On a deep board the ticket lands in
   `Intake`; the Intake prompt consumes the intent instead of re-asking and a
   `Track: micro` intent skips Research. On a default four-lane board the
   ticket lands in the first active state and Todo triage routes it. No new
   human gate is added anywhere downstream; Review and Verify keep their
   existing round caps and `Human Review` fallout.
4. **New projects can be born deep.** The existing project-setup proposal
   accepts an optional `preset` (`default` or `deep`). The chat preamble tells
   the agent to propose `deep` for application delivery, so "make me an app"
   yields a deep board without operator configuration.
5. **Bugs.** #29 moves per-dispatch env into `BackendInit.env`; #28 snapshots
   touched files onto `RetryEntry`; #30 retries the JSON rename on a held
   handle; #32 namespaces `.symphony/*.json` state per workflow file. #58 is
   already fixed on `dev` and is closed after merge; #31 gets a current-code
   analysis comment (resolved symlink targets are already injected).
6. **Debt.** Extract the worker-exit state machine out of `core.py`, migrate
   claude and pi onto `PerTurnCliBackend`, and split `_run_agent_attempt` by
   phase. Each step is behavior-preserving and gated on the full suite.

## Domain terms (added to CONTEXT.md)

- **Intent proposal**: a server-owned chat action created only from the strict
  intent marker. It holds the slug, title, track and intent markdown, expires
  after 30 minutes, and is superseded by any ordinary reply.
- **Intent approval**: the operator's explicit confirmation of one Intent
  proposal. It is the only human gate in the cycle. Approval files the request
  ticket and records `.sdlc/work/<slug>/intent.md`; it never runs agent code.
- **Track**: `full` (Intake through Document) or `micro` (Intake skips
  Research). Recorded on the intent and frozen by approval.

## Components and data flow

```
operator ──chat──▶ agent ──<symphony-intent>──▶ ChatManager._record_agent_message
                                                    │ parse (strict) → IntentAction (pending)
                                                    ▼
                                            web card / "approve" reply
                                                    │ confirm_intent (token, expiry, idempotent)
                                                    ▼
                                    IntentFiler (server-owned)
                                    ├─ FileBoardTracker.create_validated(...)
                                    └─ .sdlc/work/<slug>/intent.md (+ ## Approval)
                                                    │ request_refresh()
                                                    ▼
                          Intake → Research → Plan ⇄ Review → Done ─▶ Build → QA → Verify → Document
```

- `chat.py`: `IntentAction` dataclass, `_intent_spec(text)` parser,
  `ChatSession.intent_actions`, `ChatManager.intent_for_reply(text, session_id)`,
  `ChatManager.confirm_intent(action_id, session_id, confirmation_token)`,
  `IntentFiler` protocol with `file_intent_request` default. Events:
  `intent_action`, `intent_status`, `intent_removed`. Snapshot key:
  `intent_actions`.
- `webapi.py`: `POST /api/v1/chat/sessions/{session_id}/intent/{action_id}/approve`
  (header `X-Symphony-Chat-Confirmation`, empty body). `_send` treats a bare
  `approve` / `approve <slug>` as approval when exactly one pending proposal
  matches.
- `web/static/app.js`, `i18n.js`, `style.css`: intent card with title, slug,
  track, collapsible intent body, Approve button, status chip and ticket id.
- Prompts: `_BOARD_PREAMBLE` rewritten around the intent protocol;
  `docs/symphony-prompts/file/deep/intake.md` consumes the intent;
  `docs/symphony-prompts/file/deep/base.md` names the intent as the request
  source of truth.
- `projects.py` / `webapi.py`: project-setup proposal accepts `preset`; the
  registered creator applies `apply_lane_preset(workflow, "deep")` when asked.

## Error handling

- A malformed marker (wrong keys, bad slug, oversized body, missing required
  headings) is shown as plain text. It never becomes an action.
- Approval on a non-file tracker is refused with a clear error; proposals are
  not parsed there at all.
- A failed filing keeps the card in `failed` with the error and stays
  approvable for a retry, mirroring project setup.
- Expiry is checked on every read; an expired card fails closed.
- Concurrent approvals of one card await the same task and return the same
  result (idempotent).

## Testing

- `tests/test_chat.py`: parser strictness (keys, slug, size, headings),
  proposal in QA and edit mode, supersede on ordinary reply, expiry, token
  requirement, idempotent approval, filer failure path, snapshot shape.
- `tests/test_chat_intent_filer.py`: default filer creates the ticket with
  `request == slug`, state `Intake` on deep boards and the first active state
  otherwise, description carries the intent, `intent.md` written with
  `## Approval`, refuses non-file trackers.
- `tests/test_webapi_chat_intent.py`: approve route (403 without token, 404
  unknown, 409 expired/failed), `approve` reply routing.
- `tests/test_deep_preset_e2e.py`: a ticket produced by the filer walks
  Intake to Done with the mock backend; `Track: micro` intake prompt text.
- Bug regressions: one test per issue (#28, #29, #30, #32).
- Gates: `python -m pytest -q`, `ruff check src tests`,
  `scripts/check_i18n.py`, `symphony-pyright`.

## Out of scope

- Writing sdlc-kit approval records (`approve.sh` format) or running
  `tools/tripwire.sh`; the kit stays framework-only.
- Intent gate for Linear and Jira trackers.
- A TUI intent card (the web board is the chat surface).
- Changing this repository's own `.sdlc/config.md` lazymode.
