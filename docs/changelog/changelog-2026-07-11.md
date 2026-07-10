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
