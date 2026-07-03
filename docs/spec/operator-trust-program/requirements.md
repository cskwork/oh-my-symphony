# Requirements: Operator Trust Program

## Introduction

Symphony already has the core pieces of a resilient single-node orchestrator:
run leases, a registry, a web board, a TUI, doctor checks, and backend process
controls. The remaining product gap is operator trust: when something is
healthy, degraded, stuck, retrying, lease-blocked, or misconfigured, the system
must say so directly and prove it through the same surfaces operators use.

This spec combines the selected 1-3 scope into one phased program: operator
trust signals first, reliability backbone completion second, and public
onboarding polish third.

## Glossary

| Term | Definition |
|---|---|
| Operator Trust Program | The phased work that makes Symphony's health, stuck-work status, reliability behavior, and onboarding proof visible and testable. |
| Health Snapshot | A structured runtime status object that reports whether Symphony is healthy, degraded, or unable to operate. |
| Degraded Reason | A machine-readable cause explaining why the Health Snapshot is not healthy. |
| Attention Signal | A per-ticket status cause shown to operators when a ticket needs attention or is waiting on a retry, lease, budget, or external system. |
| Run History | A read-only view of completed, failed, orphaned, or expired run-registry records. |
| Backend Lifecycle Cleanup | Process-group termination, bounded reap, malformed-stream handling, EOF handling, and force-eject cleanup for agent backends. |
| Doctor v2 | Expanded preflight checks that explain startup risks before operators dispatch real work. |
| Smoke Check | A repeatable command that proves a local Symphony service exposes the expected health and board APIs. |
| Fresh-Clone Quickstart | The README and example workflow path that a new operator can follow from clone to healthy local board. |

## Requirements

### Requirement 1: Health Snapshot

**User story:** As an operator, I want one health answer for the running
workflow, so that I can tell the difference between healthy idle, degraded, and
broken states without reading logs first.

**Acceptance criteria (EARS):**
1.1 WHEN the orchestrator is running THEN the system SHALL expose a Health Snapshot through `Orchestrator.health()`, `/api/v1/health`, and the existing state snapshot.
1.2 WHEN the tick loop records consecutive errors THEN the system SHALL mark the Health Snapshot as degraded and include at least one Degraded Reason.
1.3 WHEN the registry is unavailable or locked beyond the configured guard THEN the system SHALL mark the Health Snapshot as degraded without killing the API server.
1.4 WHEN no tick has completed yet THEN the system SHALL return a Health Snapshot that states startup is pending instead of reporting healthy.
1.5 IF the workflow cannot bind its configured port THEN the startup path SHALL explain whether the port is owned by this workflow service or by another process when that can be determined.

**Edge cases:**
- Missing workflow file -> startup fails with an actionable error, not a Python traceback.
- Broken workflow config -> API surfaces the workflow error distinctly from transport failure.
- Registry guard exception -> one degraded reason is recorded; the next tick can recover.
- Port check cannot identify owner -> message still gives host, port, and next diagnostic command.

### Requirement 2: Attention Signals

**User story:** As an operator, I want tickets to explain why they are waiting
or blocked, so that I can decide whether to wait, retry, pause, or intervene.

**Acceptance criteria (EARS):**
2.1 WHEN a ticket has exhausted its token or turn budget THEN the system SHALL expose a `budget_exhausted` Attention Signal.
2.2 WHEN a retry is scheduled THEN the system SHALL expose a `retry_scheduled` Attention Signal with due time and reason.
2.3 WHEN a worker is stalled after cancellation begins THEN the system SHALL expose a `stalled` Attention Signal.
2.4 WHEN a run loses or is blocked by a lease THEN the system SHALL expose a `lease_blocked` Attention Signal.
2.5 WHEN tracker calls fail in a way that affects a ticket THEN the system SHALL expose a `tracker_error` Attention Signal with a concise reason.
2.6 WHEN the web board or TUI renders a ticket with an Attention Signal THEN the UI SHALL show the cause without requiring the operator to open log files.

**Edge cases:**
- Multiple causes -> deterministic priority order is used and documented in tests.
- Terminal tickets -> stale runtime signals do not appear after cleanup.
- Missing due time -> retry signal still renders with the available reason.
- Unknown signal kind -> web/TUI fall back to readable text instead of failing render.

### Requirement 3: Run History

**User story:** As an operator, I want to inspect recent run attempts, so that I
can answer what happened to a ticket after retries, restarts, or backend exits.

**Acceptance criteria (EARS):**
3.1 WHEN a Run History request is made without an issue filter THEN the system SHALL return the most recent runs with a bounded default limit.
3.2 WHEN a Run History request includes an issue identifier THEN the system SHALL return only runs for that issue.
3.3 WHEN no runs exist THEN the system SHALL return an empty list with a successful response.
3.4 WHEN a run includes an error or orphaned status THEN the system SHALL include the status and concise error fields already stored or derived from the registry.
3.5 WHEN the CLI requests Run History THEN the command SHALL print a compact table suitable for terminal use.

**Edge cases:**
- Invalid limit -> reject or clamp consistently; never load unbounded history.
- Missing registry -> health is degraded and run-history request returns an actionable error.
- Workspace path unavailable -> row still appears with an empty or null workspace field.

### Requirement 4: Backend Lifecycle Cleanup

**User story:** As an operator, I want failed or ejected agent backends to stop
cleanly, so that retries do not overlap with leaked agent processes in the same
workspace.

**Acceptance criteria (EARS):**
4.1 WHEN an agent backend starts on POSIX THEN the system SHALL start it in a process group.
4.2 WHEN a backend is stopped THEN the system SHALL terminate the process group, wait with a bounded timeout, and escalate to kill if needed.
4.3 WHEN a backend stream reaches EOF before turn completion THEN the system SHALL fail the turn promptly with a classified backend failure.
4.4 WHEN consecutive malformed backend output exceeds the configured limit THEN the system SHALL fail the turn with a malformed-stream reason.
4.5 WHEN a worker is force-ejected while a child process id is known THEN the system SHALL kill the recorded process group before scheduling the retry.
4.6 WHEN existing tests prove the behavior already works THEN implementation SHALL keep the existing code and record the proof instead of rewriting it.

**Edge cases:**
- Backend process already exited -> stop path does not send unnecessary signals.
- Windows -> behavior falls back safely where POSIX process groups are unavailable.
- Codex app-server pid and generic agent pid both present -> deterministic pid choice is used.
- Kill fails due missing process -> failure is logged but cleanup continues.

### Requirement 5: Doctor v2 and Smoke Check

**User story:** As a new or returning operator, I want preflight and smoke
checks that match the real service path, so that I can fix setup issues before
dispatching work.

**Acceptance criteria (EARS):**
5.1 WHEN `symphony doctor` runs THEN the system SHALL check prompt file existence for every configured stage.
5.2 WHEN the configured port is busy THEN Doctor v2 SHALL report whether the port appears owned by this workflow service when service metadata is available.
5.3 WHEN an agent kind is configured THEN Doctor v2 SHALL check command availability and report auth checks only when a cheap reliable probe exists.
5.4 WHEN the Smoke Check targets a running local service THEN it SHALL verify health, board state, and at least one read-only API path.
5.5 WHEN the Smoke Check fails THEN it SHALL print the failed endpoint, status, and next diagnostic step.

**Edge cases:**
- Optional online tracker ping disabled -> doctor does not require network.
- Auth probe unavailable -> warning with manual guidance, not fake pass/fail.
- Service up but workflow broken -> smoke reports server-up/workflow-broken separately from unreachable.

### Requirement 6: Fresh-Clone Quickstart

**User story:** As a first-time operator, I want one canonical file-tracker
quickstart, so that I can run Symphony locally without choosing among stale or
advanced examples.

**Acceptance criteria (EARS):**
6.1 WHEN a reader starts from README THEN the Fresh-Clone Quickstart SHALL lead with the file tracker path.
6.2 WHEN advanced trackers are documented THEN the docs SHALL label them as secondary paths with required credentials.
6.3 WHEN the docs describe runtime verification THEN they SHALL point to Doctor v2, `/api/v1/health`, Run History, and the Smoke Check.
6.4 WHEN examples reference workflow stages THEN they SHALL match the current four active lanes.
6.5 WHEN docs mention reliability behavior THEN they SHALL avoid claiming high availability or external orchestration that Symphony does not provide.

**Edge cases:**
- Korean README present -> English and Korean quickstart claims stay aligned.
- Example moved or renamed -> all README and skill references update in the same change.
- Fresh clone lacks optional browser dependencies -> quickstart still works without them.

### Requirement 7: Compatibility and Rollout

**User story:** As a maintainer, I want the program to land in small verified
slices, so that existing workflows keep working while trust surfaces improve.

**Acceptance criteria (EARS):**
7.1 WHEN implementation starts THEN the first task SHALL re-audit current code and tests because some reliability items may already be landed.
7.2 WHEN a slice changes API payloads THEN existing clients SHALL keep their current fields unless a documented additive field is introduced.
7.3 WHEN a slice changes visible UI text THEN static contract tests SHALL be updated with the new intentional strings.
7.4 WHEN a slice changes backend lifecycle behavior THEN focused lifecycle tests and the full Python suite SHALL pass before commit.
7.5 WHEN the program finishes THEN the repo SHALL have a changelog entry summarizing decisions, rejected alternatives, and verification evidence.

**Edge cases:**
- Existing local worktree dirty -> implementation stages only the current slice's files.
- Full suite has documented environment-dependent failures -> final report names them and shows focused gates.
- Live port already in use -> verification uses service metadata or an alternate port, not a false success.

## Non-functional requirements

- **Performance:** `/api/v1/health` and `/api/v1/runs` SHALL complete in under 200 ms p95 on a local board with 100 tickets and 1,000 run rows.
- **Reliability:** A tick-loop exception SHALL not permanently stop orchestration while the process remains alive.
- **Security:** Health, attention, and history payloads SHALL not expose secrets, raw tokens, or full backend stderr beyond existing concise error tails.
- **Compatibility:** Public API additions SHALL be additive unless a breaking change is explicitly approved and documented.
- **Accessibility:** Web attention badges SHALL include readable text, not color-only meaning.

## Out of scope

- Multi-node high availability.
- External queues, distributed locks, or managed observability services.
- New workflow stages or a new ticket state machine.
- Replacing Markdown tickets as the human source of truth.
- Rebuilding the web UI framework.
- Always-on browser E2E as part of default `pytest`.

## Open questions

- None. The approved direction is one phased Operator Trust Program covering options 1-3, with health and operator signals first.
