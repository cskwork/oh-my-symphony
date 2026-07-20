# 010 - Integrated fake end-to-end

Route: GREENFIELD

Status: pending

Blocked by: 001-009

Unblocks: 011

## Goal

Prove the complete resolver with fake Jira, disposable Git services, fake Jenkins/dev runtime, mock Codex,
browser, and TUI, including restart and duplicate prevention without touching real systems.

## Acceptance criteria

- A fake assigned subtask with empty body imports once with parent context and routes to a fixture service.
- One card traverses all gates through Dev QA/Done with hashes, SHAs, approvals, and side-effect evidence.
- Restart mid-run proves lease recovery, pause persistence, trigger idempotency, and no duplicate card,
  worker, worktree, merge, or Jenkins run.
- Negative cases prove wrong assignee, ambiguous route, protected occupancy, stale/missing proof, failed QA,
  timeout correlation, and wrong deployed SHA cannot advance.
- API, browser, and TUI agree on state; health and UI diagnostics are clean.
- Cleanup removes only disposable state and proves real Jira, AIDT, Jenkins, and port state were untouched.

## Proof commands and surfaces

- pytest -q tests/test_aidt_resolver_e2e.py
- SYMPHONY_BROWSER_E2E=1 pytest -q -rs tests/test_aidt_resolver_browser_e2e.py
- Port-9918 health/state/board, browser/TUI captures, registry/history/stats, Git refs, and fake-Jenkins ledger.

## Scope boundaries

- Uses only temporary boards, repositories, identities, service state, and fake external systems.
- Does not authenticate, push AIDT refs, deploy, mutate dev, or install a real LaunchAgent.

## External blockers

- No external credentials are required.
- Local Python/browser dependencies and an available loopback test port are required.
