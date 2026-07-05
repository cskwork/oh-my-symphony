# 2026-07-05 - Delivery reliability gates

## Failed-dependency blocker gate

## Goal

Prevent downstream Symphony tickets from starting when an upstream dependency
ended in a failed terminal state such as `Blocked`.

## Decision

Change orchestrator dependency resolution from "any terminal blocker is
resolved" to "only successful terminal blockers are resolved." `Done`, `Human
Review`, and custom non-failure terminal names such as `Closed` can still
release downstream work. `Blocked`, `Cancelled`/`Canceled`, `Duplicate`, and
archive-style states keep the downstream ticket ineligible and show the
operator-visible `Blocked dependency` attention signal.

The live Jira board exposed the defect: `TASK-013` started while it had
`blocked_by: TASK-012` and `TASK-012` was `Blocked`. The root cause was
`Orchestrator._eligible()` and `issue_attention()` treating all
`tracker.terminal_states` as successful dependency completion.

- Rejected: removing `Blocked` from `terminal_states`. Blocked cards must stay
  terminal for worker cleanup, TUI grouping, and budget/merge-failure parking.
- Rejected: requiring only literal `Done`. The current four-stage workflow uses
  `Human Review` as the agent-complete handoff, and some integrations use
  successful custom terminal names.
- Rejected: fixing only dispatch eligibility. The same predicate drives
  operator attention; a blocked dependency must be visible as well as skipped.

## Verification

- Red before fix:
  `pytest tests/test_orchestrator_dispatch.py::test_todo_with_blocked_terminal_blocker_remains_blocked tests/test_orchestrator_dispatch.py::test_issue_attention_reports_failed_terminal_dependency -q`
  failed: 2 tests.
- Green after fix:
  `pytest tests/test_orchestrator_dispatch.py::test_todo_with_blocked_terminal_blocker_remains_blocked tests/test_orchestrator_dispatch.py::test_issue_attention_reports_failed_terminal_dependency -q`
  passed: 2 tests.
- Surrounding blocker/retry checks:
  `rtk pytest tests/test_orchestrator_dispatch.py::test_todo_with_non_terminal_blocker_blocked tests/test_orchestrator_dispatch.py::test_todo_with_done_blocker_eligible tests/test_orchestrator_dispatch.py::test_todo_with_blocked_terminal_blocker_remains_blocked tests/test_orchestrator_dispatch.py::test_active_state_issue_with_unresolved_blocker_is_ineligible tests/test_orchestrator_dispatch.py::test_retry_timer_waits_for_unresolved_blocker_then_recovers tests/test_orchestrator_dispatch.py::test_issue_attention_reports_unresolved_dependency tests/test_orchestrator_dispatch.py::test_issue_attention_reports_failed_terminal_dependency -q`
  passed: 7 tests.

---

## File-board lifecycle E2E guard

## Goal

Prove the operator-facing Symphony Kanban automation with real Markdown board
files, not only mocked state refreshes or prompt-string assertions.

## Decision

Add a file-board E2E regression in `tests/test_agent_lifecycle_e2e.py`. The
test creates a real `kanban/LIFE-1.md` card, lets `_on_tick()` auto-triage it
from `Todo` to `In Progress`, dispatches a fake backend through the normal
worker path, mutates the card through `Verify` and `Learn` with the real
`FileBoardTracker`, satisfies the stage-contract artefact checks under
`docs/LIFE-1/`, and asserts the card reaches `Human Review` with no running,
retry, or claimed worker slot left behind.

- Rejected: relying on the existing lifecycle E2E alone. It fakes
  `_refresh_issue_state`, so it cannot catch file-board write-back or tracker
  parsing regressions.
- Rejected: an API-only smoke test. `/api/v1/state` proves visibility, not
  that the Markdown board can be mutated through the full worker lifecycle.
- Rejected: launching a real CLI agent in unit CI. The durable regression here
  is Symphony's board/runtime contract; backend CLI auth and model quality stay
  in manual/live board QA.

## Verification

- `pytest tests/test_agent_lifecycle_e2e.py::test_file_board_e2e_auto_triage_dispatches_and_reaches_human_review -q`
  passed: 1 test.
- `rtk pytest tests/test_agent_lifecycle_e2e.py -q` passed: 5 tests.

---

## Human Review history gate

## Goal

Ensure Symphony board agents do not leave final Human Review handoffs as
uncommitted or unpushed local state.

## Decision

Add a Learn-stage Final History Gate to both file and Linear prompt flavors.
Before a worker exposes a card as `Human Review`, it must commit the final
ticket/wiki/learn evidence record from the host repo, push the target branch
when it has a remote/upstream, verify the remote tip with `git ls-remote`, and
record the local/remote SHA evidence in the card. If commit, push, or remote
verification fails, the worker must set the ticket to `Blocked` with `##
History Failure` instead of leaving a dirty Human Review card.

The gate stages exact paths only and explicitly rejects `git add -A`; broad
staging caused the `jira-symphony` host checkout to accumulate destructive
source/test/QA deletions unrelated to the Human Review handoff.

- Rejected: relying on Verify's merge commit. Verify runs before Learn, while
  Learn writes the final wiki and Human Review card text.
- Rejected: committing every dirty host file. The board handoff must preserve
  history without sweeping unrelated or destructive workspace changes.
- Rejected: recording Human Review only in the card. A board state that is not
  committed and pushed is not durable project history.

## Verification

- `rtk pytest tests/test_workflow_pipeline_prompt.py::test_learn_stage_writes_wiki_and_human_review_handoff -q`
  passed: 2 tests.
- `jira-symphony` pushed prior verified TASK-001..TASK-010 history to
  `origin/main` (`bdb21a4`) and then pushed the scoped Human Review board-state
  commit (`6180964`). Remote read-back reported `6180964` for `refs/heads/main`.

---

## Full integration defect loop

## Goal

Ensure a board can prove that all committed, pushed, and merged task work still
functions as one product before delivery is considered complete.

## Decision

Release/integration and app-delivery verification tickets now have an explicit
Full Integration Gate. They must run against the committed target branch, check
local/remote SHA alignment when an upstream exists, execute clean
install/build/start/readiness/customer-flow QA, and review
console/network/server failures. Any defect found during this proof becomes a
new Kanban bug ticket with repro evidence, expected behavior, fix boundary, and
verification commands. The release ticket records those IDs in `blocked_by`,
moves to `Blocked`, and loops until the merged target passes.

- Rejected: treating per-ticket unit tests as enough. They do not prove the
  merged app starts or that customer workflows compose correctly.
- Rejected: letting a final release ticket only write a defect list. Defects
  must be registered back into the board so agents can resolve them and rerun
  the release proof.
- Rejected: testing a worker branch for final QA. The delivery claim is about
  the committed/pushed target branch that users will run.

## Verification

- `rtk pytest tests/test_workflow_pipeline_prompt.py::test_verify_stage_demands_review_qa_and_merge_evidence tests/test_workflow_pipeline_prompt.py::test_learn_stage_writes_wiki_and_human_review_handoff tests/test_workflow_pipeline_prompt.py::test_operator_skill_routes_ticket_registration_by_work_type -q`
  passed: 5 tests.

---

## v0.10.0 release decision

## Goal

Merge the verified `dev` branch to `main` and publish a new minor release with
release notes that explain the reliability improvements, not just the version
bump.

## Decision

Bump `0.9.3` to `0.10.0` because the post-0.9.3 changes add runtime controls
and operator-visible safety behavior: state-local turn watchdogs, token
attention telemetry, prompt-context compaction, workspace ownership checks,
strict contract-failure scope, doctor warnings, and the issue-detail JSON
serialization fix.

- Rejected: `0.9.4`. The change set is larger than a patch because workflows
  can now opt into new runtime controls and the release changes how reliability
  failures are surfaced to operators.
- Rejected: tagging without synchronizing version surfaces. The package version,
  CLI `__version__`, public Pages badge, changelog, annotated tag, and GitHub
  release should agree.
- Rejected: a merge commit from `dev` to `main` when a fast-forward is possible.
  The branch history is linear, so fast-forwarding keeps release provenance
  simpler.

## Planned verification

- Confirm the release tag does not already exist.
- Run focused version/changelog checks on `dev` before committing the release
  bump.
- Push `dev`, then fast-forward `main` only after confirming `origin/main` is an
  ancestor of `origin/dev`.
- Run the full test suite on `main`.
- Create an annotated `v0.10.0` tag on the verified `main` commit.
- Publish GitHub release notes from the checked commit range and verify the
  remote tag, peeled tag target, and release metadata.

---

# 2026-07-05 - Default prompt compaction on

## Goal

Make prompt compaction the default behavior instead of requiring every workflow
to opt in.

## Decision

Set `agent.compact_issue_context` to default `true` in both the typed agent
config and the workflow builder fallback. Keep `agent.compact_issue_context:
false` as the explicit opt-out for custom boards that need every worker prompt
to include the full raw ticket history.

Release as `v0.10.1` instead of moving the already-published `v0.10.0` tag.
The follow-up is small but release-visible: package metadata, CLI version,
GitHub release notes, and the Pages badge should all include the new default.

- Rejected: deleting the flag. Some custom ticket formats may still need a
  rollback switch while the heading selector learns more board-specific
  section names.
- Rejected: changing only `AgentConfig`. Parsed workflow configs also use the
  builder fallback when the YAML omits the key, so both surfaces must agree.
- Rejected: retagging `v0.10.0`. A public release already exists, and moving a
  published tag would make downstream provenance ambiguous.

## Verification

- `rtk pytest tests/test_workflow.py -k "compact_issue_context" -q`
  passed: 3 tests.
- `rtk pytest tests/test_prompt_context.py tests/test_prompt.py::test_compact_issue_context_changes_rendered_prompt_description -q`
  passed: 8 tests.
- `python -m py_compile src/symphony/workflow/config.py src/symphony/workflow/builder.py`
  passed.
- `git diff --check`
  passed.
- Full suite on `dev` pre-push passed: 1110 passed, 2 skipped.
- Full suite on `main` passed locally and in the pre-push hook: 1110 passed,
  2 skipped.
- Confirmed `jira-symphony` has `agent.compact_issue_context: true` in
  `WORKFLOW.md`. The running local `jira-symphony` service was still version
  0.9.3 with an active worker, so it needs a restart/upgrade before relying on
  the 0.10.1 package default.
- Fresh remote-clone Codex E2E:
  `/private/tmp/symphony-codex-e2e-compact-xMLREd/repo`, commit
  `f8a5473532d574eeec38043fdae3ad0d536179db`.
  The E2E workflow intentionally omitted `agent.compact_issue_context`; loaded
  config reported `compact_issue_context=True`.
- Clean release-gate ticket `CODEX-E2E-005` reached `Human Review`, exited with
  `worker_exit reason=normal`, API reported `running=0` and `retrying=0`, and
  had no `## Contract Failure` or `## Contract Warning` heading.
- E2E artifacts:
  `docs/CODEX-E2E-005/work/compaction-default.md` contained
  `compact_issue_context default: true`,
  `docs/CODEX-E2E-005/qa/verify.log` contained
  `verified compact default true`, and
  `docs/CODEX-E2E-005/learn/handoff.md` contained the Learn handoff.

## E2E harness note

The first two temporary E2E tickets (`CODEX-E2E-003` and `CODEX-E2E-004`)
proved the worker could finish, but they were not accepted as the release gate:
the harness prompts omitted required contract sections or used the wrong
`AC Scorecard` table shape. The final ticket (`CODEX-E2E-005`) corrected the
harness to the validator's actual contract and is the evidence used for the
release decision.

---

# 2026-07-05 - Product-ready app delivery gate

## Goal

Prevent Symphony from treating a set of locally passing implementation tickets
as a production-ready customer app when the final merged product has not been
researched, started, and exercised through real customer workflows.

## Decision

Update the operator skill and OneShot/delegation guides so app delivery starts
with a product-readiness brief and ends with a merged-target release
verification ticket.

Also update ticket-registration guidance to route by work type before slicing:
bugfix, feature/enhancement, customer-facing app delivery, release/integration,
docs/config/tooling, or research/spike. Correct issue registration is the
primary control; product-discovery tickets are required for app delivery but
would be noise for a narrow defect fix.

The brief must name the target customer, comparable-product/domain research or
explicit assumptions, core customer workflows, must-have functionality,
non-goals, data/auth/deploy constraints, and the final release matrix. Verify
now tells app/release tickets to prove the merged target branch through clean
setup, build, declared start command, readiness check, browser/API workflow
smoke, and console/network/server review. A startup failure such as `curl 000`,
a missing listening port, undocumented manual setup, or a missing must-have
workflow is `Not market-ready` and rewinds or blocks delivery.

- Rejected: only adding more per-ticket unit tests. Unit tests can pass while
  the merged app does not start or lacks required customer functionality.
- Rejected: relying on per-ticket merge proof alone. A clean merge commit proves
  Git integration, not product readiness.
- Rejected: making every small UI ticket run a full product benchmark. The full
  release proof belongs on app delivery and final release-verification tickets;
  smaller tickets still verify their owned workflow and evidence.
- Rejected: routing every Symphony request through the new-product pattern.
  Symphony also coordinates bugfixes, bounded feature work, docs/config changes,
  research spikes, and release verification, each needing a different ticket
  shape.

## Verification

- Skill guidance tests cover product discovery, release matrix ticketing, and
  work-type-specific issue registration for bugfix, feature, app-delivery, and
  release-verification routes.

## Correction

The production-readiness guidance belongs in the Symphony skill used by agents
when registering board issues, not in the built-in worker prompt templates.
The prompt-template edits were reverted so existing workflows keep their current
stage prompt contract unless an operator explicitly updates a workflow prompt.

---

# 2026-07-05 - Service status stale-PID API probe

## RCA

`symphony service status` trusted only the saved `orchestrator_pid` in
`.symphony/run/*.json`. In a real `jira-symphony` run, the saved PID was stale
while the recorded port still served a healthy Symphony API. Result:
`service status` reported `stopped`, but `curl /api/v1/state` showed a live
orchestrator dispatching work.

## Decision

When the saved PID is not alive, probe the recorded
`http://<host>:<port>/api/v1/state` endpoint. If it responds like Symphony,
report the service as running with `stale pid=... (api alive)` and have doctor's
port failure explain that the recorded API is still alive but the saved PID is
stale.

Rejected alternatives:
- Keep PID-only status and rely on `lsof`: accurate for processes, but it loses
  workflow context and confused the operator.
- Treat any occupied port as this workflow's service: unsafe, because the port
  may belong to another process.
- Kill by port from `service status`: too destructive for a status command.

## Verification

- Added regression tests for `service_status`, CLI status output, and doctor
  port-owner hints when PID is stale but the Symphony API responds.

---

# 2026-07-05 - Re-run before_run at worker turn boundaries

## RCA

`before_run` ran once at worker startup, but long multi-turn tickets can mutate
workspace invariants between turns. In the live `jira-symphony` TASK-021 run,
an in-worktree merge replaced the required `kanban/` host-board symlink with a
real tracked directory. The worker moved the branch-local card to `Verify`,
while Symphony kept refreshing the host tracker as `In Progress` and launched a
continuation turn against stale board state.

## Decision

Run the configured `before_run` hook again before every continuation or phase
turn. This lets workflow-level guards reassert board symlinks, refresh remotes,
or fail fast before another backend turn spends tokens on a broken workspace.

Rejected alternatives:
- Only change the `jira-symphony` hook. The orchestration contract says
  `before_run` protects a turn; core must honor that across continuations.
- Sync branch-local `kanban/` files back to the host automatically. That would
  blur the tracker/source boundary and risk overwriting host-owned review
  history.
- Stop workers when a workspace link breaks. Failing `before_run` already gives
  a precise blocker without killing a healthy workspace preemptively.

## Verification

- Added a regression test proving two active-state turns invoke `before_run`
  twice, so workspace invariants are rechecked before the continuation turn.

---

# 2026-07-05 - First-progress stall clock

## RCA

The live `jira-symphony` TASK-021 OpenCode worker stayed `In Progress` for more
than 30 minutes with zero token totals, an empty last message, and repeated
`other_message` events. Symphony updated `last_codex_timestamp` for UI
freshness, but no `last_progress_timestamp` existed yet. `_reconcile_running`
fell back from missing progress to fresh codex activity, so the no-progress
stall timer never measured from worker start.

## Decision

Before any real model/lifecycle/token progress is recorded, measure the stall
window from `started_at`. After real progress exists, keep measuring from
`last_progress_timestamp`. `last_codex_timestamp` remains useful for UI
freshness but must not extend the worker no-progress deadline.

Rejected alternatives:
- Treat all OpenCode `other_message` events as progress. Empty stream events
  are liveness noise, not proof the agent is advancing the ticket.
- Remove `last_codex_timestamp`. Operators still need to see backend activity
  recency separate from actual stall progress.
- Increase the stall timeout. That would only hide the same failure for longer
  and would not help a worker that never produces tokens or lifecycle progress.

## Verification

- Red before fix:
  `PYTHONPATH=src python -m pytest tests/test_orchestrator_dispatch.py::test_reconcile_stalls_from_start_when_only_codex_noise_seen -q`
  failed because `cancelled_at` stayed `None`.
- Green after fix:
  `PYTHONPATH=src python -m pytest tests/test_orchestrator_dispatch.py::test_reconcile_stalls_from_start_when_only_codex_noise_seen tests/test_orchestrator_dispatch.py::test_reconcile_stalls_on_progress_timestamp_not_codex_timestamp tests/test_orchestrator_dispatch.py::test_on_codex_event_user_role_other_message_does_not_advance_progress tests/test_orchestrator_dispatch.py::test_codex_other_message_with_input_only_token_growth_does_not_advance_progress tests/test_orchestrator_dispatch.py::test_on_codex_event_extracts_nested_item_preview_without_stall_progress -q`
  passed: 5 tests.
- Focused runtime regression suite:
  `PYTHONPATH=src python -m pytest tests/test_orchestrator_phase_transition.py tests/test_agent_lifecycle_e2e.py tests/test_orchestrator_dispatch.py::test_todo_with_blocked_terminal_blocker_remains_blocked tests/test_orchestrator_dispatch.py::test_issue_attention_reports_failed_terminal_dependency tests/test_orchestrator_dispatch.py::test_reconcile_stalls_from_start_when_only_codex_noise_seen tests/test_orchestrator_dispatch.py::test_reconcile_stalls_on_progress_timestamp_not_codex_timestamp -q`
  passed: 40 tests.
- Full suite:
  `PYTHONPATH=src python -m pytest -q`
  passed: 1119 tests, 2 skipped, 2 warnings.

---

# 2026-07-05 - Continuous improvement heartbeat plan

## Goal

Plan a default-off web setting that periodically verifies the integrated
baseline, records deterministic evidence, and registers discovered
production-readiness defects as normal Symphony Kanban tickets.

## Decision

Save the GPT Pro review as a staged implementation plan:
`docs/plans/2026-07-05-continuous-improvement-heartbeat.md`.

The plan treats the heartbeat as an opt-in workflow-level inspector, not a
hidden coding agent. It verifies the merged baseline against a rubric, writes
machine-owned report output, and registers findings through tracker APIs so the
normal Symphony workflow owns remediation.

- Rejected: a default-on loop. Running checks and writing tickets without
  explicit operator opt-in is too risky.
- Rejected: browser-editable command lists. The first web surface should expose
  only `enabled` and `interval_ms`; command execution must stay predefined or in
  trusted workflow config.
- Rejected: direct code edits by the heartbeat. Defects must become scoped
  Kanban tickets that go through the normal Symphony run.
- Rejected: direct Markdown ticket writes. The registrar must reuse existing
  file-tracker creation/mutation paths and de-duplicate by fingerprint.

## Verification

- Confirmed the plan file exists.
- Scanned the plan for placeholder terms that would make it non-actionable.
- Ran `git diff --check`.

---

# Architecture improvement plan — implementation session (E, C, A1, B, D)

Executed the first tranche of
`docs/improvements/architecture-improvement-plan-2026-07-05.md` in plan
order (E -> C -> A step 1 -> B -> D), one green commit per step on `dev`
(24aa571, a2c5746, e1c676d, 7f1a421, 1e7a3d2, 78bd505, 67eb6ba, 45c4a01,
8711925, 51cb5c3).

## E — CI gates (24aa571)

ruff (default E4/E7/E9/F, `__init__.py` F401 per-file-ignore), pyright
basic scoped to `src`, and `pytest --cov-fail-under=80` (baseline 82%)
are now blocking CI steps. All 9 pre-existing pyright errors and ~30
ruff findings were fixed rather than baselined — the baseline was small
enough that report-only mode would have added ratchet bureaucracy for
nothing.

- Rejected: `ruff format` gate. Formatting the whole repo is a
  giant, review-hostile diff; lint-only now, format can ratchet later.
- Rejected: pyright over `tests/`. Tests carry deliberate loose
  patterns (Optional-heavy fixtures); gate `src` first, burn tests down
  later.
- Rejected: suppressing `LinearClient.append_note` protocol mismatch
  with a type-ignore. Implemented an explicit no-op (the orchestrator
  already treats note-append as best-effort via a getattr guard).

## C — backend contract + Template Method (a2c5746, e1c676d, 7f1a421, 1e7a3d2)

`tests/test_backend_contract.py` is the Testcase Superclass every
per-turn adapter runs identically (lifecycle order, MUST-emit events,
shared envelope, TurnFailed on nonzero exit, idempotent stop).
`backends/per_turn.py` now owns the per-turn family skeleton
(spawn -> feed prompt -> bounded collect -> safe_proc_wait reap ->
normalized emit, closed-flag race, cancellation reap); plain_cli
(agy/kiro), gemini, and opencode migrated one commit at a time.

- Codex deliberately NOT forced into the base — it is the second
  lifecycle family (persistent app-server, JSON-RPC over stdio).
- claude/pi remain unmigrated: they are the *streaming* per-turn
  variant (readline loop, malformed-line streak, mid-turn progress);
  they need a streaming sibling of the skeleton, left as follow-up.
  The contract suite already pins them.
- Out-of-pipeline live CLI contract tests (run daily against real
  binaries) also remain follow-up.
- Test doubles now patch the consumer namespace
  (`symphony.backends.per_turn`) per CPython "where to patch".
- opencode's wholesale rewrite was verified by AST-diffing old vs new:
  removed defs exactly equal the base's, only `__init__` changed.

## A step 1 — DispatchState (78bd505)

`orchestrator/dispatch_state.py` owns
running/claimed/retry/completed/persisted_retry_attempts/
turn_budget_exhausted. The three historically-regressed rules are
encoded once: `available_slots` subtracts running AND retry-pending;
`entry_owned_by` requires worker-task identity before eviction;
`schedule_retry` cancels the previous timer. G1 prune became
`prune_claims_not_in`.

- Strangler approach: Orchestrator exposes the collections through
  read-only properties so ~100 legacy read sites and the test suite
  keep working; only the mutation clusters were converted. Rejected a
  big-bang conversion of every touch point — the 72+ dispatch tests pin
  behaviour, and the property alias makes each future conversion local.
- Found by the new pyright gate during the switch: a `self._claimed -=`
  augmented assignment (property without setter) — converted into the
  owner's mutator.

## B — supervised background tasks (67eb6ba)

`_spawn_supervised` pins fire-and-forget tasks (worker-exit cleanup,
retry firing, escalations) in `_background_tasks`, logs non-cancel
exceptions, and `stop()` drains the set (bounded 5s) before closing the
run registry.

- Rejected (for now): full `asyncio.TaskGroup` adoption. Sibling
  auto-cancel would couple independent ticket workers — one ticket's
  unexpected exception must not cancel the others. The plan's own
  fallback (identity check in DispatchState + exception-checked
  done-callbacks + strong refs) delivers the leak-proofing without the
  semantic change. Revisit if worker isolation ever moves into
  per-ticket scopes.

## D — DI over monkeypatch indirection (45c4a01, 8711925, 51cb5c3)

All 15 `_pkg`/`_tui_pkg` indirection sites are gone. `build_backend` is
constructor-injectable on `Orchestrator`; it and
`commit_workspace_on_done` / `auto_merge_on_done_best_effort` are
imported directly into `core` and late-bound from its module globals,
so tests patch `symphony.orchestrator.core.<name>`
(`symphony.tui.app.<name>` for the TUI fetch helpers). The "bind names
before importing core" package contract is no longer load-bearing;
architecture.md updated in the same commits.

- Rejected: converting the two workspace/git helpers to constructor
  params as well. The composition seam that matters is the backend
  factory; module-global late binding keeps the helpers patchable
  without widening the constructor for YAGNI DI.

## Verification

Every commit: `ruff check src tests`, `pyright` (0 errors), full
`pytest` (1119 -> 1151 tests, all green), pushed to `origin/dev` with
the pre-push hook re-running the suite.

## Remaining from the plan (follow-ups)

- A steps 2–4: TokenAccountant / HealthReporter / PauseController /
  StallReconciler extracts; Split Phase of the worker-exit decision.
- C: streaming-family base for claude/pi; daily live contract tests.
- E ratchets: broaden ruff rule set (I, B, UP), pyright over tests,
  raise coverage floor from 80 as it grows.

## Continuous improvement heartbeat — docs-first contract (step 1)

## Goal

Define the rubric, ticket contract, and invariants for a default-off
heartbeat that periodically re-verifies the integrated baseline and files
defects as normal Kanban tickets, before any runtime code lands. Docs-first
so the implementation (config parsing, web API, scheduler, runner,
registrar) has one pinned contract to build against and review.

## Decision

Add `docs/continuous-improvement/rubric.md` (result semantics —
`passed` / `failed` / `not_available` / `not_proven`; default checks;
no-code-edit invariant; command safety; cross-process lease; tracker
support matrix; de-duplication), `docs/continuous-improvement/
ticket-template.md` (ticket body fields + `CI Fingerprint` hash rule), and
a placeholder `docs/continuous-improvement/latest.md` with
`<!-- ci:auto:* -->` markers so the future report writer can rewrite
machine-owned sections while preserving operator notes. Added a
"Continuous improvement heartbeat" subsection to `docs/architecture.md`
mapping config, web API endpoints, scheduler, runner module, report
writer, and registrar.

- Rejected: default-on. The heartbeat runs real commands against the
  integrated branch on a schedule; an operator must opt in explicitly, per
  the plan's non-goals.
- Rejected: browser-editable command lists. Letting the web UI configure
  arbitrary check commands turns a read-only inspector into a remote code
  execution surface; only `enabled` / `interval_ms` / `max_turns` /
  `agent_kind` are browser-editable, everything else is trusted
  `WORKFLOW.md` config.
- Rejected: one omnibus ticket per run. Findings are fingerprinted and
  registered as individual tickets (capped by `max_tickets_per_run`) so
  normal workers can pick up and close them independently, matching the
  existing file-tracker Kanban model instead of an unbounded triage doc.
- Rejected: letting the heartbeat edit product code directly. Its job is to
  prove the baseline and describe defects; fixes flow through the normal
  worker/ticket pipeline so they get the same review and contract checks as
  any other change.

## Verification

- `git diff --check` — clean (no trailing-whitespace / conflict-marker
  issues in the changed docs).
- Docs-only change; no code touched, no test suite run required for this
  step.

## Remaining from the plan (follow-ups)

- Step 2: `ContinuousImprovementConfig`, strict parsing, workflow mutation.
- Step 3: web API + settings card.
- Step 4: orchestrator scheduler skeleton + lease.
- Step 5+: runner, report writer, registrar implementation against the
  rubric and ticket template defined here.

## Continuous improvement heartbeat — runtime delivery

## Goal

Finish the default-off continuous-improvement heartbeat so an operator can
enable safe periodic baseline verification, inspect status from the web UI,
and receive bounded Kanban tickets for product-readiness failures.

## Decision

Deliver the heartbeat as a conservative inspector: the scheduler runs outside
normal worker slots, uses a durable lease and turn budget, proves the baseline
with read-only Git commands, executes only fixed argv checks (`pytest`,
`ruff`, `pyright`) with subprocess timeouts through `safe_proc_wait`, writes
machine-owned report sections, and creates file-board CI tickets only through
`FileBoardTracker.create_with_next_identifier`.

The web surface edits only `enabled`, `interval_ms`, `max_turns`, and
`agent_kind`. The registrar stamps `agent_kind` on created CI tickets when
configured, while `""` inherits the workflow default. Runner result status
uses the rubric vocabulary (`passed`, `failed`, `not_proven`) so the report,
web status, and scheduler status describe the same outcome.

- Rejected: preserving the placeholder runner seam. The scheduler already
  fires in production when enabled; a placeholder would turn the feature into
  a scheduled failure instead of a useful inspector.
- Rejected: treating every runner completion as `succeeded`. The operator
  needs to distinguish a clean run from `failed` checks and `not_proven`
  baseline proof.
- Rejected: running checks on any current branch when
  `agent.auto_merge_target_branch` is configured. A host-branch mismatch
  creates a temporary detached worktree for the target; unresolved targets
  are `not_proven`. The heartbeat must not silently QA a worker branch.
- Rejected: direct ticket Markdown writes. Reusing the tracker creation path
  keeps identifier allocation, locks, and per-ticket `agent.kind` formatting
  consistent with normal board operations.
- Rejected: including arbitrary browser-editable command lists. Fixed commands
  keep the web settings card from becoming a remote command-execution surface.

## Verification

- Fresh-context adversarial review found target-branch proof, cancellation,
  fingerprint, and `not_proven` UI gaps; all four were fixed before final
  verification.
- `python -m pytest tests/test_shell.py tests/test_continuous_improvement.py -q`
  passed: 26 tests.
- `python -m pytest tests/test_continuous_improvement.py -q` passed: 14 tests.
- `python -m pytest tests/test_orchestrator_continuous_improvement.py -q`
  passed: 19 tests.
- `python -m pytest tests/test_webapi.py
  tests/test_orchestrator_continuous_improvement.py
  tests/test_web_static_contract.py -q` passed: 46 tests.
- Exact throwaway-repo E2E is codified in
  `tests/test_continuous_improvement.py`: current branch `feature`, configured target
  `dev`, failing real `python -m pytest -q`, generated `CI-1.md`, wrote report
  evidence, preserved host branch, removed temporary worktree.
- `python -m pytest tests/test_workflow.py -q` passed: 90 tests.
- `python -m ruff check src tests` passed.
- `python -m pyright` passed: 0 errors.
- `python -m pytest -q` passed: 1233 passed, 2 skipped, 2 warnings.

## Remaining risk

- Browser and DB probes intentionally report `not_available` until an explicit
  safe configuration exists.
- Non-file trackers return `unsupported_tracker` until their safe creation
  contracts are implemented.

---

## Retryable backend failures do not auto-pause

## Goal

Let transient backend throttling recover through Symphony's existing retry
backoff instead of leaving a Todo/In Progress ticket manually paused after one
failed turn.

## Decision

Classify explicit rate-limit/overload worker failures as retryable and route
them through normal retry scheduling without setting `paused=1`. Keep generic
worker crashes on the existing auto-pause path so unclear failures still stop
for operator inspection. OpenCode `exit -15` is also treated as retryable
because the live Jira board showed OpenCode throttling can surface with no
stderr and only that SIGTERM-shaped exit code.
If an older run already persisted that OpenCode auto-pause with a retry
attempt, startup now clears only the pause fields and lets the saved retry
attempt dispatch normally.

- Rejected: unpausing every `turn_error`. That would hide real backend crashes
  and keep restarting tickets that need operator action.
- Rejected: changing retry timers or retry caps. `agent.max_retries` already
  provides the bounded safety gate; the defect was the premature pause flag.
- Rejected: clearing existing operator pauses. A manually paused ticket still
  remains held; this change only avoids creating a new auto-pause for
  retryable errors.
- Rejected: treating all `exit -15` rows as OpenCode throttling. Persisted
  pause text must identify OpenCode before Symphony releases a legacy pause.

## Verification

- Red before fix:
  `python -m pytest tests/test_orchestrator_dispatch.py::test_worker_exit_retryable_rate_limit_schedules_retry_without_pause tests/test_orchestrator_dispatch.py::test_worker_exit_opencode_sigterm_schedules_retry_without_pause tests/test_orchestrator_dispatch.py::test_worker_exit_error_auto_pauses_hard_failure_with_visible_reason -q`
  failed: 2 retryable cases still set `paused=True`.
- Green after fix:
  same command passed: 3 tests.
- Surrounding pause/retry regression check:
  `python -m pytest tests/test_orchestrator_dispatch.py::test_worker_exit_preserves_pause_flag_for_held_ticket tests/test_orchestrator_dispatch.py::test_worker_exit_retryable_rate_limit_schedules_retry_without_pause tests/test_orchestrator_dispatch.py::test_worker_exit_opencode_sigterm_schedules_retry_without_pause tests/test_orchestrator_dispatch.py::test_worker_exit_error_auto_pauses_hard_failure_with_visible_reason tests/test_orchestrator_dispatch.py::test_eligible_refuses_paused_ticket_for_dispatch_and_retry tests/test_orchestrator_dispatch.py::test_retry_timer_reparks_paused_ticket_without_dispatching tests/test_orchestrator_dispatch.py::test_resume_worker_releases_held_retry_immediately tests/test_orchestrator_dispatch.py::test_snapshot_retry_row_includes_paused_flag -q`
  plus `test_retryable_persisted_pause_restarts_as_retry` passed: 9 tests.
- Persisted pause compatibility:
  `python -m pytest tests/test_orchestrator_dispatch.py::test_retryable_persisted_pause_restarts_as_retry tests/test_orchestrator_dispatch.py::test_non_opencode_persisted_sigterm_pause_stays_paused -q`
  passed: 2 tests.
- Full dispatch regression:
  `python -m pytest tests/test_orchestrator_dispatch.py -q` passed: 133 tests.
- Static checks:
  `python -m ruff check src/symphony/orchestrator/core.py tests/test_orchestrator_dispatch.py`
  passed; `python -m pyright src/symphony/orchestrator/core.py` passed;
  `git diff --check -- src/symphony/orchestrator/core.py tests/test_orchestrator_dispatch.py docs/changelog/changelog-2026-07-05.md`
  passed.

---

## Blocked ticket RCA self-healing

## Goal

Let Symphony investigate and resolve Blocked tickets without pretending the
blocked dependency is resolved or requiring manual state edits.

## Decision

Blocked must remain a failed terminal state for dependency safety, but it is
now actionable. On a poll tick, after normal active-ticket dispatch, the
orchestrator scans terminal `Blocked` cards when worker slots remain. If a
source ticket has no existing `## Blocked RCA` marker, Symphony creates one
`RCA-*` ticket in an active lane using the source ticket's valid agent or the
workflow default. The RCA worker must fix and verify the real root cause before
moving the source back to the configured reopen target (`Todo` by default).

Keep the `recover-blocked` API route and web action for compatibility, but make
it use the same RCA-ticket path and reject duplicates. Add
`agent.auto_recover_blocked` (default `true`) so sensitive workflows can opt out
of automatic RCA creation. For file boards, strip the stale `## Blocked RCA`
marker when the source is restored to an active state, so a later independent
block can receive a fresh RCA.

- Rejected: treating `Blocked` as a successful dependency terminal. That would
  let downstream tickets run against a failed prerequisite.
- Rejected: silently auto-reopening every Blocked card on each idle tick. The
  RCA worker must prove the root cause before the source re-enters `Todo`.
- Rejected: moving the original Blocked ticket directly back to `In Progress`.
  The live smoke showed that this starts normal worker execution before the RCA
  is resolved, which makes the blocked state meaningless and can let dependent
  tickets proceed on unproven work.
- Rejected: creating an RCA ticket that is blocked by the source ticket. The RCA
  ticket must be runnable so the default or chosen valid agent can resolve the
  root cause; the source remains Blocked until that ticket proves the fix.
- Rejected: opening RCA tickets without a duplicate marker. A failed terminal
  state can persist for many ticks; automatic recovery must be idempotent.

## Verification

- Focused recovery regression:
  `python -m pytest tests/test_orchestrator_dispatch.py::test_recover_blocked_issue_opens_rca_ticket_and_keeps_source_blocked tests/test_webapi.py::test_recover_blocked_route_calls_orchestrator tests/test_server_routes.py::test_recover_blocked_route_returns_recovery_payload -q`
  red before fix: 3 failures against the direct-reopen implementation.
- Focused auto-RCA regression:
  `python -m pytest tests/test_orchestrator_dispatch.py::test_tick_auto_opens_blocked_rca_ticket_once tests/test_orchestrator_dispatch.py::test_tick_auto_recovery_skips_existing_blocked_rca_note tests/test_orchestrator_dispatch.py::test_tick_auto_recovery_respects_disabled_config tests/test_orchestrator_dispatch.py::test_recover_blocked_issue_rejects_duplicate_rca tests/test_tracker_file.py::test_g5_strip_conflict_budget_and_blocked_rca_sections_on_active_restore tests/test_workflow.py::test_build_service_config_defaults_auto_recover_blocked_on tests/test_workflow.py::test_build_service_config_reads_auto_recover_blocked -q`
  red before fix: missing auto-RCA sweep and stale `## Blocked RCA` cleanup;
  green after fix: 7 passed.

---

## Multi-stage max-turns preflight guard

## Goal

Prevent generated/default Symphony harnesses from creating workflows that are
guaranteed to block after the first successful stage transition.

## Decision

Treat `agent.max_turns` as a full worker-attempt budget, not a per-stage
budget. A workflow with multiple active states needs at least one turn per
active state in the common staged path. `validate_for_dispatch` now rejects
workflows where `agent.max_turns` is lower than the configured active-state
count, and `symphony doctor` reports the same check as `agent.max_turns`.

This catches the bad harness shape `active_states: [Todo, In Progress, Verify,
Learn]` with `max_turns: 1`: the first turn can move `Todo -> In Progress`,
but Symphony then hits the attempt ceiling and blocks the ticket before Verify
or Learn can run.

- Rejected: leaving this as operator knowledge. The failure spends real agent
  turns and produces misleading `Blocked` cards.
- Rejected: changing `max_turns` semantics to per-stage. Existing dispatch
  budgeting, retry, and no-stage-change behavior already rely on it being an
  attempt-level ceiling.
- Rejected: a doctor-only warning. Service startup also needs to refuse the
  impossible workflow, not just report it in optional preflight output.

## Verification

- Red before fix:
  `python -m pytest tests/test_workflow_preflight_full.py::test_multi_stage_workflow_rejects_too_low_max_turns tests/test_doctor.py::test_stage_turn_budget_fails_for_multi_stage_one_turn_workflow -q`
  failed because the guard/check did not exist.
- Focused green:
  same command passed: 2 tests.
- Surrounding startup/preflight regression:
  `python -m pytest tests/test_workflow_preflight_full.py tests/test_doctor.py tests/test_cli_run_startup.py -q`
  passed: 63 tests, 2 existing aiohttp `NotAppKeyWarning` warnings.
- Static checks:
  `python -m ruff check src/symphony/workflow/preflight.py src/symphony/cli/doctor.py tests/test_workflow_preflight_full.py tests/test_doctor.py`
  passed; `python -m pyright src/symphony/workflow/preflight.py src/symphony/cli/doctor.py`
  passed.

---

## File-board delimiter recovery

## Goal

Keep a real agent from losing a live file-board ticket when it preserves the
ticket body and `state:` line but accidentally drops the YAML `---`
delimiters.

## Decision

Extend the file tracker's parser with a narrow delimiterless recovery path. A
Markdown file without frontmatter delimiters is treated as a ticket only when
the first line is a canonical YAML ticket key and the parsed prefix includes
`state`. For that recovered ticket, the tracker fills missing `id`,
`identifier`, and `title` from the filename, so the next state update rewrites
the file back to valid serialized YAML.

This fixes the real Gemini E2E failure where the ticket became:
`state: Verify` followed by Markdown evidence. Without recovery, the tracker
could no longer find `E2E-GEMINI`, so the worker parked the issue with
`issue_state_refresh_failed`.

- Rejected: accepting any Markdown file that contains `state:` somewhere in
  the body. That would turn notes into tickets.
- Rejected: papering over the refresh error in the orchestrator. The root cause
  is parser resilience for a known file-board corruption shape.
- Rejected: silently moving the issue without restoring canonical YAML. The
  board file must be repaired on the next tracker write so future tools see the
  normal format.

## Verification

- Focused regression:
  `python -m pytest tests/test_tracker_file.py::test_parse_ticket_file_recovers_missing_yaml_delimiters tests/test_tracker_file.py::test_file_tracker_update_state_restores_yaml_for_recovered_ticket tests/test_workflow_preflight_full.py::test_shipped_workflow_example_passes_dispatch_preflight tests/test_workflow_preflight_full.py::test_readme_quickstart_workflow_passes_dispatch_preflight -q`
  passed: 5 tests.
- Surrounding tracker/preflight regression:
  `python -m pytest tests/test_tracker_file.py tests/test_workflow_preflight_full.py tests/test_doctor.py -q`
  passed: 105 tests.
- Static checks:
  `python -m ruff check src/symphony/trackers/file.py tests/test_tracker_file.py tests/test_workflow_preflight_full.py README.md README.ko.md examples/WORKFLOW.smoke.md`
  passed; `python -m pyright src/symphony/trackers/file.py` passed.

---

## Shipped workflow budget examples

## Goal

Keep the new multi-stage `max_turns` preflight guard from rejecting the
quickstart workflows shipped in the README and smoke example.

## Decision

Raise the README quickstart and `examples/WORKFLOW.smoke.md` from
`max_turns: 3` to `max_turns: 4`, matching the default active-state path
`Todo -> In Progress -> Verify -> Learn`. Add regression tests that extract the
README quickstart workflow snippets and run the smoke example through
`validate_for_dispatch`.

- Rejected: exempting examples from the guard. They are the first workflows a
  new operator copies, so they must obey the same startup contract.
- Rejected: lowering the example active-state count. The docs should continue
  to demonstrate the default multi-stage flow.

## Verification

- README/example dispatch-preflight checks are included in:
  `python -m pytest tests/test_workflow_preflight_full.py -q`
  passed as part of the 105-test tracker/preflight regression above.
- Broader affected regression:
  `python -m pytest tests/test_orchestrator_dispatch.py tests/test_webapi.py tests/test_server_routes.py tests/test_workflow.py tests/test_tracker_file.py tests/test_workflow_preflight_full.py tests/test_doctor.py tests/test_cli_run_startup.py tests/test_web_static_contract.py -q`
  passed: 390 tests, 2 existing aiohttp `NotAppKeyWarning` warnings.
- Backend/lifecycle regression:
  `python -m pytest tests/test_agent_lifecycle_e2e.py tests/test_backend_contract.py tests/test_backends.py tests/test_backends_edges.py tests/test_backends_lifecycle.py tests/test_codex_approvals.py tests/test_claude_cache_tokens.py -q`
  passed: 239 tests.

---

## Source-scoped blocked RCA identifiers

## Goal

Prevent newly-created blocked-RCA tickets from inheriting stale runtime flags
from unrelated historical RCA tickets.

## Decision

Allocate file-board RCA tickets with a source-scoped prefix:
`RCA-<SOURCE-ID>-N` instead of the global `RCA-N`. The live Jira board exposed
the issue after the first auto-RCA sweep: a new `RCA-1` for `TASK-015`
inherited an old persisted pause flag from a previous `RCA-1` run. The source
ticket stayed safe, but the new RCA worker was born paused even though this was
a fresh recovery ticket.

Source-scoped IDs keep the operator-readable relationship while avoiding
collisions with prior generic RCA history. Existing operator pauses remain
sticky for the same issue id.

- Rejected: clearing all paused flags when creating any RCA. That would break
  the intentional pause contract for real tickets.
- Rejected: changing run-registry keys globally. Registry identity needs a
  larger compatibility design; the RCA allocator can avoid the collision
  without widening the change.
- Rejected: keeping global `RCA-N` and adding more duplicate-note checks. The
  collision happened below the tracker layer, in persisted runtime flags.

## Verification

- Focused allocator regression:
  `python -m pytest tests/test_orchestrator_dispatch.py::test_blocked_rca_create_uses_source_scoped_file_identifier tests/test_orchestrator_dispatch.py::test_tick_auto_opens_blocked_rca_ticket_once tests/test_orchestrator_dispatch.py::test_recover_blocked_issue_opens_rca_ticket_and_keeps_source_blocked -q`
  passed: 3 tests.
- Broader affected regression:
  `python -m pytest tests/test_orchestrator_dispatch.py tests/test_webapi.py tests/test_server_routes.py tests/test_workflow.py tests/test_tracker_file.py tests/test_workflow_preflight_full.py tests/test_doctor.py tests/test_cli_run_startup.py tests/test_web_static_contract.py -q`
  passed: 391 tests, 2 existing aiohttp `NotAppKeyWarning` warnings.
- Backend/lifecycle regression:
  `python -m pytest tests/test_agent_lifecycle_e2e.py tests/test_backend_contract.py tests/test_backends.py tests/test_backends_edges.py tests/test_backends_lifecycle.py tests/test_codex_approvals.py tests/test_claude_cache_tokens.py -q`
  passed: 239 tests.
- Static checks:
  `python -m ruff check ...` over the touched source/tests passed;
  `python -m pyright ...` over the touched source passed.

---

## Transient connection errors retry without operator pause

## Goal

Keep transient coding-agent network failures from parking a ticket in a
paused retry state that requires operator intervention before the agent gets a
normal retry.

## Decision

Classify `connection error`, `network error`, `connection reset`, and
`connection timed out` as retryable worker errors. The real Pi E2E failed on
the first turn with `turn_failed: Connection error.; stderr:` and Symphony
scheduled a retry, but the retry was marked paused for operator inspection.
The Pi CLI succeeded on a direct retry and then passed the full disposable
ticket lifecycle, so this class belongs with rate-limit/temporary-service
failures rather than hard backend crashes.

- Rejected: always pausing every worker `turn_error`. That is correct for hard
  crashes but wrong for transient provider/network failures that a retry can
  resolve.
- Rejected: hiding the retry. The retry row still records the cleaned error
  and attempt count; it just does not set the paused flag.
- Rejected: treating all stderr-bearing failures as retryable. Unknown backend
  crashes still pause with a visible reason.

## Verification

- Focused retry regression:
  `python -m pytest tests/test_orchestrator_dispatch.py::test_worker_exit_connection_error_retries_without_pause tests/test_orchestrator_dispatch.py::test_worker_exit_retryable_rate_limit_schedules_retry_without_pause tests/test_orchestrator_dispatch.py::test_worker_exit_error_auto_pauses_hard_failure_with_visible_reason -q`
  passed: 3 tests.
- Pi direct CLI check:
  `printf 'Return exactly pi-ok.\n' | timeout 120 pi --mode json -p ""`
  produced a valid JSON stream ending in `agent_end`.
- Pi real-agent E2E:
  `env SYMPHONY_E2E_AGENTS=pi SYMPHONY_E2E_TURN_TIMEOUT_MS=480000 SYMPHONY_E2E_AGENT_TIMEOUT_S=900 /opt/anaconda3/bin/python /private/tmp/symphony-real-agent-e2e-yIeicD/run_e2e.py`
  passed in 130.3 seconds with ticket `E2E-PI` ending in `Done`.
- AGY real-agent E2E retry after increasing only the outer harness wall-clock:
  `env SYMPHONY_E2E_AGENTS=agy SYMPHONY_E2E_TURN_TIMEOUT_MS=600000 SYMPHONY_E2E_AGENT_TIMEOUT_S=1500 SYMPHONY_E2E_BASE_PORT=19520 /opt/anaconda3/bin/python /private/tmp/symphony-real-agent-e2e-yIeicD/run_e2e.py`
  passed in 198.4 seconds with ticket `E2E-AGY` ending in `Done`.
- OpenCode real-agent E2E, per operator request instead of rerunning all seven:
  `env SYMPHONY_E2E_AGENTS=opencode SYMPHONY_E2E_TURN_TIMEOUT_MS=600000 SYMPHONY_E2E_AGENT_TIMEOUT_S=900 SYMPHONY_E2E_BASE_PORT=19720 /opt/anaconda3/bin/python /private/tmp/symphony-real-agent-e2e-yIeicD/run_e2e.py`
  passed in 178.4 seconds with ticket `E2E-OPENCODE` ending in `Done`.
- Broader affected regression:
  `python -m pytest tests/test_orchestrator_dispatch.py tests/test_webapi.py tests/test_server_routes.py tests/test_workflow.py tests/test_tracker_file.py tests/test_workflow_preflight_full.py tests/test_doctor.py tests/test_cli_run_startup.py tests/test_web_static_contract.py tests/test_workflow_pipeline_prompt.py tests/test_orchestrator_contracts.py tests/test_agent_lifecycle_e2e.py -q`
  passed: 474 tests, 2 existing aiohttp `NotAppKeyWarning` warnings.
- Backend/lifecycle regression:
  `python -m pytest tests/test_agent_lifecycle_e2e.py tests/test_backend_contract.py tests/test_backends.py tests/test_backends_edges.py tests/test_backends_lifecycle.py tests/test_codex_approvals.py tests/test_claude_cache_tokens.py -q`
  passed: 240 tests.
- Static checks:
  `python -m ruff check ...` over the touched source/tests passed;
  `python -m pyright ...` over the touched source passed.

---

## Legacy Human Review normalization

## Goal

Let existing boards continue after the Human Review policy change without
hand-editing every older completion handoff.

## Decision

Add a file-board poll-tick sweep that runs before candidate fetch. If a
terminal `Human Review` card is a legacy completion handoff, Symphony appends
`## Human Review Normalized` and moves it to `Done` so downstream dependencies
can proceed. The sweep is intentionally narrow: it only applies to file boards,
skips blocked-RCA tickets, skips explicit intervention/failure markers, and
requires completion evidence such as `Confirm Done`, `## As-Is -> To-Be
Report`, or `## Unblock Note`.

This preserves the new policy for future work while unfreezing historical
boards where `Human Review` meant "normal success awaiting confirmation".

- Rejected: treating every `Human Review` dependency as resolved. That would
  hide real critical/manual intervention.
- Rejected: requiring the operator to manually move every old handoff to
  `Done`. The orchestrator already has the full terminal card body and can
  make the narrow compatibility decision.
- Rejected: normalizing blocked-RCA `Human Review` cards. RCA source tickets
  still reopen only after a resolved RCA with no intervention marker.

## Verification

- Focused legacy-normalization regression:
  `python -m pytest tests/test_orchestrator_dispatch.py::test_tick_normalizes_legacy_human_review_confirm_done_before_candidates tests/test_orchestrator_dispatch.py::test_tick_normalizes_legacy_human_review_unblock_note_after_merge_failure tests/test_orchestrator_dispatch.py::test_tick_keeps_intervention_human_review_blocked tests/test_orchestrator_dispatch.py::test_tick_keeps_blocked_rca_at_human_review_blocked tests/test_orchestrator_dispatch.py::test_todo_with_human_review_blocker_remains_blocked tests/test_orchestrator_dispatch.py::test_tick_does_not_reopen_blocked_source_at_human_review -q`
  passed: 6 tests.
- Broader affected regression:
  `python -m pytest tests/test_orchestrator_dispatch.py tests/test_webapi.py tests/test_server_routes.py tests/test_workflow.py tests/test_tracker_file.py tests/test_workflow_preflight_full.py tests/test_doctor.py tests/test_cli_run_startup.py tests/test_web_static_contract.py tests/test_workflow_pipeline_prompt.py tests/test_orchestrator_contracts.py tests/test_agent_lifecycle_e2e.py -q`
  passed: 474 tests, 2 existing aiohttp `NotAppKeyWarning` warnings.
- Backend/lifecycle regression:
  `python -m pytest tests/test_agent_lifecycle_e2e.py tests/test_backend_contract.py tests/test_backends.py tests/test_backends_edges.py tests/test_backends_lifecycle.py tests/test_codex_approvals.py tests/test_claude_cache_tokens.py -q`
  passed: 240 tests.
- Static checks:
  `python -m ruff check ...` over the touched source/tests passed;
  `python -m pyright ...` over the touched source passed.

---

## Human Review intervention-only policy

## Goal

Stop treating `Human Review` as the normal success path. Normal successful
Learn work should close as `Done`; `Human Review` should mean a real
critical/manual intervention or explicit operator review remains.

## Decision

Change the default file and Linear prompts so Learn appends `## Wiki Updates`
and `## As-Is -> To-Be Report`, runs the Final History Gate, and sets `Done`
for normal success. The `## Human Review` branch remains available only when
the ticket records a manual decision, credential, external system, approval, or
critical intervention that the agent cannot resolve locally.

Update the Learn contract validator to accept either the final report or an
intervention handoff, and update blocker dependency handling so `Human Review`
does not satisfy `blocked_by` or blocked-RCA resolution. RCA tickets now reopen
their blocked source only when the RCA reaches a successful terminal state such
as `Done` and neither card records operator intervention.

- Rejected: keeping `Learn -> Human Review -> Done` as the default. That made
  every successful ticket look manually blocked and caused RCA tickets parked
  in Human Review to reopen blocked sources too early.
- Rejected: removing `Human Review` entirely. The board still needs a terminal
  intervention state for real manual decisions and explicit operator review.
- Rejected: treating any terminal state as dependency success. `Blocked`,
  `Archive`, `Duplicate`, `Cancelled`, and `Human Review` are not proof that a
  dependency was resolved.

## Verification

- Focused prompt/contract/lifecycle checks:
  `python -m pytest tests/test_orchestrator_contracts.py::test_learn_contract_requires_completion_record_or_human_review tests/test_orchestrator_contracts.py::test_learn_contract_passes_with_completion_record tests/test_orchestrator_contracts.py::test_learn_contract_passes_with_intervention_handoff tests/test_workflow_pipeline_prompt.py::test_learn_stage_writes_wiki_and_done_or_intervention_handoff tests/test_workflow_pipeline_prompt.py::test_base_prompt_declares_four_stage_pipeline_and_skip_learn tests/test_workflow_pipeline_prompt.py::test_pipeline_demo_ticket_is_a_complete_worked_example tests/test_agent_lifecycle_e2e.py::test_full_todo_to_done_pipeline_rebuilds_backend_per_phase tests/test_agent_lifecycle_e2e.py::test_lifecycle_stops_each_intermediate_backend_exactly_once tests/test_agent_lifecycle_e2e.py::test_file_board_e2e_auto_triage_dispatches_and_reaches_done -q`
  passed: 11 tests.
- Focused RCA blocker checks:
  `python -m pytest tests/test_orchestrator_dispatch.py::test_todo_with_human_review_blocker_remains_blocked tests/test_orchestrator_dispatch.py::test_tick_reopens_blocked_source_after_resolved_rca tests/test_orchestrator_dispatch.py::test_tick_does_not_reopen_blocked_source_at_human_review tests/test_orchestrator_dispatch.py::test_tick_does_not_reopen_blocked_source_after_failed_rca tests/test_orchestrator_dispatch.py::test_tick_does_not_reopen_source_when_rca_needs_operator_intervention tests/test_orchestrator_dispatch.py::test_tick_does_not_reopen_source_with_recorded_operator_action -q`
  passed: 6 tests.
- Broader affected regression:
  `python -m pytest tests/test_orchestrator_dispatch.py tests/test_webapi.py tests/test_server_routes.py tests/test_workflow.py tests/test_tracker_file.py tests/test_workflow_preflight_full.py tests/test_doctor.py tests/test_cli_run_startup.py tests/test_web_static_contract.py tests/test_workflow_pipeline_prompt.py tests/test_orchestrator_contracts.py tests/test_agent_lifecycle_e2e.py -q`
  passed: 474 tests, 2 existing aiohttp `NotAppKeyWarning` warnings.
- Backend/lifecycle regression:
  `python -m pytest tests/test_agent_lifecycle_e2e.py tests/test_backend_contract.py tests/test_backends.py tests/test_backends_edges.py tests/test_backends_lifecycle.py tests/test_codex_approvals.py tests/test_claude_cache_tokens.py -q`
  passed: 240 tests.
- Static checks:
  `python -m ruff check ...` over the touched source/tests passed;
  `python -m pyright ...` over the touched source passed.

---

## Blocked RCA source reopen enforcement

## Goal

Make the orchestrator, not only the RCA worker prompt, responsible for moving
a source ticket out of `Blocked` once its RCA ticket is resolved.

## Decision

Add a poll-tick sweep for resolved blocked-RCA tickets. When a Symphony-created
RCA card reaches a successful terminal state such as `Done`, and neither the
RCA nor the source records operator intervention, the orchestrator finds the
structured source ticket, appends `## Blocked RCA Resolved`, and moves the
source back to the configured Todo lane. RCA cards that end in failure or
intervention terminals such as `Blocked`, `Cancelled`, `Archive`, or
`Human Review` do not reopen the source.

This fixes the live `127.0.0.1:9999` Jira Symphony board behavior where
RCA handoff state lived only in the worker prompt. The prompt was correct, but
the control plane did not enforce the handoff. After the Human Review policy
above, that enforcement deliberately excludes `Human Review`; only a resolved
RCA with no intervention marker can reopen the source.

- Rejected: rely only on the RCA worker to edit the source ticket. That is the
  fragile path that failed on the live board.
- Rejected: reopen the source when the RCA is still `Blocked` or in
  `Human Review`. That would hide a real unresolved blocker or manual
  intervention requirement.
- Rejected: send the source straight to Done. The source must continue through
  the configured Todo/In Progress/Verify/Learn workflow after unblocking.

## Verification

- Focused RCA handoff regression:
  `python -m pytest tests/test_orchestrator_dispatch.py::test_tick_reopens_blocked_source_after_resolved_rca tests/test_orchestrator_dispatch.py::test_tick_does_not_reopen_blocked_source_after_failed_rca -q`
  passed: 2 tests.
- Broader affected regression:
  `python -m pytest tests/test_orchestrator_dispatch.py tests/test_webapi.py tests/test_server_routes.py tests/test_workflow.py tests/test_tracker_file.py tests/test_workflow_preflight_full.py tests/test_doctor.py tests/test_cli_run_startup.py tests/test_web_static_contract.py -q`
  passed: 398 tests, 2 existing aiohttp `NotAppKeyWarning` warnings.

---

## Terminal cleanup grace clock

## Goal

Prevent a terminal ticket from holding a worker slot indefinitely when the
backend keeps emitting activity after the board already moved terminal.

## Decision

Track `RunningEntry.terminal_seen_at` and measure terminal cleanup grace from
the first reconcile tick that observes the terminal tracker state. Previously
the cleanup grace was measured from `last_codex_timestamp`; a chatty backend
could keep that fresh forever, so a card in `Human Review` or `Done` stayed in
the live `running` map and blocked later work.

This was visible on the live `127.0.0.1:9999` board after `RCA-1` reached
`Human Review`: `/api/v1/state` still showed it running and reconciliation
kept logging `reconcile_skip_active_worker`.

- Rejected: lower the global stall timeout. That targets active work, not
  terminal cleanup after a tracker state change.
- Rejected: cancel immediately on any terminal observation. A short grace
  window still protects workers that are already exiting cleanly.
- Rejected: use latest backend progress as the terminal grace clock. That is
  the behavior that let keepalives starve cleanup.

## Verification

- Focused terminal cleanup regression:
  `python -m pytest tests/test_orchestrator_dispatch.py::test_reconcile_terminate_terminal_commits_before_remove tests/test_orchestrator_dispatch.py::test_reconcile_terminate_terminal_skips_commit_when_auto_off tests/test_orchestrator_dispatch.py::test_reconcile_terminal_cleanup_uses_terminal_seen_not_event_age -q`
  passed: 3 tests.

---

## AGY state-dir doctor guard

## Goal

Catch managed-sandbox AGY failures before a ticket enters the worker loop.

## Decision

Add an AGY-specific doctor check for
`~/.gemini/antigravity-cli` writability. The real AGY E2E failed inside the
managed Codex sandbox with `operation not permitted` while AGY tried to create
`brain/`, `conversations/`, and `cache/` files under that directory. Rerunning
the same E2E outside the sandbox passed, proving the failure was an operator
environment constraint, not a Symphony stage-transition bug.

The guard is intentionally a preflight failure for `agent.kind: agy` only.
Symphony should not dispatch AGY when the CLI cannot write its own state; it
should tell the operator to run outside the sandbox or grant the necessary
home-directory write access.

- Rejected: redirecting `HOME` to a workspace-local directory. AGY auth did
  not survive simple home redirection and attempted a fresh OAuth flow.
- Rejected: copying OAuth credentials into ticket workspaces. That would be a
  security regression.
- Rejected: treating AGY empty output as success. The board must still move
  through real states and evidence.

## Verification

- Focused doctor regression:
  `python -m pytest tests/test_doctor.py::test_agy_state_dir_skipped_for_non_agy tests/test_doctor.py::test_agy_state_dir_passes_when_home_state_is_writable tests/test_doctor.py::test_agy_state_dir_fails_when_state_is_not_writable tests/test_doctor.py::test_run_checks_returns_one_result_per_check -q`
  passed: 4 tests.
- AGY real-agent E2E:
  sandboxed run failed with AGY `operation not permitted` under
  `~/.gemini/antigravity-cli`; unsandboxed rerun
  `env SYMPHONY_E2E_AGENTS=agy SYMPHONY_E2E_TURN_TIMEOUT_MS=480000 /opt/anaconda3/bin/python /private/tmp/symphony-real-agent-e2e-yIeicD/run_e2e.py`
  passed in 494.9 seconds with ticket `E2E-AGY` ending in `Done`.
- Broader affected regression:
  `python -m pytest tests/test_orchestrator_dispatch.py tests/test_webapi.py tests/test_server_routes.py tests/test_workflow.py tests/test_tracker_file.py tests/test_workflow_preflight_full.py tests/test_doctor.py tests/test_cli_run_startup.py tests/test_web_static_contract.py -q`
  passed: 396 tests, 2 existing aiohttp `NotAppKeyWarning` warnings.
- Backend/lifecycle regression:
  `python -m pytest tests/test_agent_lifecycle_e2e.py tests/test_backend_contract.py tests/test_backends.py tests/test_backends_edges.py tests/test_backends_lifecycle.py tests/test_codex_approvals.py tests/test_claude_cache_tokens.py -q`
  passed: 240 tests.
- Static checks:
  `python -m ruff check ...` over the touched source/tests passed;
  `python -m pyright ...` over the touched source passed.

---

## Idle paused RCA resume

## Goal

Let an operator resume a blocked-RCA worker that was born idle-paused from a
persisted runtime flag, even when no running worker exists yet.

## Decision

Extend resumable issue resolution to include `_paused_issue_ids`, not only
running workers and retry-held workers. The live Jira board exposed this after
a newly-created `RCA-1` inherited an old pause flag: `/api/v1/RCA-1/resume`
returned `issue_not_resumable`, so the operator had no API path to release the
idle-paused RCA.

The source ticket still stays Blocked until the RCA resolves; this change only
restores the control-plane path needed to start an intentionally paused issue.

- Rejected: auto-clearing pause flags during dispatch eligibility. That would
  ignore real operator pauses.
- Rejected: making the HTTP route accept any identifier. Resume should still
  be constrained to known paused/running/retry-held issues.
- Rejected: moving source tickets back to Todo directly. The RCA ticket must
  resolve the blocker first.

## Verification

- Focused resume regression:
  `python -m pytest tests/test_orchestrator_dispatch.py::test_find_resumable_issue_id_resolves_idle_paused_file_identifier tests/test_server_routes.py::test_resume_route_releases_idle_paused_file_issue tests/test_server_routes.py::test_resume_route_releases_paused_retry_worker -q`
  passed: 3 tests.
- Live board proof:
  `POST /api/v1/RCA-1/resume` returned `changed: true`; subsequent
  `/api/v1/state` showed `RCA-1` running and `paused: false`.

---

## AGY fresh-turn default

## Goal

Keep AGY workers from losing staged Symphony prompts after the first state
transition.

## Decision

Change AGY's default `resume_across_turns` to `false`. The real AGY E2E
advanced `Todo -> In Progress` on turn 1, then continuation turns with
`agy --print - --continue` returned no actionable state change and eventually
blocked on the no-stage-change watchdog. Starting fresh per turn keeps the
current stage prompt authoritative. Workflows that know their local AGY CLI
handles continuation can still opt in with `agy.resume_across_turns: true`.

- Rejected: raising no-stage-change budgets. That would hide a prompt-delivery
  failure and slow down blocker detection.
- Rejected: disabling AGY continuation support entirely. The explicit opt-in
  remains useful for operators whose AGY build handles `--continue` correctly.
- Rejected: treating empty AGY continuation output as success. The board must
  move stages or surface a real blocker.

## Verification

- Focused AGY regression:
  `python -m pytest tests/test_backends.py::test_agy_workflow_config_defaults_and_antigravity_alias tests/test_backends.py::test_agy_plain_text_stdout_is_completed_and_appends_permissions tests/test_backends.py::test_agy_continuation_adds_continue_without_duplicate_permissions tests/test_backends.py::test_agy_default_continuation_starts_fresh_turn -q`
  passed: 4 tests.
- Broader affected regression:
  `python -m pytest tests/test_orchestrator_dispatch.py tests/test_webapi.py tests/test_server_routes.py tests/test_workflow.py tests/test_tracker_file.py tests/test_workflow_preflight_full.py tests/test_doctor.py tests/test_cli_run_startup.py tests/test_web_static_contract.py -q`
  passed: 393 tests, 2 existing aiohttp `NotAppKeyWarning` warnings.
- Backend/lifecycle regression:
  `python -m pytest tests/test_agent_lifecycle_e2e.py tests/test_backend_contract.py tests/test_backends.py tests/test_backends_edges.py tests/test_backends_lifecycle.py tests/test_codex_approvals.py tests/test_claude_cache_tokens.py -q`
  passed: 240 tests.
- Static checks:
  `python -m ruff check ...` over the touched source/tests passed;
  `python -m pyright ...` over the touched source passed.

---

## Reconcile test terminal grace

## Goal

Keep the pre-push full pytest gate from hanging when reconcile tests exercise
terminal-state cleanup.

## Decision

Update `test_reconcile_isolates_per_issue_failures` to model an expired
terminal grace period before expecting worker cancellation. Production
reconcile intentionally waits briefly after first seeing a terminal state so a
worker can exit through its natural completion path; the old test treated the
first terminal observation as immediately cancellable, then awaited a parked
worker that was correctly still running.

The shared test drain helper now fails fast if a parked worker was not
cancelled, instead of waiting for the worker's long sleep.

- Rejected: removing the production terminal grace. It protects real workers
  from losing completion artifacts during a state-transition race.
- Rejected: bypassing the pre-push hook. The hook surfaced a legitimate
  regression in the test harness.

## Verification

- Reproduced hang:
  `.git/symphony-quality/ci-dev-venv/bin/python -m pytest tests/test_orchestrator_reconcile.py::test_reconcile_isolates_per_issue_failures -vv -o faulthandler_timeout=20`
  hung in the event loop before the fix.
