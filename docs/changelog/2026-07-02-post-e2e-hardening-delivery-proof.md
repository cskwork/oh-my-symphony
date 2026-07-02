# Delivery Proof

## Eval Intent

- Goal: turn the post-pipeline manual E2E findings into repeatable browser/API QA and targeted web UX hardening.
- Constraints: keep the Python test stack primary; make browser QA opt-in; avoid Node dependencies; preserve compact default web board behavior; keep changes scoped to the plan.
- Tradeoffs: add Playwright as an optional Python extra; add additive API payload fields that old clients can ignore; keep `All` mode as the full horizontal board.
- Rejected approaches: mandatory browser E2E in default pytest; Node `@playwright/test`; unconditionally moving exhausted tickets to `Blocked`.

## Before State

- Mode: LEGACY.
- Proof: `docs/plans/2026-07-02-post-e2e-hardening-plan.md` identifies gaps in browser DOM coverage, live API smoke coverage, budget-exhausted operator visibility, mobile board ergonomics, and terminal-state wording.
- Command or artifact: `.venv/bin/python -m pytest tests/test_web_static_contract.py tests/test_webapi.py -q` before implementation.
- What this proves: existing static/API contracts are green before the hardening changes.
- What this does not prove: browser behavior, live server smoke behavior, or Playwright availability.

## After Target

- Expected behavior: opt-in browser E2E exists and skips by default; live API smoke command exists and cleans up its card; budget-exhausted tickets surface in API/card/drawer; mobile active board uses lane tabs; docs describe the compact terminal group.
- Compatibility to preserve: normal `python -m pytest -q` must remain usable without browser binaries; existing board API clients should tolerate the additive `attention` field; default active-board column filtering remains.
- Intentional drift: terminal group title changes from `Terminal states` to `Review and parked` while retaining the accessibility label.

## Command Manifest

| Name | Command | Source | Proves | Used when |
|---|---|---|---|---|
| baseline web contracts | `.venv/bin/python -m pytest tests/test_web_static_contract.py tests/test_webapi.py -q` | frozen_repo | current web static/API tests pass before changes | before |
| markers | `.venv/bin/python -m pytest --markers` | frozen_repo | pytest marker registration | after |
| browser skip | `.venv/bin/python -m pytest tests/test_web_browser_e2e.py -q` | frozen_repo | browser E2E stays skipped by default | after |
| smoke script | `.venv/bin/python -m pytest tests/test_web_api_smoke_script.py -q` | frozen_repo | API smoke runner works against test server | after |
| focused web | `.venv/bin/python -m pytest tests/test_webapi.py tests/test_web_static_contract.py tests/test_orchestrator_dispatch.py::test_issue_attention_reports_budget_exhaustion tests/test_orchestrator_dispatch.py::test_turn_budget_exhaustion_survives_next_tick_claim_prune -q` | frozen_repo | attention API/static guards and scheduler regression stay green | after |
| docs static | `.venv/bin/python -m pytest tests/test_web_static_contract.py tests/test_workflow_pipeline_prompt.py -q` | frozen_repo | terminal wording/docs prompt contract stays green | after |
| js syntax | `node --check src/symphony/web/static/app.js` | frozen_repo | static JS parses | after |
| whitespace | `git diff --check` | frozen_repo | diff has no whitespace errors | after |
| full suite | `.venv/bin/python -m pytest -q` | frozen_repo | full repo tests pass with browser E2E skipped unless enabled | after |
| enabled browser | `SYMPHONY_BROWSER_E2E=1 .venv/bin/python -m pytest tests/test_web_browser_e2e.py -q -rs` | frozen_repo | real browser UI flow works when Playwright/browser are available | after |
| live smoke | `.venv/bin/python scripts/smoke_web_api.py --base-url http://127.0.0.1:9999 --prefix SMOKE` | frozen_repo | smoke command works against a real local server | after |

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | no-op | resolved | Browser binaries may be unavailable on some machines. | Keep E2E opt-in and skip by default. | default browser test skip |
| d2 | auto-fix | resolved | Exact code shape differed from the implementation plan snippets. | Matched current flex board and response parsing behavior. | focused web tests, browser E2E |
| d3 | no-op | resolved | Playwright 1.61 browser install hung and Chromium launch failed inside the sandbox. | Verified with Playwright 1.60, which satisfies `playwright>=1.48`, and reran the browser test with sandbox escalation. | enabled browser |

## After Evidence

| Check | Status | Evidence | Verifies | Does not verify |
|---|---|---|---|---|
| baseline web contracts | PASS | `19 passed in 3.17s` | pre-change web static/API contracts | browser behavior |
| marker registration | PASS | `@pytest.mark.browser_e2e: browser-driven web UI tests...` | opt-in marker is registered | browser execution |
| browser skip | PASS | `1 skipped in 0.19s` | default pytest can collect and skip browser E2E without Playwright/binaries | enabled browser behavior |
| smoke script test | PASS | `1 passed in 0.24s` | smoke script handles static JS and API CRUD against a test server | live process binding |
| focused web | PASS | `22 passed in 0.99s` | attention API/UI static guards and budget scheduler regression | full-suite interactions |
| docs static | PASS | `34 passed in 0.29s` | terminal wording and pipeline prompt contract | browser rendering |
| js syntax | PASS | no output, exit 0 | `app.js` parses | runtime behavior |
| whitespace | PASS | no output, exit 0 | no whitespace errors | semantic correctness |
| full suite | PASS | `866 passed, 2 skipped in 57.76s` | repo-wide tests with browser E2E skipped by default | enabled browser |
| enabled browser | PASS | `1 passed in 6.11s` with sandbox escalation | desktop/mobile board UI, issue CRUD, terminal grouping, no console errors | manual human visual inspection |
| live smoke | PASS | `ok state` ... `ok workflow stats skills`, `count: 8`; temp `kanban/` empty after cleanup | real local server smoke command and cleanup | skip-learn optional path |

## Residual Risk

- Not proven: manual visual QA beyond the automated browser assertions; skip-learn live smoke optional path without a prepared Learn ticket.
- Follow-up: none required for this plan.
