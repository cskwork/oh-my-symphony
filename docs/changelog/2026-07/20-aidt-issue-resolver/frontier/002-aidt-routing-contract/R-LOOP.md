# R-LOOP - Frontier 002 AIDT Routing Contract

## Iteration 1 - stopped before verification

- Trusted finding: read-only Frontier 003 exploration proved the frozen clean-working-tree/`HEAD` trust rule blocks
  the live dirty `aidt-viewer-api` checkout and conflicts with the requirement to preserve user state.
- Smallest correction: bind all marker/anchor evidence to regular blobs in fixed local
  `refs/remotes/origin/aidt-prd`, ignore working-tree dirtiness, and retain repository-identity/ref-drift preflight.
- Preserved build evidence: red import proof passed; the partial implementation reached 14 focused routing passes.
  Its `HEAD`/status/working-tree reader is obsolete and must be replaced, not verified.
- Next trusted command: fresh plan attack of the immutable-object amendments, then a fresh iteration-2 builder runs
  `rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing.py -x`.

## Iteration 2 - build authorized

- Plan attack result: PASS after binding all eight immutable-object/cohesion MUSTs and three SHOULDs.
- Approved execution: independent fresh builders own non-overlapping contract/Git-object and strict-Jira paths;
  later fresh builders integrate decision/storage and runtime/core after those file results are present.
- Trusted focused command now uses the five split routing test modules recorded in PLAN.

## Iteration 2 - build complete, verification pending

- Split routing suites: 98 passed.
- Plan-listed affected regressions: 327 passed; broader orchestrator/service/web matrix: 375 passed.
- Builder static/structure gates: Ruff, Pyright, diff check, function length, and nesting passed.
- Doctor retains only the accepted external workspace-root permission and absent file-board categories.
- Next trusted action: a fresh verifier re-runs evidence and audits the complete diff; builder results do not close
  the frontier.

## Iteration 2 - verifier FAIL

- Trusted failure: isolated `tests/test_aidt_routing_storage.py` and standalone
  `import symphony.trackers.aidt_routes` fail before collection.
- Root cause: eager facade import of `.runtime` re-imports a partially initialized `trackers.aidt_routes` while that
  storage adapter imports `aidt_routing.contract`; combined-suite import order masked the cycle.
- Smallest correction: keep the frozen facade `__all__` but defer runtime exports until attribute access, and add
  explicit standalone import-order regressions.
- Next trusted commands: standalone storage-first/public-first imports; isolated storage/runtime suites; all five
  split suites. Maximum iteration 3 is now active.

## Iteration 3 - correction complete, verification pending

- Lazy typed facade preserves the exact 11-name public surface without importing runtime during package-submodule
  initialization; cached exports retain runtime function identity/signatures.
- Fresh-process storage-first, package-first, public-runtime-first, and core facade imports pass.
- Builder evidence: isolated split suites total 101; affected matrix 331; Ruff/Pyright/diff/function/nesting pass.
- Next trusted action: a new verifier runs the same imports in fresh processes, complete audit, repository-wide parity,
  and doctor. No fourth implementation iteration is available.

## Iteration 3 - verifier FAIL, loop exhausted

- Import/package correction passed: four fresh-process orders and five isolated suites (101 total) are green.
- Empty/all-disabled catalog behavior is safe under the frozen contract.
- Trusted failure: malformed `AidtRoutingResult` booleans/counts/blocked IDs survive construction and leak payload/
  path text through repr, structured logs, and health.
- The three-iteration limit is exhausted. No fourth Frontier 002 implementation loop is authorized.
- Smallest correction is isolated as Wayfinder Frontier 002a; after it closes, a fresh Frontier 002 verifier resumes
  the unrun affected/full/static/doctor gates.

## Iteration Contract

Record only trusted failures, the smallest required correction, and the next trusted command.
Maximum Build/Verify iterations: 3.
