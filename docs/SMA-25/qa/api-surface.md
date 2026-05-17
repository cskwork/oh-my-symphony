# SMA-25 QA API Surface

**What**: No HTTP API surface changed in this ticket.
**Why**: QA uses the non-API fallback because the diff changes a Git/Bash auto-commit path and its tests/docs.

## Surface Map

| field | value |
|-------|-------|
| method | n/a |
| path | n/a |
| auth | n/a |
| request schema | n/a |
| response schema | n/a |
| fallback | run real test suite against As-Is and To-Be builds |

## Code Anchors

- src/symphony/workspace.py:366
- tests/test_workspace.py:461
