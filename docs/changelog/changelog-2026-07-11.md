# Changelog — 2026-07-11

## Codex registry access without full sandbox escape

- Problem: shipped workflow examples and the OneShot template showed only the
  `workspace-write` string shorthand. Codex v2 translates that to a turn policy
  with network disabled, so dependency installation could fail even though
  filesystem writes were correctly confined.
- Decision: keep `thread_sandbox: workspace-write` and use
  `turn_sandbox_policy: {type: workspaceWrite, networkAccess: true}` in coding
  workflow templates. Document the string shorthand as the offline option and
  the tagged turn policy as the package-registry option.
- Why: network permission and filesystem confinement are independent controls.
  Enabling the narrower turn-level network flag fixes registry access without
  exposing the host filesystem.
- Rejected: recommend `danger-full-access` for registry failures. It grants
  unrelated filesystem and process capabilities and is unnecessary when only
  network access is missing.
- Rejected: change the runtime default globally. Some operators intentionally
  run offline workers; keeping the choice in `WORKFLOW.md` makes the trust
  boundary explicit and preserves existing workflows.
- Rejected: change the repository's live `WORKFLOW.md`. Its Codex workers use
  full access for a separately proven macOS Chromium bootstrap limitation, not
  for package downloads.

## Terminal auto-merge upstream proof

- Problem: a worker could push during Learn, then write late evidence before
  exiting Done. The terminal fallback correctly committed and auto-merged that
  evidence, but stopped at the local target branch, leaving the ticket Done
  while its configured upstream was behind.
- Decision: after creating the terminal merge commit, push the target branch
  to its configured upstream and verify the remote ref equals the local target
  SHA. A rejected push or mismatched read-back fails the existing merge gate,
  which moves the ticket to Blocked and preserves the workspace.
- Why: the target branch models the dependency history other tickets trust.
  Done is safe only when that complete terminal history is confirmed at the
  shared upstream; local-only workflows remain unchanged when no upstream is
  configured.
- Rejected: suppress terminal fallback commits after Learn. Late QA evidence
  is valid work and silently discarding it would restore the original data-loss
  risk that fallback commits prevent.
- Rejected: rely on the worker's Learn-stage push. It happens before the exit
  fallback and therefore cannot publish files written afterward.
- Rejected: force-push or roll back the local merge after a failed push. A
  force-push could overwrite concurrent remote work, while rollback would hide
  the recoverable terminal snapshot; blocking the ticket exposes the failure
  without destructive Git operations.
