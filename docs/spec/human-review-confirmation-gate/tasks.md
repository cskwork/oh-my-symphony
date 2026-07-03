# Tasks: Human Review Confirmation Gate

## Task List

- [ ] 1. Add failing service-board static contract tests
  - Assert `Confirm Done` appears in `src/symphony/web/static/app.js`.
  - Assert a `confirmDone` API client method posts to
    `/api/v1/issues/{identifier}/confirm-done`.
  - Assert card rendering gates the button on normalized `human review`.
  - Requirements: 1.1, 1.2, 1.3, 1.4, 4.1, 4.4.

- [ ] 2. Add failing service API tests
  - Human Review ticket confirms to Done.
  - Learn or Todo ticket returns 409 and remains unchanged.
  - Missing ticket returns 404.
  - Successful confirmation records stats and requests refresh.
  - Requirements: 2.1, 2.2, 2.3, 2.4.

- [ ] 3. Implement `POST /api/v1/issues/{identifier}/confirm-done`
  - Add route in `src/symphony/webapi.py` near the issue routes.
  - Reuse `FileBoardTracker`, `_valid_states`, `_check_identifier`, stats,
    and `orchestrator.request_refresh()`.
  - Keep wrong-state errors actionable and non-destructive.
  - Requirements: 2.1, 2.2, 2.3, 2.4, 5.3.

- [ ] 4. Implement the service-board card action
  - Add `api.confirmDone`.
  - Render `Confirm Done` for non-read-only Human Review cards in
    `buildCardEl`.
  - Stop click propagation and use the existing control-action/toast flow.
  - Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.5.

- [ ] 5. Add browser-gated proof
  - Extend `tests/test_web_browser_e2e.py` to click `Confirm Done` on a seeded
    Human Review card.
  - Assert the card moves to Done and no page errors are emitted.
  - Requirements: 4.3.

- [ ] 6. Import and pin the Verify evidence contract
  - Add `docs/llm-wiki/verify-evidence-contract.md`.
  - Add the `verify-evidence-contract` row to `docs/llm-wiki/INDEX.md`.
  - Add or tighten contract tests for docs-root-relative evidence paths.
  - Requirements: 3.1, 3.2, 3.3, 3.4, 3.5.

- [ ] 7. Verify and record
  - Run focused tests:
    `.venv/bin/python -m pytest -q tests/test_web_static_contract.py tests/test_webapi.py tests/test_orchestrator_contracts.py tests/test_workflow_pipeline_prompt.py`.
  - Run browser E2E when available:
    `SYMPHONY_BROWSER_E2E=1 .venv/bin/python -m pytest -q tests/test_web_browser_e2e.py`.
  - Run `symphony doctor ./WORKFLOW.md`.
  - Run whitespace checks for touched docs and code.
  - Append the final implementation decision and verification to
    `docs/changelog/changelog-2026-07-03.md`.
  - Requirements: 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 5.4.

## Implementation Notes

- Keep this as an additive API change.
- Do not let agents mark Done.
- Do not change workflow states.
- Do not touch the pre-existing modified `tests/test_orchestrator_dispatch.py`
  unless the operator explicitly assigns that separate work.
