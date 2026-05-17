# SMA-25 QA Details

**What**: Fresh QA compared the base build and current build with real pytest commands.
**Why**: The ticket is non-API work, so the strongest proof is command execution against the Git/Bash test path and the whole project suite.

## Environment Constraint

- Requested As-Is setup: `git worktree add ../asis $(git config symphony.basesha)`.
- Observed constraint: `git worktree add` attempted to write host metadata under `/Users/danny/Documents/PARA/Resource/symphony-multi-agent/.git/worktrees/asis` and failed with `Operation not permitted`.
- Substitute: a local clone under `/private/tmp`, checked out detached at `fc80d707f720f27d5bf7633b7e131ff7d00aac67`; this preserves Git-tracked source and avoids writing host `.git/worktrees` metadata.

## Scenario Rationale

- `focused-doctor-port`: proves the QA-rewind fix changed the sandbox-sensitive doctor fixture from failing setup to passing behavior verification.
- `workspace-suite`: proves the `symphony.autocommitExclude` regression tests remain green across As-Is and To-Be.
- `full-suite`: proves the mandatory non-API fallback now passes on To-Be, while As-Is shows the exact previous blocker.
- `tobe-doc-check`: proves the operator-facing documentation still exists and mentions `symphony.autocommitExclude`.
- `workflow-doctor-preflight`: recorded as environment preflight; it still fails because this sandbox cannot bind `127.0.0.1:9999` or create under `/Users/danny/symphony_workspaces`.

## Performance Gate

- `workspace-suite`: As-Is `23234.321 ms`, To-Be `27480.481 ms`, factor `1.18`, pass under `2.0x`.
- `full-suite`: As-Is `71454.300 ms`, To-Be `57714.163 ms`, factor `0.81`, pass under `2.0x`.
- `focused-doctor-port`: As-Is failed correctness, so performance regression is not evaluated for that scenario.
