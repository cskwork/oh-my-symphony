# Changelog - 2026-07-18

## Production hardening at six trust boundaries

### Problem theory

Symphony had six small boundary defects with outsized operational effects. Auto-merge treated Git
paths as regular expressions and recursively staged capture directories, so a protected descendant
could leak while unrelated tracked edits could enter a merge. Retry timers treated dependency and
capacity waits as failed attempts that still consumed a worker slot. The TUI independently scanned
the file board for active and terminal cards. Server startup stored the bind host under an untyped
aiohttp key, and package metadata still used the deprecated license-table form.

These are boundary problems, not reasons to redesign the orchestrator. The patch therefore keeps
the public tracker, CLI, result-status, and retry-limit contracts and changes only the point where
each boundary is represented.

### Decisions

- Auto-merge asks Git about each excluded root with a literal pathspec. Exit 1 blocks the merge;
  Git errors fail the gate; displayed names are diagnostic only.
- Capture roots are enumerated as ignored-aware, NUL-delimited untracked paths into one manifest.
  One literal-pathspec add stages that manifest. Every post-merge failure resets exactly those paths
  before aborting the merge, preserving the manifest and merge state if reset itself fails.
- Retry eligibility has classified outcomes. Agent backoff and pause waits retain their slot;
  capacity, lease, CI, per-state, and blocker waits keep duplicate-prevention ownership without a
  slot or a new attempt. Durable rejection releases stale retry ownership without erasing a running
  or finalizing owner.
- The TUI fetches one close-safe snapshot. File boards scan once and partition in source order;
  Jira and Linear retain their two sequential queries on one client.
- The Host guard owns one typed `aiohttp.web.AppKey[str]`, imported by the server for the matching
  write. The absent-key loopback default remains unchanged.
- Package metadata uses the SPDX expression `Apache-2.0`, explicitly includes `LICENSE` and
  `NOTICE`, and raises only the isolated Setuptools build floor to 77.

### Alternatives rejected

- Escaping a generated exclusion regex still makes safety depend on parsed display names and
  filename delimiters. Git's literal pathspec engine already owns the correct boundary semantics.
- Recursive `git add` plus a later broad reset can stage or destroy operator-owned tracked edits.
  The exact untracked manifest is the smallest reversible unit.
- Incrementing retries during contention confuses waiting with agent failure and can exhaust a
  one-retry budget before the agent runs. Dropping all retry ownership would allow duplicate
  dispatch, so only capacity ownership is released.
- A cross-poll TUI cache adds invalidation and staleness rules. One immutable per-poll snapshot
  removes duplicate parsing without persistence semantics.
- Two separately constructed aiohttp keys compare by identity and would disconnect the guard read
  from the server write. One owner and one imported singleton avoid that failure.
- Upgrading runtime dependencies is unnecessary. `AppKey` is already within the declared aiohttp
  floor; only the build backend needs the PEP 639-capable Setuptools floor.

### Compatibility and residual risk

Public signatures and status codes remain stable, the legacy underscore TUI helpers remain
available, and non-file trackers keep their query semantics. Non-slot retries remain in the
in-flight identity set, so they cannot duplicate-dispatch. Capture rollback cannot make recovery
safe if Git refuses the exact reset; in that case Symphony deliberately leaves `MERGE_HEAD` and the
manifest for operator recovery. Lease acquisition still has the existing check-to-acquire race,
which requires a separate registry-level design rather than a retry-timer patch.

### Verification record

- RED: the new focused suites failed on descendant/metacharacter exclusions, tracked capture dirt,
  partial add rollback, commit-hook rollback, retry attempt/kind/capacity ownership, double TUI
  parsing, `NotAppKeyWarning`, and the old build-backend floor.
- GREEN: `tests/test_auto_merge.py` passes all 21 tests; the two full dispatch files pass 212;
  the combined auto-merge/server/TUI/file/package set passes 153; and the focused classification
  and real one-slot timer repros pass.
- Full gate: 1,415 passed and 6 skipped with 84.18% coverage; Ruff passes; Pyright reports 0 errors
  and 0 warnings; `git diff --check` passes.
- Benchmark: the 1,000-card median is 221.344 ms, or 48.77% of baseline, with exactly 1,000 parses;
  the 5,000-card median is 1,188.875 ms, or 52.91% of baseline, with exactly 5,000 parses. Ordered
  membership hashes match the frozen baseline at both sizes.
- Package gate: isolated wheel and sdist inspection reports `License-Expression: Apache-2.0`, both
  legal files, installed CLI and static-resource success, no deprecation warning, and no source-tree
  pollution. Installed-wheel server startup also passes with `PYTHONWARNINGS=error`.
- Operator gate: the repository virtual environment's `symphony doctor ./WORKFLOW.md` passes every
  check when allowed to probe the configured external workspace root. The evaluator-owned full
  CLI/API lifecycle harness remains outside this builder's write scope.
