# 003i - Default-off operator profile example

Route: DOCS / CONFIG BUILD

Status: closed - verified

Blocked by: 001, 002, 003b

Unblocks: 003v

## Goal and interface contract

Document one usable, default-off AIDT resolver profile covering `jira_intake`, `aidt_routing`, and `aidt_worktree`,
including environment indirection and every safety constraint required before enablement.

## Bounded file ownership

- `examples/WORKFLOW.aidt.example.md`
- `tests/test_aidt_workflow_example.py`
- `README.md`

The completed docs/config slice is bounded to these three files and remains below 500 net lines. Product validation
was not changed.

## Acceptance criteria

- The example is syntactically loadable and keeps all three blocks disabled by default.
- It documents exact allowed keys, file tracker/board and absolute root requirements, `preserve`, absent hooks,
  disabled generic commit/merge, fixed production ref, and environment-variable names rather than secret values.
- The docs explain that enablement can fetch/create temporary ticket worktrees but never authorizes live cleanup,
  Jira writes, merge, push, Jenkins, or deployment.
- Automated config loading and `symphony doctor` validate the shipped example without contacting Jira or AIDT.

## Proof

- Recorded build proof is in `/private/tmp/f003-operator-example-result.md`; it does not close this ticket.
- Fresh verification repeats the focused loader test, affected config/contract tests, static checks, Markdown path/
  discoverability checks, secret pattern scan, and whitespace checks without network or live filesystem mutation.

Fresh lifecycle/example verification passed 7 cases; a temporary-copy doctor exited 0 with 12 PASS checks and the
expected legacy viewer warning. All three feature blocks remain disabled and secrets remain environment-indirected.

## Scope boundary

Does not activate the profile, touch a real repository, change runtime behavior, or edit operator credentials.
