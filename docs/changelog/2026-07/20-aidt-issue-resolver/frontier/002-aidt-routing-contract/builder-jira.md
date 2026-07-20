# Builder B - Strict Jira Transport and Source Snapshot

## Decision

Live Jira intake now requires the complete routing wire contract before constructing any DTO or writing any card.
Direct `JiraInboxIssue` construction keeps append-only defaults for Frontier 001 callers, but those defaults are not
used to repair missing live fields.

## Theory

The live HTTP payload and the internal DTO have different compatibility duties. The transport must fail closed when
Jira omits or malforms authoritative routing input. The DTO remains source-compatible for local tests and callers
that construct its original four fields directly. Once live data is validated, the source snapshot records the exact
normalized fields and a deterministic revision while the file board continues to own state, priority, URL, routing,
timestamps, and evidence outside the Jira source map/marker.

## RED Proof

Command:

```bash
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_jira_intake.py -k live_wire_uses_complete_fields_and_ignores_nested_transport_keys -x
```

Exact result: exit 1; 1 failed, 51 deselected in 0.37s. `_intake_components` rejected a realistic Jira component
object containing `id` and `self` because it required the nested object to contain only `name`.

A second focused red changed the control case from NUL to an embedded newline. Exact result: exit 1; 1 failed,
58 deselected in 0.28s because a scalar Jira `name` accepted the layout control and the intake wrote the batch.

## Implemented Contract

- Search requests explicitly include `components,status,priority,updated`.
- All four fields must be present; `priority` alone may have a JSON null value.
- Components must be a bounded list of objects. Status, non-null priority, issue type, and components extract only a
  bounded non-empty `name`; unrelated Jira transport keys are ignored.
- Scalar names reject every control character. Component names reject duplicate `casefold()` identities and enforce
  count/byte limits.
- `updated` must be a timezone-bearing parseable timestamp and is normalized to UTC seconds.
- Every recorded parent is hydrated, including a child with a non-empty description. Parent components are requested
  and mandatory.
- Validation completes for the full HTTP batch before `run_jira_intake` calls the board upsert, so late invalid
  missing/wrong/control/oversize/duplicate data produces zero writes.
- `build_source_snapshot` records summary, description, sorted components, status, nullable priority, issue type,
  updated, URL, and complete nullable parent data under `aidt-jira-source-v1`. Its SHA-256 revision uses canonical
  UTF-8 JSON and is stable across component ordering.
- Legacy DTO defaults remain empty values only. Source construction no longer invents `Unknown` or an epoch timestamp.

## API Handoff

- `JiraClient.fetch_assigned_inbox() -> list[JiraInboxIssue]` now guarantees every live result has non-empty status,
  normalized updated time, computed browse URL, bounded components, nullable priority, and complete parent fields
  whenever `parent_key` is present.
- `JiraInboxIssue` adds only defaulted trailing fields: `components`, `status`, `priority`, `updated`, `url`, and
  `parent_components`. Existing direct four-field or parent-field construction remains valid.
- `build_source_snapshot(item) -> dict[str, Any]` is the structured routing input. `source["revision"]` changes only
  with normalized source semantics; callers must not parse the display marker.
- Jira intake passes that map through `ExternalSourceUpdate.source`. Refresh tests prove the source revision changes
  while local title/state/priority/URL/routing/labels/agent fields and delivery evidence remain owned locally.

## GREEN and Verification Proof

| Command | Exact result |
|---|---|
| Strict live success/failure/parent HTTP matrix | exit 0; 13 passed, 52 deselected in 0.48s |
| Complete `tests/test_jira_intake.py` | exit 0; 65 passed in 4.18s |
| Intake plus Jira tracker regressions | exit 0; 116 passed in 6.53s |
| Ruff `--no-cache` on the three owned product/test paths | exit 0; all checks passed |
| Pyright on `trackers/jira.py` and `jira_intake.py` | exit 0; 0 errors, 0 warnings, 0 informations |
| AST function/nesting audit on the three owned product/test paths | exit 0; `[]` violations for >50 lines or >4 nesting |
| `rtk git diff --check` | exit 0; no output |

No live Jira request, credential use, board activation, commit, or network mutation was performed.
