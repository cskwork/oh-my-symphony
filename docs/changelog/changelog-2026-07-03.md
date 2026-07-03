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
