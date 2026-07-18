# GOAL - Production hardening

Single source of "done". Only the verifier ticks a box; unticking needs regression evidence.
Never delete or reword an unmet criterion - append. Mid-run discovered musts are APPENDED as new
unchecked criteria tagged `(surfaced: ...)`. Ambiguous/product-changing candidates go to
`## Decision Gates` as `ask-user`, not into criteria.

## Original Request

> find and fix any bugs that may have issue get ready this app for production quality use refactoring and performance optimizations too actual test run too

## Spec

Audit the current Symphony runtime and operator surfaces for concrete correctness, resilience,
maintainability, and performance risks. Select only issues supported by source, tests, or runtime
evidence. Reproduce bugs with failing tests before fixing them; preserve public behavior during
refactors; optimize only measured hot paths; and run the repository's real production gates. Keep
unrelated user work and broad speculative rewrites out of scope.

## Success Criteria

Each item is falsifiable and names its verification method.

- [x] Baseline behavior and current failures are captured before source changes - verify: `pytest -q`
- [x] Every production-code bug fixed by this run has a red-first regression test and a structurally different alternative repro - verify: targeted `pytest` tests plus DEBUG gate output
- [x] Refactors preserve affected public behavior and reduce a documented cohesion or complexity problem without pass-through abstractions - verify: characterization tests and final diff audit
- [x] Performance changes target a measured hot path and do not regress representative latency or throughput - verify: evaluator-owned before/after benchmark
- [x] The full repository test, lint, type-check, build/install, and workflow-doctor gates pass - verify: frozen commands from `pyproject.toml`, CI, and operator docs
- [x] A real CLI/API lifecycle smoke test exercises production entry points and cleanup - verify: evaluator-owned isolated runtime E2E
- [x] Production-safety review finds no unresolved high-severity data, concurrency, subprocess, permission, security, or compatibility issue in the changed surface - verify: fresh-context adversarial audit
- [x] The final diff contains only approved hardening scope and every modified symbol has consumer coverage - verify: `git diff --check`, changed-symbol reconciliation, and `Backward-trace: clean`
- [x] Excluded auto-merge roots block both the literal root and every descendant—including tab/newline filenames—without pathspec or prefix false matches - verify: red/green auto-merge tests plus literal Git pathspec repro `(surfaced: correctness audit)`
- [x] Capture roots add only NUL-safe untracked, non-ignored artifacts, fail closed, and never stage a pre-existing tracked modification - verify: red/green auto-merge tests plus Git index/worktree assertions `(surfaced: correctness audit)`
- [x] If capture staging or the merge commit fails, rollback unstages the exact retained NUL path manifest before abort so captured operator files survive byte-for-byte as untracked and prior tracked/index state is restored - verify: partial-add failure and failing `commit-msg` hook public-API tests plus standalone Git repro `(surfaced: scope re-review)`
- [x] Retry timer transient waits preserve attempt and attempt kind, remain in-flight without holding capacity, and cannot exhaust `max_retries` without another agent failure; with one slot an unresolved blocker can dispatch, normal backoff still owns its slot, and durable rejection cannot persist as either retry form - verify: dispatch-state ownership tests, classified-decision tests, scheduled timer repro, and cap assertions `(surfaced: correctness audit and plan audit)`
- [x] The prior retry criterion's non-slot behavior applies only to classified contention/dependency waits; tracker-poll failure preserves the incoming `holds_slot` value so intermittent polling cannot accumulate board-sized claims beyond configured capacity - verify: slot-holding and non-slot retry poll-failure tests plus claimed/timer count assertions `(surfaced: scope re-review)`
- [x] Server startup uses the aiohttp 3.9-compatible typed application key and emits no `NotAppKeyWarning` while preserving Host enforcement - verify: warning-as-error startup test and Web API guard suite `(surfaced: baseline warning)`
- [x] A file-tracker TUI poll parses each ticket once, returns the same candidate/terminal membership, observes the next-poll mutation, and reaches the frozen <=65% median threshold at 1,000 and 5,000 cards - verify: parse-count tests and evaluator benchmark `(surfaced: performance audit)`
- [x] Package builds use current SPDX license metadata, include `LICENSE` and `NOTICE`, and emit no Setuptools license deprecation warning - verify: isolated wheel/sdist build and archive metadata inspection `(surfaced: build audit)`
- [x] Newly added evaluator code follows the repository cohesion limits of functions no longer than 50 lines and nesting no deeper than 4 - verify: changed-function AST scan plus evaluator-script lint `(surfaced: AGENTS.md repository rule and final-diff audit)`

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | ask-user | resolved | `.domain-agent/` was absent | User authorized completion; use recommended local ignored `.domain-agent/` | Before Build |
| d2 | ask-user | resolved | Build required an explicitly approved frozen plan | User approved all planned fixes, improvements, final tests, and commit | Before Build |
