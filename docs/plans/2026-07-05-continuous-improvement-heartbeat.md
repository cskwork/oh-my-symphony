# Continuous Improvement Heartbeat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` for parallelizable implementation
> slices or `superpowers:executing-plans` for sequential execution. Keep the
> checkbox state in this document current as each step lands.

**Goal:** add a default-off Symphony web setting that periodically verifies the
integrated baseline, writes deterministic evidence, and registers discovered
production-readiness defects as normal Kanban tickets.

**Theory:** Symphony models a board as a production line. Feature tickets are
work orders, the merged branch is the assembled product, stage contracts are
quality gates, and the requested heartbeat is a conservative inspector that
keeps checking the assembled product after normal work is merged. It must not
become a hidden code-editing agent. Its job is to prove the current baseline,
record what was checked, and create precise tickets when the baseline is not
production-ready.

**Architecture:** add a typed workflow config surface, expose only safe controls
in the web app, schedule bounded background verification outside the tick loop,
write machine-owned reports, and register findings through the existing tracker
mutation path. The implementation follows the `supergoal` baseline-first
discipline and Symphony's existing final integration defect loop.

**Tech stack:** Python 3.12, pytest, ruff, pyright, aiohttp web API, static web
board, Symphony file tracker, orchestrator tick loop, workflow YAML mutation,
Markdown report artifacts, optional browser and DB checks.

**Primary target repo:** `/Users/danny/Documents/PARA/Resource/symphony-multi-agent`

**Source:** GPT Pro review of the prepared Symphony bundle, incorporated as a
docs-first plan. Chrome automation was unavailable, so the response was provided
through the manual bundle handoff.

---

## Grounded Facts

- `docs/changelog/changelog-2026-07-05.md` already defines a full integration
  defect loop: release/app-delivery tickets prove the committed target branch,
  register defects as Kanban bugs, add those bugs as blockers, and loop until
  the merged target passes.
- `skills/symphony-skill/SKILL.md` and
  `skills/symphony-skill/reference/delegation.md` require integration defects
  to be registered back into the board rather than kept as an informal list.
- `docs/architecture.md` is the resident map of public package surfaces and
  must be updated when public surfaces or runtime paths change.
- File-board writes already have lock/compare-and-swap and identifier
  allocation paths. The heartbeat must reuse those paths instead of rewriting
  Markdown cards directly.

## Assumptions

- The setting belongs in `WORKFLOW.md`, because existing web settings mutate
  workflow configuration through `symphony.workflow.mutate`.
- First implementation supports automatic issue registration for the file
  tracker. Other trackers may report `unsupported_tracker` until a safe create
  and update contract exists.
- The first web surface exposes only `enabled` and `interval_ms`. Command lists,
  templates, environment variables, and paths are not browser-editable.
- `superloop` is treated as the requested continuous-loop behavior pattern, not
  as a separate installed runtime dependency in this repo.

## Non-Goals

- Do not enable the heartbeat by default.
- Do not let the browser configure arbitrary commands.
- Do not run heavy verification inline inside the orchestrator tick loop.
- Do not consume normal agent slots with the heartbeat itself.
- Do not directly edit product code.
- Do not directly rewrite ticket Markdown outside tracker APIs.
- Do not auto-run destructive DB migrations, resets, seeds, or production DB
  commands.
- Do not treat optional browser or DB checks as hard failures when they are not
  configured.

## Proposed Configuration

```yaml
continuous_improvement:
  enabled: false
  interval_ms: 1800000
  ticket_prefix: CI
  max_tickets_per_run: 5
  require_idle_board: true
```

Only `enabled` and `interval_ms` are editable in the first web UI. The other
fields are trusted workflow configuration parsed from `WORKFLOW.md`.

Parsing rules:

- Missing section means disabled, interval `1_800_000` ms.
- `enabled` accepts only YAML booleans. Strings like `"false"` and integers like
  `1` are validation errors.
- `interval_ms` accepts only positive integers, rejects booleans/strings, and
  enforces a lower bound of `60_000` ms.
- `max_tickets_per_run` is clamped or validated as a small positive integer.
- `ticket_prefix` is identifier-safe and defaults to `CI`.

## Baseline Rubric

Every heartbeat run produces a report with these result states:

- `passed`: check ran and succeeded.
- `failed`: check ran and produced a product-readiness defect.
- `not_available`: optional check is not configured or the required tool is not
  installed.
- `not_proven`: the baseline cannot be trusted, such as dirty worktree, missing
  target branch, unreachable upstream, or command infrastructure failure.

Initial default checks:

- baseline source proof: branch, SHA, dirty status, upstream alignment when
  available.
- unit/integration: `python -m pytest -q`.
- lint: `python -m ruff check src tests`.
- type check: `python -m pyright`.
- targeted API/static web contracts for narrow profiles when configured.
- browser QA only when dependencies and required environment flags are present.
- DB checks only from explicit read-only configuration or recognized safe status
  probes.

The run must verify the integrated target branch, not whichever worker branch
happens to be checked out. Use a temporary worktree or read-only Git commands;
do not `git checkout` in the host worktree.

## Ticket Contract

Each created ticket must include:

- rubric item.
- failing command or check name.
- normalized failure summary.
- capped evidence excerpt with obvious secret redaction.
- expected behavior.
- proposed fix boundary.
- verification commands.
- `CI Fingerprint: <hash>`.

The registrar must:

- compute a stable fingerprint per finding.
- search active tickets for an existing fingerprint.
- append an observation or skip when a duplicate exists.
- create at most `max_tickets_per_run` new tickets.
- use `FileBoardTracker.create_with_next_identifier(prefix="CI")` for file
  tracker boards.
- report `unsupported_tracker` instead of crashing on trackers without safe
  creation support.

## Runtime Status

Expose read-only heartbeat status through `/api/v1/state` or
`/api/v1/continuous-improvement/status`:

- `enabled`
- `interval_ms`
- `in_flight`
- `current_phase`
- `last_started_at`
- `last_finished_at`
- `next_due_at`
- `last_result`
- `last_error`
- `tickets_created`
- `skipped_reason`
- `last_verified_branch`
- `last_verified_sha`

The settings card should make the operator able to distinguish disabled,
waiting, skipped because the board is busy, running, failed, and completed.

## Planned File Changes

Primary implementation files:

- `src/symphony/workflow/config.py`
- `src/symphony/workflow/builder.py`
- `src/symphony/workflow/mutate.py`
- `src/symphony/webapi.py`
- `src/symphony/web/static/app.js`
- `src/symphony/web/static/style.css` if styling is needed
- `src/symphony/orchestrator/core.py`
- `src/symphony/continuous_improvement.py` (new)
- `src/symphony/trackers/file.py` only if a small tracker helper is required

Tests:

- `tests/test_workflow.py`
- `tests/test_webapi.py`
- `tests/test_web_static_contract.py`
- `tests/test_continuous_improvement.py` (new)
- focused orchestrator scheduler tests in the existing orchestrator test module
  that best matches the scheduler seam

Docs:

- `docs/continuous-improvement/rubric.md`
- `docs/continuous-improvement/ticket-template.md`
- `docs/continuous-improvement/latest.md`
- `docs/architecture.md`
- `docs/changelog/changelog-2026-07-05.md`

## Implementation Sequence

### 1. Docs-First Rubric and Architecture Contract

**Files:**

- Create `docs/continuous-improvement/rubric.md`.
- Create `docs/continuous-improvement/ticket-template.md`.
- Create `docs/continuous-improvement/latest.md`.
- Modify `docs/architecture.md`.
- Modify `docs/changelog/changelog-2026-07-05.md`.

**Steps:**

- [ ] Define the baseline verification rubric and pass/fail/not-available/
  not-proven semantics.
- [ ] Define the ticket body template and fingerprint rule.
- [ ] Define the no-code-edit invariant.
- [ ] Define default-off behavior and command safety rules.
- [ ] Define the cross-process lease requirement.
- [ ] Define the tracker support matrix.
- [ ] Verify with `git diff --check`.
- [ ] Commit `docs: define continuous improvement heartbeat`.

### 2. Config Model, Parser, and Workflow Mutation

**Files:**

- Modify `src/symphony/workflow/config.py`.
- Modify `src/symphony/workflow/builder.py`.
- Modify `src/symphony/workflow/mutate.py`.
- Modify `tests/test_workflow.py`.

**Steps:**

- [ ] Add failing tests for default disabled config and default interval.
- [ ] Add failing tests for strict boolean validation.
- [ ] Add failing tests for interval validation.
- [ ] Add failing tests proving workflow mutation preserves comments/order.
- [ ] Add `ContinuousImprovementConfig`.
- [ ] Add strict parsing helpers for booleans and interval values.
- [ ] Add `ServiceConfig.continuous_improvement`.
- [ ] Add `set_continuous_improvement_settings(...)`.
- [ ] Verify with `python -m pytest tests/test_workflow.py -q`.
- [ ] Commit `feat: add continuous improvement workflow config`.

### 3. Web API and Settings UI

**Files:**

- Modify `src/symphony/webapi.py`.
- Modify `src/symphony/server.py` only if route wiring requires it.
- Modify `src/symphony/web/static/app.js`.
- Modify `src/symphony/web/static/style.css` if needed.
- Modify `tests/test_webapi.py`.
- Modify `tests/test_web_static_contract.py`.

**Steps:**

- [ ] Add failing API tests for `GET /api/v1/workflow` including the config.
- [ ] Add failing API tests for
  `PUT /api/v1/workflow/continuous-improvement`.
- [ ] Add failing validation tests for malformed JSON, wrong schema, Host guard,
  and content-type guard.
- [ ] Add static UI contract tests for the toggle, interval field, save action,
  and status labels.
- [ ] Add the workflow payload field and strict PUT handler.
- [ ] Reload workflow state after mutation.
- [ ] Add a settings card exposing only enable/disable and interval.
- [ ] Add read-only status rendering.
- [ ] Verify with `python -m pytest tests/test_webapi.py -q`.
- [ ] Verify with `python -m pytest tests/test_web_static_contract.py -q`.
- [ ] Commit `feat: expose continuous improvement settings`.

### 4. Heartbeat Scheduler Skeleton

**Files:**

- Modify `src/symphony/orchestrator/core.py`.
- Create `src/symphony/continuous_improvement.py`.
- Add focused orchestrator scheduler tests.

**Steps:**

- [ ] Add failing tests proving disabled config schedules no task.
- [ ] Add failing tests proving enabled-but-not-due schedules no task.
- [ ] Add failing tests proving a due heartbeat schedules exactly one task.
- [ ] Add failing tests proving a second tick while in flight does not schedule
  another task.
- [ ] Add failing tests proving runner exceptions update status and do not kill
  the tick loop.
- [ ] Add failing tests proving `require_idle_board` postpones while workers are
  running or retrying.
- [ ] Add scheduler fields: `_improvement_task`,
  `_last_improvement_monotonic`, `_next_improvement_due_monotonic`, and
  `_improvement_status`.
- [ ] Add `_maybe_schedule_continuous_improvement(config_snapshot)`.
- [ ] Run work in a bounded background task with subprocess timeouts.
- [ ] Add a fakeable durable lease abstraction.
- [ ] Verify with the focused orchestrator tests.
- [ ] Commit `feat: schedule continuous improvement heartbeat`.

### 5. Check Runner and Baseline Verification

**Files:**

- Modify `src/symphony/continuous_improvement.py`.
- Create or extend `tests/test_continuous_improvement.py`.
- Modify `docs/continuous-improvement/rubric.md`.

**Steps:**

- [ ] Add failing tests for baseline branch/SHA/dirty-status capture.
- [ ] Add failing tests for safe command execution with `shell=False`.
- [ ] Add failing tests for timeout handling.
- [ ] Add failing tests for output cap and redaction.
- [ ] Add failing tests for result normalization.
- [ ] Add failing tests for `passed`, `failed`, `not_available`, and
  `not_proven`.
- [ ] Implement baseline source proof without changing the host worktree.
- [ ] Implement predefined check registry.
- [ ] Implement command runner with argv arrays, timeout, output caps, and
  redaction.
- [ ] Implement browser and DB `not_available` handling.
- [ ] Verify with `python -m pytest tests/test_continuous_improvement.py -q`.
- [ ] Commit `feat: add continuous improvement check runner`.

### 6. Docs Report Writer

**Files:**

- Modify `src/symphony/continuous_improvement.py`.
- Create/update `docs/continuous-improvement/latest.md`.
- Extend `tests/test_continuous_improvement.py`.

**Steps:**

- [ ] Add failing tests for deterministic report rendering.
- [ ] Add failing tests that only machine-owned report sections are rewritten.
- [ ] Add failing tests that architecture drift becomes a finding instead of a
  broad prose rewrite.
- [ ] Render last branch/SHA, commands, outcomes, skipped checks, and ticket
  registration summary.
- [ ] Verify with `python -m pytest tests/test_continuous_improvement.py -q`.
- [ ] Commit `feat: write continuous improvement reports`.

### 7. Kanban Registrar and De-Duplication

**Files:**

- Modify `src/symphony/continuous_improvement.py`.
- Modify `src/symphony/trackers/file.py` only if a focused helper is necessary.
- Extend `tests/test_continuous_improvement.py`.
- Extend `tests/test_webapi.py` if API-visible status changes.

**Steps:**

- [ ] Add failing tests for fingerprint stability.
- [ ] Add failing tests for duplicate finding suppression.
- [ ] Add failing tests for max tickets per run.
- [ ] Add failing tests for unsupported tracker behavior.
- [ ] Add failing tests that ticket creation uses the normal tracker path.
- [ ] Implement `IssueFinding` normalization and fingerprinting.
- [ ] Search active tickets for existing fingerprints.
- [ ] Create one ticket per unique finding, capped by config.
- [ ] Append or skip when a duplicate active ticket exists.
- [ ] Report unsupported trackers as status, not a crash.
- [ ] Verify with `python -m pytest tests/test_continuous_improvement.py -q`.
- [ ] Verify with `python -m pytest tests/test_webapi.py -q`.
- [ ] Commit `feat: register heartbeat findings as kanban tickets`.

### 8. Final Integration and Full Verification

**Files:**

- Modify `docs/architecture.md`.
- Modify `docs/changelog/changelog-2026-07-05.md`.
- Keep all touched tests green.

**Steps:**

- [ ] Re-read the whole plan and requirements from this document.
- [ ] Confirm default config does not schedule the heartbeat.
- [ ] Confirm enabling through web persists to `WORKFLOW.md`.
- [ ] Confirm a due heartbeat runs once.
- [ ] Confirm failures become normal Kanban tickets.
- [ ] Confirm duplicate failures do not create repeated tickets.
- [ ] Confirm tick loop survives runner errors.
- [ ] Confirm architecture docs describe the new config, web API, scheduler,
  runner, report writer, and registrar.
- [ ] Run `python -m pytest -q`.
- [ ] Run `python -m ruff check src tests`.
- [ ] Run `python -m pyright`.
- [ ] Commit `docs: record continuous improvement heartbeat delivery proof`.

## Edge Cases

- Missing `continuous_improvement` section.
- `enabled: "false"` must be rejected, not treated as true.
- Interval is zero, negative, boolean, string, or unreasonably low.
- Setting is toggled off while a run is in flight.
- Multiple orchestrator processes point at the same workflow directory.
- Normal Symphony workers are already running when the heartbeat becomes due.
- Git is unavailable.
- Git repo is dirty.
- Target branch does not exist locally.
- `dev` or `main` does not exist.
- Upstream remote is unavailable.
- QA command times out.
- QA command emits huge output.
- QA command emits secret-looking values.
- Browser QA dependencies are absent.
- DB tooling is absent.
- DB tooling exists but only destructive commands are available.
- Ticket tracker is not file-backed.
- Existing active ticket already has the same fingerprint.
- More findings are discovered than `max_tickets_per_run`.
- Report file is missing, unwritable, or manually edited.
- Web request has malformed JSON.
- Web request has valid JSON but wrong schema.
- Web request violates Host/content-type guard.

## Rejected Alternatives

- **Default-on heartbeat:** too risky because it runs checks and writes tickets.
- **Browser-editable shell commands:** unnecessary command execution surface.
- **Synthetic normal ticket for the heartbeat:** consumes normal agent capacity
  and can create recursive workflow behavior.
- **Direct application-code edits:** bypasses the Kanban workflow this feature
  should reinforce.
- **Direct Markdown ticket writes:** bypasses existing tracker invariants.
- **One omnibus ticket per run:** hides fix boundaries.
- **Unlimited tickets per run:** can flood the board every interval.
- **Testing whichever branch is checked out:** does not prove the integrated
  dev/main baseline.
- **Automatic DB migrations/seeds/resets:** unsafe for local or shared DBs.
- **Treating optional browser/DB checks as hard failures:** creates false
  negatives when those stacks are not configured.

## Verification Plan

Plan-only verification:

```bash
test -f docs/plans/2026-07-05-continuous-improvement-heartbeat.md
rg -n 'TB[D]|TO[D]O|implement[ ]later|fill[ ]in details|Similar[ ]to Task' \
  docs/plans/2026-07-05-continuous-improvement-heartbeat.md
git diff --check
```

Implementation verification after the runtime work lands:

```bash
python -m pytest tests/test_workflow.py -q
python -m pytest tests/test_webapi.py -q
python -m pytest tests/test_web_static_contract.py -q
python -m pytest tests/test_continuous_improvement.py -q
python -m pytest -q
python -m ruff check src tests
python -m pyright
```
