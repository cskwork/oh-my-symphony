# Intent: linear-polling-schema-drift

- Date: 2026-08-29
- Type: brownfield
- Requested by: Danny

## Problem

A workflow with `tracker.kind: linear` cannot poll candidate issues. Linear rejects Symphony's candidate query with HTTP 400 because the query passes a removed `filter` argument to `Issue.inverseRelations`. No ticket can reach dispatch. The same obsolete argument also affects full issue fetches used during stage-contract validation.

## Evidence

- Linear returned `GRAPHQL_VALIDATION_FAILED` with `Unknown argument "filter" on field "Issue.inverseRelations"` for the current query. [verified: raw response and schema introspection in GitHub issue #58, https://github.com/cskwork/oh-my-symphony/issues/58]
- Current `main` still sends the rejected argument in the candidate and full issue query documents. [verified: `src/symphony/trackers/linear.py:56,120`]
- The normalizer already keeps only incoming relations whose type is `blocks`. [verified: `src/symphony/trackers/linear.py:174-185`]
- Existing tests pass without validating the GraphQL document against the removed argument. [verified: `.venv/bin/python -m pytest -q tests/test_tracker_linear_full.py` returned `28 passed in 0.19s` on 2026-08-29]
- The affected flow and risk review are recorded in `.sdlc/work/linear-polling-schema-drift/research.md`. [verified: file exists]
- Reproduction evidence: received 2026-08-29 from GitHub issue #58; this cycle will not repeat the request against Linear because the user chose secret-free CI.
- Verification debt: no live Linear schema call will run. Deterministic query-shape and response-normalization tests replace it for this cycle.

## Success criteria

- [ ] Candidate polling and full issue fetch no longer send `inverseRelations(filter: ...)`.
- [ ] Both queries request a bounded `inverseRelations` connection that Linear's current schema accepts according to issue #58.
- [ ] A deterministic test fails on the old query shape.
- [ ] Tests prove mixed incoming relation types produce blockers only for `type == "blocks"` in both affected fetch paths.
- [ ] `.venv/bin/python -m pytest -q tests/test_tracker_linear_full.py` passes.
- [ ] The full test, lint, translation, type, and wheel commands in `.sdlc/config.md` pass before ship.

## Out of scope / must not change

- No live Linear credential or network dependency in normal CI.
- No new dependency or abstraction.
- No change to the `Issue` model, tracker interface, state mutation, archive behavior, or non-Linear trackers.
- Existing client-side blocker filtering must remain unchanged.
- Nested relation pagination beyond the chosen bound is not solved in this cycle unless specification research proves the minimal query would otherwise lose real blockers.

## Constraints

- Preserve dispatch blocking for every `blocks` relation returned by Linear.
- Keep normal CI deterministic and secret-free.
- Make the smallest source and regression-test change.
- Do not touch the four pre-existing untracked paths outside `.sdlc/`.

## Open questions

- During specification, confirm the supported nested `inverseRelations(first: N)` bound from current Linear schema evidence. If the proposed bound can hide real blockers, stop and revise the scope before planning.

## Researcher findings

Current source confirms both obsolete query sites and the client-side blocker filter. The affected test file is fast and green, but it does not inspect the obsolete GraphQL argument. The strongest counterargument is relation truncation after removing the server filter. The success criteria therefore require a bound and mixed-relation tests, while carrying completeness beyond that bound into specification. Full report: `.sdlc/work/linear-polling-schema-drift/research.md`.
