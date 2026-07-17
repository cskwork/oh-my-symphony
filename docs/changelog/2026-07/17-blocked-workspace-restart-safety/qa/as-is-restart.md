# As-is restart evidence

Source: the independent real Codex/Git E2E at
`../17-qa-auto-merge-orchestrator-e2e/qa/restart-probe.md` and its final report.

Before restart, ticket `E2E-AMR-001` was `Blocked`, the local target contained exactly one merge, the
remote target was stale, and the workspace contained the ignored file
`docs/E2E-AMR-001/qa/lifecycle.log`.

On the next service start, while the ticket remained `Blocked`, Symphony emitted:

```text
2026-07-17T09:09:54Z auto_commit_start
2026-07-17T09:09:55Z auto_commit_completed stdout="auto_commit: nothing to commit"
2026-07-17T09:09:55Z before_remove
2026-07-17T09:09:56Z hook_completed before_remove
```

After startup, the workspace and ignored lifecycle log were gone. Git refs and the single local merge
were unchanged. This proves the loss belongs to startup terminal cleanup rather than Git retry logic.
