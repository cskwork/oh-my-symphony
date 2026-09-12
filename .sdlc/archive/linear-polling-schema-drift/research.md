# Researcher report: Linear polling schema drift

- Date: 2026-08-29
- Scope: current `main`, GitHub issue #58, Linear tracker source, and tracker tests
- Write authority: read-only research

## Entry points

- `src/symphony/trackers/linear.py:33-66` defines the candidate polling query.
- `src/symphony/trackers/linear.py:100-130` defines the full issue query used by contract validation.
- `src/symphony/trackers/linear.py:159-190` normalizes incoming relations and keeps only `type == "blocks"` as blockers.
- `src/symphony/trackers/linear.py:241-246` runs candidate polling.
- `src/symphony/trackers/linear.py:268-285` fetches one full issue.

## Claims checked

- GitHub issue #58 reports that Linear returns HTTP 400 because `Issue.inverseRelations` no longer accepts `filter`. The issue includes the raw GraphQL validation error and schema introspection. [verified: https://github.com/cskwork/oh-my-symphony/issues/58]
- Current `main` still sends `inverseRelations(filter: { type: { eq: "blocks" } })` in both affected queries. [verified: `src/symphony/trackers/linear.py:56,120`]
- Removing the server filter does not admit non-blocking relations into `Issue.blocked_by`; `_normalize_node` already rejects relations whose type is not `blocks`. [verified: `src/symphony/trackers/linear.py:174-185`]
- Existing tests mock response payloads. They do not reject the obsolete GraphQL argument. [verified: `tests/test_tracker_linear_full.py:88-178`; baseline command passed]
- Narrow baseline: `.venv/bin/python -m pytest -q tests/test_tracker_linear_full.py` returned `28 passed in 0.19s`. [verified: command output, 2026-08-29]
- Linear documents Relay-style pagination with `first` and `after`. A connection returns 50 records by default, and clients must follow `pageInfo.hasNextPage` with `endCursor` to retrieve the rest. [verified: https://linear.app/developers/pagination]
- Linear warns that nested connection defaults multiply query complexity and recommends an explicit record count. [verified: https://linear.app/developers/rate-limiting]
- The current public Linear schema lists `first`, `after`, `last`, `before`, `includeArchived`, and `orderBy` for `Issue.inverseRelations`; it does not list `filter`. The `first` field defaults to 50. [verified: `linear/linear` public `packages/sdk/src/schema.graphql`, fetched 2026-08-29]
- The project keeps `main` release-ready and asks for small focused changes. The repository remote is `origin`. The package and test workflow define no deploy command for this fix. [verified: `CONTRIBUTING.md:10-12`; `git remote -v`; `pyproject.toml`; `.github/workflows/tests.yml`]
- The four pre-existing untracked paths are `.github/hooks/`, `.probe_artifacts.py`, `.probe_web.py`, and `.workmux.yaml`. Their recursive file hashes are recorded in `untracked-baseline.sha256`. [verified: `git status --short`; SHA-256 capture, 2026-08-29]
- If candidate polling raises, `Orchestrator._on_tick` logs `candidate_fetch_failed`, notifies observers, and returns before it can dispatch any candidate. [verified: `src/symphony/orchestrator/core.py:3689-3705`]
- If a full issue refresh raises during a forward stage-contract check, `_refresh_issue_full` logs `issue_full_refresh_failed`, records the tracker error, and returns `None`. The phase-transition path then evaluates the minimal issue whose description is `None`; required sections fail, so it appends the contract failure and rewinds to the producing state. [verified: `src/symphony/orchestrator/core.py:6824-6908,7268-7298,7699-7720`]

## Feasibility

The change is local to one query module and its existing test module. It needs no new dependency, public API change, data migration, or credential in normal CI.

## Adversarial review

Strongest objection: deleting the filter can make unrelated inverse relation types consume the nested connection page and hide a blocker. The specification resolves this without an N+1 query or new abstraction: request `inverseRelations(first: 50)` plus `pageInfo`, keep client-side type filtering, and fail closed if `hasNextPage` is true. The worker never dispatches from an incomplete blocker set.

## Unknowns

- This session did not call Linear. The user chose secret-free CI and accepted issue #58 as the live-schema reproduction.
- Linear does not document 50 as a hard maximum. This cycle uses 50 because it is the documented default and adds a fail-closed truncation guard instead of assuming completeness.

## Domain candidates

- A blocker is an incoming Linear issue relation whose relation type is `blocks`. [verified: `src/symphony/trackers/linear.py:174-185`]
- Candidate polling and full issue validation must use GraphQL documents accepted by Linear's current schema. [verified: `src/symphony/trackers/linear.py:33-130`; issue #58]
