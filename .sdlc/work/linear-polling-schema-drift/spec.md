# Spec: linear-polling-schema-drift

- From: `intent.md`, approved 2026-08-29
- Type: brownfield

## Human summary

Linear rejects both Symphony queries that request blocker relations, so Linear boards cannot poll or complete stage validation. The fix removes the obsolete relation filter from both GraphQL documents. Each document requests 50 incoming relations and their pagination state. Symphony keeps its existing client-side rule that only `blocks` relations become blockers. Symphony stops the affected read if Linear reports another relation page, rather than dispatching from an incomplete blocker set. Normal CI remains deterministic and does not need Linear credentials. The Issue model, tracker interface, mutations, archive behavior, retries, and other trackers stay unchanged. Recommendation: accept the fail-closed 50-relation limit for this cycle instead of adding one relation query per issue.

## Requirements

- R1: `_CANDIDATE_QUERY` and `_BY_ID_FULL_QUERY` contain zero `inverseRelations(filter:` arguments. (intent: "Candidate polling and full issue fetch no longer send `inverseRelations(filter: ...)`.")
- R2: Both queries request `inverseRelations(first: 50)` with `nodes` and `pageInfo { hasNextPage }`. (intent: "Both queries request a bounded `inverseRelations` connection that Linear's current schema accepts according to issue #58.")
- R3: `_normalize_node` continues to add a `BlockerRef` only when an incoming relation has `type == "blocks"`. (intent: "Existing client-side blocker filtering must remain unchanged.")
- R4: `_normalize_node` raises `LinearUnknownPayload` for an unusable relation connection. Exact messages are `issue.inverseRelations missing` when the connection is absent, null, or not a mapping; `issue.inverseRelations.nodes missing` when `nodes` is absent, null, or not a list; `issue.inverseRelations.pageInfo missing` when `pageInfo` is absent, null, or not a mapping; `issue.inverseRelations.pageInfo.hasNextPage missing` when the flag is absent or not a boolean; and `issue.inverseRelations incomplete` when the flag is true. `LinearClient.fetch_candidate_issues` and `fetch_issue_full_by_id` propagate that exception. The orchestrator logs `candidate_fetch_failed` and returns before dispatch on a candidate error. During a forward stage-contract check, a full refresh error logs `issue_full_refresh_failed`, records the tracker error, leaves the minimal issue description as `None`, fails the required-section contract, and rewinds the ticket to the producing state. (intent: "Preserve dispatch blocking for every `blocks` relation returned by Linear."; open question requires stopping if the bound can hide blockers.)
- R5: Deterministic tests inspect the outgoing candidate and full issue GraphQL documents, reject the obsolete filter, require the bound and pagination fields, and prove mixed relation types produce only `blocks` blockers. (intent: "A deterministic test fails on the old query shape." and "Tests prove mixed incoming relation types produce blockers only for `type == \"blocks\"` in both affected fetch paths.")
- R6: Tracker tests cover an empty complete connection, a complete 50-node connection, `hasNextPage: true`, absent/null/non-mapping `inverseRelations`, absent/null/non-list `nodes`, absent/null/non-mapping `pageInfo`, and absent/non-boolean `hasNextPage`. Each invalid or incomplete shape must raise `LinearUnknownPayload` with the R4 message. One orchestrator contract-integration test makes the full refresh return `None` during a forward transition and proves the ticket returns to the producing state with a contract-failure note. (intent: relation-bound open question and "Preserve dispatch blocking for every `blocks` relation returned by Linear.")
- R7: No live Linear call, credential, dependency, model change, tracker-interface change, mutation change, archive change, or non-Linear tracker change is part of this work. (intent: "No live Linear credential or network dependency in normal CI." and the full out-of-scope list.)
- R8: The narrow Linear tests and every command in `.sdlc/config.md` pass before ship. (intent: both verification success criteria.)

## Data shapes

### GraphQL request shape

Both affected documents request this nested connection:

```graphql
inverseRelations(first: 50) {
  nodes {
    type
    issue { id identifier state { name } }
  }
  pageInfo { hasNextPage }
}
```

Linear's public schema documents `first` and `after` for this connection. It does not document `filter`. Linear returns 50 connection records by default and uses `pageInfo` for more records.

### Response shape

- `inverseRelations`: non-null mapping.
- `inverseRelations.nodes`: non-null list of relation objects.
- Relation object: `type: str` and `issue: {id, identifier, state: {name}}`.
- `inverseRelations.pageInfo`: non-null mapping with boolean `hasNextPage`.
- Complete connection: `hasNextPage == false`.
- Incomplete or malformed connection: `LinearUnknownPayload` with the exact R4 message.

### Domain shape

`_normalize_node` maps each returned `type == "blocks"` relation to one `BlockerRef` in `Issue.blocked_by`. It ignores other relation types. No schema, storage, migration, API, or serialization format changes.

## Behavior: AS-IS → TO-BE

| # | Flow | AS-IS (evidence) | TO-BE |
| --- | ------ | ------------------ | ------- |
| B1 | Poll candidate issues | Linear rejects `_CANDIDATE_QUERY` because line 56 sends the removed relation filter. The poll returns no candidates. [issue #58; `linear.py:33-66`] | Linear accepts the document described by issue #58. Symphony returns normalized candidates when every nested relation connection is complete. |
| B2 | Fetch one full issue | `_BY_ID_FULL_QUERY` sends the same removed filter at line 120, so stage-contract refresh can fail. [`linear.py:106-130`] | The full read uses the same valid bounded relation shape and returns the normalized issue when complete. An incomplete or malformed connection raises `LinearUnknownPayload`. During a forward stage-contract check, the missing full body makes the contract fail and rewinds the ticket to the producing state. |
| B3 | Mixed relation types | The rejected query asks Linear to return only `blocks`; `_normalize_node` also filters client-side. [`linear.py:174-185`] | Linear may return mixed relation types. The unchanged client rule maps only `blocks` to `Issue.blocked_by`. |
| B4 | Empty relation connection | A valid empty `nodes` list produces no blockers in mocked tests. [`test_tracker_linear_full.py:88-178`] | Empty `nodes` with complete `pageInfo` produces no blockers. |
| B5 | More than 50 incoming relations | Current live query fails before relation completeness matters. Removing the filter without a guard could hide blockers after the first page. [research report; Linear pagination docs] | `hasNextPage: true` raises `LinearUnknownPayload("issue.inverseRelations incomplete")`. Candidate polling returns no list, and the orchestrator returns before dispatch. |
| B6 | Malformed relation connection | Current code treats missing or null `inverseRelations.nodes` as empty and does not request nested `pageInfo`. [`linear.py:56-61,120-125,174`] | The exact invalid shapes in R6 raise `LinearUnknownPayload` with the exact R4 message. |
| B7 | Unauthorized, rate-limited, retried, or top-level malformed response | `_post` and `send_with_retry` handle these cases outside relation normalization. [`linear.py:213-239`; existing tracker tests] | Unchanged. |

## What stays untouched

- U1: `Issue.blocked_by` contains only incoming `blocks` relations; checked by existing blocker assertions plus the new mixed-relation cases.
- U2: The `Issue`, `BlockerRef`, `Tracker`, and `TrackerConfig` interfaces remain unchanged; checked by type checking and the full suite.
- U3: Linear state mutation and archive behavior remain unchanged; checked by the full suite and a source diff limited to the two approved files.
- U4: File-board, Jira, ADO, and other tracker behavior remains unchanged; checked by the full suite and a source diff limited to the two approved files.
- U5: `.github/hooks/`, `.probe_artifacts.py`, `.probe_web.py`, and `.workmux.yaml` retain the recursive file hashes recorded in `.sdlc/work/linear-polling-schema-drift/untracked-baseline.sha256`; checked by recomputing the same manifest and requiring an exact match.

## Release procedure

- branch `fix/linear-polling-schema-drift` → merge to release-ready `main` → push to `origin` → deploy: `none` [required by the SDLC spec contract; project evidence in `research.md`]

## Flagged concerns

- [ ] Accept a fail-closed read when an issue has more than 50 incoming relations, instead of adding per-issue relation pagination; owner: product and operations; recommendation: accept for this cycle because it preserves blocker safety without a new query flow.

## Open questions from intent

- Supported nested `inverseRelations(first: N)` bound: answered. Linear documents 50 as the default, not a hard maximum. This spec requests 50 explicitly and rejects `hasNextPage: true`, so it does not assume the first page is complete.

## Adversarial review

- Objection: the failure contract did not name an exception, message, malformed shapes, or orchestrator outcome. Resolution: R4 and R6 now define `LinearUnknownPayload`, exact messages, every tested connection shape, and the candidate/full-refresh outcomes.
- Objection: `git status` could not prove the four untracked paths stayed unchanged. Resolution: U5 names each path and requires an exact recursive hash-manifest comparison.
- Objection: the release line lacked upstream evidence. Resolution: research now records the project's release-ready `main`, `origin`, and absence of a separate deploy command; the line is limited to the template's required release procedure.
- Objection: untouched-behavior claims exceeded the supplied evidence. Resolution: the section now contains only the approved intent boundaries and names concrete proof.
- Concern: `endCursor` was unused under fail-closed pagination. Resolution: removed it from the request and response contract.
- Round 2 objection: the full issue refresh recorded an error but the spec did not prove stage validation stopped. Resolution: R4, R6, and B2 now define and test the existing fail-closed contract rewind when the full body is unavailable.
- Round 3 verdict: no blocking objections remain. The reviewer carried the 50-relation availability trade-off to the human gate.
