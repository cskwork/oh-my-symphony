# Rejected-push gate evidence

The real Codex worker advanced `E2E-AMR-001` through In Progress, Verify, and Learn. It produced the contract evidence under `docs/E2E-AMR-001/` and returned the card to Done. Symphony then auto-committed feature SHA `88f35e3da11fbb4d06b20681b145ce98e70be0c8`.

At `2026-07-17T09:08:30Z`, the terminal auto-merge started. At `09:08:32Z`, the bare origin hook rejected the target push. Symphony reported:

```text
auto_merge_failed rc=52
FAIL: push dev to origin/refs/heads/dev
remote: E2E: rejecting target push
auto_merge_gate_blocked_ticket status=push_failed
```

Immediate post-failure state:

- Ticket state: `Blocked`, with a merge-gate note carrying `push_failed`.
- Worker workspace: present and still registered.
- Local `dev`: `571950551e1dccb56f702e1221ab63383f8c7a38`.
- Remote `origin/dev`: `445b0ce2353236226762f8f5da6395880a3a4246`.
- Feature branch: `88f35e3da11fbb4d06b20681b145ce98e70be0c8`.
- Merge delta from the baseline: exactly one merge commit.
- Merge commit: `571950551e1dccb56f702e1221ab63383f8c7a38 445b0ce2353236226762f8f5da6395880a3a4246 88f35e3da11fbb4d06b20681b145ce98e70be0c8 merge: E2E-AMR-001 from symphony/E2E-AMR-001 (88f35e3)`.
- API: `running=0`, `retrying=0`, health `ok`.
- Ignored worker artifact `docs/E2E-AMR-001/qa/lifecycle.log`: present and non-empty in the preserved workspace.

This proves the intended failure transaction before any restart: the remote remained stale, the local merge existed once, and the workspace was preserved.
