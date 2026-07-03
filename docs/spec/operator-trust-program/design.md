# Design: Operator Trust Program

## Overview

The Operator Trust Program turns existing reliability work into visible,
testable operator truth. Symphony stays single-node and file-first: Markdown
tickets remain the human source of truth, and SQLite registry data remains the
runtime ledger. The design adds shared health, attention, run-history, backend
lifecycle, doctor, smoke, and onboarding surfaces without introducing a new
platform dependency.

Implementation is phased. Phase 1 builds the operator-facing trust layer.
Phase 2 closes or verifies backend lifecycle reliability gaps that make the
trust layer meaningful. Phase 3 makes the fresh-clone path prove the same
signals through docs and commands.

## Architecture

```text
Agent backends
  -> backend lifecycle cleanup
  -> Orchestrator runtime state
  -> RunRegistry durable ledger
  -> health + attention + run history APIs
  -> TUI, web board, CLI, doctor, smoke checks
```

The existing orchestrator remains the coordination point. It already owns the
tick loop, running entries, retry entries, lease checks, and tracker calls, so
it is the right place to derive Health Snapshots and Attention Signals.
RunRegistry remains the durable reader for Run History and persistent safety
state. Web, TUI, and CLI consume the same data instead of each inventing a
different definition of "stuck" or "healthy".

## Components and interfaces

### Health Core

**Purpose:** Provide one runtime answer for workflow health.

**Responsibilities:**
- Extend `Orchestrator.health()` with status, last tick time, consecutive tick errors, registry status, tracker failure counters, and Degraded Reasons.
- Thread the same snapshot into `/api/v1/health` and the existing state snapshot.
- Preserve reachable-server behavior when workflow state is degraded.

**Interface:** input orchestrator runtime fields and registry guard result / output Health Snapshot dict / dependencies `orchestrator/core.py`, `server.py`, `webapi.py`.

_Requirements: 1.1, 1.2, 1.3, 1.4, 7.2_

### Attention Taxonomy

**Purpose:** Explain why a ticket needs operator attention.

**Responsibilities:**
- Extend `issue_attention(issue)` to return deterministic Attention Signals.
- Include attention payloads in board and issue-detail API responses.
- Render attention text in web cards, drawer detail, and TUI detail/card surfaces.

**Interface:** input issue plus runtime maps (`_running`, `_retry`, budget flags, lease status, tracker errors) / output optional Attention Signal / dependencies `orchestrator/core.py`, `webapi.py`, `web/static/app.js`, `web/static/style.css`, TUI files.

_Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 7.3_

### Run History Reader

**Purpose:** Make the existing registry audit trail usable.

**Responsibilities:**
- Add a read-only registry query for recent runs with optional issue filter and bounded limit.
- Add `GET /api/v1/runs` and a `symphony runs` CLI command.
- Add a compact issue drawer history section for recent attempts.

**Interface:** input `issue_id | None`, `limit` / output bounded Run History list / dependencies `orchestrator/run_registry.py`, API router, CLI router, web drawer.

_Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

### Backend Lifecycle Completion

**Purpose:** Ensure failed or ejected workers do not leak into retries.

**Status (audited 2026-07-03):** Verified done on `dev` by commit `1818d60`;
see `audit.md` Requirement 4. Remaining work is documentation of the accepted
OpenCode malformed-stream deviation, not code.

**Responsibilities:**
- Re-audit current backend lifecycle behavior before editing. (done — `audit.md`)
- Verify or finish POSIX process groups, bounded termination, EOF turn failure, malformed-stream failure, and force-eject process-group kill. (done)
- Keep existing behavior when tests already prove it.

**Interface:** input backend subprocess handles and emitted pid events / output classified failures and cleanup results / dependencies `backends/*`, `_shell.py`, `orchestrator/core.py`, backend lifecycle tests.

_Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 7.4_

### Doctor v2 and Smoke Check

**Purpose:** Make preflight and live verification match real operator failure modes.

**Responsibilities:**
- Expand doctor prompt-file, port ownership, command availability, and optional online checks.
- Add a smoke command or script that verifies health, board state, and read-only API paths.
- Print actionable failures with endpoint, status, and next diagnostic step.

**Interface:** input workflow path and optional service URL / output pass-warn-fail rows and smoke result / dependencies `cli/doctor.py`, service metadata, smoke script tests.

_Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

### Fresh-Clone Quickstart

**Purpose:** Give new operators one truthful path to a working board.

**Responsibilities:**
- Lead README and README.ko with file tracker quickstart.
- Label Linear/Jira and other advanced paths as credentialed secondary examples.
- Document doctor, health, run history, and smoke checks as the proof path.
- Keep example workflow stages aligned with the four active lanes.

**Interface:** input current examples and verified commands / output updated docs / dependencies README files, examples, skills references.

_Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

## Data models

### Health Snapshot

The base shape below is already implemented (`Orchestrator.health()`,
`core.py:609`); fields marked "planned" are the additive remainder. See
`audit.md` for the field-by-field verification.

| Field | Type | Required | Validation |
|---|---|---|---|
| status | string | yes | one of `starting` (planned), `ok`, `degraded` |
| degraded_reasons | list[string] | yes | stable machine-readable strings (`tick_loop_dead`, `tick_failures`, `tracker_fetch_failures`, `run_registry_error`) |
| version | string | yes | package version |
| generated_at | ISO timestamp | yes | UTC timestamp string |
| workflow_path | string | planned | path already known to the process; additive |
| tick | object | yes | `alive`, `started`, `last_completed_at` (null before first tick), `seconds_since_last`, `consecutive_failures`, `error_count`, `loop_restarts`, `last_error` |
| tracker | object | yes | `consecutive_fetch_failures` |
| run_registry | object | yes | `enabled`, `error_count`, `last_error` (concise, no secrets) |
| counts | object | yes | `running`, `retrying` |

**Relationships:** Derived by orchestrator; embedded in `/api/v1/health` and state snapshot.

### Attention Signal

The implemented base shape is `{kind, label, message}`
(`Orchestrator.issue_attention`, `core.py:735`); `severity` and `due_at` are
additive. A separate `reason` field was dropped — `message` already carries
the concise cause.

| Field | Type | Required | Validation |
|---|---|---|---|
| kind | string | yes | one of `budget_exhausted` (implemented), `stalled`, `lease_blocked`, `tracker_error`, `retry_scheduled` (planned) |
| label | string | yes | short human-readable badge text |
| message | string | yes | concise operator-facing cause, no secrets |
| severity | string | planned | `info`, `warning`, or `error`; additive |
| due_at | ISO timestamp or null | planned | present for retry signals when known; additive |

**Priority when multiple causes apply (highest first):** `stalled`,
`lease_blocked`, `budget_exhausted`, `tracker_error`, `retry_scheduled`.
Permanent or intervention-needing causes outrank transient ones; the order is
pinned by tests.

**Relationships:** Attached to issue card and issue-detail payloads.

### Run History Row

The `runs` table already stores everything below; no schema change is
needed. There is no separate `error` column — terminal cause is carried in
`status` (for example `force_ejected_zombie`), so the row exposes `status`
as-is instead of a derived error field.

| Field | Type | Required | Validation |
|---|---|---|---|
| run_id | string | yes | registry primary key |
| issue_id | string | yes | issue identifier from tracker |
| identifier | string | yes | human-facing ticket identifier |
| attempt | integer | yes | existing registry value |
| attempt_kind | string | yes | existing registry value |
| agent_kind | string | no | existing registry value when available |
| status | string | yes | existing registry status; doubles as terminal cause |
| started_at | ISO timestamp | yes | registry timestamp |
| completed_at | ISO timestamp or null | no | null for active/orphaned rows |
| workspace_path | string or null | no | no existence guarantee |

**Relationships:** Read from RunRegistry; exposed by API, CLI, and web drawer.

## Error handling

| Scenario | Response | Action |
|---|---|---|
| Tick loop error | Health Snapshot becomes degraded | Log structured error, increment counter, continue with backoff |
| Registry unavailable | Health and run-history report actionable registry error | Guard registry access; do not kill API server |
| Backend EOF before completion | Turn fails promptly | Emit classified failed event and stop backend |
| Malformed backend stream | Turn fails after configured streak | Record malformed-stream reason |
| Force-eject with pid | Kill process group before retry | Log cleanup result; continue retry scheduling |
| Port busy | Startup or doctor prints owner-aware message when possible | Suggest service status or alternate port |
| Workflow config broken | Web shows server-up/workflow-broken, not unreachable | Preserve HTTP error body and UI state |
| Run-history invalid limit | Clamp or reject consistently | Test the chosen behavior |

## Testing strategy

- **Unit:** Health Snapshot derivation, Attention Signal priority, RunRegistry history query, backend lifecycle helpers, doctor row classification.
- **Integration:** `/api/v1/health`, `/api/v1/state`, `/api/v1/runs`, issue detail attention payloads, `symphony runs`, doctor checks.
- **Lifecycle:** Backend EOF, malformed stream, process-group termination, force-eject pid cleanup.
- **UI contract:** Static web tests for attention labels, health/degraded copy, and run-history drawer strings; TUI tests for attention rendering where existing patterns allow.
- **Docs:** README command snippets and example workflow references checked by grep or focused tests when the repo has existing doc guards.
- **Smoke:** One real local service smoke against a file board before final closeout, using an alternate port if `9999` is busy.

## Decisions

### Decision: Use one phased Operator Trust Program

**Context:** The user selected options 1-3: operator trust layer, reliability backbone completion, and onboarding polish.

**Options considered:**
1. One phased spec - pros: one product outcome, less duplicated health/reliability wording / cons: larger scope.
2. Three separate specs - pros: cleaner ownership / cons: repeated decisions and weaker end-to-end story.
3. Extend only the reliability plan - pros: fastest docs path / cons: stays engineering-backlog shaped and under-specifies user-facing trust.

**Decision:** Use one phased spec.

**Rationale:** Operator trust depends on all three areas. Health badges are weak if backend lifecycle leaks still exist, and onboarding is weak if it does not teach the trust surfaces.

### Decision: Reuse RunRegistry instead of a new event store

**Context:** Run leases and run records already exist in SQLite.

**Options considered:**
1. Reuse RunRegistry - pros: smallest surface, existing tests and migration patterns / cons: bounded by current schema.
2. Add a separate observability store - pros: cleaner analytics separation / cons: new migration and sync failure modes.

**Decision:** Reuse RunRegistry.

**Rationale:** The program needs operational truth, not analytics infrastructure. Existing registry data is enough for health and run history.

### Decision: Keep Health Snapshot as the shared truth

**Context:** Web, TUI, CLI, and smoke checks all need the same answer.

**Options considered:**
1. Shared `Orchestrator.health()` object - pros: one contract, easy API and test reuse / cons: orchestrator owns more presentation-adjacent fields.
2. Let each surface compute health - pros: local flexibility / cons: inconsistent operator answers.

**Decision:** Use shared `Orchestrator.health()` plus additive API fields.

**Rationale:** Inconsistent health answers are the problem this program is meant to remove.

### Decision: Align spec data models with the landed implementation

**Context:** The 2026-07-03 audit (`audit.md`) found that commit `1818d60` had
already implemented the Health Snapshot and the first Attention Signal with
field names and status values that differ from this spec's original tables
(`ok` vs `healthy`, `tick.consecutive_failures` vs `consecutive_tick_errors`,
`{kind, label, message}` vs `{kind, severity, message, due_at, reason}`).

**Options considered:**
1. Update the spec to the implemented shape and add remaining fields additively - pros: no breaking API change, existing tests stay authoritative / cons: spec loses its original cleaner naming.
2. Rename the implemented fields to match the spec - pros: spec stays pristine / cons: breaks `/api/v1/health` and attention consumers for cosmetic gain, violates the compatibility NFR.

**Decision:** Update the spec to the implemented shape; add `starting`,
`workflow_path`, `severity`, and `due_at` as additive fields only.

**Rationale:** The compatibility NFR says additions must be additive unless a
break is explicitly approved. Field renames are exactly the kind of break the
NFR forbids, and the implemented shape is already covered by passing tests.

### Decision: Audit before editing backend lifecycle code

**Context:** Recent commits may already have completed parts of R2/R7 after the older handoff was written.

**Options considered:**
1. Re-audit current behavior first - pros: avoids rewriting landed fixes / cons: one extra task.
2. Re-implement from the older handoff - pros: direct execution / cons: high risk of churn and duplicate fixes.

**Decision:** Re-audit first, then implement only failing gaps.

**Rationale:** The live branch is ahead of the handoff. The spec should preserve proven behavior and close only real gaps.
