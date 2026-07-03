# Delivery Proof: Operator Trust Program

## Eval Intent

- Goal: implement the remaining Operator Trust Program tasks from `docs/plans/2026-07-03-operator-trust-implementation.md`.
- Constraints: reliability-first, additive public API changes, no secrets or raw backend stderr in payloads, test-first slices, preserve four-lane workflow behavior.
- Tradeoffs: finish backend/data/API/CLI proof surfaces before web/TUI/docs surfaces so UI renders one shared truth.
- Rejected approaches: no new observability store; no field renames; no multi-node HA claims.

## Before State

- Mode: LEGACY
- Proof: current `dev` already has the audited health core and budget attention, but the spec tasks remain open for `starting`, owner-aware port text, attention taxonomy, run history, doctor prompt visibility, smoke health, docs, and final verification.
- Command or artifact: `docs/spec/operator-trust-program/tasks.md`, `docs/plans/2026-07-03-operator-trust-implementation.md`, focused baseline tests below.
- What this proves: the change is completing a checked-in plan, not inventing new scope.
- What this does not prove: full runtime service behavior until the final smoke gate runs.

## After Target

- Expected behavior: operators can read health startup state, attention causes, recent run history, doctor prompt/port context, smoke health failures, and README proof commands from shared runtime surfaces.
- Compatibility to preserve: existing health, state, issue, service, board, doctor, and CLI commands keep current fields and behavior unless fields are additive.
- Intentional drift: `health.status` may now be `starting` before the first completed tick.

## Command Manifest

| Name | Command | Source | Proves | Used when |
|---|---|---|---|---|
| focused-attention-ui | `PYTHONPATH=/private/tmp/symphony-operator-trust-run/src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py tests/test_run_registry.py tests/test_webapi.py tests/test_web_static_contract.py tests/test_tui.py` | worktree | Attention taxonomy, run registry, API, static web contract, TUI rendering | after |
| focused-touched-slices | `PYTHONPATH=/private/tmp/symphony-operator-trust-run/src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/test_orchestrator_health.py tests/test_run_registry.py tests/test_webapi.py tests/test_cli_main_routing.py tests/test_cli_run_startup.py tests/test_doctor.py tests/test_web_api_smoke_script.py tests/test_web_static_contract.py tests/test_tui.py` | worktree | All touched health/API/CLI/doctor/smoke/web/TUI slices | after |
| compileall | `PYTHONPATH=/private/tmp/symphony-operator-trust-run/src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m compileall -q src/symphony/orchestrator src/symphony/tui src/symphony/cli src/symphony/webapi.py scripts/smoke_web_api.py` | worktree | Syntax/import compile pass for touched Python modules | after |
| full-suite | `PYTHONPATH=/private/tmp/symphony-operator-trust-run/src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q` | worktree | Whole repo regression check | after |
| default-doctor | `PYTHONPATH=/private/tmp/symphony-operator-trust-run/src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m symphony.cli doctor ./WORKFLOW.md` | worktree | Repo workflow preflight truth in this sandbox | after |
| temp-doctor | `PYTHONPATH=/private/tmp/symphony-operator-trust-run/src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m symphony.cli doctor /private/tmp/symphony-operator-trust-smoke/WORKFLOW.md` | temp workflow | Launchable local file-board preflight | after |
| live-smoke | `PYTHONPATH=/private/tmp/symphony-operator-trust-run/src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python scripts/smoke_web_api.py --base-url http://127.0.0.1:54017` | live temp server | Health, state, board, static assets, issue CRUD, refresh, workflow stats | after |
| diff-check | `git diff --check` | worktree | Whitespace hygiene | after |

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | no-op | resolved | Backend lifecycle tasks are audited done in current spec. | Do not rewrite lifecycle code. | Spec audit + lifecycle tests in full suite. |
| d2 | additive API | resolved | Health and attention need richer state without breaking current clients. | Add `starting`, `workflow_path`, `severity`, and `due_at`; preserve existing fields. | API/web/TUI focused tests. |
| d3 | local ledger | resolved | Run history already has an authoritative SQLite table. | Expose bounded reads from RunRegistry instead of adding a second store. | RunRegistry/API/CLI tests and live `/api/v1/runs` request. |
| d4 | temp smoke | resolved | Default repo workflow is blocked by environment-specific workspace and board setup in this sandbox. | Use a temp file-board workflow with writable roots for the launch/smoke proof, and record the default doctor failures honestly. | Temp doctor and live smoke. |

## After Evidence

| Check | Status | Evidence | Verifies | Does not verify |
|---|---|---|---|---|
| focused-attention-ui | pass | `190 passed in 25.80s` | Attention taxonomy, priority order, terminal suppression, run history filter, API payloads, TUI/web rendering contracts | Real browser pixels |
| focused-touched-slices | pass | `137 passed, 2 warnings in 18.78s` | Health, run registry, web API, CLI routing/startup, doctor, smoke script, static web, TUI | Full repo outside touched slices |
| compileall | pass | no output / exit 0 | Touched Python files compile | Runtime semantics |
| full-suite | pass | `965 passed, 2 skipped, 2 warnings in 62.69s` | Whole repo regression gate | Browser E2E skipped unless explicitly enabled |
| default-doctor | fail expected | `workspace.root=/Users/danny/symphony_workspaces not writable`; `tracker.board_root .../kanban does not exist`; all other rows passed | Default workflow reports real local environment blockers instead of hiding them | Launchability in this sandbox |
| temp-doctor | pass with warning | Required rows passed; warning only for missing legacy separate `tools/board-viewer/server.py` | Temp file-board workflow is launchable on loopback with writable roots | Legacy board-viewer process |
| live-health | pass | `/api/v1/health` returned `status: ok`, `workflow_path`, `tick.alive: true`, `run_registry.enabled: true` | Health API startup/runtime payload | Degraded branches beyond tests |
| live-runs-api | pass | `/api/v1/runs?limit=5` returned `{"runs": [], "count": 0}` | Run-history endpoint exists and handles empty registry | Non-empty live dispatch history |
| live-smoke | pass | nine checks: health, state, board, static assets, issue create, issue detail, issue patch, refresh, workflow stats skills | Production server command and HTTP API CRUD/static path | External backend account dispatch |
| docs-grep | pass | README proof commands and API table found; no stale two-lane quickstart snippet found | Fresh-clone proof path and lane docs | Human readability beyond reviewed diff |
| diff-check | pass | no output / exit 0 | Whitespace hygiene | Functional behavior |

## Residual Risk

- Browser visual E2E was not run; static web contracts and live HTTP smoke cover
  the drawer/API wiring, not rendered screenshots.
- The live smoke uses `symphony.mock_codex` and does not prove a real external
  Claude/Codex/Gemini/Pi account dispatch.
- The default repo `WORKFLOW.md` still depends on the operator's real
  `~/symphony_workspaces` and board setup; doctor now reports those blockers
  clearly in restricted sandboxes.
