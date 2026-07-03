# 2026-07-03 - OpenCode backend support

## Goal

Add OpenCode as a first-class Symphony backend so operators can set
`agent.kind: opencode` globally or on individual file-board tickets.

## Decisions

### 1. Use `opencode run` for the first backend

The backend will spawn `opencode run --format json --auto` once per turn,
append the Symphony prompt as the documented `message` argument, and use
`--session <id>` for continuation after OpenCode reports a session id and
`resume_across_turns` is enabled.

- Rejected: managing a persistent `opencode serve` process. It may reduce
  cold-start cost later, but it adds port/password/lifecycle failure modes to
  the first integration.
- Rejected: implementing the ACP protocol now. It is a larger protocol layer
  than needed to make OpenCode usable from Symphony's existing backend shape.

### 2. Keep token telemetry best-effort

OpenCode JSON output is parsed for known token keys when present. Missing or
unknown usage fields leave the standard buckets at zero instead of failing a
turn that otherwise completed.

- Rejected: requiring one exact JSON schema before accepting the backend. The
  documented CLI contract is `--format json` raw events, so tolerant parsing is
  safer across OpenCode versions.

### 3. Keep TUI issue descriptions plain text

Full-suite verification exposed that Textual raises when tree-sitter is
installed but its Markdown language package is absent. The new/edit issue
dialogs now use plain `TextArea` fields instead of requesting Markdown syntax
highlighting, because the field's purpose is ticket text entry and a missing
optional highlighter should not block the TUI.

- Rejected: adding a hard tree-sitter Markdown dependency. It would make a
  cosmetic editor feature part of the runtime install contract.

# 2026-07-03 - Operator Trust Program spec

## Goal

Define the next improvement program for Symphony as a product-facing trust
layer, not only a reliability backlog.

## Decisions

### 1. Combine options 1-3 into one phased spec

The selected scope covers operator trust signals, reliability backbone
completion, and onboarding polish. Keeping them in one spec makes the outcome
testable end to end: a healthy system must expose health, explain stuck work,
avoid leaked backend processes, and teach new operators how to verify the same
surfaces.

- Rejected: three separate specs. They would duplicate decisions around health,
  run history, and doctor checks.
- Rejected: extending only the reliability plan. That would keep the work as an
  engineering checklist and under-specify the user-facing trust surfaces.

### 2. Keep Symphony single-node and file-first

The spec preserves Markdown tickets as the human source of truth and the
existing SQLite run registry as the runtime ledger. Health, attention, and run
history are additive surfaces over the current architecture.

- Rejected: external queues, distributed locks, or managed observability for
  this program. They do not match the current product shape or failure budget.

### 3. Re-audit backend lifecycle before editing

Recent commits may already satisfy parts of the older R2/R7 handoff. The spec
therefore requires implementation to prove current behavior first, then change
only the failing gaps.

- Rejected: blindly reimplementing the older handoff. The live branch is ahead
  of that document, so direct replay risks churn and duplicate fixes.

# 2026-07-03 - Operator Trust Program audit and spec alignment

## Goal

Verify the day-old Operator Trust Program spec (`8838bfa`) against the code
actually on `dev`, update the spec to match reality, and leave a detailed
implementation plan for the remaining gaps.

## Decisions

### 1. Record the audit as a spec artifact, not a chat summary

`docs/spec/operator-trust-program/audit.md` completes spec task 1.1 with
file/test evidence per requirement. Verification: focused suites (48 passed)
and the full suite (942 passed, 2 skipped) are green on `dev`, confirming
`1818d60` superseded the older `feat/reliability-hardening` WIP and its five
red backend tests.

- Rejected: re-implementing from the 2026-07-02 reliability handoff. The
  audit shows backend lifecycle (R4) is fully landed; replaying the handoff
  would churn proven code.

### 2. Align spec data models with the landed implementation

The Health Snapshot ships as `ok`/`degraded` with `tick.*`/`run_registry.*`
sub-objects, and the Attention Signal ships as `{kind, label, message}`.
design.md now documents those shapes; `starting`, `workflow_path`,
`severity`, and `due_at` remain as additive planned fields.

- Rejected: renaming implemented fields to the spec's original names
  (`healthy`, `consecutive_tick_errors`, `severity`). That breaks
  `/api/v1/health` and attention consumers for cosmetic gain and violates
  the spec's own additive-only compatibility NFR.

### 3. Reuse `status` as the run-history terminal cause

The `runs` table has no `error` column; terminal causes such as
`force_ejected_zombie` already live in `status`. The Run History Row exposes
`status` as-is instead of adding a schema migration.

- Rejected: adding an `error` column. A migration plus dual-write brings new
  failure modes for information the ledger already stores.

### 4. Re-scope doctor's prompt-file check to a visibility row

Missing prompt files already fail config load with
`ConfigValidationError("prompt file not found")` before doctor's checks run,
so existence is enforced upstream. Doctor gains a row listing the resolved
prompt template paths instead.

- Rejected: a duplicate existence check inside doctor. It could never fire —
  config load raises first.

Remaining work is sequenced in
`docs/plans/2026-07-03-operator-trust-implementation.md` (slices A-K:
health `starting` status, owner-aware port messages, full attention
taxonomy, TUI rendering, run history query/API/CLI/drawer, doctor prompt
row, smoke health check, README proof path, final verification).

# 2026-07-03 - Public docs surface sync

## Goal

Bring the public documentation surfaces in line with the current `dev` branch:
five agent backends, the built-in web board, file-tracker write APIs, and the
SQLite reliability ledger.

## Decisions

### 1. Update current-facing docs, not historical plans

The README pair, landing page, architecture map, package description, and
top-level changelog are the surfaces a new operator reads first. Historical
plans under `docs/plans/` and ticket-specific notes stay as records of when
they were written.

- Rejected: sweeping old plan text. That would blur audit history and create a
  larger review surface without changing the current operator contract.

### 2. Describe reliability as single-node and local

The docs now say the run registry provides local SQLite leases and issue flags,
and that Markdown tickets remain the human source of truth. This matches the
implementation without implying a distributed queue or restartable worker
reattach.

- Rejected: marketing it as generic crash recovery. The current code persists
  claims and retry state, but an in-process worker is still lost on a hard
  crash.

### 3. Keep English, Korean, and landing-page copy in sync

The Korean README and landing-page i18n strings were updated in the same pass
as the English README so backend counts, endpoint names, and unsupported
surfaces do not diverge.

- Rejected: only updating the English README. The repo already presents Korean
  as a first-class operator surface.

# 2026-07-03 - README navigation and Pages deploy ownership

## Goal

Make the long GitHub README easier to navigate and replace the opaque legacy
Pages dynamic deploy path with a repo-owned workflow that can be rerun and
debugged directly.

## Decisions

### 1. Add a compact top-level table of contents

The README now links to the major operator sections before the long feature
and setup walkthrough. The Korean README mirrors the same navigation so the two
entry docs stay structurally aligned.

- Rejected: a full heading-by-heading table. The README is already long, and a
  dense table would push the useful content farther down.

### 2. Use an explicit static Pages workflow

The generated `pages-build-deployment` job uploaded the `docs/` artifact, then
failed in the deploy step with only `Deployment failed, try again later.` A
checked-in workflow keeps the same `docs/` source but makes permissions,
actions versions, concurrency, and reruns visible in the repo.

- Rejected: rerunning the failed dynamic deployment as the only fix. The rerun
  failed at the same deploy step and did not expose a repo-side error.

# 2026-07-03 - Operator Trust Program implementation

## Goal

Complete the remaining Operator Trust Program tasks so an operator can trust
the system from public surfaces: health, attention signals, run history,
doctor, smoke checks, and fresh-clone documentation.

## Decisions

### 1. Keep health and attention changes additive

`health.status` now reports `starting` before the first completed tick and the
payload includes `workflow_path`. Attention payloads keep `kind`, `label`, and
`message`, then add `severity` and `due_at` where useful. This preserves
existing consumers while giving operators clearer startup and stuck-work
diagnostics.

- Rejected: renaming the existing health or attention fields to match newer
  spec wording. That would turn a trust-program polish pass into a breaking
  API change.

### 2. Derive attention from existing runtime facts

The attention taxonomy is built from existing retry entries, cancelled/stalled
running entries, lease-loss markers, budget-exhausted state, and issue-scoped
tracker errors. Priority is deterministic:
`stalled > lease_blocked > budget_exhausted > tracker_error > retry_scheduled`.

- Rejected: adding a second issue-state store for attention. It would create a
  reconciliation problem with Markdown tickets, leases, and the SQLite run
  registry without improving operator truth.

### 3. Reuse the run registry as the history ledger

Run history uses the existing SQLite registry and exposes bounded reads through
`/api/v1/runs`, `symphony runs`, and the web drawer. Filtering accepts both
the internal issue id and the operator-facing identifier so UI calls can stay
human-readable.

- Rejected: adding a schema migration or separate web history cache. The
  registry already stores attempt rows and terminal statuses; duplicating it
  would add failure modes for the same evidence.

### 4. Prefer deterministic local smoke over external-agent dispatch

Final runtime proof used a temporary file-board workflow under `/private/tmp`
with `python -m symphony.mock_codex`, a writable workspace root, and the
production server command. This verifies health, board CRUD, refresh, static
assets, workflow stats, and run-history reachability without depending on a
live Claude/Codex account.

- Rejected: claiming `doctor ./WORKFLOW.md` success in this sandbox. The repo
  workflow correctly reported environmental failures: the configured
  `~/symphony_workspaces` root is not writable here and the worktree has no
  local `kanban/` directory.

## Verification

- Focused attention/API/UI batch:
  `PYTHONPATH=/private/tmp/symphony-operator-trust-run/src .../.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py tests/test_run_registry.py tests/test_webapi.py tests/test_web_static_contract.py tests/test_tui.py`
  -> `190 passed`.
- Focused touched-slices batch:
  `PYTHONPATH=/private/tmp/symphony-operator-trust-run/src .../.venv/bin/python -m pytest -q tests/test_orchestrator_health.py tests/test_run_registry.py tests/test_webapi.py tests/test_cli_main_routing.py tests/test_cli_run_startup.py tests/test_doctor.py tests/test_web_api_smoke_script.py tests/test_web_static_contract.py tests/test_tui.py`
  -> `137 passed, 2 warnings`.
- Full suite:
  `PYTHONPATH=/private/tmp/symphony-operator-trust-run/src .../.venv/bin/python -m pytest -q`
  -> `965 passed, 2 skipped, 2 warnings`.
- Static checks: `compileall` on touched Python packages, `git diff --check`,
  README proof-command grep, and workflow/snippet lane grep passed.
- Runtime checks: temp workflow `doctor` exited 0 with only the legacy
  board-viewer warning; live smoke against `http://127.0.0.1:54017` passed
  all nine checks; `/api/v1/health` returned `status: ok` and
  `/api/v1/runs?limit=5` returned an empty run list.
