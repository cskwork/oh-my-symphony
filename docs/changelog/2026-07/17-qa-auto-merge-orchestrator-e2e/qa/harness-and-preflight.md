# Harness and preflight evidence

- Target root: `/private/tmp/symphony-e2e-auto-merge-20260717-g6CdGQ`
- Repository: `/private/tmp/symphony-e2e-auto-merge-20260717-g6CdGQ/repo`
- Workflow: `E2E-WORKFLOW.md`
- Patched runtime: `/private/tmp/symphony-auto-merge-retry-safety-20260717/.venv/bin/python`
- Service/API port: `19097`
- Baseline target and remote `dev`: `445b0ce2353236226762f8f5da6395880a3a4246`
- Baseline merge count: `29`
- Browser actions: `0`

`symphony doctor ./E2E-WORKFLOW.md` passed before the worker run and after each disposable-fixture correction. The target started with no ticket workspace. The original checkout was observation-only.

## Harness-only setup failures

These failures happened before the patched auto-merge behavior was under test and are not classified as product defects.

1. The sandboxed child `codex app-server` could not initialize its SQLite state under `/Users/danny/.codex`. The same disposable service command was rerun outside the filesystem sandbox.
2. The fixture pinned `gpt-5-codex`, which the available ChatGPT account rejected with HTTP 400. The disposable workflow only was changed to `gpt-5.6-sol`, matching the locally configured supported model, and inherited nested-Codex control variables were unset in its command.
3. The fixture's untracked `kanban` symlink was included by the first auto-commit and correctly blocked by the merge gate as `excluded_paths`. The disposable setup script and active worktree were corrected with the documented per-worktree setting `symphony.autocommitExclude=kanban`; the disposable feature commit was amended to remove only that harness artifact.

After correction, the feature branch contained only the intended worker evidence files. No product source, tests, original checkout file, or real board card was changed by QA.
