# Evidence: linear-polling-schema-drift

- From: `plan.md`, approved 2026-08-29
- Diff: uncommitted `main` working tree versus `HEAD`
- Product and test scope: four approved paths under `src/` and `tests/`

## Decision summary

The obsolete Linear relation filter is gone from both affected GraphQL documents. Symphony now requests 50 incoming relations, checks connection completeness, and keeps only `blocks` relations as blockers. An incomplete or malformed connection raises `LinearUnknownPayload`. Candidate errors stop before scheduling or dispatch. Full-refresh errors follow the existing contract-failure rewind path.

The real admin HTML started in the local browser E2E stack. Five browser tests passed with no unexpected page or console errors. They covered board load, issue CRUD, Markdown and XSS safety, settings at five widths, mobile lanes, scheduling, run history, Git actions, and chat flows.

The code is ready for a ship decision with two known limits. No live Linear schema call was made. An issue with more than 50 incoming relations stops the candidate read instead of risking a hidden blocker.

## Proof per requirement

### R1: remove both obsolete filters

Command:

```bash
! rg -n 'inverseRelations\(filter:' src/symphony/trackers/linear.py
```

Result: exit 0 with no matches.

### R2: request a bounded connection and completeness flag

Command and output:

```text
rg -n 'inverseRelations\(first: 50\)|pageInfo \{ hasNextPage \}' src/symphony/trackers/linear.py
56:      inverseRelations(first: 50) {
61:        pageInfo { hasNextPage }
121:    inverseRelations(first: 50) {
126:      pageInfo { hasNextPage }
```

Both candidate polling and full issue fetch use the approved shape.

### R3: keep only `blocks` relations

Command:

```bash
.venv/bin/python -m pytest -q tests/test_tracker_linear_full.py
```

Post-fix result: `56 passed in 0.30s`. Candidate and full-fetch fixtures each contain one `related` relation and one `blocks` relation. Each result contains only the blocker.

### R4: fail closed through client and orchestrator paths

Command:

```bash
.venv/bin/python -m pytest -q \
  tests/test_tracker_linear_full.py \
  tests/test_orchestrator_contract_integration.py \
  tests/test_orchestrator_dispatch.py::test_candidate_payload_failure_logs_and_returns_before_dispatch
```

Final result after code-review strengthening: `62 passed in 0.81s`.

Observed checks:

- Candidate and full client reads propagate every approved `LinearUnknownPayload` message.
- Candidate failure logs `candidate_fetch_failed`.
- Candidate failure makes zero dispatch calls.
- Candidate failure does not enter post-fetch scheduling.
- Full-refresh failure logs `issue_full_refresh_failed` and records the tracker error.
- The minimal issue description remains `None`.
- The contract-failure note is written and the ticket returns to `In Progress`.

### R5: deterministic red-green regression

Before the source fix, the new tracker tests returned:

```text
28 failed, 28 passed in 0.54s
```

After both query and validation changes:

```text
56 passed in 0.30s
```

The focused end-to-end set finished with `62 passed in 0.81s` after the final test-strengthening edit.

### R6: cover complete, incomplete, and malformed connections

Parameterized tests exercise both client reads for these shapes:

- missing, null, and non-mapping `inverseRelations`
- missing, null, and non-list `nodes`
- missing, null, and non-mapping `pageInfo`
- missing and non-boolean `hasNextPage`
- `hasNextPage: true`
- an empty complete connection
- a complete 50-node connection with 49 unrelated relations and one blocker

Each invalid shape checks the exact approved error message. The 50-node case preserves the one blocker.

### R7: keep the approved scope

Command and output:

```text
git diff --name-only -- src tests
src/symphony/trackers/linear.py
tests/test_orchestrator_contract_integration.py
tests/test_orchestrator_dispatch.py
tests/test_tracker_linear_full.py
```

This command returned no output:

```bash
git diff --name-only -- \
  pyproject.toml \
  src/symphony/issue.py \
  src/symphony/trackers/__init__.py
```

No test or implementation uses a live Linear endpoint or credential.

### R8: pass focused and configured checks

Fresh verifier round 2 result:

```text
Finding resolved: YES
New defect: NO
Config/test mismatch: none
VERDICT: PASS
```

Fresh verifier round 2 returned `2324 passed, 11 skipped` with 84.61% coverage before release synchronization. After rebasing onto `origin/main`, the conflict-sensitive set returned `64 passed`, and the second full integration run returned `2415 passed, 14 skipped` with 84.62% coverage.

## Behavior evidence

| Flow | Observed result | Evidence |
| --- | --- | --- |
| B1 candidate poll | The outgoing document has the current schema shape. Complete mocked candidates normalize. Live Linear acceptance was not checked. | R1, R2, focused tests |
| B2 full issue fetch | The outgoing document has the same bounded connection. Complete full issues normalize. Incomplete refreshes enter contract failure and rewind. | R2, R4 |
| B3 mixed relation types | Only `type == "blocks"` becomes `Issue.blocked_by`. | R3 |
| B4 empty connection | Empty `nodes` with `hasNextPage: false` returns no blockers. | R6 |
| B5 more than 50 relations | `hasNextPage: true` raises `linear_unknown_payload: issue.inverseRelations incomplete`; scheduling and dispatch do not run. | R4, R6 |
| B6 malformed connection | Every approved malformed container or flag raises its exact typed error. | R4, R6 |
| B7 existing HTTP and retry behavior | No related production path changed. The existing tracker tests and full suite pass. | full suite, R7 |

## Regression

Baseline command before implementation:

```bash
.venv/bin/python -m pytest -q \
  tests/test_tracker_linear_full.py \
  tests/test_orchestrator_contract_integration.py
```

Baseline: `32 passed in 0.28s`.

After: `61 passed in 0.27s` in verifier round 1. The increase is 29 tests. It consists of 26 dual-client malformed-shape cases, two dual-client complete-50 cases, and one full-refresh integration case. The candidate failure test brings the combined focused set to 62.

- U1: mixed-relation tests pass for both client reads.
- U2: `symphony-pyright` reports no errors or warnings; interface paths have no diff.
- U3: no Linear mutation or archive implementation changed; the full suite passes.
- U4: no non-Linear tracker implementation changed; the full suite passes.
- U5: `.github/hooks/` still contains only `.github/hooks/workmux-status/hooks.json`. The SHA-256 manifest reports `OK` for that file, `.probe_artifacts.py`, `.probe_web.py`, and `.workmux.yaml`.

## Full checks

- Build: `python3.12 -m pip wheel . --no-deps --wheel-dir /tmp/oh-my-symphony-wheel` → exit 0 after rebase; `Successfully built oh-my-symphony`; wheel size 650245 bytes; SHA-256 `00139db524e71b0640a5e58b39d8bc1ef91cbae3d7fbae5c1f02855831834d32`.
- Test: `env -u SYMPHONY_LANG .venv/bin/python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80` → exit 0 after rebase; `2415 passed, 14 skipped in 266.93s`; 84.62% coverage, above 80%.
- Lint: `.venv/bin/python -m ruff check src tests` → `All checks passed!` after rebase.
- Translations: `.venv/bin/python scripts/check_i18n.py` → `OK: 551 keys, languages=['en', 'ko'], all used and translated` after rebase.
- Type check: `.venv/bin/symphony-pyright` → no errors or warnings after rebase.
- Safe run check: `.venv/bin/python -m symphony --help` → exit 0 and CLI usage. No agent or orchestrator started.
- Browser E2E: `SYMPHONY_BROWSER_E2E=1 env -u SYMPHONY_LANG .venv/bin/python -m pytest -q -rs tests/test_web_browser_e2e.py` → exit 0 after rebase; `5 passed in 22.48s`.
- Whitespace: `git diff --check -- src tests` → no output.
- Pi-lens: primary Python diagnostics are clean. Six auxiliary false positives on raw `dict` test annotations were removed by adding precise nested dictionary types.

The post-rebase configured test command reports 14 conditional skips because it does not enable every optional environment. The five browser E2E cases were rerun separately and all passed. The quiet full-suite output did not expand the remaining platform, permission, git, symlink, and interpreter capability skip reasons. None is in the changed Linear tracker behavior.

## Browser QA

- Mode: LOCAL-OFFLINE regression.
- Server: real `symphony.server.build_app` on an aiohttp test server.
- Backend seam: local file board and stub orchestrator. No coding agent started.
- Browser: Playwright Chromium at desktop and mobile viewport sizes.
- Result after rebase: `5 passed in 22.48s`.
- Side effects: no unexpected page errors or console errors. Deliberately rejected Git and chat requests were limited to expected resource-error messages.
- Evidence logs: `.pi/tasks/session-75864-75864/bd1bfb8c3.output` before rebase and `.pi/tasks/session-75864-75864/b958ad820.output` after rebase.

## Adversarial code review

- Blocking finding: zero dispatch alone did not prove candidate failure returned before post-fetch scheduling. Accepted and fixed. The test now installs a `_sort_with_wait_age_bump` sentinel and asserts it is never called.
- Round 2: the reviewer confirmed the sentinel fails if scheduling continues and found no new defect. Verdict: `NO BLOCKERS`.
- Formatting churn inside approved files: rejected as a ship blocker. Repository formatting hooks rewrote existing style inside the same four approved paths. The semantic diff and all tests were reviewed. No fifth implementation path changed. This remains a reviewability cost.
- Reviewer did not rerun every configured command: resolved by the final full receipts above and fresh verifier round 2.
- Malformed individual relation nodes: rejected as new scope. Their permissive handling predates this change. The approved work validates the relation connection container and completeness flag.

## Accepted and declined build findings

Accepted:

- Corrected the build command because the project virtual environment has no pip module.
- Replaced unsafe source-repository startup with a `--help` startup check.
- Removed ambient `SYMPHONY_LANG` from the full test process.
- Strengthened the candidate failure test with a post-fetch scheduling sentinel.
- Resolved the rebase conflict by preserving upstream's Windows-safe `snapshot.as_posix()` behavior and the verified candidate-failure test.
- Replaced two raw `dict` test annotations with precise nested dictionary types to remove six auxiliary false positives.

Declined:

- None in the final fix loop.

Details: `.sdlc/work/linear-polling-schema-drift/deviations.md`.

## Rebase integration

- Rebasing onto `origin/main` produced one conflict in `tests/test_orchestrator_dispatch.py`. The resolved hunk retained the upstream POSIX path conversion. The feature's candidate-failure test remained intact.
- Conflict-sensitive command: `64 passed in 4.78s`.
- The first post-rebase full run hit one unchanged process-reclaim timing failure. That test then passed three isolated runs in 0.45 to 0.46 seconds.
- The second post-rebase full run passed all 2415 executed tests. No source change was made for the timing failure.
- Post-rebase browser E2E passed all five cases.

## Not verified

- No live Linear API or schema call ran. Current-schema evidence comes from GitHub issue #58, the public Linear schema, and deterministic request inspection.
- Behavior above 50 incoming relations is intentionally fail-closed. It does not paginate the nested relation connection.
- The full suite's remaining non-browser skips were not expanded by the configured quiet output. They are reported as skips, not passes.
- The rebased commit exists locally. No push, package publication, or deployment ran.
- Protected `main` push remains pending the final clean-tree and evidence-hash update.

## Retro lessons

- Unset ambient language when testing workflow precedence → `.sdlc/memory/lessons/2026-08-29-unset-language-test-env.md`.
- Smoke every SDLC proof command before approval → `.sdlc/memory/lessons/2026-08-29-smoke-sdlc-config-commands.md` [promote: `skills/1-intent`].

The main surprise was not the Linear change. It was the proof environment. Three initial SDLC commands were invalid or non-deterministic for this checkout. Future intent work should execute each proof command before approval.
