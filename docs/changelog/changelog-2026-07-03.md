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
