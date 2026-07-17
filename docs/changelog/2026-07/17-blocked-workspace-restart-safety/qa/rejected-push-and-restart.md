# Rejected push and restart evidence

The bare-origin `pre-receive` hook rejected only `refs/heads/dev`. Symphony reported
`auto_merge_failed rc=52`, `FAIL: push dev to origin/refs/heads/dev`, and
`auto_merge_gate_blocked_ticket status=push_failed`.

Immediate checkpoint:

- Ticket: `Blocked`.
- Local `dev`: `670c1c07141508f262047f7bf6f82fd2fdd92c27`.
- `origin/dev` and `ls-remote origin refs/heads/dev`:
  `2b73710cd1b96363f3a1e517905ec91827931251`.
- Feature: `b6ec063acee74e41726b4378b58249745f5d8716`.
- Merge delta from the corrected baseline: exactly `1`.
- Workspace: present and registered.
- Ignored `docs/E2E-BRS-001/qa/lifecycle.log`: present, non-empty, `57` bytes.
- API: health `ok`, `running=0`, `retrying=0`.

The service then stopped cleanly while the card stayed Blocked. Raw checkpoint and service output:

- `/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh/evidence/blocked-before-stop.txt`
- `/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh/evidence/stopped-before-restart.txt`
- `/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh/evidence/service-run3-escalated.log`

On the next start of the same workflow, before the HTTP server came up, the patched runtime emitted:

```text
startup_terminal_cleanup_preserved_blocked_workspace identifier=E2E-BRS-001 path=/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh/workspaces/E2E-BRS-001
```

After startup, the ticket was still Blocked; all three refs, the single merge, the registered
worktree, and the ignored diagnostic matched the pre-stop checkpoint. The pre-recovery restart log
audit counted one preservation warning, zero ticket `auto_commit_*` events, and zero ticket
`before_remove` events.

Raw restart evidence:

- `/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh/evidence/service-restart.log`
- `/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh/evidence/blocked-after-restart.txt`
- `/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh/evidence/restart-log-audit.txt`
