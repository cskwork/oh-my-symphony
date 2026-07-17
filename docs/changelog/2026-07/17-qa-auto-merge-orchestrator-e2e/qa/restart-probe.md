# Restart durability probe

The service was stopped cleanly while `E2E-AMR-001` remained Blocked and the failed workspace still existed. On the next service startup, before the HTTP server started, Symphony emitted:

```text
2026-07-17T09:09:54Z auto_commit_start
2026-07-17T09:09:55Z auto_commit_completed stdout="auto_commit: nothing to commit"
2026-07-17T09:09:55Z before_remove
2026-07-17T09:09:56Z hook_completed before_remove
```

At `09:11:19Z` the ticket was still Blocked, but its workspace no longer existed and only the main worktree remained registered. Local `dev`, feature, and remote refs were unchanged, and the merge delta remained exactly one.

## Tester observation

This is a restart durability failure. The rejection path says the workspace is preserved, but startup removes it before the operator can resume the Blocked ticket. The ignored `docs/E2E-AMR-001/qa/lifecycle.log` artifact was consequently lost and was not restored when the workspace was later recreated. Tracked evidence survived on the feature branch, so the prescribed retry could still complete; that does not restore the lost workspace state.
