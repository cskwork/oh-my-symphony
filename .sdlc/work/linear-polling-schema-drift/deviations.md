# Build deviations: linear-polling-schema-drift

## Accepted

- Configured build command failed before building because `.venv/bin/python` has no `pip` module. Evidence: `.venv/bin/python -m pip wheel ...` exited 1 with `No module named pip` on 2026-08-29.
- Build command correction: `.sdlc/config.md` now uses `python3.12 -m pip wheel . --no-deps --wheel-dir /tmp/oh-my-symphony-wheel`. The host Python is 3.12.11 with pip 25.1.1. Product code and product configuration remain unchanged.

- Configured run command without arguments tried to start the protected source repository and exited 1 by design. `.sdlc/config.md` now uses `.venv/bin/python -m symphony --help`, which exercises CLI startup without dispatching agents and exits 0.
- Verifier round 1 inherited `SYMPHONY_LANG=ko`, so unchanged default-language tests failed. The finding is accepted. The first correction set `SYMPHONY_LANG=en`, which fixed default-language cases but wrongly overrode workflow-level Korean cases. The corrected `.sdlc/config.md` command uses `env -u SYMPHONY_LANG`, which tests both the default and explicit workflow settings without an ambient override.
- Rebasing onto `origin/main` produced one conflict in an upstream Windows-safe shell-path test. The resolution kept upstream's `snapshot.as_posix()` behavior and the feature's candidate-failure test. The conflict-sensitive set passed 64 tests.
- Pi-lens reported auxiliary false positives on two raw `dict` annotations in existing `execute_raw` tests. Precise nested dictionary annotations removed all six findings without runtime behavior changes.

## Declined

- None.

## Verification

- Local focused command: 62 passed.
- Local configured build, lint, translations, and type check: passed. Full test suite after removing the ambient language override: 2324 passed, 11 skipped, 84.61% coverage.
- Fresh verifier round 1: failed only because its inherited `SYMPHONY_LANG=ko` broke unchanged default-language tests. No change-specific defect was found.
- Verifier round 2: PASS. The corrected exact test command reported 2324 passed and 11 skipped at 84.61% coverage. The focused command reported 62 passed, and the product diff remained the same four approved files.
- Post-rebase focused and conflict checks: 64 passed.
- First post-rebase full run hit one unchanged process-reclaim timing failure. The test then passed three isolated runs. A second full run passed 2415 tests with 14 conditional skips and 84.62% coverage.
- Post-rebase lint, translations, and type check passed. Browser E2E passed 5 tests in 22.48 seconds.
