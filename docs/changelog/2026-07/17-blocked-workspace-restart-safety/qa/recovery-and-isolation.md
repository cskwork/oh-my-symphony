# Recovery, cleanup, and isolation evidence

The rejecting hook was disabled non-destructively as `pre-receive.disabled`, the disposable card was
moved from `Blocked` to `Learn`, and `POST /api/v1/refresh` queued `poll` and `reconcile`. A real Codex
worker re-entered Learn and returned the ticket to Done without duplicating its existing sections.

Symphony then reported:

```text
auto_commit_completed stdout="auto_commit: nothing to commit"
auto_merge_nothing_to_apply
hook_completed hook=before_remove
```

Final checkpoint:

- Ticket: `Done`.
- Local `dev`, `origin/dev`, and remote `refs/heads/dev`:
  `670c1c07141508f262047f7bf6f82fd2fdd92c27`.
- Feature branch retained at `b6ec063acee74e41726b4378b58249745f5d8716` and is an ancestor of
  `dev`.
- Merge delta from baseline: exactly `1`; the only merge is `670c1c0` with parents
  `2b73710` and `b6ec063`.
- Final ticket workspace: absent; only the main worktree remains registered.
- API before shutdown: health `ok`, `running=0`, `retrying=0`.
- `git fsck --no-dangling --no-progress`: exit `0`, no output.
- Service: `shutdown_initiated`, `shutdown_complete`; port `19117` stopped.

Raw recovery and final-state evidence:

- `/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh/evidence/recovery-refresh.json`
- `/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh/evidence/service-restart.log`
- `/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh/evidence/final-before-stop.txt`
- `/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh/evidence/isolation-and-teardown.txt`

The isolated product worktree stayed on
`codex/auto-merge-retry-safety-20260717@ee04e5dcc2f188f6686127be537039e5b197006a`; no disposable ticket
appeared in its board. The original checkout stayed at the same local and remote `dev` SHA with only
its two pre-existing untracked July 12 documents, and its board also had no `E2E-BRS-001` card.
