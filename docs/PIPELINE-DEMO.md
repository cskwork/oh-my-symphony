---
id: PIPELINE-DEMO
identifier: PIPELINE-DEMO
title: Reference ticket showing the In Progress / Verify / Learn / Done shape
state: Done
priority: 3
labels:
- demo
- docs
created_at: '2026-05-09T20:00:00Z'
updated_at: '2026-07-02T00:00:00Z'
---

This ticket is a worked example. It illustrates the sections a completed
four-stage Symphony ticket should leave behind.

## Plan

- Change `/api/v1/refresh` responses from `Cache-Control: max-age=0` to
  `Cache-Control: no-store`.
- Touch only `src/symphony/server.py` and the focused server route test.
- First failing test: `test_refresh_sets_no_store_cache_header`.

## Acceptance Tests

- `POST /api/v1/refresh` returns 200 with `Cache-Control: no-store`.
- Existing refresh payload shape is unchanged.
- No unrelated API route header changes.

## Done Signals

- Focused server route test passes.
- Manual curl shows the new header.
- Diff contains only the endpoint header change and its regression test.

## Implementation

- `src/symphony/server.py` - `_handle_refresh()` sets `Cache-Control:
  no-store`.
- `tests/test_server.py` - regression test asserts the response header.
- `docs/PIPELINE-DEMO.md` - no runtime behavior; this file only demonstrates
  the ticket body shape.

## Self-Critique

- The fix is intentionally narrow. It does not change `/api/v1/state`, which
  may deserve a separate cache-policy ticket.
- Header spelling is inline because the value has one use site.

## Security Audit

| Area | Status | Evidence |
| --- | --- | --- |
| auth/session | pass | refresh route has no auth behavior change |
| input validation | pass | endpoint still accepts no request body |
| data exposure | pass | payload shape unchanged |
| destructive actions | pass | route only requests orchestrator refresh |
| secrets | pass | no config or credential paths touched |

## Review

- Diff matches the plan and does not widen route behavior.
- Regression test covers the changed observable header.
- No blocking review findings.

## QA Evidence

```text
$ pytest -q tests/test_server.py
....                                                                     [100%]
4 passed in 0.42s
exit code: 0

$ curl -i -X POST http://127.0.0.1:9999/api/v1/refresh
HTTP/1.1 200 OK
Content-Type: application/json
Cache-Control: no-store
...
exit code: 0
```

artefacts:
- `docs/PIPELINE-DEMO/qa/refresh-response-asis.json`
- `docs/PIPELINE-DEMO/qa/refresh-response-tobe.json`

## AC Scorecard

| AC | Result | Evidence |
| --- | --- | --- |
| refresh returns no-store | pass | curl output above |
| payload shape unchanged | pass | `tests/test_server.py` |
| no unrelated API route changes | pass | reviewed diff |

## Merge Status

- target branch: `main`
- feature branch: `symphony/PIPELINE-DEMO`
- proof: merged with `--no-ff`; final ref recorded in
  `docs/PIPELINE-DEMO/verify/merge-proof.txt`.

## Wiki Updates

- `docs/llm-wiki/api-cache-policy.md` - documented that refresh-like control
  routes should use `no-store` when operators expect immediate state.

## Human Review

### Summary
- Refresh responses now bypass proxy storage.

### Evidence
- `pytest -q tests/test_server.py` rc=0.
- Manual curl shows `Cache-Control: no-store`.

### Residual Risk
- Other polling routes may still need a broader cache-policy audit.

## As-Is -> To-Be Report

### As-Is
- `/api/v1/refresh` returned `Cache-Control: max-age=0`, allowing short proxy
  reuse in some deployments.

### To-Be
- `/api/v1/refresh` returns `Cache-Control: no-store`, so forced refreshes
  request fresh state.

### Reasoning
- One header change solves the reported behavior without changing unrelated
  handlers. A broader cache policy is deferred because it needs route-by-route
  review.

### Evidence
- Commands: `pytest -q tests/test_server.py` rc=0; `curl -i -X POST
  http://127.0.0.1:9999/api/v1/refresh` rc=0.
- Test: `tests/test_server.py::test_refresh_sets_no_store_cache_header`.
- Artefacts: `docs/PIPELINE-DEMO/qa/refresh-response-asis.json`,
  `docs/PIPELINE-DEMO/qa/refresh-response-tobe.json`.
