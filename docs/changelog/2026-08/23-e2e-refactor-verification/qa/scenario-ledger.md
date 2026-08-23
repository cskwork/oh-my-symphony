# E2E scenario ledger

| ID | Risk | Scenario | Outcome | Evidence / reason |
|---|---|---|---|---|
| browser-native | must | Run every native browser E2E flow. | PASS | `qa/shards/browser-native.md`: 5/5 passed. |
| orchestrator-native | must | Run lifecycle/deep/contract/release/backend-lifecycle E2E. | PASS | `qa/shards/orchestrator-native.md`: 77 passed, 18 capability skips. |
| full-suite | must | Run all repository tests with browser E2E enabled. | PASS | 2311 passed, 80 declared skips, 0 failures/errors. |
| live-pipeline | must | Real OpenCode worker crosses active phases and produces executable proof. | PASS | `qa/shards/live-pipeline.md`. |
| browser-live | must | Drive board, issue, run, state, refresh, console, and network surfaces. | PASS | `qa/shards/browser-live.md`; 25 actions. |
| service-stop | must | Stop the real Windows venv launcher/child using ordinary CLI stop. | PASS | Different launcher/health PIDs, matching instance ID, exit 0, both gone, port closed, record cleared. |
| doctor-static | must | Validate workflow, lint, and types. | PASS | Doctor passed; Ruff clean; Pyright 0. |
| providers | could | Repeat with every authenticated provider. | NOT COVERED | No isolated safe credentials/configuration supplied. |
| remote-trackers | could | Repeat with Linear/Jira. | NOT COVERED | No isolated remote project/credentials supplied. |
| non-windows | could | Repeat lifecycle checks on Linux/macOS. | NOT COVERED | Current host is Windows. |

Action count: 25 / 100.
