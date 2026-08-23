# 100% Coverage Patch Release Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Raise measured `src/symphony` line coverage from 83.426% to exactly
100.00%, enforce the gate in CI, and publish patch release `v0.21.1` only after
all platform, E2E, packaging, and adversarial-review gates pass.

**Architecture:** Preserve production behavior and cover real contracts through
unit, integration, CLI, browser, and service-lifecycle tests. Partition work by
source domain so test files do not overlap. Because Windows and POSIX paths are
mutually exclusive, retain independent platform test jobs and combine their raw
coverage data before enforcing the repository-wide 100% line gate.

**Tech Stack:** Python 3.12+, pytest, pytest-cov/coverage.py, Playwright,
GitHub Actions, uv, Ruff, Pyright.

---

## Coverage policy

- The measured target is line coverage for every executable statement under
  `src/symphony`: `missing_lines == 0` and `percent_covered == 100.0`.
- Do not lower a threshold, omit a source package, use broad `pragma: no cover`,
  or add assertions that do not verify behavior.
- Prefer deletion of proven dead code over tests that preserve unreachable code.
- Platform-only code must be executed on its native runner or through an
  existing injectable seam. Any exclusion requires concrete impossibility
  evidence and independent adversarial approval.
- Each new test states the user/operator/runtime contract it protects.
- Production behavior remains unchanged unless a test exposes a real defect;
  such a defect follows a separate RED -> GREEN TDD cycle.

## Baseline

- Command: browser-enabled full pytest with `--cov=src/symphony`.
- Result at `v0.21.0`: 23,006 statements, 3,813 missed, 137 excluded,
  83.42606276623489% line coverage.
- Largest gaps: orchestrator core (825), TUI app (215), web API (199),
  workspace (175), release contracts (154), chat (138), mock backend (134),
  service (129), shell helpers (126), projects (120), Codex backend (115), and
  run registry (104).

### Task 1: Establish reproducible coverage inventory

**Files:**
- Create: `docs/plans/2026-08-24-coverage-100-patch-release.md`
- Modify later: `.github/workflows/tests.yml`

1. Generate JSON coverage from the full browser-enabled suite.
2. Record every file and missing-line set, sorted by missed statements.
3. Group files into disjoint domains with distinct test-file ownership.
4. Re-run the current full suite to confirm the baseline is reproducible.
5. Commit the plan and baseline evidence on the coverage branch.

### Task 2: Cover orchestration and persistence contracts

**Files:**
- Source scope: `src/symphony/orchestrator/**`
- Test scope: `tests/test_orchestrator_*.py`, `tests/test_run_registry.py`,
  `tests/test_migrations.py`, plus new uniquely named coverage test modules.

1. Select one uncovered behavior slice from the JSON report.
2. Write the smallest behavior-focused test and verify it executes that slice.
3. Run the focused test and domain coverage report.
4. Repeat until the domain has no missing executable lines.
5. Run all orchestrator, release-contract, migration, and registry tests.

### Task 3: Cover web, TUI, chat, project, and workspace contracts

**Files:**
- Source scope: `src/symphony/webapi.py`, `src/symphony/tui/**`,
  `src/symphony/chat.py`, `src/symphony/projects.py`,
  `src/symphony/workspace.py`
- Test scope: corresponding existing tests plus new uniquely named coverage
  modules and existing browser E2E tests.

1. Cover API authentication/error/serialization boundaries through HTTP tests.
2. Cover TUI actions through messages, key bindings, and rendered state.
3. Cover chat lifecycle and cancellation through observable session behavior.
4. Cover project/workspace filesystem and Git boundaries in temporary repos.
5. Run the full browser E2E suite after domain tests are green.

### Task 4: Cover services, shell, backends, CLI, and remaining modules

**Files:**
- Source scope: remaining `src/symphony/**` files, especially `service.py`,
  `_shell.py`, `backends/**`, `cli/**`, `trackers/**`, and utilities.
- Test scope: corresponding existing tests plus new uniquely named coverage
  modules.

1. Exercise subprocess and service behavior with bounded, self-cleaning probes.
2. Exercise backend protocol/error paths with deterministic fake processes.
3. Exercise CLI entry points through their public parsers and return codes.
4. Cover small utility tails and delete only code proven unreachable.
5. Run all affected integration suites and confirm no temporary residue.

### Task 5: Enforce honest combined-platform coverage

**Files:**
- Modify: `.github/workflows/tests.yml`
- Modify if required: `pyproject.toml`

1. Add independent Linux and Windows test jobs that save raw coverage data.
2. Add a coverage-combine job that downloads both artifacts.
3. Run `coverage combine`, `coverage report --fail-under=100`, and generate XML.
4. Keep Ruff, i18n, Pyright, and platform test failures independently blocking.
5. Verify the workflow on a pull request before merging.

### Task 6: Final verification and `v0.21.1`

**Files:**
- Modify: `CHANGELOG.md`, `docs/index.html`, `pyproject.toml`,
  `src/symphony/__init__.py`, `uv.lock`

1. Run fresh Ruff, native/Linux Pyright, i18n, and lock checks.
2. Run the full Windows suite with browser E2E and real process-tree capability.
3. Require combined coverage JSON to report zero missing lines and 100.00%.
4. Run live authenticated managed-service start/health/ordinary-stop E2E.
5. Build wheel/sdist, run Twine, and install-smoke the wheel in fresh Python.
6. Obtain independent standards/spec/security review with no blocking findings.
7. Merge through `dev` and a checked `dev -> main` merge commit.
8. Require exact-commit Tests and Pages success on main.
9. Bump to `0.21.1`, repeat the release-candidate gates, create annotated tag,
   publish the GitHub release, and audit public state.
