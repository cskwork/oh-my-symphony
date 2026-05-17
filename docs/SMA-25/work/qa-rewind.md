# SMA-25 Work Note — QA Rewind

**What**: The full-suite blocker was in the doctor test fixture, not in the SMA-25 auto-commit mechanism.
**Why**: The required QA fallback must pass in restricted sandboxes where opening a local listener is not allowed.
**As-Is → To-Be**:
- As-Is: `test_port_fail_when_already_bound` tried to bind a real socket before calling `check_port`, so the sandbox failed the setup step with `PermissionError`.
- To-Be: The test now uses a small fake occupied socket and verifies the same user-visible doctor result: a `cannot bind` failure for the configured port.

## Verification

- Red: `.venv/bin/pytest tests/test_doctor.py::test_port_fail_when_already_bound -q` failed with `PermissionError: [Errno 1] Operation not permitted`.
- Green: `.venv/bin/pytest tests/test_doctor.py::test_port_fail_when_already_bound -q` returned `1 passed`.
- SMA-25 guard: `.venv/bin/pytest tests/test_workspace.py -q` returned `30 passed`.
- Full fallback: `env -u SYMPHONY_CODEX_WRITABLE_ROOTS .venv/bin/pytest -q` returned `525 passed, 6 skipped`.
- Preflight: `symphony doctor ./WORKFLOW.md` still returned 1 because this sandbox cannot bind `127.0.0.1:9999` or create a probe under `/Users/danny/symphony_workspaces`.

## Scope

- Changed only `tests/test_doctor.py`; the already-reviewed `symphony.autocommitExclude` production path and regression tests were left unchanged.
