# Recovery and final consistency evidence

The rejecting hook was disabled non-destructively as `pre-receive.disabled`. The card was moved `Blocked -> Learn`, and `POST /api/v1/refresh` returned:

```json
{"queued":true,"coalesced":false,"requested_at":"2026-07-17T09:11:36Z","operations":["poll","reconcile"]}
```

The real Codex worker re-entered Learn and returned the card to Done. Symphony reported `auto_commit: nothing to commit`, then `auto_merge_nothing_to_apply`; it pushed/verified the already-created local target commit without creating a second merge. The completed workspace was removed.

A post-service library-level replay exercised the configured-empty capture path (`e2e-capture`) against the already-merged feature branch and returned:

```text
ok=True status=nothing_to_apply
Already up to date.
SKIP: nothing staged after merge
OK: verified dev at origin/refs/heads/dev (571950551e1dccb56f702e1221ab63383f8c7a38)
```

Frozen final state:

- Local `dev`: `571950551e1dccb56f702e1221ab63383f8c7a38`.
- `origin/dev` and `git ls-remote origin refs/heads/dev`: the same SHA.
- Feature branch: `88f35e3da11fbb4d06b20681b145ce98e70be0c8`.
- Merge delta from `445b0ce2353236226762f8f5da6395880a3a4246`: `1`, the original merge `5719505` only.
- Feature SHA is an ancestor of local `dev`.
- Ticket state: `Done`.
- Workspace: absent; only the main worktree is registered.
- API before shutdown: health `ok`, `running=0`, `retrying=0`.
- `git fsck --no-dangling --no-progress`: exit `0`, no output.
- Service shutdown at `2026-07-17T09:15:37Z`: `shutdown_initiated`, then `shutdown_complete`; port `19097` no longer listened.
- Target working tree: only the two declared disposable-fixture corrections, `E2E-WORKFLOW.md` and `e2e/setup-worktree.sh`.
- Original checkout: still only its two pre-existing untracked July 12 documents; `kanban/E2E-AMR-001.md` remained absent; original local and remote `dev` both remained `ee04e5dcc2f188f6686127be537039e5b197006a`.
