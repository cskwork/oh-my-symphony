# CLI preflight and real-worker evidence

- Fixture: `/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh`
- Workflow: `/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh/repo/E2E-WORKFLOW.md`
- Runtime source: `/private/tmp/symphony-auto-merge-retry-safety-20260717/src`
- Port: `19117`
- Corrected fixture baseline: `2b73710cd1b96363f3a1e517905ec91827931251`
- Baseline merge count: `29`

The patched runtime passed:

```text
PYTHONPATH=/private/tmp/symphony-auto-merge-retry-safety-20260717/src \
  /private/tmp/symphony-auto-merge-retry-safety-20260717/.venv/bin/symphony \
  doctor ./E2E-WORKFLOW.md
```

All doctor checks reported `PASS`, including the free port, five prompts, customized worktree hook,
writable workspace root, and one file-board ticket. The corrected command passed immediately before
the successful launch and again after teardown. Retained raw outputs are `evidence/doctor.log` and
`evidence/doctor-after-teardown.log` inside the disposable fixture.

After the harness corrections below, a real Codex worker using `gpt-5.6-sol` and low reasoning moved
`E2E-BRS-001` from `In Progress` to `Verify`, `Learn`, and `Done`. It created non-empty
`docs/E2E-BRS-001/work/feature.md`, `qa/security.md`, and the ignored workspace-only
`qa/lifecycle.log`. Symphony committed the tracked feature evidence as
`b6ec063acee74e41726b4378b58249745f5d8716`.

Raw lifecycle log:
`/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh/evidence/service-run3-escalated.log`.

## Harness-only observations

1. The first disposable baseline mistakenly tracked the ignored board ticket, so the feature
   worktree contained a real `kanban/` directory and the symlink assertion failed in `before_run`.
   The fixture was corrected before any worker turn by keeping the ticket outside Git, removing the
   failed disposable worktree/branch, and re-running doctor.
2. The sandboxed second service start ended before any worker turn with
   `port_exit: codex app-server closed stdout (rc=1)`. The prompt-authorized service-only escalated
   rerun started the real Codex session; only the disposable pause was cleared through
   `POST /api/v1/E2E-BRS-001/resume`.

Neither event reached the product behavior under test. Their raw logs are
`evidence/service-run1.log` and `evidence/service-run2.log` inside the disposable fixture.
