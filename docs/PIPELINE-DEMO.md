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

- Goal: make manual refresh responses bypass intermediary caches.
- Before state: `/api/v1/refresh` can be stored briefly by a proxy.
- After target: `/api/v1/refresh` always returns `Cache-Control: no-store`.
- Change `/api/v1/refresh` responses from `Cache-Control: max-age=0` to
  `Cache-Control: no-store`.
- Touch only `src/symphony/server.py` and the focused server route test.
- First failing test: `test_refresh_sets_no_store_cache_header`.

## Acceptance Tests

- `POST /api/v1/refresh` returns 200 with `Cache-Control: no-store` - prove
  with curl output.
- Existing refresh payload shape is unchanged - prove with focused route test.
- No unrelated API route header changes - prove with reviewed diff.

## Done Signals

- Focused server route test passes.
- Manual curl shows the new header.
- Diff contains only the endpoint header change and its regression test.
- Not proven: broader route-by-route cache policy.

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
- Verify should focus on the real response header and confirm the payload did
  not drift.

## Security Audit

| Area | Status | Evidence |
| --- | --- | --- |
| secrets | pass | `qa/security-audit.md` |
| input-validation | pass | `qa/security-audit.md` |
| injection | n/a | `qa/security-audit.md` |
| xss | n/a | `qa/security-audit.md` |
| csrf | n/a | `qa/security-audit.md` |
| authz | pass | `qa/security-audit.md` |
| rate-limit | n/a | `qa/security-audit.md` |

## Review

- Diff matches the plan and does not widen route behavior.
- Regression test covers the changed observable header.
- No blocking review findings.

## QA Evidence

What worked:
- Focused route test passed.
- Manual curl showed `Cache-Control: no-store`.

What did not work:
- None.

Not covered:
- Broader cache policy for other routes.

How to re-run:
- `pytest -q tests/test_server.py`
- `curl -i -X POST http://127.0.0.1:9999/api/v1/refresh`

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
| refresh returns no-store | pass | `qa/refresh-response-tobe.json` |
| payload shape unchanged | pass | `qa/server-test.log` |
| no unrelated API route changes | pass | `work/diff-review.md` |

## Merge Status

- target branch: `main`
- feature branch: `symphony/PIPELINE-DEMO`
- proof: merged with `--no-ff`; final ref recorded in
  `docs/PIPELINE-DEMO/verify/merge-proof.txt`.

## Wiki Updates

- `docs/llm-wiki/api-cache-policy.md` - documented that refresh-like control
  routes should use `no-store` when operators expect immediate state.

## Human Review

### What Changed
- Refresh responses now bypass proxy storage.

### Why It Matters
- Operators get fresh state after a manual refresh.

### Evidence
- `pytest -q tests/test_server.py` rc=0.
- Manual curl shows `Cache-Control: no-store`.

### Risks
- Other polling routes may still need a broader cache-policy audit.

### Human Checklist
- [ ] Confirm the focused test command passed.
- [ ] Confirm curl shows `Cache-Control: no-store`.
- [ ] Confirm no unrelated route was changed.

### Decision Needed
Confirm Done

## As-Is -> To-Be Report

### Goal
- Manual refresh responses bypass intermediary caches.

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

### Not Covered
- Broader cache policy for other polling routes.

### How To Re-run
- Run `pytest -q tests/test_server.py`, then call
  `curl -i -X POST http://127.0.0.1:9999/api/v1/refresh`.
