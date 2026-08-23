# Orchestrator native E2E shard

## Verdict

PASS — 77 passed, 18 skipped, 0 failed, 0 errors in 89.68 seconds.

## Command scope

The final normal-host run used the repository venv, disabled pytest cache, an
external test-owned basetemp, and these files:

- `tests/test_agent_lifecycle_e2e.py`
- `tests/test_deep_preset_e2e.py`
- `tests/test_orchestrator_contract_integration.py`
- `tests/test_orchestrator_release_contract_integration.py`
- `tests/test_backends_lifecycle.py`

The real grandchild process-tree test passed under normal host permissions.
All 18 skips came from release-contract scenarios requiring Windows symlink
privilege unavailable on this host.

One earlier run observed a concurrent release-reservation assertion. The exact
test then passed once in verbose isolation and 5/5 repetitions; the clean full
shard above and the later 2311-test repository run also passed it. No related
source change was made.
