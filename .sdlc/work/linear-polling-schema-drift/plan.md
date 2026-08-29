# Plan: linear-polling-schema-drift

- From: `spec.md`, approved 2026-08-29

## Human summary

Change the two rejected Linear query fragments and validate the nested relation connection before normalization. Add tracker tests for query shape, mixed relations, complete pages, exact payload errors, and exception propagation through both client reads. Add orchestrator tests that prove candidate failure prevents dispatch and full-refresh failure rewinds the stage transition. The main risk is that one issue with more than 50 incoming relations stops the whole candidate read, which the approved spec accepts to avoid hidden blockers. Focused tests plus the configured full checks prove the change.

## Gate tier

- migration/schema: no · data deletion: no · public API: no · security paths: no · infra/config: no · beyond spec scope: no
- Tier: agent — this is an internal GraphQL document and payload-validation fix with regression tests. It changes no stored data, public signature, credential handling, deployment configuration, or behavior outside the approved spec.

## Files that change

- `src/symphony/trackers/linear.py` (modified): remove the obsolete server filter from both query documents, request `first: 50` plus `pageInfo.hasNextPage`, and fail with the approved `LinearUnknownPayload` messages before blocker normalization when the nested connection is incomplete or malformed.
- `tests/test_tracker_linear_full.py` (modified): assert both outgoing query shapes and cover mixed relations, empty and 50-node complete connections, truncation, and every invalid shape and message from R4 and R6.
- `tests/test_orchestrator_contract_integration.py` (modified): raise `LinearUnknownPayload` through the real full-refresh wrapper and prove it records the tracker error, adds a contract-failure note, and rewinds to the producing state.
- `tests/test_orchestrator_dispatch.py` (modified): raise `LinearUnknownPayload` from candidate fetch and prove `_on_tick` logs `candidate_fetch_failed` and returns without dispatch.

No change is planned for `src/symphony/orchestrator/core.py` or `src/symphony/errors.py`. The implementation uses their existing rewind path and `LinearUnknownPayload` type.

## Order of work

1. Re-run the saved baseline command and require `32 passed` before editing.
2. In `tests/test_tracker_linear_full.py`, add request-body assertions for both query documents. Add mixed-relation cases to candidate and full issue normalization. Add table-driven invalid-shape cases for the exact R4 messages. Run the narrow tracker file to observe the intended failures against the old query and permissive normalizer.
3. In `src/symphony/trackers/linear.py`, change both nested query fragments to `inverseRelations(first: 50)` with `pageInfo { hasNextPage }`. Validate `inverseRelations`, `nodes`, `pageInfo`, and boolean `hasNextPage` inside `_normalize_node` before iterating nodes. Raise the exact R4 errors and reject `hasNextPage: true`. Run the tracker tests until green.
4. In `tests/test_orchestrator_contract_integration.py`, make the real `_tracker_call_full_by_id` path raise `LinearUnknownPayload` during an `In Progress` to `Verify` transition. Do not stub `_refresh_issue_full`. Reuse the existing backend, ticket, and contract-failure helpers. Assert `issue_full_refresh_failed`, the recorded tracker error, `o._running[issue.id].issue.description is None`, a `## Contract Failure` note, and final `In Progress` state.
5. In `tests/test_orchestrator_dispatch.py`, make `_fetch_candidates` raise `LinearUnknownPayload`, observe `_on_tick`, and assert `candidate_fetch_failed`, zero dispatch calls, and no returned candidate work. These two orchestrator tests and the direct client tests together prove R4 end to end without production changes outside `linear.py`.
6. Run the focused regression commands. Inspect `git diff -- src/symphony/trackers/linear.py tests/test_tracker_linear_full.py tests/test_orchestrator_contract_integration.py tests/test_orchestrator_dispatch.py` and require no other implementation path.
7. Recompute U5 with `test "$(find .github/hooks -type f -print | LC_ALL=C sort)" = ".github/hooks/workmux-status/hooks.json"`, then run `shasum -a 256 -c .sdlc/work/linear-polling-schema-drift/untracked-baseline.sha256`.
8. Run every configured test, lint, translation, type, and wheel command. Record exact outputs for ship.

## Risks

- Availability: one candidate with more than 50 incoming relations makes the candidate read fail. This is the approved fail-closed trade-off. The log must expose `linear_unknown_payload: issue.inverseRelations incomplete`.
- Strictness: missing relation data previously normalized as an empty blocker set. The approved spec deliberately changes that malformed response to a typed failure.
- Test fixture churn: every full Linear issue fixture in the approved tracker test file must include complete nested `pageInfo`. If another file fails because it constructs this response shape, stop before editing it, update the plan's file list, and repeat adversarial review.
- Stage-contract proof: the integration test is expected to pass without `core.py` changes. A failure is evidence that the approved spec's existing rewind claim is wrong and requires reopening the spec.
- Working tree: local `main` is behind `origin/main` and already contains unrelated `.gitignore`, `.gitattributes`, probe, hook, workmux, and SDLC paths. Do not reset, clean, stage, or edit unrelated paths.
- Approved-file wording: U3 and U4 say "two approved files", while R4 and R6 require tracker and orchestrator proofs. This plan follows the specific behavioral requirements and limits the implementation diff to the four paths listed above. Any fifth implementation path requires a revised plan and another adversarial review.
- No live API proof: CI remains secret-free. Query compatibility rests on issue #58, the public Linear schema, and deterministic request-shape tests.

## Proof

- R1 → request-body assertions in candidate and full-fetch tests; `! rg -n 'inverseRelations\(filter:' src/symphony/trackers/linear.py`.
- R2 → request-body assertions require two `inverseRelations(first: 50)` fragments and `pageInfo { hasNextPage }`; `.venv/bin/python -m pytest -q tests/test_tracker_linear_full.py`.
- R3 → candidate and full-fetch mixed-relation assertions require only `type == "blocks"` entries in `Issue.blocked_by`.
- R4 → direct candidate and full client calls must propagate `LinearUnknownPayload` with each exact message; `tests/test_orchestrator_dispatch.py` proves candidate error logging and zero dispatch; `tests/test_orchestrator_contract_integration.py` proves the real full-refresh wrapper records the error, leaves the running issue description as `None`, and rewinds.
- R5 → the request-shape tests fail on current main and pass only after both query documents change; mixed-relation tests cover both fetch paths.
- R6 → tracker tests cover empty, 50-node, truncated, absent, null, wrong-container, and wrong-boolean shapes; the contract integration test covers the full-refresh outcome.
- R7 → `git diff --name-only -- src tests` must equal the four listed paths; `git diff -- pyproject.toml src/symphony/issue.py src/symphony/trackers/__init__.py` must be empty; no test reads a live credential or network endpoint.
- R8 → `.venv/bin/python -m pytest -q tests/test_tracker_linear_full.py tests/test_orchestrator_contract_integration.py`; the new candidate-failure node in `tests/test_orchestrator_dispatch.py`; then every command in `.sdlc/config.md`.

## Regression baseline

- Commands: `.venv/bin/python -m pytest -q tests/test_tracker_linear_full.py tests/test_orchestrator_contract_integration.py`
- Result: `32 passed in 0.34s`
- Saved to: `.sdlc/work/linear-polling-schema-drift/baseline.txt`
- U1 → mixed candidate and full-fetch relation tests plus the focused tracker command.
- U2 → `.venv/bin/symphony-pyright` and the configured full test command.
- U3 → implementation diff contains no Linear mutation or archive path; configured full test command passes.
- U4 → implementation diff contains no other tracker path; configured full test command passes.
- U5 → `test "$(find .github/hooks -type f -print | LC_ALL=C sort)" = ".github/hooks/workmux-status/hooks.json"` plus `shasum -a 256 -c .sdlc/work/linear-polling-schema-drift/untracked-baseline.sha256`.

## Adversarial review

- Objection: R4 stubbed downstream `None` and did not prove client propagation, candidate dispatch prevention, or the full-refresh wrapper. Resolution: direct client tests now prove both propagation paths; dispatch and contract integration tests exercise the real orchestrator handling and observable outcomes.
- Objection: fixture churn allowed an unnamed extra file. Resolution: any additional fixture path now stops implementation and requires plan revision plus another adversarial review.
- Objection: U5 named a file-list condition without a command. Resolution: the plan now includes an exact `find` comparison before checksum verification.
- Round 2 objection: the full-refresh integration did not explicitly prove the minimal issue description stayed `None`. Resolution: step 4 and R4 proof now require a direct assertion on the running issue before accepting the rewind proof.
- Gate tier re-check: the adversary found no migration, deletion, public API, security, infra/config, or beyond-spec trip-wire. Agent tier remains correct.
- Round 3 verdict: no blocking objections remain. The reviewer carried the approved 50-relation availability trade-off, secret-free compatibility proof, and stale spec wording as non-blocking notes.
- VERDICT: NO BLOCKERS.
